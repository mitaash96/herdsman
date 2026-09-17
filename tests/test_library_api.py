"""Sprint 9 Library API/CLI/watch: daemon routes, the $EDITOR path, and the
revision stream. Error semantics are asserted by name: unknown 404, stale
conflict 409, malformed 400.
"""

import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.request import Request

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from herdsman import cli
from herdsman.classes import Assignment, InitiativeSpec, PlanCreated, PlanProposed, Routes
from herdsman.daemon import Daemon, create_app
from herdsman.library import parse_asset
from herdsman.store import EventStore
from fastapi import FastAPI
from tests.test_daemon import CapturingRuntime, StubCollector, packet_from_command
from tests.test_kitchen_api import AT, request
from tests.test_library import bundled

LUNA = Assignment(harness="luna", model="cheap-1")


async def call(
    app: FastAPI, method: str, path: str, body: object | None = None
) -> tuple[int, dict[str, object]]:
    """The kitchen helper's shape: object (JSON dict) responses."""
    status, payload = await request(app, method, path, body)
    return status, payload


async def call_rows(
    app: FastAPI, method: str, path: str, body: object | None = None
) -> tuple[int, list[dict[str, object]]]:
    """Browse responses arrive as JSON arrays of row objects."""
    status, payload = await call(app, method, path, body)
    return status, cast(list[dict[str, object]], cast(object, payload))


def daemon(tmp_path: Path) -> tuple[EventStore, Daemon]:
    store = EventStore(tmp_path / "events.db")
    return store, Daemon(store, project_root=tmp_path)


def evidence(root: Path, relative: str) -> str:
    """A real project file referenced as ``path@sha256`` evidence."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text("proof\n", encoding="utf-8")
    return f"{relative}@{hashlib.sha256(path.read_bytes()).hexdigest()}"


@pytest.fixture
def shipped(tmp_path: Path, monkeypatch: MonkeyPatch) -> Path:
    """A small deterministic bundled tree instead of the repo's real assets."""
    root = bundled(tmp_path / "bundled")
    monkeypatch.setattr("herdsman.library._bundled_root", lambda: root)
    return root


def test_browse_show_and_create_round_trip_through_the_api(
    tmp_path: Path, shipped: Path
) -> None:
    del shipped
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, asset = await call(
            app, "POST", "/library",
            {"kind": "skill", "name": "navigate", "title": "Navigate",
             "body": "run herdsman nav codemap"},
        )
        assert status == 200
        digest = cast(str, asset["digest"])
        assert asset["ref"] == "skill/navigate"
        assert asset["origin"] == "project"

        status, rows = await call_rows(app, "GET", "/library")
        assert status == 200
        assert [row["ref"] for row in rows] == [
            "contract/default", "role/implementer", "skill/navigate",
        ]

        status, rows = await call(
            app, "GET", "/library?kind=skill&query=nothing-matches"
        )
        assert rows == []
        status, rows = await call_rows(app, "GET", "/library?status=retired")
        assert rows == []

        status, shown = await call(app, "GET", "/library/skill/navigate")
        assert status == 200
        assert shown["digest"] == digest
        assert shown["body"] == "run herdsman nav codemap"

        status, _ = await call(app, "GET", "/library/skill/absent")
        assert status == 404

        status, body = await call(
            app, "POST", "/library", {"kind": "prompt", "name": "nope"}
        )
        assert status == 400
        assert "unknown asset kind" in str(body["detail"])

        status, body = await call(
            app, "POST", "/library", {"kind": "skill", "name": "navigate"}
        )
        assert status == 400
        assert "already exists" in str(body["detail"])

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_editing_a_bundled_asset_copies_it_into_the_project(
    tmp_path: Path, shipped: Path
) -> None:
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, asset = await call(app, "GET", "/library/role/implementer")
        assert status == 200
        assert asset["origin"] == "bundled"

        status, edited = await call(
            app, "PUT", "/library/role/implementer",
            {
                "body": "Produce changes in scope, then verify.",
                "expect_digest": asset["digest"],
            },
        )
        assert status == 200
        assert edited["origin"] == "project"
        assert edited["digest"] != asset["digest"]

        status, rows = await call_rows(app, "GET", "/library?kind=role")
        by_ref = {str(row["ref"]): row for row in rows}
        assert by_ref["role/implementer"]["shadows_bundled"] is True
        # The shipped file itself was never touched.
        assert (shipped / "roles" / "implementer.md").read_text(
            encoding="utf-8"
        ).endswith("Produce changes in scope.\n")

        # Checkout hands the editor a project-local file, copy-on-edit included.
        status, checkout = await call(
            app, "POST", "/library/contract/default/checkout"
        )
        assert status == 200
        assert Path(cast(str, checkout["path"])).is_file()

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_a_stale_expect_digest_refuses_with_409(tmp_path: Path) -> None:
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, asset = await call(
            app, "POST", "/library",
            {"kind": "skill", "name": "navigate", "body": "first"},
        )
        stale = cast(str, asset["digest"])

        status, _ = await call(
            app, "PUT", "/library/skill/navigate", {"body": "second"}
        )
        assert status == 200

        status, body = await call(
            app, "PUT", "/library/skill/navigate",
            {"body": "concurrent terminal edit", "expect_digest": stale},
        )
        assert status == 409
        assert "changed since it was read" in str(body["detail"])

        status, current = await call(app, "GET", "/library/skill/navigate")
        assert current["body"] == "second"

        status, _ = await call(
            app, "PUT", "/library/skill/navigate",
            {"body": "third", "expect_digest": current["digest"]},
        )
        assert status == 200

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_malformed_requests_are_400_and_unknown_targets_are_404(
    tmp_path: Path,
) -> None:
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, _ = await call(
            app, "PUT", "/library/skill/absent", {"body": "x"}
        )
        assert status == 404

        status, _ = await call(app, "POST", "/library/skill/absent/archive")
        assert status == 404

        status, _ = await call(
            app, "POST", "/library",
            {"kind": "skill", "name": "Bad Name", "body": "x"},
        )
        assert status == 400
        status, _ = await call_rows(app, "GET", "/library?status=bogus")
        assert status == 400
        status, _ = await call_rows(app, "GET", "/library?kind=bogus")
        assert status == 400

        # A contract asset with an unrepresentable gate refuses at write time.
        status, body = await call(
            app, "POST", "/library",
            {"kind": "contract", "name": "gated", "fields": {"require_patch": "maybe"}},
        )
        assert status == 400
        assert "true or false" in str(body["detail"])

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_the_memory_shelf_browses_filters_and_retires_through_the_api(
    tmp_path: Path, shipped: Path
) -> None:
    del shipped
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, leaf = await call(
            app, "POST", "/library",
            {
                "kind": "memory-leaf", "name": "deploys", "title": "deploys",
                "body": "Deploys go through the release lane.\nMore prose.",
                "references": [evidence(tmp_path, "notes/deploys.md")],
            },
        )
        assert status == 200
        assert leaf["status"] == "active"
        assert cast(str, cast(list[object], leaf["references"])[0]).startswith(
            "notes/deploys.md@"
        )

        status, rows = await call(
            app, "GET", "/library?kind=memory-leaf&status=stale"
        )
        assert rows == []

        # A leaf marked stale on disk, out of band, is visible on the next
        # call with nothing to invalidate -- reads always reflect the disk.
        path = tmp_path / ".herdsman" / "memory" / "deploys.md"
        _ = path.write_text(
            path.read_text(encoding="utf-8").replace(
                "status: active", "status: stale"
            ),
            encoding="utf-8",
        )
        status, rows = await call_rows(
            app, "GET", "/library?kind=memory-leaf&status=stale"
        )
        assert [row["ref"] for row in rows] == [
            "memory-leaf/deploys",
        ]

        status, retired = await call(
            app, "POST", "/library/memory-leaf/deploys/archive"
        )
        assert status == 200
        assert retired["status"] == "retired"

        status, rows = await call_rows(app, "GET", "/library?kind=memory-leaf")
        assert rows == []
        status, rows = await call_rows(
            app, "GET", "/library?kind=memory-leaf&status=retired"
        )
        assert [row["ref"] for row in rows] == [
            "memory-leaf/deploys",
        ]

        status, revived = await call(
            app, "POST", "/library/memory-leaf/deploys/unarchive"
        )
        assert status == 200
        assert revived["status"] == "active"

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_checkout_hands_the_editor_a_project_path_and_digest(tmp_path: Path) -> None:
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, _ = await call(
            app, "POST", "/library",
            {"kind": "skill", "name": "navigate", "body": "run herdsman nav codemap"},
        )
        status, checkout = await call(app, "POST", "/library/skill/navigate/checkout")
        assert status == 200
        path = Path(cast(str, checkout["path"]))
        assert path.is_file()
        assert path.is_relative_to(tmp_path / ".herdsman" / "library")
        status, shown = await call(app, "GET", "/library/skill/navigate")
        assert checkout["digest"] == shown["digest"]

        status, _ = await call(app, "POST", "/library/skill/absent/checkout")
        assert status == 404

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_an_editor_write_is_visible_through_the_projection(
    tmp_path: Path, shipped: Path
) -> None:
    """The $EDITOR seam at the API level: checkout, external save, revision."""
    del shipped
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    async def scenario() -> None:
        status, checkout = await call(
            app, "POST", "/library/role/implementer/checkout"
        )
        path = Path(cast(str, checkout["path"]))
        _ = path.write_text(
            path.read_text(encoding="utf-8").replace(
                "Produce changes in scope.",
                "Produce changes in scope, then verify.",
            ),
            encoding="utf-8",
        )
        asset = parse_asset(
            path.read_text(encoding="utf-8"),
            kind="role", name="implementer", origin="project",
        )
        status, edited = await call(
            app, "PUT", "/library/role/implementer",
            {
                "title": asset.title,
                "body": asset.body,
                "references": asset.references,
                "fields": asset.fields,
                "expect_digest": checkout["digest"],
            },
        )
        assert status == 200
        assert "then verify" in cast(str, edited["body"])

        status, rows = await call_rows(app, "GET", "/library?kind=role")
        assert cast(str, rows[0]["digest"]) == edited["digest"]
        status, revision = await call(app, "GET", "/library/revision")
        assert cast(str, revision["revision"])

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_the_revision_stream_reports_an_external_change(tmp_path: Path) -> None:
    store, herd = daemon(tmp_path)
    library = herd.library()

    async def scenario() -> None:
        _ = library.create("skill", "navigate", body="first")
        stream = herd.watch_library(interval=0.05)
        first = await anext(stream)
        assert first["changed"] == []
        revision = cast(str, first["revision"])

        async def edit_soon() -> None:
            await asyncio.sleep(0.1)
            _ = library.edit("skill/navigate", body="changed on disk")

        task = asyncio.create_task(edit_soon())
        change = await asyncio.wait_for(anext(stream), timeout=5)
        _ = await task
        assert change["previous"] == revision
        assert change["changed"] == ["skill/navigate"]
        assert change["revision"] != revision

    try:
        asyncio.run(scenario())
    finally:
        store.close()


def test_cli_edit_without_an_editor_fails_explicitly(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    result = CliRunner().invoke(cli.app, ["library", "edit", "skill/navigate"])
    assert result.exit_code != 0
    assert "no $EDITOR" in result.output

    monkeypatch.setenv("EDITOR", "definitely-not-installed-editor")
    result = CliRunner().invoke(cli.app, ["library", "edit", "skill/navigate"])
    assert result.exit_code != 0
    # The rich error panel wraps and borders text; compare border-stripped.
    flat = " ".join(result.output.replace("│", " ").split())
    assert "editor 'definitely-not-installed-editor' is not installed or not on PATH" in flat


def test_cli_edit_opens_the_editor_and_records_the_revision(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    store, herd = daemon(tmp_path)
    _ = herd.library().create("skill", "navigate", body="before")
    shown = herd.library().show("skill/navigate")
    path = herd.library().checkout("skill/navigate")
    store.close()

    editor = tmp_path / "fake-editor"
    _ = editor.write_text("#!/bin/sh\necho 'edited line' >> \"$1\"\n", encoding="utf-8")
    _ = editor.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor))

    calls: list[tuple[str, bytes, str]] = []

    def http(request_obj: Request, *, timeout: float) -> BytesIO:
        del timeout
        payload = b"" if request_obj.data is None else cast(bytes, request_obj.data)
        calls.append(
            (str(request_obj.full_url), payload, str(request_obj.method))
        )
        if str(request_obj.full_url).endswith("/checkout"):
            body = '{"path": "%s", "digest": "%s"}' % (path, shown.digest)
        else:
            body = '{"digest": "after123"}'
        return BytesIO(body.encode())

    monkeypatch.setattr(cli, "urlopen", http)
    result = CliRunner().invoke(
        cli.app, ["library", "edit", "skill/navigate", "--port", "8123"]
    )
    assert result.exit_code == 0, result.output
    url, payload, method = calls[-1]
    assert url.endswith("/library/skill/navigate")
    assert method == "PUT"
    sent = cast(dict[str, object], json.loads(payload))
    assert sent["expect_digest"] == shown.digest
    assert sent["body"] == "before\nedited line"

    # An editor that saves nothing records nothing.
    calls.clear()
    quiet = tmp_path / "quiet-editor"
    _ = quiet.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    _ = quiet.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(quiet))
    result = CliRunner().invoke(
        cli.app, ["library", "edit", "skill/navigate", "--port", "8123"]
    )
    assert result.exit_code == 0
    assert "unchanged" in result.output
    assert all(method != "PUT" for _, _, method in calls)


def test_cli_browse_builds_the_query_and_validate_exits_on_errors(
    monkeypatch: MonkeyPatch,
) -> None:
    calls: list[str] = []

    def http(request_obj: Request, *, timeout: float) -> BytesIO:
        del timeout
        calls.append(f"{request_obj.method} {request_obj.full_url}")
        if "/validate" in str(request_obj.full_url):
            body = (
                '{"issues": [{"code": "reference-missing", "severity": "error",'
                ' "ref": "skill/absent", "message": "referenced asset is not in'
                ' the Library", "detail": ""}]}'
            )
        else:
            body = "[]"
        return BytesIO(body.encode())

    monkeypatch.setattr(cli, "urlopen", http)
    result = CliRunner().invoke(
        cli.app,
        ["library", "browse", "--kind", "skill", "--status", "stale,conflicted",
         "--query", "nav", "--port", "8123"],
    )
    assert result.exit_code == 0
    assert calls[-1] == (
        "GET http://127.0.0.1:8123/library?kind=skill&status=stale%2Cconflicted&query=nav"
    )

    result = CliRunner().invoke(
        cli.app, ["library", "validate", "skill/absent", "--port", "8123"]
    )
    assert result.exit_code == 1
    assert calls[-1].startswith("POST http://127.0.0.1:8123/library/validate")


def test_an_editor_change_reaches_a_newly_planned_approved_initiative(
    tmp_path: Path, shipped: Path
) -> None:
    """The sprint exit over the daemon surface: edit -> new plan -> packet."""
    del shipped
    mapping = tmp_path / ".herdsman" / "luna.json"
    mapping.parent.mkdir(parents=True, exist_ok=True)
    _ = mapping.write_text('{"binary": "luna-test"}', encoding="utf-8")
    store, herd = daemon(tmp_path)
    app = create_app(herd)

    def spec(initiative_id: str, *assets: str) -> InitiativeSpec:
        return InitiativeSpec(
            id=initiative_id,
            name=initiative_id,
            brief=f"work on {initiative_id}",
            assignment=LUNA,
            routes=Routes(writes=[f"src/{initiative_id}/**"]),
            assets=list(assets),
        )

    async def scenario() -> None:
        # The asset exists on the shelf before the first plan is approved.
        status, _ = await call(
            app, "POST", "/library",
            {"kind": "skill", "name": "navigate", "title": "Navigate",
             "body": "Produce changes in scope."},
        )
        assert status == 200

        _ = herd.append(PlanCreated(plan_id="plan_1", at=AT, brief="first plan"))
        _ = herd.append(
            PlanProposed(
                plan_id="plan_1", at=AT, version=1,
                initiatives=[spec("init_a", "skill/navigate")],
            )
        )
        status, plan = await call(app, "POST", "/plans/plan_1/approve")
        assert status == 200
        snapshot_1 = cast(
            dict[str, object], cast(dict[str, object], plan["asset_snapshots"])["1"]
        )
        frozen_1 = {
            asset["ref"]: asset
            for asset in cast(list[dict[str, object]], snapshot_1["assets"])
        }
        old_body = cast(str, frozen_1["skill/navigate"]["body"])

        # A terminal editor change: checkout, save, validated revision.
        status, checkout = await call(app, "POST", "/library/skill/navigate/checkout")
        path = Path(cast(str, checkout["path"]))
        _ = path.write_text(
            path.read_text(encoding="utf-8") + "Run `herdsman nav codemap` first.\n",
            encoding="utf-8",
        )
        asset = parse_asset(
            path.read_text(encoding="utf-8"),
            kind="skill", name="navigate", origin="project",
        )
        status, _ = await call(
            app, "PUT", "/library/skill/navigate",
            {
                "title": asset.title,
                "body": asset.body,
                "references": asset.references,
                "fields": asset.fields,
                "expect_digest": checkout["digest"],
            },
        )
        assert status == 200

        status, shown = await call(app, "GET", "/library/skill/navigate")
        assert "nav codemap" in cast(str, shown["body"])

        # A newly planned initiative, planned after the edit, freezes the edit.
        _ = herd.append(PlanCreated(plan_id="plan_2", at=AT, brief="second plan"))
        _ = herd.append(
            PlanProposed(
                plan_id="plan_2", at=AT, version=1,
                initiatives=[spec("init_b", "skill/navigate"), spec("init_c")],
            )
        )
        status, _ = await call(app, "POST", "/plans/plan_2/approve")
        assert status == 200

        # The earlier approved version keeps its exact frozen bytes.
        status, plan_1 = await call(app, "GET", "/plans/plan_1")
        snapshot = cast(
            dict[str, object],
            cast(dict[str, object], plan_1["asset_snapshots"])["1"],
        )
        frozen = {
            asset["ref"]: asset
            for asset in cast(list[dict[str, object]], snapshot["assets"])
        }
        assert frozen["skill/navigate"]["body"] == old_body

        runner = CapturingRuntime()
        _ = await herd.run_initiative(
            "plan_2", "init_b", runtime=runner, collector=StubCollector()
        )
        packet = packet_from_command(runner.commands[-1])
        carried = cast(list[dict[str, object]], packet["assets"])
        assert [item["ref"] for item in carried] == ["skill/navigate"]
        assert "nav codemap" in cast(str, carried[0]["body"])

        # And init_c declared nothing: no Library section reaches its packet.
        _ = await herd.run_initiative(
            "plan_2", "init_c", runtime=runner, collector=StubCollector()
        )
        assert "assets" not in packet_from_command(runner.commands[-1])

    try:
        asyncio.run(scenario())
    finally:
        store.close()
