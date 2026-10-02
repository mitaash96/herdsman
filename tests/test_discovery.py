"""Discovery lane: read-only probes over declared Kitchen adapters."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from herdsman.discovery import (
    ProbeResult,
    Runner,
    discover,
    discoverable,
    subprocess_runner,
)
from herdsman.kitchen import Adapter, Kitchen, ModelEntry


def make_executable(path: Path, body: str) -> Path:
    _ = path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    _ = path.chmod(0o755)
    return path


def adapter(name: str, argv0: str) -> Adapter:
    return Adapter(name=name, argv=[argv0, "--print", "{prompt}"])


def stub_runner(
    *,
    returncode: int | None = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    error: str = "",
) -> tuple[list[list[str]], Runner]:
    calls: list[list[str]] = []

    def run(argv: Sequence[str], timeout: float) -> ProbeResult:
        calls.append(list(argv))
        _ = timeout
        return ProbeResult(
            returncode=returncode, stdout=stdout, stderr=stderr,
            timed_out=timed_out, error=error,
        )

    return calls, run


def kitchen(*adapters: Adapter, models: list[ModelEntry] | None = None) -> Kitchen:
    return Kitchen(adapters=list(adapters), models=models or [])


# --- healthy resolution and version ------------------------------------------


def test_absolute_executable_reports_version_and_probe_argv(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "tool", 'echo "tool 1.2.3"')
    calls, run = stub_runner(stdout="tool 1.2.3\n")
    result = discover(
        kitchen(adapter("pi", str(script))), project_root=tmp_path, runner=run
    )
    assert calls == [["herdr", "integration", "status"], [str(script), "--version"]]
    fact = result.facts[0]
    assert fact.harness == "pi"
    assert fact.executable == str(script)
    assert fact.version == "tool 1.2.3"
    assert fact.health == "healthy"
    assert fact.detail == ""


def test_path_name_resolved_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = make_executable(tmp_path / "bin" / "fake-harness", 'echo "fake 9"')
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    calls, run = stub_runner(stdout="fake 9\n")
    result = discover(kitchen(adapter("fake", "fake-harness")), runner=run)
    resolved = str(tmp_path / "bin" / "fake-harness")
    assert calls == [["herdr", "integration", "status"], [resolved, "--version"]]
    assert result.facts[0].executable == resolved
    assert result.facts[0].health == "healthy"


def test_relative_path_resolves_against_project_root(tmp_path: Path) -> None:
    _ = make_executable(tmp_path / "bin" / "tool", 'echo "tool 0"')
    calls, run = stub_runner(stdout="tool 0\n")
    result = discover(
        kitchen(adapter("tool", "bin/tool")), project_root=tmp_path, runner=run
    )
    resolved = str(tmp_path / "bin" / "tool")
    assert calls == [["herdr", "integration", "status"], [resolved, "--version"]]
    assert result.facts[0].executable == resolved


def test_default_runner_and_real_subprocess(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "real", 'echo "sh-based 4.1"')
    result = discover(kitchen(adapter("sh", str(script))))
    fact = result.facts[0]
    assert fact.health == "healthy"
    assert fact.version == "sh-based 4.1"


# --- explicit absence, failure, timeout --------------------------------------


def test_missing_path_name_is_explicit_not_healthy() -> None:
    calls, run = stub_runner()
    result = discover(kitchen(adapter("gone", "no-such-harness-xyz")), runner=run)
    assert calls == [["herdr", "integration", "status"]]
    fact = result.facts[0]
    assert fact.executable is None
    assert fact.version is None
    assert fact.health == "unknown"
    assert "not found on PATH" in fact.detail


def test_missing_absolute_executable_is_explicit(tmp_path: Path) -> None:
    calls, run = stub_runner()
    result = discover(kitchen(adapter("x", str(tmp_path / "absent"))), runner=run)
    assert calls == [["herdr", "integration", "status"]]
    fact = result.facts[0]
    assert fact.executable is None
    assert fact.health == "unknown"
    assert "not found at" in fact.detail


def test_non_executable_file_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "dormant"
    _ = path.write_text("not runnable\n", encoding="utf-8")
    calls, run = stub_runner()
    result = discover(kitchen(adapter("dormant", str(path))), runner=run)
    assert calls == [["herdr", "integration", "status"]]
    fact = result.facts[0]
    assert fact.executable is None
    assert "not executable" in fact.detail


def test_nonzero_exit_is_unhealthy(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "bad", "true")
    _, run = stub_runner(returncode=2, stderr="boom\n")
    fact = discover(kitchen(adapter("bad", str(script))), runner=run).facts[0]
    assert fact.executable == str(script)
    assert fact.health == "unhealthy"
    assert fact.version is None
    assert "exited 2" in fact.detail and "boom" in fact.detail


def test_timeout_is_unhealthy(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "slow", "sleep 5")
    _, run = stub_runner(timed_out=True)
    fact = discover(kitchen(adapter("slow", str(script))), runner=run).facts[0]
    assert fact.health == "unhealthy"
    assert fact.version is None
    assert "timed out" in fact.detail


def test_default_runner_bounds_a_hanging_probe(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "hang", "sleep 30")
    result = subprocess_runner([str(script), "--version"], 0.3)
    assert result.timed_out is True
    assert result.returncode is None


def test_spawn_failure_is_unhealthy(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "denied", "true")
    _, run = stub_runner(returncode=None, error="PermissionError")
    fact = discover(kitchen(adapter("denied", str(script))), runner=run).facts[0]
    assert fact.health == "unhealthy"
    assert "PermissionError" in fact.detail


def test_exit_zero_without_version_output_keeps_version_unknown(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "quiet", "true")
    _, run = stub_runner(returncode=0, stdout="   \n")
    fact = discover(kitchen(adapter("quiet", str(script))), runner=run).facts[0]
    assert fact.health == "healthy"
    assert fact.version is None
    assert "no version output" in fact.detail


def test_nonzero_exit_never_yields_a_version(tmp_path: Path) -> None:
    script = make_executable(tmp_path / "odd", "true")
    _, run = stub_runner(returncode=1, stdout="version-ish text\n")
    fact = discover(kitchen(adapter("odd", str(script))), runner=run).facts[0]
    assert fact.health == "unhealthy"
    assert fact.version is None


# --- surface shape -----------------------------------------------------------


def test_facts_follow_declaration_order(tmp_path: Path) -> None:
    _ = make_executable(tmp_path / "zeta", "true")
    _ = make_executable(tmp_path / "alpha", "true")
    _, run = stub_runner(stdout="v1\n")
    result = discover(
        kitchen(
            adapter("zeta", str(tmp_path / "zeta")),
            adapter("alpha", str(tmp_path / "alpha")),
        ),
        runner=run,
    )
    assert [fact.harness for fact in result.facts] == ["zeta", "alpha"]
    assert [fact.version for fact in result.facts] == ["v1", "v1"]


def test_no_models_discovered_and_declarations_authoritative() -> None:
    doc = kitchen(
        adapter("pi", "/usr/bin/pi"),
        models=[ModelEntry(harness="pi", model="opus-5")],
    )
    _, run = stub_runner()
    result = discover(doc, runner=run)
    assert result.models == []
    assert doc.models[0].model == "opus-5"


def test_empty_kitchen_yields_no_facts() -> None:
    _, run = stub_runner()
    result = discover(kitchen(), runner=run)
    assert result.facts == []


@pytest.mark.parametrize("status", [
    ProbeResult(returncode=0, stdout="pi: current (v9) (/some/path)\nopencode: outdated (v8 < v13) (/some/path)\n"),
    ProbeResult(returncode=1),
    ProbeResult(returncode=None, timed_out=True),
    ProbeResult(returncode=None, error="missing herdr"),
])
def test_integration_probe_once_and_maps_executable_kind(
    tmp_path: Path, status: ProbeResult,
) -> None:
    calls: list[list[str]] = []

    def run(argv: Sequence[str], timeout: float) -> ProbeResult:
        calls.append(list(argv))
        _ = timeout
        return status if list(argv) == ["herdr", "integration", "status"] else ProbeResult(returncode=0, stdout="v1")

    kinds = ["claude", "codex", "pi", "opencode", "unknown-kind"]
    adapters = [adapter(f"alias-{kind}", str(make_executable(tmp_path / kind, "true"))) for kind in kinds]
    result = discover(kitchen(*adapters), runner=run)
    assert calls.count(["herdr", "integration", "status"]) == 1
    assert len(calls) == 6
    assert [fact.harness for fact in result.facts] == [item.name for item in adapters]
    for kind, fact in zip(kinds, result.facts, strict=True):
        if kind in {"claude", "codex"} or (kind == "pi" and status.returncode == 0):
            assert fact.integration_action == ""
        else:
            assert fact.integration_action == f"run herdr integration install {kind}"
            assert fact.version == "v1"
    # The daemon keeps DiscoveryResult and serializes it directly; the new
    # facts must retain the corrective action on that wire projection.
    assert result.model_dump()["facts"][-1]["integration_action"] == "run herdr integration install unknown-kind"


def test_discoverable_locates_undeclared_known_harnesses_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # claude is declared by argv[0] under another name; codex is declared by
    # name; devin is on PATH and undeclared; gemini is absent from PATH.
    for exe in ("claude", "codex", "devin"):
        _ = make_executable(tmp_path / exe, "exit 1")
    monkeypatch.setenv("PATH", str(tmp_path))
    kitchen = Kitchen(adapters=[adapter("my-claude", "claude"), adapter("codex", "/x/cx")])

    found = discoverable(kitchen)

    assert [(item.harness, item.executable) for item in found] == [
        ("devin", str(tmp_path / "devin"))
    ]
