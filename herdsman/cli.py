"""CLI layer."""

import json
import sqlite3
from collections.abc import Callable
from http.client import HTTPResponse
from pathlib import Path
from typing import Annotated, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import typer
import uvicorn

from . import nav
from .contracts import validate_checkpoint
from .daemon import Daemon, RunResponse, create_app
from .classes import Plan
from .graph import downstream_impact, plan_graph, risk_report
from .runtime import LunaConfigError, resolve_model_tiers
from .store import EventStore

app = typer.Typer(no_args_is_help=True)


@app.command()
def init() -> None:
    """Initialize the project-local Herdsman runtime."""
    try:
        store = EventStore()
    except (OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(
            f"cannot initialize project-local runtime in .herdsman: {exc}"
        ) from exc
    store.close()
    typer.echo("Initialized project-local Herdsman runtime in .herdsman/events.db")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the local daemon."""
    store = EventStore()
    try:
        uvicorn.run(create_app(Daemon(store)), host=host, port=port)
    finally:
        store.close()


@app.command()
def up(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the supported daemon and report unavailable runtime surfaces."""
    typer.echo("Starting Herdsman daemon.")
    typer.echo(
        "Herdr session not started: start a compatible herdr server separately "
        + "(or configure .herdsman/herdr.json)."
    )
    typer.echo(
        f"Browser UI not started: no runnable UI is present in this repository; "
        + f"use the daemon API at http://{host}:{port}."
    )
    serve(host, port)


@app.command()
def create(
    brief: str,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Plan one brief with the supervised frontier planner."""
    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans",
            {"brief": brief},
            timeout=130,
        )
    )


@app.command()
def run(
    initiative_id: str,
    plan_id: str | None = None,
    timeout: float = 600.0,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run one approved frontier initiative through Herdr."""
    _run_action("run", initiative_id, plan_id, timeout, host, port)


@app.command()
def retry(
    initiative_id: str,
    plan_id: str | None = None,
    timeout: float = 600.0,
    by: str = "operator",
    yes: bool = False,
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
    typer.echo(
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
        typer.echo(_post_json(f"http://{host}:{port}/plans/{plan_id}/salvage", None, timeout=30))
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
        contract = initiative.spec.contract
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
) -> None:
    """Run or retry one initiative; both print the bare checkpoint."""
    store = EventStore()
    try:
        selected_plan = _plan_for_initiative(store, initiative_id, plan_id)
        if disruptive:
            _show_impact(store, selected_plan, initiative_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    if disruptive and not yes:
        _ = typer.confirm("Proceed?", abort=True)

    payload: dict[str, object] = {"timeout": timeout}
    if by is not None:
        payload["by"] = by
    response = _post_json(
        f"http://{host}:{port}/plans/{selected_plan}/initiatives/{initiative_id}/{action}",
        payload,
        timeout=timeout + 10,
    )
    try:
        result = RunResponse.model_validate_json(response)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid Herdsman daemon response: {exc}") from exc
    if result.checkpoint is not None:
        typer.echo(result.checkpoint.model_dump_json())


@app.command(name="run-plan")
def run_plan(
    plan_id: str,
    max_concurrent: int | None = None,
    timeout: float = 600.0,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run every ready initiative in an approved plan, respecting the DAG.

    `--timeout` bounds each initiative, not the plan. The client deadline is
    derived from it, because a fully serial chain legitimately takes as long as
    the sum of its nodes -- bounding the request at one initiative's timeout
    would abandon a run that is still healthy.
    """
    nodes = _projection(plan_id, lambda plan: str(len(plan.initiatives)))
    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{plan_id}/run",
            {"timeout": timeout, "max_concurrent": max_concurrent},
            timeout=timeout * max(int(nodes), 1) + 10,
        )
    )


@app.command()
def graph(plan_id: str) -> None:
    """Print the running graph and per-node status as JSON."""
    typer.echo(_projection(plan_id, lambda plan: plan_graph(plan).model_dump_json()))


@app.command()
def risk(plan_id: str) -> None:
    """Print the plan-gate structural risk report as JSON."""
    typer.echo(
        _projection(
            plan_id,
            lambda plan: risk_report(
                plan, tiers=resolve_model_tiers()
            ).model_dump_json(),
        )
    )


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
        selected_plan = _plan_for_initiative(store, initiative_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/initiatives/{initiative_id}/settle/{checkpoint_id}",
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
    """Discard one retained attempt worktree through the running daemon."""
    store = EventStore()
    try:
        selected_plan = _plan_for_initiative(store, initiative_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/initiatives/{initiative_id}/discard/{attempt_id}",
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
            "brief": brief,
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
    initiative_id: str,
    by: str = "operator",
    plan_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Re-issue the live attempt's command in place; not a retry."""
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
        selected_plan = _plan_for_attempt(store, attempt_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/attempts/{attempt_id}/answer",
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
        selected_plan = _plan_for_attempt(store, attempt_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/attempts/{attempt_id}/auto-answer",
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
        selected_plan = _plan_for_initiative(store, initiative_id, plan_id)
        rendered = downstream_impact(
            store.load(selected_plan), initiative_id
        ).model_dump_json()
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()
    typer.echo(rendered)


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
        selected_plan = _plan_for_initiative(store, initiative_id, plan_id)
        if disruptive:
            _show_impact(store, selected_plan, initiative_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    if disruptive and not yes:
        _ = typer.confirm("Proceed?", abort=True)

    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{selected_plan}/initiatives/{initiative_id}/{action}",
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
        + f"{len(impact.started)} already ran ({started}), {pending} pending."
    )


def _plan_for_initiative(
    store: EventStore, initiative_id: str, plan_id: str | None
) -> str:
    if plan_id is not None:
        plan = store.load(plan_id)
        if initiative_id not in plan.initiatives:
            raise ValueError(f"unknown initiative {initiative_id}")
        return plan_id
    matches = [
        candidate
        for candidate in store.plans()
        if initiative_id in store.load(candidate).initiatives
    ]
    if not matches:
        raise ValueError(f"unknown initiative {initiative_id}")
    if len(matches) > 1:
        raise ValueError("initiative belongs to multiple plans; pass --plan-id")
    return matches[0]


def _plan_for_attempt(
    store: EventStore, attempt_id: str, plan_id: str | None
) -> str:
    def owns(plan_id: str) -> bool:
        return any(
            attempt.id == attempt_id
            for initiative in store.load(plan_id).initiatives.values()
            for attempt in initiative.attempts
        )

    if plan_id is not None:
        if not owns(plan_id):
            raise ValueError(f"unknown attempt {attempt_id}")
        return plan_id
    matches = [candidate for candidate in store.plans() if owns(candidate)]
    if not matches:
        raise ValueError(f"unknown attempt {attempt_id}")
    if len(matches) > 1:
        raise ValueError("attempt belongs to multiple plans; pass --plan-id")
    return matches[0]


@app.command()
def plan(plan_id: str) -> None:
    """Print one plan, projected from its event stream, as JSON."""
    store = EventStore()
    try:
        typer.echo(store.load(plan_id).model_dump_json())
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


def _post_json(url: str, payload: dict[str, object] | None, *, timeout: float) -> str:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = Request(url, data=data, headers=headers, method="POST")
    try:
        with cast(HTTPResponse, urlopen(request, timeout=timeout)) as response:
            return response.read().decode()
    except HTTPError as exc:
        raise typer.BadParameter(exc.read().decode()) from exc
    except URLError as exc:
        raise typer.BadParameter(
            f"cannot reach Herdsman daemon: {exc.reason}; start `herdsman up`"
        ) from exc


@app.command()
def approve(
    plan_id: str,
    version: int | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Approve a proposed plan through the running daemon."""
    query = f"?{urlencode({'version': version})}" if version is not None else ""
    typer.echo(
        _post_json(
            f"http://{host}:{port}/plans/{plan_id}/approve{query}",
            None,
            timeout=10,
        )
    )


@app.command()
def events(plan_id: str) -> None:
    """Print one plan's events, in append order, as NDJSON."""
    store = EventStore()
    try:
        for event in store.read(plan_id):
            typer.echo(event.model_dump_json())
    finally:
        store.close()


agent_app = typer.Typer(no_args_is_help=True)
app.add_typer(agent_app, name="agent")


@agent_app.command(name="memory")
def agent_memory(
    identifier: Annotated[str | None, typer.Argument()] = None,
    query: Annotated[str | None, typer.Option("--query")] = None,
    plan_id: Annotated[str | None, typer.Option("--plan-id")] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Pull one deterministic memory leaf through the daemon protocol."""
    if identifier is not None and query is not None:
        raise typer.BadParameter("pass an id or --query, not both")
    params: dict[str, str] = {}
    if identifier is not None:
        params["leaf_id"] = identifier
    if query is not None:
        params["query"] = query
    suffix = "" if plan_id is None else f"/plans/{plan_id}/memory"
    typer.echo(_get_json(f"http://{host}:{port}{suffix or '/memory'}?{urlencode(params)}", timeout=10))


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
