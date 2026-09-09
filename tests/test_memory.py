from datetime import UTC, datetime
from hashlib import sha256
import json

import pytest

from herdsman.classes import MemoryLeaf
from herdsman.memory import (
    MemoryCapabilities,
    MemoryFileStore,
    deliver_memory,
    eligible_memory,
    leaf_version,
)


def _leaf(tmp_path, *, leaf_id="one", claim="use tabs", scope=("src",), **updates):
    evidence_path = tmp_path / "fact.txt"
    evidence_path.write_text("fact", encoding="utf-8")
    leaf = MemoryLeaf(
        id=leaf_id,
        subject=updates.pop("subject", "style"),
        claim=claim,
        origin="salvage",
        at=datetime.now(UTC),
        evidence=[f"fact.txt@{sha256(b'fact').hexdigest()}"],
        scope=list(scope),
        lifetime="project",
        **updates,
    )
    return leaf


def test_markdown_round_trip_is_atomic_and_evidence_checked(tmp_path):
    store = MemoryFileStore(tmp_path)
    leaf = _leaf(tmp_path, body="diagnosis")
    stored = store.write(leaf)
    assert store.get("one").claim == "use tabs"
    assert stored.content_hash == store.file_hash("one")
    (tmp_path / "fact.txt").write_text("changed", encoding="utf-8")
    assert eligible_memory([store.get("one")], store=store) == []


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
    assert caps.ensure_sugar(tmp_path, "luna").is_file()
    with pytest.raises(ValueError):
        caps.for_harness("missing")
