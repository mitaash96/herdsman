from datetime import UTC, datetime, timedelta
from hashlib import sha256
import asyncio
import json

import pytest

from herdsman.classes import (
    Assignment,
    InitiativeFailed,
    InitiativeSpec,
    MemoryLeaf,
    PlanApproved,
    PlanCreated,
    PlanProposed,
)
from herdsman.daemon import Daemon
from herdsman.memory import (
    MemoryCapabilities,
    MemoryFileStore,
    deliver_memory,
    eligible_memory,
    leaf_version,
)
from herdsman.runtime import PiMemoryAuthor
from herdsman.store import EventStore


def _leaf(tmp_path, *, leaf_id="one", claim="use tabs", scope=("src",), **updates):
    evidence_path = tmp_path / "fact.txt"
    evidence_path.write_text("fact", encoding="utf-8")
    leaf = MemoryLeaf(
        id=leaf_id,
        subject=updates.pop("subject", "style"),
        claim=claim,
        origin="salvage",
        at=datetime.now(UTC),
        evidence=updates.pop("evidence", [f"fact.txt@{sha256(b'fact').hexdigest()}"]),
        scope=list(scope),
        lifetime="project",
        **updates,
    )
    return leaf


def test_markdown_round_trip_is_atomic_and_evidence_checked(tmp_path):
    store = MemoryFileStore(tmp_path)
    leaf = _leaf(tmp_path, body="diagnosis")
    stored = store.write(leaf)
    stored_leaf = store.get("one")
    assert stored_leaf is not None
    assert stored_leaf.claim == "use tabs"
    assert stored.content_hash == store.file_hash("one")
    (tmp_path / "fact.txt").write_text("changed", encoding="utf-8")
    assert eligible_memory([stored_leaf], store=store) == []


def test_evidence_requires_a_full_sha256(tmp_path):
    store = MemoryFileStore(tmp_path)
    leaf = _leaf(tmp_path, leaf_id="bad", evidence=["fact.txt@"])
    with pytest.raises(ValueError, match="SHA-256"):
        store.write(leaf)


def test_run_leaves_do_not_use_project_behavioral_expiry(tmp_path):
    leaf = MemoryLeaf(
        id="run", subject="run", claim="still true", origin="nudge",
        at=datetime.now(UTC) - timedelta(days=90),
    )
    assert eligible_memory([leaf], now=datetime.now(UTC), run_count=100) == [leaf]


def test_default_project_ttl_applies_to_non_file_evidence(tmp_path):
    leaf = _leaf(
        tmp_path,
        evidence=["check:lint"],
    ).model_copy(update={"at": datetime.now(UTC) - timedelta(days=29)})
    store = MemoryFileStore(tmp_path)
    resolver = lambda ref: ref == "check:lint"
    assert eligible_memory(
        [leaf], store=store, evidence_resolver=resolver, run_count=19,
    ) == [leaf]
    assert eligible_memory(
        [leaf], store=store, evidence_resolver=resolver, run_count=20,
    ) == []


def test_markdown_round_trip_preserves_string_spine_scalars(tmp_path):
    leaf = _leaf(
        tmp_path,
        subject="123",
        claim="true",
        scope=("false",),
        by="123",
        ttl="123",
        evidence=["check:123"],
    )
    store = MemoryFileStore(tmp_path)
    store.write(leaf, resolver=lambda ref: ref == "check:123")
    restored = store.get("one")
    assert restored is not None
    assert (restored.subject, restored.claim, restored.scope, restored.by, restored.ttl) == (
        "123", "true", ["false"], "123", "123",
    )


def test_selection_is_scoped_deterministic_and_conflicts_are_excluded(tmp_path):
    first = _leaf(tmp_path, leaf_id="first", claim="one")
    second = _leaf(tmp_path, leaf_id="second", claim="two")
    docs = _leaf(tmp_path, leaf_id="docs", scope=("docs",))
    assert eligible_memory([docs], scopes=["src/app.py"], store=MemoryFileStore(tmp_path)) == []
    assert [leaf.id for leaf in eligible_memory([first], scopes=["src"], store=MemoryFileStore(tmp_path))] == ["first"]
    assert eligible_memory([first.model_copy(update={"scope": ["src"]}), second.model_copy(update={"scope": ["src"]})], scopes=["src"], store=MemoryFileStore(tmp_path)) == []


def test_delivery_hard_budgets_and_versions(tmp_path):
    leaves = [_leaf(tmp_path, leaf_id=f"leaf-{n}", claim="claim") for n in range(100)]
    pointer = deliver_memory(leaves, "A")
    inline = deliver_memory(leaves, "C")
    assert sum(len(line.split()) for line in pointer.pointers) <= 40
    assert sum(len(line.split()) for line in inline.inline) <= 200
    assert pointer.versions[0] == leaf_version(leaves[0])


def test_capability_declaration_and_class_b_sugar(tmp_path):
    path = tmp_path / ".herdsman"
    path.mkdir()
    (path / "memory.json").write_text(json.dumps({"harnesses": {"luna": "B"}}), encoding="utf-8")
    caps = MemoryCapabilities.load(tmp_path)
    assert caps.for_harness("luna") == "B"
    sugar = caps.ensure_sugar(tmp_path, "luna")
    assert sugar is not None and sugar.is_file()
    with pytest.raises(ValueError):
        caps.for_harness("missing")


def _failed_daemon(tmp_path, author=None):
    project = tmp_path / ".herdsman"
    project.mkdir()
    artifact = project / "artifacts" / "diagnostic.patch"
    artifact.parent.mkdir()
    artifact.write_text("diff --git a/a b/a\n", encoding="utf-8")
    path = project / "events.db"
    store = EventStore(path)
    at = datetime.now(UTC)
    spec = InitiativeSpec(
        id="a", name="a", brief="b", assignment=Assignment(harness="luna", model="m")
    )
    for event in (
        PlanCreated(plan_id="p", at=at, brief="b"),
        PlanProposed(plan_id="p", at=at, version=1, initiatives=[spec]),
        PlanApproved(plan_id="p", at=at, version=1),
        InitiativeFailed(
            plan_id="p", at=at, initiative_id="a", reason="bad",
            evidence=[".herdsman/artifacts/diagnostic.patch"],
        ),
    ):
        store.append(event)
    return store, Daemon(store, project_root=tmp_path, memory_author=author)


class _Author:
    def __init__(self):
        self.reports = []
        self.calls = 0

    def salvage(self, report):
        self.calls += 1
        self.reports.append(report)
        return {"leaves": [{
            "id": "salvaged", "subject": "failure", "claim": "repair it",
            "evidence": [".herdsman/artifacts/diagnostic.patch"], "scope": ["src"],
        }]}


def test_daemon_salvage_canonicalizes_preserved_paths_and_records_evidence(tmp_path):
    author = _Author()
    store, daemon = _failed_daemon(tmp_path, author)
    try:
        leaves = asyncio.run(daemon.salvage_memory("p"))
        assert leaves[0].evidence[0].startswith(".herdsman/artifacts/diagnostic.patch@")
        assert leaves[0].origin == "salvage"
        assert leaves[0].lifetime == "project"
        assert leaves[0].by == "daemon"
        assert "diff --git" in author.reports[0]
        assert store.read("p")[-1].type == "memory_use_recorded"
    finally:
        store.close()


def test_dreaming_is_idle_budgeted_and_attributed(tmp_path):
    author = _Author()
    store, daemon = _failed_daemon(tmp_path, author)
    try:
        result = asyncio.run(daemon.dream(token_budget=1000, leaf_budget=2))
        assert result["dreamed"]
        assert author.calls == 1
        assert any(event.type == "memory_digest_recorded" for event in store.read("p"))
        assert any(event.operation == "dreaming" for event in daemon.plan("p").memory_receipts)
        assert asyncio.run(daemon.dream(token_budget=1000, leaf_budget=2))["dreamed"] == []
        assert author.calls == 1
    finally:
        store.close()


def test_configured_memory_author_is_wired_from_project_config(tmp_path):
    project = tmp_path / ".herdsman"
    project.mkdir()
    (project / "memory.json").write_text(
        json.dumps({"harnesses": {"luna": "A"}, "author": {"binary": "pi", "model": "memory"}}),
        encoding="utf-8",
    )
    store = EventStore(project / "events.db")
    try:
        assert isinstance(Daemon(store, project_root=tmp_path).memory_author, PiMemoryAuthor)
    finally:
        store.close()
