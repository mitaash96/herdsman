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
from .daemon import Daemon, RunResponse, create_app
from .classes import Plan
from .graph import plan_graph, risk_report
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
    store = EventStore()
    try:
        selected_plan = _plan_for_initiative(store, initiative_id, plan_id)
    except (RuntimeError, ValueError, PermissionError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        store.close()

    response = _post_json(
        f"http://{host}:{port}/plans/{selected_plan}/initiatives/{initiative_id}/run",
        {"timeout": timeout},
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
