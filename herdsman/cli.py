"""CLI layer."""

import json
import sqlite3
from http.client import HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import typer
import uvicorn

from .daemon import Daemon, RunResponse, create_app
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
