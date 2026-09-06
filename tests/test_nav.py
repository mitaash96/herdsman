"""Regression checks for `herdsman nav` (Effort A).

Real-repo tests pin structural behavior (links resolve, labels appear, a
cross-module flow cites modules in order); fixture-tree tests pin the
git-less/degraded/offline behavior without touching the network.
"""

import json
import re
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from herdsman import cli, nav

REPO_ROOT = Path(__file__).resolve().parents[1]
CITATION = re.compile(r"`([\w./-]+\.(?:py|toml)):(\d+)")


def _fixture_tree(tmp_path: Path) -> None:
    """A minimal repo shape: no git, one module, one test, one unknown call."""
    _ = (tmp_path / "herdsman").mkdir()
    _ = (tmp_path / "herdsman" / "__init__.py").write_text("", encoding="utf-8")
    _ = (tmp_path / "herdsman" / "core.py").write_text(
        '''class Thing:
    """A tiny class."""

    def ping(self) -> str:
        return "pong"


def use() -> None:
    thing = Thing()
    _ = thing.ping()
    ghost()
''',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    _ = (tmp_path / "tests" / "test_core.py").write_text(
        '''from herdsman.core import Thing


def test_thing() -> None:
    assert Thing().ping() == "pong"
''',
        encoding="utf-8",
    )
    _ = (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "mini"\n\n[project.scripts]\nmini = "herdsman.core:use"\n',
        encoding="utf-8",
    )


def _alien_tree(tmp_path: Path) -> None:
    """A repo whose package is not named herdsman: discovery and resolution stay generic."""
    (tmp_path / "widget").mkdir()
    _ = (tmp_path / "widget" / "__init__.py").write_text('__all__ = ["Gadget"]\n', encoding="utf-8")
    _ = (tmp_path / "widget" / "gadget.py").write_text(
        '''class Gadget:
    """A gadget."""

    def spin(self) -> str:
        return "whirr"


def operate() -> None:
    gadget = Gadget()
    _ = gadget.spin()
    ghost()
''',
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    _ = (tmp_path / "tests" / "test_gadget.py").write_text(
        '''from widget.gadget import Gadget


def test_gadget() -> None:
    assert Gadget().spin() == "whirr"
''',
        encoding="utf-8",
    )
    _ = (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "widget"\n\n[project.scripts]\nwidget = "widget.gadget:operate"\n',
        encoding="utf-8",
    )
    # VCS/virtualenv/cache/build trees must never be indexed.
    for junk in (
        tmp_path / ".venv" / "lib" / "bogus.py",
        tmp_path / "widget" / "__pycache__" / "old.py",
        tmp_path / "build" / "out.py",
    ):
        junk.parent.mkdir(parents=True, exist_ok=True)
        _ = junk.write_text("", encoding="utf-8")


def test_generic_repo_not_named_herdsman(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _alien_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    paths = [str(f["path"]) for f in nav.build_index(tmp_path).files]
    assert "widget/gadget.py" in paths and "tests/test_gadget.py" in paths
    assert ".venv/lib/bogus.py" not in paths
    assert "widget/__pycache__/old.py" not in paths
    assert "build/out.py" not in paths

    runner = CliRunner()
    gadget = runner.invoke(cli.app, ["nav", "symbol", "Gadget"])
    assert gadget.exit_code == 0
    assert "`widget/gadget.py:" in gadget.output  # in-repo, not herdsman/
    assert "exported: yes" in gadget.output  # via widget/__init__.py __all__
    assert "Importers of widget.gadget" in gadget.output
    assert "Linked tests" in gadget.output and "tests/test_gadget.py:" in gadget.output
    assert "Facet" not in gadget.output  # curated herdsman facets must not attach

    mapped = runner.invoke(cli.app, ["nav", "codemap"])
    assert mapped.exit_code == 0
    assert "Widget codemap" in mapped.output
    assert "widget/gadget.py" in mapped.output

    toured = runner.invoke(cli.app, ["nav", "tour"])
    assert toured.exit_code == 0
    assert "widget → widget.gadget:operate" in toured.output
    assert "widget/gadget.py" in toured.output
    assert "Herdsman's daemon" not in toured.output

    flowed = runner.invoke(cli.app, ["nav", "flow", "create-approve-run-settle"])
    assert flowed.exit_code == 2
    assert "no curated flows for Widget" in flowed.output

    data = cast(
        "dict[str, object]",
        json.loads(runner.invoke(cli.app, ["nav", "codemap", "--json"]).output),
    )
    unresolved = cast("list[dict[str, object]]", data["unresolved"])
    assert any(u["name"] == "ghost" for u in unresolved)
    entry_points = cast("dict[str, object]", data["entry_points"])
    console_script = cast("dict[str, object]", entry_points["console_script"])
    assert console_script["target"] == "widget.gadget:operate"
    tests = cast("list[dict[str, object]]", entry_points["tests"])
    assert any(t["node"] == "tests/test_gadget.py::test_gadget" for t in tests)

    out = tmp_path / "guide.md"
    refreshed = runner.invoke(cli.app, ["nav", "guide", "--refresh", "--out", str(out)])
    assert refreshed.exit_code == 0
    guide = out.read_text(encoding="utf-8")
    assert "# Widget architecture guide" in guide
    assert "widget → widget.gadget:operate" in guide
    assert "class Gadget" in guide
    assert "No curated flow is declared" in guide
    assert "Herdsman's daemon" not in guide
    assert "RuntimeObserved" not in guide


def test_guide_links_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    out = tmp_path / "guide.md"
    result = CliRunner().invoke(
        cli.app, ["nav", "guide", "--refresh", "--out", str(out)]
    )

    assert result.exit_code == 0
    assert out.is_file()
    guide = out.read_text(encoding="utf-8")
    assert re.search(r"repo_ref: \S+@[0-9a-f]+", guide)  # shape, not branch-pinned
    assert "fingerprint: sha256:" in guide
    for path, line in cast("list[tuple[str, str]]", CITATION.findall(guide)):
        cited = REPO_ROOT / path
        assert cited.is_file(), f"missing citation target {path}"
        line_count = len(cited.read_text(encoding="utf-8").splitlines())
        assert 1 <= int(line) <= line_count, f"citation {path}:{line} is past end of file"
    assert "pyproject.toml" in guide and "herdsman/cli.py" in guide


def test_symbol_drilldown_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    runner = CliRunner()

    settled = runner.invoke(cli.app, ["nav", "symbol", "Daemon.run_and_settle"])
    assert settled.exit_code == 0
    assert "def run_and_settle" in settled.output
    assert "`herdsman/daemon.py:" in settled.output
    assert "Callers" in settled.output and "run_plan" in settled.output
    assert re.search(r"tests/test_\w+\.py:\d+", settled.output)
    assert "static" in settled.output and "external" in settled.output

    adapter = runner.invoke(cli.app, ["nav", "symbol", "HerdrAdapter"])
    assert adapter.exit_code == 0
    assert "herdsman/daemon.py" in adapter.output  # importer
    assert "intent unknown" in adapter.output

    fake_runtime = "tests.test_dag_run:FakeRuntime"
    false_constructions = [
        edge
        for edge in nav.build_index(REPO_ROOT).edges
        if edge["kind"] == "instantiates"
        and edge["dst"] == fake_runtime
        and edge["file"] != "tests/test_dag_run.py"
    ]
    assert not false_constructions

    missing = runner.invoke(cli.app, ["nav", "symbol", "NotAThing"])
    assert missing.exit_code == 2
    assert "unknown symbol" in missing.output


def test_flow_cross_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    runner = CliRunner()

    flowed = runner.invoke(cli.app, ["nav", "flow", "create-approve-run-settle"])
    assert flowed.exit_code == 0
    cited = cast("list[tuple[str, str]]", CITATION.findall(flowed.output))
    files = list(dict.fromkeys(path for path, _line in cited))
    assert len(files) >= 4
    assert files[0] == "herdsman/cli.py"
    for first, second in (
        ("herdsman/cli.py", "herdsman/daemon.py"),
        ("herdsman/daemon.py", "herdsman/classes.py"),
        ("herdsman/daemon.py", "herdsman/checkpoint.py"),
    ):
        assert files.index(first) < files.index(second), f"{first} must precede {second}"
    assert "[absent]" in flowed.output and "UI" in flowed.output

    unknown = runner.invoke(cli.app, ["nav", "flow", "nope"])
    assert unknown.exit_code == 2
    assert "create-approve-run-settle" in unknown.output


def test_stale_guide_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fixture_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    refreshed = runner.invoke(cli.app, ["nav", "guide", "--refresh"])
    assert refreshed.exit_code == 0
    guide_path = tmp_path / ".herdsman" / "nav" / "guide.md"
    assert guide_path.is_file()
    assert "repo_ref: unknown" in guide_path.read_text(encoding="utf-8")  # git-less

    fresh = runner.invoke(cli.app, ["nav", "guide"])
    assert fresh.exit_code == 0
    assert "fresh" in fresh.output

    core = tmp_path / "herdsman" / "core.py"
    edited = core.read_text(encoding="utf-8") + "\n\ndef extra() -> None: ...\n"
    _ = core.write_text(edited, encoding="utf-8")
    stale = runner.invoke(cli.app, ["nav", "guide"])
    assert stale.exit_code == 1
    assert "stale" in stale.output
    assert "--refresh" in stale.output

    regenerated = runner.invoke(cli.app, ["nav", "guide", "--refresh"])
    assert regenerated.exit_code == 0
    fresh_again = runner.invoke(cli.app, ["nav", "guide"])
    assert fresh_again.exit_code == 0
    assert "fresh" in fresh_again.output


def test_no_daemon_no_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fixture_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    def no_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("nav must not touch the network or a daemon")

    monkeypatch.setattr(cli, "urlopen", no_network)
    runner = CliRunner()

    help_text = runner.invoke(cli.app, ["nav", "--help"])
    assert help_text.exit_code == 0
    for command in ("guide", "codemap", "tour", "flow", "symbol"):
        assert command in help_text.output

    assert runner.invoke(cli.app, ["nav", "guide", "--refresh"]).exit_code == 0
    assert runner.invoke(cli.app, ["nav", "guide"]).exit_code == 0
    assert runner.invoke(cli.app, ["nav", "codemap"]).exit_code == 0
    assert runner.invoke(cli.app, ["nav", "tour"]).exit_code == 0
    assert runner.invoke(cli.app, ["nav", "symbol", "Thing"]).exit_code == 0

    blocker = tmp_path / "blocker"
    _ = blocker.write_text("not a directory", encoding="utf-8")
    unwritable = runner.invoke(
        cli.app, ["nav", "guide", "--refresh", "--out", str(blocker / "g.md")]
    )
    assert unwritable.exit_code == 2
    assert "cannot write guide" in unwritable.output

    flowed = runner.invoke(cli.app, ["nav", "flow", "create-approve-run-settle"])
    assert flowed.exit_code == 2
    assert "no curated flows for Mini" in flowed.output

    mapped = runner.invoke(cli.app, ["nav", "codemap", "--json"])
    assert mapped.exit_code == 0
    data = cast("dict[str, object]", json.loads(mapped.output))
    unresolved = cast("list[dict[str, object]]", data["unresolved"])
    assert any(u["name"] == "ghost" for u in unresolved)
    entry_points = cast("dict[str, object]", data["entry_points"])
    console_script = cast("dict[str, object]", entry_points["console_script"])
    assert console_script["target"] == "herdsman.core:use"
    assert not list((tmp_path / ".herdsman").rglob("*.json"))
    assert not (tmp_path / ".codegraph").exists()

    # --deep degrade: an unavailable optional tool must not fail the command.
    def broken_probe(_root: Path) -> bool:
        raise OSError("codegraph unavailable")

    monkeypatch.setattr(nav, "_codegraph_probe", broken_probe)
    deep = runner.invoke(cli.app, ["nav", "guide", "--refresh", "--deep"])
    assert deep.exit_code == 0
    assert "degraded" in deep.output
