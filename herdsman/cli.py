"""CLI layer."""

import sqlite3
from http.client import HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import typer
import uvicorn

from .daemon import Daemon, create_app
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


@app.command()
def approve(
    plan_id: str,
    version: int | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Approve a proposed plan through the running daemon."""
    query = f"?{urlencode({'version': version})}" if version is not None else ""
    request = Request(
        f"http://{host}:{port}/plans/{plan_id}/approve{query}", data=b"", method="POST"
    )
    try:
        with cast(HTTPResponse, urlopen(request, timeout=10)) as response:
            typer.echo(response.read().decode())
    except HTTPError as exc:
        raise typer.BadParameter(exc.read().decode()) from exc
    except URLError as exc:
        raise typer.BadParameter(f"cannot reach Herdsman daemon: {exc.reason}") from exc


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
