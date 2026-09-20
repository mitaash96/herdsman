"""CLI layer."""

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import webbrowser
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from http.client import HTTPResponse
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Annotated, NamedTuple, cast, override
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import typer
import uvicorn
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from . import discovery, nav
from .classes import AssetKind, Plan
from .contracts import validate_checkpoint
from .daemon import Daemon, RunResponse, create_app
from .demo import BRIEF as DEMO_BRIEF, demo_spec
from .fleet import deep_link as fleet_deep_link
from .graph import downstream_impact, plan_graph, risk_report
from .herdr import HERDR_INSTALL_HINT, HerdrAdapter, HerdrError, pin_status
from .kitchen import KITCHEN_DIR, KITCHEN_FILE, Kitchen, KitchenConfigError
from .library import Library, parse_asset, parse_ref
from .lifecycle import (
    RECORD_PATH,
    DaemonRecord,
    clear_record,
    lock_holder,
    peek_record,
    port_free,
    read_record,
    ui_bundle,
    write_record,
)
from .memory import parse_leaf
from .runtime import LunaConfigError, resolve_harness, resolve_model_tiers
from .store import DB_PATH, LOCK_PATH, SCHEMA_VERSION, EventStore, LockBusy, migrate as migrate_store, project_lock

app = typer.Typer(no_args_is_help=True)
_output_format = "json"


@app.callback()
def options(
    project: Annotated[Path | None, typer.Option("--project", "-C")] = None,
    output_format: Annotated[str, typer.Option("--format")] = "json",
) -> None:
    """Operate on one project with stable text, JSON, or NDJSON output."""
    global _output_format
    if output_format not in {"text", "json", "ndjson"}:
        raise typer.BadParameter("--format must be text, json, or ndjson")
    root = _discover_project_root(project or Path.cwd())
    try:
        os.chdir(root)
    except OSError as exc:
        raise typer.BadParameter(f"cannot use project {root}: {exc}") from exc
    _output_format = output_format


def _discover_project_root(start: Path) -> Path:
    """Find the nearest initialized project, then the nearest Git root."""
    start = start.expanduser().resolve()
    if not start.is_dir():
        raise typer.BadParameter(f"project path is not a directory: {start}")
    candidates = (start, *start.parents)
    for marker in (KITCHEN_DIR, ".git"):
        for candidate in candidates:
            if (candidate / marker).exists():
                return candidate
    return start


def _value(raw: str | object) -> object:
    if not isinstance(raw, str):
        return raw
    try:
        return cast(object, json.loads(raw))
    except json.JSONDecodeError:
        return raw


def _emit(raw: str | object, *, text: str | None = None) -> None:
    """Emit deterministic machine output; text is intentionally plain."""
    value = _value(raw)
    if _output_format == "text":
        typer.echo(text if text is not None else json.dumps(value, indent=2, sort_keys=True))
    elif _output_format == "ndjson" and isinstance(value, list):
        for item in cast(list[object], value):
            typer.echo(json.dumps(item, separators=(",", ":"), sort_keys=True))
    else:
        typer.echo(json.dumps(value, separators=(",", ":"), sort_keys=True))


def _stdin(value: str | None, label: str) -> str:
    if value not in {None, "-"}:
        return cast(str, value)
    if sys.stdin.isatty():
        raise typer.BadParameter(f"pass {label} or pipe it on stdin")
    text = sys.stdin.read().strip()
    if not text:
        raise typer.BadParameter(f"{label} cannot be empty")
    return text


@app.command()
def init() -> None:
    """Initialize the project-local Herdsman runtime without replacing local config."""
    directory = (Path.cwd() / KITCHEN_DIR).resolve()
    events_db = directory / "events.db"
    existed = events_db.exists()
    try:
        store = EventStore(events_db)
        store.close()
        ignore = directory / ".gitignore"
        if not ignore.exists():
            _ = ignore.write_text(
                "events.db\nevents.db-*\ndaemon.json\nproject.lock\n",
                encoding="utf-8",
            )
    except (OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(
            f"cannot initialize project-local runtime in .herdsman: {exc}"
        ) from exc
    typer.echo(
        f"Already initialized at {events_db}" if existed
        else f"Initialized project-local Herdsman runtime in {events_db}"
    )


class _SPAStaticFiles(StaticFiles):
    """StaticFiles with the one fallback a client-routed SPA needs."""

    @override
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = FileResponse(Path(cast(str, self.directory)) / "index.html")
        if response.status_code == 404:
            return FileResponse(Path(cast(str, self.directory)) / "index.html")
        return response


def _daemon_app(daemon: Daemon, bundle: Path | None) -> FastAPI:
    daemon_app = create_app(daemon)

    async def create_demo() -> dict[str, object]:
        harness, model = _demo_assignment()
        plan = await daemon.create_plan(
            DEMO_BRIEF, planner=_DemoPlanner(harness, model)
        )
        return cast(dict[str, object], plan.model_dump(mode="json"))

    daemon_app.add_api_route("/demo", create_demo, methods=["POST"])
    if bundle is not None:
        daemon_app.mount("/", _SPAStaticFiles(directory=bundle, html=True), name="ui")
    return daemon_app


def _herdr_warning() -> str | None:
    adapter = HerdrAdapter(project_root=Path.cwd())
    if shutil.which(adapter.config.binary) is None:
        warning = pin_status(None, None)
        return f"herdr binary {adapter.config.binary!r} is missing; {warning}"
    try:
        asyncio.run(adapter.check_ready())
    except HerdrError as exc:
        return f"herdr server unavailable: {exc}"
    peer = cast(tuple[str, int] | None, getattr(adapter, "_ready", None))
    return pin_status(*(peer or (None, None)))


def _run_server(app_instance: FastAPI, listener: socket.socket) -> None:
    config = uvicorn.Config(app_instance, log_level="info")
    uvicorn.Server(config).run(sockets=[listener])


def _record_matches(record: DaemonRecord, host: str, port: int) -> bool:
    return record.host == host and (record.port == port or port == 0)


def _clear_own_lock() -> None:
    held, holder = lock_holder()
    if not held and holder == os.getpid():
        Path(LOCK_PATH).unlink(missing_ok=True)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the foreground daemon, publishing its address for lifecycle commands."""
    record = read_record()
    if record is not None:
        if _record_matches(record, host, port):
            typer.echo(f"Herdsman is already running at http://{host}:{port} (pid {record.pid}).")
            return
        raise typer.BadParameter(
            f"Herdsman is already running at http://{record.host}:{record.port} "
            + f"(pid {record.pid}); stop it before using {host}:{port}"
        )
    try:
        with project_lock():
            try:
                listener = socket.create_server((host, port))
            except OSError as exc:
                raise typer.BadParameter(
                    f"cannot start Herdsman on {host}:{port}: the address is already in use"
                ) from exc
            selected_port = cast(tuple[str, int], listener.getsockname())[1]
            bundle = ui_bundle()
            store: EventStore | None = None
            try:
                store = EventStore()
                _ = write_record(host, selected_port, datetime.now(UTC).isoformat())
                typer.echo(f"Herdsman daemon: http://{host}:{selected_port}")
                daemon = Daemon(store, notification_adapter=HerdrAdapter())
                _run_server(_daemon_app(daemon, bundle), listener)
            finally:
                if store is not None:
                    store.close()
                listener.close()
                current = peek_record()
                if current is not None and current.pid == os.getpid():
                    clear_record()
    except LockBusy as exc:
        _, holder = lock_holder()
        suffix = f" (pid {holder})" if holder is not None else ""
        raise typer.BadParameter(f"project is already served by another process{suffix}: {exc}") from exc
    finally:
        _clear_own_lock()


@app.command()
def up(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start one project daemon; repeated starts return the existing address."""
    record = read_record()
    if record is not None and _record_matches(record, host, port):
        typer.echo(f"Herdsman is already running at http://{host}:{port} (pid {record.pid}).")
        return
    if record is not None:
        raise typer.BadParameter(
            f"Herdsman is already running at http://{record.host}:{record.port} "
            + f"(pid {record.pid}); requested http://{host}:{port}"
        )
    if port and not port_free(host, port):
        raise typer.BadParameter(
            f"port {host}:{port} is in use, but no Herdsman daemon record claims it"
        )
    typer.echo("Starting Herdsman daemon.")
    typer.echo("Herdr session not started by Herdsman; it uses the configured external server.")
    warning = _herdr_warning()
    if warning is not None:
        typer.echo(f"Warning: {warning}")
    bundle = ui_bundle()
    if bundle is None:
        target = "the URL reported after binding" if port == 0 else f"http://{host}:{port}"
        typer.echo(f"Browser UI not started: bundle absent; serving the API at {target}.")
    elif port == 0:
        typer.echo("Browser UI will use the URL reported after binding.")
    else:
        typer.echo(f"Browser UI: http://{host}:{port}")
    serve(host, port)


def _daemon_url(record: DaemonRecord) -> str:
    return f"http://{record.host}:{record.port}"


@app.command()
def down(timeout: float = 5.0) -> None:
    """Stop the recorded daemon; an already stopped project is success."""
    record = read_record()
    if record is None:
        typer.echo("Herdsman is already down.")
        return
    if timeout < 0:
        raise typer.BadParameter("--timeout must be non-negative")
    try:
        os.kill(record.pid, signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while record.alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if record.alive():
            os.kill(record.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        raise typer.BadParameter(f"cannot stop daemon pid {record.pid}: {exc}") from exc
    clear_record()
    typer.echo(f"Stopped Herdsman daemon pid {record.pid}.")


def _open_url(record: DaemonRecord, target: str | None) -> str:
    base = _daemon_url(record)
    if target is None:
        return base + "/"
    selected = _select(target, kinds={"plan", "initiative", "checkpoint"})
    link = fleet_deep_link(
        selected.plan_id,
        initiative_id=selected.id if selected.kind == "initiative" else None,
        checkpoint_id=selected.id if selected.kind == "checkpoint" else None,
    )
    return base + link.path


@app.command(name="open")
def open_browser(
    target: Annotated[str | None, typer.Argument(help="Optional plan, initiative, or checkpoint ID")] = None,
    no_browser: bool = False,
) -> None:
    """Open the recorded daemon, or print its URL in a headless session."""
    record = read_record()
    if record is None:
        typer.echo("Herdsman is not running; start it with `herdsman up`.")
        raise typer.Exit(1)
    try:
        url = _open_url(record, target)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    headless = sys.platform not in {"darwin", "win32"} and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )
    if no_browser or headless:
        typer.echo(url)
        return
    if not webbrowser.open(url):
        typer.echo(url)


@app.command()
def create(
    brief: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Plan one brief with the supervised frontier planner."""
    _emit(
        _post_json(
            f"http://{host}:{port}/plans",
            {"brief": _stdin(brief, "a brief")},
            timeout=130,
        )
    )


@app.command()
def run(
    initiative_id: str,
    plan_id: str | None = None,
    timeout: float = 600.0,
    unattended: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run one initiative through the daemon's initiative API."""
    _run_action(
        "run", initiative_id, plan_id, timeout, host, port, unattended=unattended
    )


@app.command()
def retry(
    initiative_id: str,
    plan_id: str | None = None,
    timeout: float = 600.0,
    by: str = "operator",
    yes: bool = False,
    action_id: Annotated[str | None, typer.Option("--action-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Retry a failed initiative as a new attempt on its current brief.

    `--by` names the retrying actor on the new attempt's event. Disruptive:
    the downstream impact is shown and confirmed first; `--yes` skips the
    prompt.
    """
    _run_action(
        "retry",
        initiative_id,
        plan_id,
        timeout,
        host,
        port,
        disruptive=True,
        yes=yes,
        by=by,
        action_id=action_id,
    )


@app.command()
def resume(
    plan_id: str,
    assume_missing: bool = False,
    timeout: float = 600.0,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Reconcile a run after a daemon death; never repeats completed work.

    Surviving panes are reattached and the work the agent finished while the
    daemon was down is collected; missing panes are closed as auditable
    failures the retry path can pick up. Pending or failed initiatives are
    NOT re-driven — starting work stays with `run`/`run-plan`/`retry`.
    `--assume-missing` force-closes stale attempts without probing herdr.
    """
    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{plan_id}/resume",
            {"assume_missing": assume_missing, "timeout": timeout},
            timeout=timeout + 10,
        )
    )


@app.command()
def pause(
    initiative_id: str,
    by: str = "operator",
    reason: str = "",
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Hold a task's scheduler; a live attempt keeps running and settles."""
    _mutate_initiative(
        initiative_id, "pause", {"by": by, "reason": reason}, plan_id, host, port
    )


@app.command()
def unpause(
    initiative_id: str,
    by: str = "operator",
    reason: str = "",
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Release a paused task; the fold recomputes failed-vs-pending."""
    _mutate_initiative(
        initiative_id, "unpause", {"by": by, "reason": reason}, plan_id, host, port
    )


@app.command()
def cancel(
    initiative_id: str,
    by: str = "operator",
    reason: str = "",
    yes: bool = False,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Cancel a task for good; a live agent is interrupted, evidence preserved.

    Cancel is terminal: no retry or settlement reaches a cancelled task, and
    downstream work stays pending. Disruptive: the downstream impact is shown
    and confirmed first; --yes skips the prompt.
    """
    _mutate_initiative(
        initiative_id,
        "cancel",
        {"by": by, "reason": reason},
        plan_id,
        host,
        port,
        disruptive=True,
        yes=yes,
        timeout=30.0,
    )


@app.command()
def salvage(
    plan_id: str,
    write: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Report preserved evidence, or ask the daemon to author leaves with --write.

    Per initiative with recorded failures: its attempts, failure reasons and
    evidence files (existence and size on disk), failed checks and summaries,
    contract violations, repeated failure signatures, and the promoted
    failure leaves. Reads the event store directly, like the other read
    commands — no daemon needed.
    """
    if write:
        _emit(_post_json(f"http://{host}:{port}/plans/{plan_id}/salvage", None, timeout=30))
        return
    store = EventStore()
    try:
        plan = store.load(plan_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()
    typer.echo(_salvage_text(plan, Path.cwd()))


def _salvage_text(plan: Plan, project_root: Path) -> str:
    """The deterministic salvage report over one plan's preserved evidence."""

    def on_disk(relative: str) -> str:
        path = project_root / relative
        return f" ({path.stat().st_size} bytes)" if path.is_file() else " (missing)"

    lines: list[str] = []
    for initiative in plan.initiatives.values():
        if not initiative.failures:
            continue
        lines.append(f"{initiative.spec.id}  {initiative.state.upper()}")
        for attempt in initiative.attempts:
            lines.append(
                f"  attempt {attempt.id}  origin={attempt.origin}  by={attempt.by}  "
                + f"{attempt.assignment.harness}/{attempt.assignment.model}"
            )
            if attempt.worktree_ref is not None:
                lines.append(f"    worktree {attempt.worktree_ref} (preserved)")
        for number, failure in enumerate(initiative.failures, start=1):
            lines.append(f"  failure {number}: {failure.reason}")
            for relative in failure.evidence:
                lines.append(f"    evidence {relative}{on_disk(relative)}")
        for attempt in initiative.attempts:
            checkpoint = attempt.checkpoint
            if checkpoint is None:
                continue
            if checkpoint.patch_path is not None:
                lines.append(
                    f"    patch {checkpoint.patch_path}{on_disk(checkpoint.patch_path)}"
                )
            for check in checkpoint.checks:
                if not check.passed:
                    lines.append(f"    check {check.name}: {check.summary}")
        contract = plan.contract_for(initiative.spec.id)
        latest = initiative.latest_checkpoint
        if contract is not None and latest is not None:
            for violation in validate_checkpoint(initiative.spec, latest, contract):
                lines.append(f"    violation {violation.code}: {violation.message}")
        for (owner, check, error), record in plan.failure_signatures.items():
            if owner == initiative.spec.id:
                lines.append(
                    f"  signature x{record.count} [{check}]: {error} "
                    + f"(attempts {', '.join(record.attempts)})"
                )
        for leaf in plan.memory_leaves:
            if leaf.origin == "failure" and leaf.subject == f"{initiative.spec.id}.failure":
                lines.append(f"  leaf [{leaf.id}] {leaf.claim}")
    return "\n".join(lines) if lines else "no failure evidence recorded"


def _run_action(
    action: str,
    initiative_id: str,
    plan_id: str | None,
    timeout: float,
    host: str,
    port: int,
    *,
    disruptive: bool = False,
    yes: bool = False,
    by: str | None = None,
    action_id: str | None = None,
    unattended: bool = False,
) -> None:
    """Run or retry one initiative; both print the bare checkpoint."""
    store = EventStore()
    try:
        selected_plan, selected_initiative = _initiative_target(
            store, initiative_id, plan_id
        )
        if disruptive:
            _show_impact(store, selected_plan, selected_initiative)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    if disruptive and not yes:
        _ = typer.confirm("Proceed?", abort=True, err=True)

    payload: dict[str, object] = {"timeout": timeout}
    if unattended:
        payload["unattended"] = True
    if by is not None:
        payload["by"] = by
    if action_id is not None:
        payload["action_id"] = action_id
    response = _post_json(
        f"http://{host}:{port}/plans/{selected_plan}/initiatives/{selected_initiative}/{action}",
        payload,
        timeout=timeout + 10,
    )
    try:
        result = RunResponse.model_validate_json(response)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid Herdsman daemon response: {exc}") from exc
    if result.checkpoint is not None:
        payload = result.checkpoint.model_dump(mode="json", exclude_none=True)
        usage = cast(dict[str, object] | None, payload.get("usage"))
        if usage is not None:
            for key, default in {
                "phase": "actual",
                "category": "execution",
                "provenance": "",
                "measurement_id": None,
                "semantic_work_id": None,
                "gateway_used": False,
            }.items():
                if usage.get(key) == default:
                    _ = usage.pop(key, None)
        _emit(payload)
    else:
        _emit(None)


def _run_plan_request(
    plan_id: str,
    node_count: int,
    max_concurrent: int | None,
    timeout: float,
    unattended: bool,
    host: str,
    port: int,
) -> str:
    return _post_json(
        f"http://{host}:{port}/plans/{plan_id}/run",
        {
            "timeout": timeout,
            "max_concurrent": max_concurrent,
            "unattended": unattended,
        },
        timeout=timeout * max(node_count, 1) + 10,
    )


@app.command(name="run-plan")
def run_plan(
    plan_id: str,
    max_concurrent: int | None = None,
    timeout: float = 600.0,
    unattended: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run every ready initiative in an approved plan, respecting the DAG.

    `--timeout` bounds each initiative, not the plan. The client deadline is
    derived from it, because a fully serial chain legitimately takes as long as
    the sum of its nodes -- bounding the request at one initiative's timeout
    would abandon a run that is still healthy.
    """
    nodes = int(_projection(plan_id, lambda plan: str(len(plan.initiatives))))
    _emit(_run_plan_request(
        plan_id, nodes, max_concurrent, timeout, unattended, host, port
    ))


@app.command()
def replay(
    plan_id: str,
    seq: Annotated[int | None, typer.Option("--seq")] = None,
    at: Annotated[str | None, typer.Option("--at")] = None,
) -> None:
    """Fold a plan's historical event prefix by sequence or timestamp."""
    if seq is not None and at is not None:
        raise typer.BadParameter("choose --seq or --at, not both")
    through_at = None
    if at is not None:
        try:
            through_at = datetime.fromisoformat(at)
        except ValueError as exc:
            raise typer.BadParameter(f"invalid --at timestamp: {at}") from exc
        if through_at.tzinfo is None:
            raise typer.BadParameter("--at must include a timezone offset")
    store = EventStore()
    try:
        plan = Plan.fold(store.read(plan_id, through_seq=seq, through_at=through_at))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()
    _emit(plan.model_dump_json())

@app.command()
def digest(plan_id: str) -> None:
    """Print the deterministic policy digest with authorizing rule IDs."""
    from .policy import digest_projection

    _emit(_projection(plan_id, lambda plan: digest_projection(plan).model_dump_json()))

@app.command()
def graph(plan_id: str) -> None:
    """Print the running graph and per-node status as JSON."""
    _emit(_projection(plan_id, lambda plan: plan_graph(plan).model_dump_json()))


@app.command()
def risk(plan_id: str) -> None:
    """Print the plan-gate structural risk report as JSON."""
    _emit(
        _projection(
            plan_id,
            lambda plan: risk_report(
                plan, tiers=resolve_model_tiers()
            ).model_dump_json(),
        )
    )


@app.command()
def recalibrate(
    plan_id: str,
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    timeout: float = 120.0,
    action_id: Annotated[str | None, typer.Option("--action-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Revise a plan's remaining work through the running daemon."""
    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{plan_id}/recalibrate",
            {"reason": reason, "timeout": timeout, "action_id": action_id},
            timeout=timeout + 10,
        )
    )


@app.command()
def revision(plan_id: str) -> None:
    """Print the plan's last recalibration diff, folded locally, as JSON."""
    store = EventStore()
    try:
        _emit(Daemon(store).revision(plan_id).model_dump_json())
    except (ValueError, LunaConfigError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()


@app.command()
def status(
    plan_id: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Print deterministic status from the running Herdsman daemon."""
    _emit(_get_json(f"http://{host}:{port}/plans/{plan_id}/status", timeout=10))


@app.command()
def tokens(
    plan_id: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Print daemon token totals and every entry's provenance."""
    _emit(_get_json(f"http://{host}:{port}/plans/{plan_id}/tokens", timeout=10))


@app.command()
def watch(
    plan_id: str,
    follow: bool = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Seed from daemon status, then optionally follow its SSE event stream."""
    _emit(_get_json(f"http://{host}:{port}/plans/{plan_id}/status", timeout=10))
    if not follow:
        return
    url = f"http://{host}:{port}/plans/{plan_id}/events"
    try:
        with cast(HTTPResponse, urlopen(url, timeout=None)) as response:
            while True:
                line = response.readline()
                if not line:
                    break
                typer.echo(line.decode("utf-8", errors="replace").rstrip("\n"))
    except (OSError, URLError) as exc:
        raise typer.BadParameter(f"watch failed: {exc}") from exc


def _projection(plan_id: str, render: "Callable[[Plan], str]") -> str:
    """Read one plan from the store and render a projection of it."""
    store = EventStore()
    try:
        return render(store.load(plan_id))
    except (ValueError, LunaConfigError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()


@app.command()
def settle(
    initiative_id: str,
    checkpoint_id: str,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Settle an initiative after reviewing its recorded checkpoint."""
    store = EventStore()
    try:
        selected_plan, selected_initiative = _initiative_target(
            store, initiative_id, plan_id
        )
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/initiatives/{selected_initiative}/settle/{checkpoint_id}",
            None,
            timeout=10,
        )
    )


@app.command()
def discard(
    initiative_id: str,
    attempt_id: str,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Discard one retained attempt worktree through the running daemon.

    Also accepts a node a revision retired: the worktree of its preserved
    attempt is exactly what this command exists to release.
    """
    store = EventStore()
    try:
        selected_plan, selected_initiative = _initiative_target(
            store, initiative_id, plan_id, include_retired=True
        )
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/initiatives/{selected_initiative}/discard/{attempt_id}",
            None,
            timeout=10,
        )
    )


@app.command()
def redirect(
    initiative_id: str,
    brief: str = "",
    checkpoint_id: str | None = None,
    by: str = "operator",
    reason: str = "",
    yes: bool = False,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Point a task at a new brief version or a checkpoint to continue from.

    Exactly one of --brief and --checkpoint-id. Disruptive: the downstream
    impact is shown and confirmed first; --yes skips the prompt.
    """
    _mutate_initiative(
        initiative_id,
        "redirect",
        {
            "brief": _stdin(brief, "a brief") if brief == "-" else brief,
            "checkpoint_id": checkpoint_id,
            "by": by,
            "reason": reason,
        },
        plan_id,
        host,
        port,
        disruptive=True,
        yes=yes,
    )


@app.command()
def reassign(
    initiative_id: str,
    harness: str,
    model: str,
    by: str = "operator",
    reason: str = "",
    yes: bool = False,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Give a task a different harness/model for its next attempt.

    Disruptive: the downstream impact is shown and confirmed first; --yes
    skips the prompt.
    """
    _mutate_initiative(
        initiative_id,
        "reassign",
        {"harness": harness, "model": model, "by": by, "reason": reason},
        plan_id,
        host,
        port,
        disruptive=True,
        yes=yes,
    )


@app.command()
def nudge(
    initiative_id: str,
    text: str,
    by: str = "operator",
    ground_truth: bool = False,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Send free-text guidance to a task's live pane."""
    _mutate_initiative(
        initiative_id,
        "nudge",
        {"text": text, "by": by, "ground_truth": ground_truth},
        plan_id,
        host,
        port,
    )


@app.command()
def restart(
    initiative_id: Annotated[
        str | None,
        typer.Argument(help="Omit to restart the daemon; pass an initiative ID to restart its live task process."),
    ] = None,
    by: str = "operator",
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Restart the daemon with no ID, or one live task process with an ID."""
    if initiative_id is None:
        down()
        up(host, port)
        return
    _mutate_initiative(initiative_id, "restart", {"by": by}, plan_id, host, port)


@app.command()
def focus(
    initiative_id: str,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Focus the herdr pane running a task, from the task reference."""
    _mutate_initiative(initiative_id, "focus", None, plan_id, host, port)


@app.command()
def answer(
    attempt_id: str,
    subject: str,
    answer_text: str,
    by: str = "operator",
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Answer an agent's live block/decision request; the answer is truth."""
    store = EventStore()
    try:
        selected_plan, selected_attempt = _attempt_target(store, attempt_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/attempts/{selected_attempt}/answer",
            {"subject": subject, "answer": answer_text, "by": by},
            timeout=10,
        )
    )


@app.command(name="auto-answer")
def auto_answer(
    attempt_id: str,
    subject: str,
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Answer a repeat request mechanically from a memory leaf, if one matches."""
    store = EventStore()
    try:
        selected_plan, selected_attempt = _attempt_target(store, attempt_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/attempts/{selected_attempt}/auto-answer",
            {"subject": subject},
            timeout=10,
        )
    )


@app.command()
def impact(
    initiative_id: str,
    plan_id: str | None = None,
) -> None:
    """Preview what a disruptive action on a task would disturb, as JSON."""
    store = EventStore()
    try:
        selected_plan, selected_initiative = _initiative_target(
            store, initiative_id, plan_id
        )
        rendered = downstream_impact(
            store.load(selected_plan), selected_initiative
        ).model_dump_json()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()
    _emit(rendered)


def _mutate_initiative(
    initiative_id: str,
    action: str,
    payload: dict[str, object] | None,
    plan_id: str | None,
    host: str,
    port: int,
    *,
    disruptive: bool = False,
    yes: bool = False,
    timeout: float = 10.0,
) -> None:
    """One initiative-scoped intervention through the running daemon."""
    store = EventStore()
    try:
        selected_plan, selected_initiative = _initiative_target(
            store, initiative_id, plan_id
        )
        if disruptive:
            _show_impact(store, selected_plan, selected_initiative)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    if disruptive and not yes:
        _ = typer.confirm("Proceed?", abort=True, err=True)

    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/initiatives/{selected_initiative}/{action}",
            payload,
            timeout=timeout,
        )
    )


def _show_impact(store: EventStore, plan_id: str, initiative_id: str) -> None:
    """Print the downstream cost of a disruptive action, before confirming."""
    impact = downstream_impact(store.load(plan_id), initiative_id)
    started = ", ".join(impact.started) or "none"
    pending = len(impact.descendants) - len(impact.started)
    typer.echo(
        f"Downstream impact: {len(impact.descendants)} descendant task(s), "
        + f"{len(impact.started)} already ran ({started}), {pending} pending.",
        err=True,
    )


def _resolve_prefix(prefix: str, values: list[str], label: str) -> str:
    exact = [value for value in values if value == prefix]
    matches = exact or [value for value in values if value.startswith(prefix)]
    unique = sorted(set(matches))
    if not unique:
        raise ValueError(f"unknown {label} {prefix}")
    if len(unique) > 1:
        raise ValueError(
            f"ambiguous {label} prefix {prefix!r}: {', '.join(unique[:8])}"
        )
    return unique[0]


def _initiative_target(
    store: EventStore,
    initiative_id: str,
    plan_id: str | None,
    *,
    include_retired: bool = False,
) -> tuple[str, str]:
    plans = store.plans()
    selected_plans = (
        [_resolve_prefix(plan_id, plans, "plan")] if plan_id is not None else plans
    )
    matches: list[tuple[str, str]] = []
    for candidate in selected_plans:
        plan = store.load(candidate)
        ids = list(plan.initiatives)
        if include_retired:
            ids.extend(item.spec.id for item in plan.retired)
        matches.extend(
            (candidate, value) for value in ids if value.startswith(initiative_id)
        )
    exact = [item for item in matches if item[1] == initiative_id]
    matches = exact or matches
    if not matches:
        raise ValueError(f"unknown initiative {initiative_id}")
    if len(matches) > 1:
        raise ValueError("initiative prefix is ambiguous; pass full ids and --plan-id")
    return matches[0]


def _attempt_target(
    store: EventStore, attempt_id: str, plan_id: str | None
) -> tuple[str, str]:
    plans = store.plans()
    selected_plans = (
        [_resolve_prefix(plan_id, plans, "plan")] if plan_id is not None else plans
    )
    matches = [
        (candidate, attempt.id)
        for candidate in selected_plans
        for initiative in store.load(candidate).initiatives.values()
        for attempt in initiative.attempts
        if attempt.id == attempt_id or attempt.id.startswith(attempt_id)
    ]
    exact = [item for item in matches if item[1] == attempt_id]
    matches = exact or matches
    if not matches:
        raise ValueError(f"unknown attempt {attempt_id}")
    if len(matches) > 1:
        raise ValueError("attempt prefix is ambiguous; pass full ids and --plan-id")
    return matches[0]


@app.command()
def plan(plan_id: str) -> None:
    """Print one plan, projected from its event stream, as JSON."""
    store = EventStore()
    try:
        _emit(store.load(plan_id).model_dump_json())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()


@app.command()
def review(plan_id: str) -> None:
    """Review one proposed plan, projected from its event stream, as JSON."""
    plan(plan_id)


def _get_json(url: str, *, timeout: float) -> str:
    request = Request(url, method="GET")
    try:
        with cast(HTTPResponse, urlopen(request, timeout=timeout)) as response:
            return response.read().decode()
    except HTTPError as exc:
        raise typer.BadParameter(exc.read().decode()) from exc
    except URLError as exc:
        raise typer.BadParameter(
            f"cannot reach Herdsman daemon: {exc.reason}; start `herdsman up`"
        ) from exc


def _post_json(
    url: str,
    payload: dict[str, object] | None,
    *,
    timeout: float,
    method: str = "POST",
) -> str:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with cast(HTTPResponse, urlopen(request, timeout=timeout)) as response:
            return response.read().decode()
    except HTTPError as exc:
        raise typer.BadParameter(exc.read().decode()) from exc
    except URLError as exc:
        raise typer.BadParameter(
            f"cannot reach Herdsman daemon: {exc.reason}; start `herdsman up`"
        ) from exc


def _approve_request(
    plan_id: str, version: int | None, host: str, port: int
) -> str:
    query = f"?{urlencode({'version': version})}" if version is not None else ""
    return _post_json(
        f"http://{host}:{port}/plans/{plan_id}/approve{query}", None, timeout=10
    )


@app.command()
def approve(
    plan_id: str,
    version: int | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Approve a proposed plan through the running daemon."""
    _emit(_approve_request(plan_id, version, host, port))


@app.command()
def events(plan_id: str) -> None:
    """Print one plan's events, in append order, as NDJSON."""
    store = EventStore()
    try:
        for event in store.read(plan_id):
            typer.echo(event.model_dump_json())
    finally:
        store.close()


class Selection(NamedTuple):
    kind: str
    id: str
    plan_id: str


def _selections() -> list[Selection]:
    store = EventStore()
    try:
        found: list[Selection] = []
        for plan_id in store.plans():
            plan = store.load(plan_id)
            found.append(Selection("plan", plan_id, plan_id))
            for initiative in [*plan.initiatives.values(), *plan.retired]:
                found.append(Selection("initiative", initiative.spec.id, plan_id))
                found.extend(
                    Selection("attempt", attempt.id, plan_id)
                    for attempt in initiative.attempts
                )
                found.extend(
                    Selection("checkpoint", checkpoint.id, plan_id)
                    for checkpoint in initiative.checkpoint_versions
                )
        return found
    finally:
        store.close()


def _select(
    prefix: str, *, kinds: set[str] | None = None, plan_id: str | None = None
) -> Selection:
    candidates = [
        item
        for item in _selections()
        if (kinds is None or item.kind in kinds)
        and (plan_id is None or item.plan_id == plan_id)
        and item.id.startswith(prefix)
    ]
    exact = [item for item in candidates if item.id == prefix]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise typer.BadParameter(f"no matching id or prefix: {prefix}")
    matches = ", ".join(f"{item.kind}:{item.id}" for item in candidates[:8])
    raise typer.BadParameter(f"ambiguous id prefix {prefix!r}: {matches}")


def _selection_payload(selection: Selection) -> dict[str, object]:
    store = EventStore()
    try:
        plan = store.load(selection.plan_id)
        if selection.kind == "plan":
            return cast(dict[str, object], plan.model_dump(mode="json"))
        initiatives = [*plan.initiatives.values(), *plan.retired]
        initiative = next(
            item for item in initiatives if item.spec.id == selection.id
        ) if selection.kind == "initiative" else None
        if initiative is not None:
            return cast(dict[str, object], initiative.model_dump(mode="json"))
        for item in initiatives:
            if selection.kind == "attempt":
                attempt = next(
                    (value for value in item.attempts if value.id == selection.id), None
                )
                if attempt is not None:
                    return cast(dict[str, object], attempt.model_dump(mode="json"))
            if selection.kind == "checkpoint":
                checkpoint = next(
                    (
                        value
                        for value in item.checkpoint_versions
                        if value.id == selection.id
                    ),
                    None,
                )
                if checkpoint is not None:
                    return cast(dict[str, object], checkpoint.model_dump(mode="json"))
    finally:
        store.close()
    raise typer.BadParameter(f"unknown {selection.kind} {selection.id}")


@app.command(name="show")
def show_entity(identifier: str) -> None:
    """Show one plan, initiative/task, attempt, or checkpoint by unique prefix."""
    selected = _select(identifier)
    value = _selection_payload(selected)
    _emit(
        {"kind": selected.kind, "plan_id": selected.plan_id, "value": value},
        text=f"{selected.kind} {selected.id} (plan {selected.plan_id})\n"
        + json.dumps(value, indent=2, sort_keys=True),
    )


@app.command(name="fleet")
def fleet_command(
    archived: Annotated[bool, typer.Option("--archived")] = False,
    include_archived: Annotated[bool, typer.Option("--all")] = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """List active runs, archived runs, or both."""
    path = "/fleet/archived" if archived else "/fleet"
    if include_archived and not archived:
        path += "?include_archived=true"
    _emit(_get_json(f"http://{host}:{port}{path}", timeout=10))


@app.command()
def attention(
    blocking_only: Annotated[bool, typer.Option("--blocking-only")] = False,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Print what needs the user; exit 3 when any item is present."""
    path = "/fleet/notifications" if blocking_only else "/fleet/attention"
    body = _get_json(f"http://{host}:{port}{path}", timeout=10)
    _emit(body)
    if cast(list[object], json.loads(body)):
        raise typer.Exit(3)


@app.command(name="while-away")
def while_away(
    since: Annotated[str | None, typer.Option("--since")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Print the cross-plan digest of non-routine events since a timestamp."""
    if since is not None:
        try:
            parsed = datetime.fromisoformat(since)
        except ValueError as exc:
            raise typer.BadParameter(f"invalid --since timestamp: {since}") from exc
        if parsed.tzinfo is None:
            raise typer.BadParameter("--since must include a timezone offset")
    _emit(
        _post_json(
            f"http://{host}:{port}/while-away",
            {"since": since},
            timeout=10,
        )
    )


def _plan_action(
    action: str,
    plan_prefix: str,
    by: str,
    reason: str,
    action_id: str | None,
    host: str,
    port: int,
) -> None:
    selected = _select(plan_prefix, kinds={"plan"})
    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected.id}/{action}",
            {"by": by, "reason": reason, "action_id": action_id},
            timeout=10,
        )
    )


@app.command(name="archive-plan")
def archive_plan(
    plan_id: str,
    by: str = "operator",
    reason: str = "",
    action_id: Annotated[str | None, typer.Option("--action-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Archive one run from active fleet navigation."""
    _plan_action("archive", plan_id, by, reason, action_id, host, port)


@app.command(name="unarchive-plan")
def unarchive_plan(
    plan_id: str,
    by: str = "operator",
    reason: str = "",
    action_id: Annotated[str | None, typer.Option("--action-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Return one archived run to active fleet navigation."""
    _plan_action("unarchive", plan_id, by, reason, action_id, host, port)


@app.command()
def recovery(
    plan_id: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Show stale attempts and recoverable resources for one plan."""
    selected = _select(plan_id, kinds={"plan"})
    _emit(
        _get_json(
            f"http://{host}:{port}/plans/{selected.id}/recovery", timeout=10
        )
    )


@app.command()
def packet(
    plan_id: str,
    attempt_id: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Show the exact packet snapshot sent to one attempt."""
    selected_plan = _select(plan_id, kinds={"plan"}).id
    selected_attempt = _select(
        attempt_id, kinds={"attempt"}, plan_id=selected_plan
    ).id
    _emit(
        _get_json(
            f"http://{host}:{port}/plans/{selected_plan}/packets/{selected_attempt}",
            timeout=10,
        )
    )


@app.command(name="packet-diff")
def packet_diff(
    plan_id: str,
    before_attempt_id: str,
    after_attempt_id: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Compare the persisted packet snapshots for two attempts."""
    selected_plan = _select(plan_id, kinds={"plan"}).id
    before = _select(
        before_attempt_id, kinds={"attempt"}, plan_id=selected_plan
    ).id
    after = _select(after_attempt_id, kinds={"attempt"}, plan_id=selected_plan).id
    _emit(
        _get_json(
            f"http://{host}:{port}/plans/{selected_plan}/packets/{before}/diff/{after}",
            timeout=10,
        )
    )


@app.command()
def checkpoints(
    plan_id: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """List checkpoint review state for one plan."""
    selected = _select(plan_id, kinds={"plan"})
    _emit(
        _get_json(
            f"http://{host}:{port}/plans/{selected.id}/checkpoints", timeout=10
        )
    )


@app.command(name="checkpoint")
def checkpoint_action(
    checkpoint_id: str,
    verdict: Annotated[str, typer.Argument(help="approve, reject, or changes")],
    reason: str = "",
    by: str = "operator",
    action_id: Annotated[str | None, typer.Option("--action-id")] = None,
    plan_id: Annotated[str | None, typer.Option("--plan-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Approve, reject, or request changes on one checkpoint."""
    if verdict not in {"approve", "reject", "changes"}:
        raise typer.BadParameter("verdict must be approve, reject, or changes")
    if verdict != "approve" and not reason.strip():
        raise typer.BadParameter("--reason is required for reject and changes")
    selected_plan = None
    if plan_id is not None:
        selected_plan = _select(plan_id, kinds={"plan"}).id
    selected = _select(
        checkpoint_id, kinds={"checkpoint"}, plan_id=selected_plan
    )
    _emit(
        _post_json(
            f"http://{host}:{port}/plans/{selected.plan_id}/checkpoints/{selected.id}/{verdict}",
            {"by": by, "reason": reason, "action_id": action_id},
            timeout=10,
        )
    )


@app.command(name="deep-link")
def deep_link(identifier: str, base_url: str = "") -> None:
    """Print the stable Run-view link for a plan, initiative, or checkpoint."""
    selected = _select(identifier, kinds={"plan", "initiative", "checkpoint"})
    link = fleet_deep_link(
        selected.plan_id,
        initiative_id=selected.id if selected.kind == "initiative" else None,
        checkpoint_id=selected.id if selected.kind == "checkpoint" else None,
    )
    path = link.path
    _emit({"path": path, "url": base_url.rstrip("/") + path}, text=base_url.rstrip("/") + path)


def _wait_state(selection: Selection, host: str, port: int) -> tuple[str, object]:
    body = cast(
        dict[str, object],
        json.loads(
            _get_json(
                f"http://{host}:{port}/plans/{selection.plan_id}/status", timeout=10
            )
        ),
    )
    graph = cast(dict[str, object], body["graph"])
    nodes = cast(list[dict[str, object]], graph["nodes"])
    if selection.kind == "initiative":
        node = next(
            (item for item in nodes if item["initiative_id"] == selection.id),
            None,
        )
        if node is None:
            raise typer.BadParameter(
                f"initiative {selection.id} is not present in daemon status graph"
            )
        return cast(str, node["state"]), node
    states = [cast(str, item["state"]) for item in nodes]
    if states and all(state == "settled" for state in states):
        return "settled", body
    if any(state == "failed" for state in states):
        return "failed", body
    if any(state == "cancelled" for state in states):
        return "cancelled", body
    return "running", body


@app.command(name="wait")
def wait_command(
    identifier: Annotated[str | None, typer.Argument()] = None,
    attention_only: Annotated[bool, typer.Option("--attention")] = False,
    timeout: float = 0,
    interval: float = 1,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Wait for settlement or, with --attention, the next attention item."""
    if timeout < 0 or interval <= 0:
        raise typer.BadParameter("--timeout must be non-negative and --interval positive")
    if not attention_only and identifier is None:
        raise typer.BadParameter("pass a plan/initiative id or use --attention")
    selection = (
        None
        if attention_only
        else _select(cast(str, identifier), kinds={"plan", "initiative"})
    )
    deadline = None if timeout == 0 else time.monotonic() + timeout
    while True:
        if attention_only:
            body = _get_json(f"http://{host}:{port}/fleet/attention", timeout=10)
            items = cast(list[object], json.loads(body))
            if items:
                _emit(items)
                raise typer.Exit(3)
        else:
            state, value = _wait_state(cast(Selection, selection), host, port)
            if state == "settled":
                _emit(value)
                return
            if state in {"failed", "cancelled"}:
                _emit(value)
                raise typer.Exit(4)
        if deadline is not None and time.monotonic() >= deadline:
            raise typer.Exit(5)
        time.sleep(interval)


agent_app = typer.Typer(no_args_is_help=True)
app.add_typer(agent_app, name="agent")


@agent_app.command(name="memory")
def agent_memory(
    identifier: Annotated[str | None, typer.Argument()] = None,
    query: Annotated[str | None, typer.Option("--query")] = None,
    scope: Annotated[list[str] | None, typer.Option("--scope")] = None,
    attempt_id: Annotated[str | None, typer.Option("--attempt-id")] = None,
    plan_id: Annotated[str | None, typer.Option("--plan-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Pull one deterministic memory leaf through the daemon protocol."""
    if identifier is not None and query is not None:
        raise typer.BadParameter("pass an id or --query, not both")
    params: dict[str, str | list[str]] = {}
    if identifier is not None:
        params["leaf_id"] = identifier
    if query is not None:
        params["query"] = query
    if scope:
        params["scope"] = scope
    if attempt_id is not None:
        params["attempt_id"] = attempt_id
    suffix = "" if plan_id is None else f"/plans/{plan_id}/memory"
    _emit(_get_json(f"http://{host}:{port}{suffix or '/memory'}?{urlencode(params, doseq=True)}", timeout=10))


nav_app = typer.Typer(no_args_is_help=True)
app.add_typer(nav_app, name="nav")


@nav_app.command(name="guide")
def nav_guide(
    refresh: Annotated[bool, typer.Option("--refresh")] = False,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    deep: Annotated[bool, typer.Option("--deep")] = False,
) -> None:
    """Report guide freshness, or generate it with --refresh."""
    out = out or nav.GUIDE_PATH
    if refresh:
        try:
            index = nav.refresh_guide(Path.cwd(), out, deep=deep)
        except nav.NavError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(
            f"{out} written (fingerprint {index.fingerprint}, repo {index.repo_ref or 'unknown'})"
        )
        typer.echo(nav.coverage_line(index))
        return
    status, recorded = nav.guide_status(Path.cwd(), out)
    typer.echo(f"{out} — {status} (fingerprint {recorded or 'n/a'})")
    if status == "fresh":
        return
    typer.echo("Regenerate with: herdsman nav guide --refresh")
    raise typer.Exit(1)


@nav_app.command(name="codemap")
def nav_codemap(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Print the module map, entry points, and unresolved edges."""
    index = nav.build_index(Path.cwd())
    typer.echo(nav.codemap_json(index) if as_json else nav.codemap_text(index))


@nav_app.command()
def tour() -> None:
    """Guided tour with file:line citations and comprehension checkpoints."""
    typer.echo(nav.tour_text(nav.build_index(Path.cwd())))


@nav_app.command()
def flow(name: str) -> None:
    """Trace a named end-to-end flow across modules."""
    try:
        typer.echo(nav.flow_text(nav.build_index(Path.cwd()), name))
    except nav.NavError as exc:
        raise typer.BadParameter(str(exc)) from exc


@nav_app.command()
def symbol(name: str) -> None:
    """Show one symbol's callers, callees, tests, and labeled edges."""
    try:
        typer.echo(nav.symbol_text(nav.build_index(Path.cwd()), name))
    except nav.NavError as exc:
        raise typer.BadParameter(str(exc)) from exc


library_app = typer.Typer(no_args_is_help=True)
app.add_typer(library_app, name="library")


class LibraryEditorError(ValueError):
    """The ``$EDITOR`` authoring path cannot run: unset, missing, or failed."""


def _editor_argv() -> list[str]:
    """The configured editor as argv words, resolved before anything is opened."""
    editor = os.environ.get("EDITOR", "").strip()
    if not editor:
        raise LibraryEditorError(
            "no $EDITOR is configured; set it (e.g. export EDITOR=vim) and retry"
        )
    argv = shlex.split(editor)
    if shutil.which(argv[0]) is None:
        raise LibraryEditorError(
            f"editor {argv[0]!r} is not installed or not on PATH"
        )
    return argv


def _open_editor(path: Path) -> None:
    """Launch the configured editor on ``path``; argv only, never a shell."""
    argv = [*_editor_argv(), str(path)]
    try:
        completed = subprocess.run(argv, check=False)
    except FileNotFoundError as exc:
        raise LibraryEditorError(
            f"editor {argv[0]!r} is not installed or not on PATH"
        ) from exc
    if completed.returncode != 0:
        raise LibraryEditorError(
            f"editor {argv[0]!r} exited with {completed.returncode}; "
            + "nothing was recorded"
        )


def _edited_fields(kind: str, name: str, path: Path) -> dict[str, object]:
    """Parse the saved editor file back into a Library edit payload.

    A memory leaf is edited in its own store format, so its subject and body
    map to the shelf's title/body and its evidence stays untouched.
    """
    text = path.read_text(encoding="utf-8")
    try:
        if kind == "memory-leaf":
            leaf = parse_leaf(text)
            return {
                "title": leaf.subject,
                "body": leaf.body,
                "status": leaf.status,
            }
        asset = parse_asset(
            text, kind=cast(AssetKind, kind), name=name, origin="project"
        )
    except ValueError as exc:
        raise typer.BadParameter(
            f"the edited file is not a valid {kind}: {exc}"
        ) from exc
    return {
        "title": asset.title,
        "body": asset.body,
        "references": asset.references,
        "fields": asset.fields,
    }


@library_app.command()
def browse(
    kind: Annotated[str | None, typer.Option("--kind")] = None,
    status: Annotated[str, typer.Option("--status")] = "active",
    query: Annotated[str | None, typer.Option("--query")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Browse the shelf; --status takes all/stale/conflicted/retired or a list."""
    params: dict[str, str] = {}
    if kind is not None:
        params["kind"] = kind
    if status != "active":
        params["status"] = status
    if query is not None:
        params["query"] = query
    suffix = f"?{urlencode(params)}" if params else ""
    _emit(_get_json(f"http://{host}:{port}/library{suffix}", timeout=10))


@library_app.command()
def show(ref: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Show one asset, project copy over bundled, with its revision digest."""
    kind, name = parse_ref(ref)
    _emit(_get_json(f"http://{host}:{port}/library/{kind}/{name}", timeout=10))


@library_app.command(name="create")
def library_create(
    kind: str,
    name: str,
    title: Annotated[str, typer.Option("--title")] = "",
    body: Annotated[str, typer.Option("--body")] = "",
    ref: Annotated[list[str] | None, typer.Option("--ref")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Author a new project-local asset; --ref repeats references/evidence.

    For a memory-leaf the first body line is its claim and --ref names its
    evidence (``path@sha256``); for a contract asset the gates are added by
    editing the created file with `herdsman library edit`.
    """
    _emit(
        _post_json(
            f"http://{host}:{port}/library",
            {
                "kind": kind,
                "name": name,
                "title": title,
                "body": body,
                "references": list(ref or ()),
            },
            timeout=10,
        )
    )


@library_app.command()
def edit(
    ref: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Open $EDITOR on the asset's project file, then record the new revision.

    The daemon checks out a project-local copy (copy-on-edit for a bundled
    asset); the save is applied against the digest read at checkout, so a
    concurrent edit refuses as a conflict instead of being overwritten.
    """
    try:
        _ = _editor_argv()
    except LibraryEditorError as exc:
        raise typer.BadParameter(str(exc)) from exc
    kind, name = parse_ref(ref)
    base = f"http://{host}:{port}"
    checkout = cast(
        dict[str, object],
        json.loads(
            _post_json(f"{base}/library/{kind}/{name}/checkout", None, timeout=10)
        ),
    )
    path = Path(cast(str, checkout["path"]))
    before = path.read_bytes()
    _open_editor(path)
    if path.read_bytes() == before:
        typer.echo(f"{ref} unchanged; revision stays {checkout['digest']}")
        return
    payload = _edited_fields(kind, name, path)
    payload["expect_digest"] = checkout["digest"]
    _emit(
        _post_json(
            f"{base}/library/{kind}/{name}", payload, method="PUT", timeout=10
        )
    )


@library_app.command()
def copy(ref: str, new_name: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Duplicate an asset under a new name in the project, bundled included."""
    kind, name = parse_ref(ref)
    _emit(
        _post_json(
            f"http://{host}:{port}/library/{kind}/{name}/copy",
            {"name": new_name},
            timeout=10,
        )
    )


@library_app.command()
def rename(ref: str, new_name: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Move a project-local asset to a new name; bundled assets cannot move."""
    kind, name = parse_ref(ref)
    _emit(
        _post_json(
            f"http://{host}:{port}/library/{kind}/{name}/rename",
            {"name": new_name},
            timeout=10,
        )
    )


@library_app.command()
def archive(ref: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Retire an asset so it leaves the active shelf (memory leaves too)."""
    kind, name = parse_ref(ref)
    _emit(
        _post_json(f"http://{host}:{port}/library/{kind}/{name}/archive", None, timeout=10)
    )


@library_app.command()
def unarchive(ref: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Bring a retired asset back to the active shelf."""
    kind, name = parse_ref(ref)
    _emit(
        _post_json(
            f"http://{host}:{port}/library/{kind}/{name}/unarchive", None, timeout=10
        )
    )


@library_app.command()
def validate(
    refs: list[str],
    owner: Annotated[str, typer.Option("--owner")] = "",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Validate reference closure, conflicts, and context size; errors exit 1."""
    body = _post_json(
        f"http://{host}:{port}/library/validate",
        {"refs": list(refs), "owner": owner},
        timeout=10,
    )
    _emit(body)
    issues = cast(list[dict[str, object]], json.loads(body)["issues"])
    if any(issue["severity"] == "error" for issue in issues):
        raise typer.Exit(1)


@library_app.command(name="watch")
def library_watch(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Follow the daemon's library revision stream until interrupted."""
    url = f"http://{host}:{port}/library/events"
    try:
        with cast(HTTPResponse, urlopen(url, timeout=None)) as response:
            while True:
                line = response.readline()
                if not line:
                    break
                typer.echo(line.decode("utf-8", errors="replace").rstrip("\n"))
    except (OSError, URLError) as exc:
        raise typer.BadParameter(f"library watch failed: {exc}") from exc


config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config")


def _kitchen_payload(kitchen: Kitchen) -> dict[str, object]:
    return cast(
        dict[str, object], kitchen.model_dump(mode="json", exclude={"notes"})
    )


def _config_value(payload: object, dotted: str) -> object:
    current = payload
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            raise typer.BadParameter(f"unknown config key {dotted!r}")
        current = cast(dict[str, object], current)[part]
    return current


@config_app.command(name="show")
def config_show() -> None:
    """Show the exact effective project-local Kitchen configuration."""
    try:
        kitchen = Kitchen.load(Path.cwd())
    except KitchenConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(_kitchen_payload(kitchen))


@config_app.command(name="get")
def config_get(key: str) -> None:
    """Get one dotted Kitchen key."""
    try:
        value = _config_value(_kitchen_payload(Kitchen.load(Path.cwd())), key)
    except KitchenConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(value, text=str(value))


@config_app.command(name="set")
def config_set(key: str, value: str) -> None:
    """Set one dotted Kitchen key; VALUE accepts JSON or a plain string."""
    try:
        current = Kitchen.load(Path.cwd())
    except KitchenConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    payload = _kitchen_payload(current)
    parts = key.split(".")
    target: dict[str, object] = payload
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            raise typer.BadParameter(f"unknown config key {key!r}")
        target = cast(dict[str, object], child)
    if parts[-1] not in target:
        raise typer.BadParameter(f"unknown config key {key!r}")
    target[parts[-1]] = _value(_stdin(value, "a value"))
    try:
        updated = Kitchen.model_validate(payload)
        canonical = Path.cwd() / KITCHEN_DIR / KITCHEN_FILE
        revision = updated.save(
            Path.cwd(), expect_revision=current.revision if canonical.exists() else ""
        )
    except (KitchenConfigError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit({"key": key, "revision": revision, "value": _config_value(payload, key)})


@config_app.command(name="edit")
def config_edit() -> None:
    """Edit the whole Kitchen document through $EDITOR, then validate and save."""
    try:
        current = Kitchen.load(Path.cwd())
        _ = _editor_argv()
    except (KitchenConfigError, LibraryEditorError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    temporary: Path | None = None
    edited = False
    keep_temporary = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8", delete=False
        ) as handle:
            json.dump(_kitchen_payload(current), handle, indent=2, sort_keys=True)
            _ = handle.write("\n")
            temporary = Path(handle.name)
        before = temporary.read_bytes()
        _open_editor(temporary)
        edited = True
        if temporary.read_bytes() == before:
            _emit({"changed": False, "revision": current.revision})
            return
        updated = Kitchen.model_validate_json(temporary.read_text(encoding="utf-8"))
        canonical = Path.cwd() / KITCHEN_DIR / KITCHEN_FILE
        revision = updated.save(
            Path.cwd(), expect_revision=current.revision if canonical.exists() else ""
        )
        _emit({"changed": True, "revision": revision})
    except (OSError, ValueError, KitchenConfigError, LibraryEditorError) as exc:
        if edited and temporary is not None:
            keep_temporary = True
            raise typer.BadParameter(
                f"{exc}; edited file preserved at {temporary}"
            ) from exc
        raise typer.BadParameter(str(exc)) from exc
    finally:
        if temporary is not None and not keep_temporary:
            temporary.unlink(missing_ok=True)


@config_app.command(name="validate")
def config_validate() -> None:
    """Validate the project-local Kitchen document without changing it."""
    try:
        kitchen = Kitchen.load(Path.cwd())
    except KitchenConfigError as exc:
        _emit({"valid": False, "error": str(exc)})
        raise typer.Exit(1) from exc
    _emit({"valid": True, "revision": kitchen.revision})


@config_app.command(name="discover")
def config_discover(
    timeout: float = 10,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Refresh read-only harness discovery through the daemon."""
    _emit(
        _post_json(
            f"http://{host}:{port}/kitchen/discovery",
            {"timeout": timeout},
            timeout=timeout + 10,
        )
    )


def _check(name: str, status: str, detail: str, remedy: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "remedy": remedy}


def _requires_python() -> tuple[int, int, str]:
    try:
        requirement = metadata("herdsman").get("Requires-Python") or ">=3.14"
    except PackageNotFoundError:
        requirement = ">=3.14"
        with suppress(OSError):
            text = Path("pyproject.toml").read_text(encoding="utf-8")
            match = re.search(r'^requires-python\s*=\s*"([^"]+)"', text, re.MULTILINE)
            if match:
                requirement = match[1]
    match = re.search(r">=\s*(\d+)\.(\d+)", requirement)
    if match is None:
        return 3, 14, requirement
    return int(match[1]), int(match[2]), requirement


def _local_path(path: Path) -> Path:
    root = Path.cwd().resolve()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"refusing to touch path outside project: {resolved}")
    return resolved


def _fix_project() -> list[str]:
    fixed: list[str] = []
    record_path = _local_path(RECORD_PATH)
    record = peek_record(record_path)
    if record_path.exists() and (record is None or not record.alive()):
        record_path.unlink(missing_ok=True)
        fixed.append("removed stale daemon record")
    lock_path = _local_path(LOCK_PATH)
    held, _ = lock_holder(lock_path)
    if lock_path.exists() and not held:
        lock_path.unlink(missing_ok=True)
        fixed.append("removed stale project lock")
    db_path = _local_path(DB_PATH)
    if db_path.is_file():
        db = sqlite3.connect(db_path)
        try:
            try:
                applied = migrate_store(db)
            except (sqlite3.Error, ValueError):
                applied = []
        finally:
            db.close()
        if applied:
            fixed.append("migrated schema to " + ", ".join(map(str, applied)))
    return fixed


def _doctor_checks(host: str, port: int) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    required_major, required_minor, requirement = _requires_python()
    python_ok = sys.version_info >= (required_major, required_minor)
    checks.append(_check(
        "python", "pass" if python_ok else "fail",
        f"{sys.version_info.major}.{sys.version_info.minor} (requires {requirement})",
        f"install Python {required_major}.{required_minor} or newer",
    ))

    directory = Path(KITCHEN_DIR)
    initialized = directory.is_dir()
    checks.append(_check(
        "project", "pass" if initialized else "fail",
        "initialized" if initialized else f"{directory} is missing",
        "run `herdsman init` in the project root",
    ))
    writable = initialized and os.access(directory, os.W_OK)
    checks.append(_check(
        "project-writable", "pass" if writable else "fail",
        "writable" if writable else f"{directory} is not writable",
        f"grant the current user write access to {directory}",
    ))

    db_path = Path(DB_PATH)
    if not db_path.is_file():
        checks.extend([
            _check("events-db", "fail", "events.db is missing", "run `herdsman init`"),
            _check("database-integrity", "fail", "not checked", "restore or initialize events.db"),
            _check("schema", "fail", "not checked", "run `herdsman migrate` after restoring events.db"),
        ])
    else:
        try:
            db = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
            try:
                integrity = cast(str, db.execute("PRAGMA integrity_check").fetchone()[0])
                version = cast(int, db.execute("PRAGMA user_version").fetchone()[0])
            finally:
                db.close()
            checks.append(_check("events-db", "pass", str(db_path), "none"))
            checks.append(_check(
                "database-integrity", "pass" if integrity == "ok" else "fail", integrity,
                "restore events.db from backup; repair never rewrites event history",
            ))
            schema_status = "pass" if version == SCHEMA_VERSION else "fail"
            remedy = (
                "none" if version == SCHEMA_VERSION
                else "run `herdsman doctor --fix` or `herdsman migrate`"
                if version < SCHEMA_VERSION else "upgrade Herdsman before opening this store"
            )
            checks.append(_check("schema", schema_status, f"{version} (current {SCHEMA_VERSION})", remedy))
        except sqlite3.Error as exc:
            checks.extend([
                _check("events-db", "fail", str(exc), "restore events.db from backup"),
                _check("database-integrity", "fail", "unreadable", "restore events.db from backup"),
                _check("schema", "fail", "unreadable", "restore events.db, then run `herdsman migrate`"),
            ])

    record_path = Path(RECORD_PATH)
    record = peek_record(record_path)
    stale_record = record_path.exists() and (record is None or not record.alive())
    checks.append(_check(
        "daemon-record", "fail" if stale_record else "pass",
        "stale" if stale_record else (_daemon_url(record) if record else "absent"),
        "run `herdsman doctor --fix` to remove the stale record",
    ))
    held, holder = lock_holder()
    stale_lock = Path(LOCK_PATH).exists() and not held
    checks.append(_check(
        "project-lock", "fail" if stale_lock else "pass",
        (f"held by pid {holder}" if held else "stale" if stale_lock else "absent"),
        "run `herdsman doctor --fix` to remove an unlocked stale lock",
    ))
    claimed = record is not None and record.alive() and (record.host, record.port) == (host, port)
    bindable = claimed or port_free(host, port)
    checks.append(_check(
        "port", "pass" if bindable else "fail",
        f"{host}:{port} is " + ("the running daemon" if claimed else "bindable" if bindable else "in use"),
        "choose another --port or stop the process using this address",
    ))

    adapter = HerdrAdapter(project_root=Path.cwd())
    binary = shutil.which(adapter.config.binary)
    if binary is None:
        warning = pin_status(None, None)
        checks.append(_check("herdr", "warn", f"binary missing; {warning}", HERDR_INSTALL_HINT))
    else:
        try:
            asyncio.run(adapter.check_ready())
            peer = cast(tuple[str, int] | None, getattr(adapter, "_ready", None))
            warning = pin_status(*(peer or (None, None)))
            detail = warning or (
                f"{peer[0]} protocol {peer[1]}" if peer is not None else "version unknown"
            )
            checks.append(_check(
                "herdr", "warn" if warning else "pass", detail, HERDR_INSTALL_HINT,
            ))
        except HerdrError as exc:
            checks.append(_check("herdr", "warn", f"binary at {binary}; server unavailable: {exc}", "start `herdr server` and rerun doctor"))

    try:
        kitchen = Kitchen.load(Path.cwd())
        facts = discovery.discover(kitchen, project_root=Path.cwd(), timeout=2).facts
        missing = [fact.detail for fact in facts if fact.executable is None]
        checks.append(_check(
            "harnesses", "fail" if missing else "pass",
            "; ".join(missing) if missing else f"{len(facts)} configured harness(es) resolvable",
            "install the missing binary or correct its project-local Kitchen argv",
        ))
    except KitchenConfigError as exc:
        checks.append(_check("harnesses", "fail", str(exc), "fix .herdsman/kitchen.json"))

    bundle = ui_bundle()
    checks.append(_check(
        "ui-bundle", "pass" if bundle else "warn",
        str(bundle) if bundle else "absent; API-only mode is available",
        "install a wheel containing the UI bundle or run the UI build",
    ))
    bundled = Library(Path.cwd()).bundled_root
    assets_ok = bundled.is_dir() and all(
        (bundled / directory).is_dir() for directory in ("agents", "roles", "skills")
    )
    checks.append(_check(
        "bundled-assets", "pass" if assets_ok else "fail", str(bundled),
        "reinstall Herdsman so its bundled assets are present",
    ))
    return checks


@app.command()
def doctor(host: str = "127.0.0.1", port: int = 8000, fix: bool = False) -> None:
    """Diagnose this project; --fix applies only safe project-local repairs."""
    fixed = _fix_project() if fix else []
    checks = _doctor_checks(host, port)
    failed = any(check["status"] == "fail" for check in checks)
    _emit({"checks": checks, "fixed": fixed, "ok": not failed})
    if failed:
        raise typer.Exit(1)


@app.command(name="migrate")
def migrate_command() -> None:
    """Apply pending event-store schema versions."""
    path = Path(DB_PATH)
    if not path.is_file():
        raise typer.BadParameter("events.db is missing; run `herdsman init`")
    try:
        with project_lock():
            db = sqlite3.connect(path)
            try:
                applied = migrate_store(db)
            finally:
                db.close()
    except (LockBusy, sqlite3.Error, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        _clear_own_lock()
    _emit({"applied": applied, "schema_version": SCHEMA_VERSION})


@app.command()
def repair() -> None:
    """Repair disposable local state; report event-log damage without rewriting it."""
    fixed = _fix_project()
    path = Path(DB_PATH)
    if not path.is_file():
        _emit({"fixed": fixed, "integrity": "missing", "ok": False})
        raise typer.Exit(1)
    try:
        with project_lock():
            db = sqlite3.connect(path)
            try:
                integrity = cast(str, db.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity == "ok":
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    fixed.append("checkpointed WAL")
            finally:
                db.close()
    except LockBusy as exc:
        _, holder = lock_holder()
        raise typer.BadParameter(f"project lock is held by pid {holder}: {exc}") from exc
    except sqlite3.Error as exc:
        integrity = str(exc)
    finally:
        _clear_own_lock()
    ok = integrity == "ok"
    _emit({
        "fixed": fixed,
        "integrity": integrity,
        "ok": ok,
        "remedy": "none" if ok else "restore events.db from backup; event history was not rewritten",
    })
    if not ok:
        raise typer.Exit(1)


def _prune_candidates() -> list[dict[str, str]]:
    candidates: dict[Path, str] = {}
    replay = Path(KITCHEN_DIR) / "replay"
    if replay.is_dir():
        for path in replay.iterdir():
            candidates[path] = "old replay scratch"
    worktrees = Path(KITCHEN_DIR) / "worktrees"
    if worktrees.is_dir():
        for path in worktrees.iterdir():
            if path.is_symlink() and not path.exists():
                candidates[path] = "orphaned worktree reference"
    if Path(DB_PATH).is_file():
        archived: dict[str, bool] = {}
        try:
            db = sqlite3.connect(f"file:{Path(DB_PATH).resolve()}?mode=ro", uri=True)
            try:
                rows = cast(
                    list[tuple[str, str]],
                    db.execute(
                        "SELECT plan_id, type FROM events "
                        + "WHERE type IN ('plan_archived', 'plan_unarchived') ORDER BY seq"
                    ).fetchall(),
                )
            finally:
                db.close()
            for plan_id, event_type in rows:
                archived[plan_id] = event_type == "plan_archived"
            for plan_id, is_archived in archived.items():
                derived = Path(KITCHEN_DIR) / "artifacts" / plan_id
                if is_archived and derived.exists():
                    candidates[derived] = "archived plan derived artifacts"
        except sqlite3.Error:
            pass
    return [
        {"path": str(path), "reason": reason}
        for path, reason in sorted(candidates.items(), key=lambda item: str(item[0]))
    ]


@app.command()
def prune(apply: bool = False) -> None:
    """Preview disposable derived files; --apply removes only those listed."""
    candidates = _prune_candidates()
    if apply:
        root = Path.cwd().resolve()
        for item in candidates:
            path = (root / item["path"]).absolute()
            if root not in path.parents:
                raise typer.BadParameter(f"refusing to prune path outside project: {path}")
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
    _emit({"applied": apply, "items": candidates, "event_history_deleted": False})


class _DemoPlanner:
    harness: str
    model: str

    def __init__(self, harness: str, model: str) -> None:
        self.harness = harness
        self.model = model

    async def propose(self, brief: str) -> object:
        del brief
        return {"initiatives": [
            spec.model_dump(mode="json") for spec in demo_spec(self.harness, self.model)
        ]}


def _demo_assignment() -> tuple[str, str]:
    kitchen = Kitchen.load(Path.cwd())
    assignment = kitchen.defaults.initiative or kitchen.defaults.planner
    return (
        (assignment.harness, assignment.model)
        if assignment else ("claude-code", "claude-opus-5")
    )


@app.command()
def demo(
    dry_run: bool = False,
    timeout: float = 600.0,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Show or run the bundled parallel-two-then-gated-one demonstration."""
    try:
        harness, model = _demo_assignment()
    except KitchenConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    specs = demo_spec(harness, model)
    if dry_run:
        _emit({
            "brief": DEMO_BRIEF,
            "default": f"{harness}/{model}",
            "initiatives": [spec.model_dump(mode="json") for spec in specs],
        })
        return
    try:
        _ = resolve_harness(harness, project_root=Path.cwd())
    except LunaConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    created = cast(
        dict[str, object],
        json.loads(_post_json(f"http://{host}:{port}/demo", None, timeout=10)),
    )
    plan_id = created.get("id")
    if not isinstance(plan_id, str):
        raise typer.BadParameter("invalid demo plan response: missing plan id")
    _ = _approve_request(plan_id, None, host, port)
    _emit(_run_plan_request(plan_id, len(specs), None, timeout, False, host, port))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
