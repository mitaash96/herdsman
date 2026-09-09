"""Focused checks for herdsman.verifier (Sprint 3 pre-write verification seam).

Fixture-tree tests pin the resolution contract: valid references pass,
phantoms block with repair suggestions, unknown/dynamic constructs warn
without ever being called valid or phantom, relative imports need a target,
and changing an existing module warns with its static dependents.
"""

from pathlib import Path

import pytest
from typing import cast

from herdsman import verifier
from herdsman.verifier import Verifier, verify_proposed


def _repo(tmp_path: Path) -> Path:
    """A minimal repo shape: one package module, one dependent, one test."""
    pkg = tmp_path / "herdsman"
    pkg.mkdir()
    _ = (pkg / "__init__.py").write_text("", encoding="utf-8")
    _ = (pkg / "core.py").write_text(
        """CONSTANT = 3


class Thing:
    def ping(self) -> str:
        return "pong"


def use() -> str:
    return Thing().ping()
""",
        encoding="utf-8",
    )
    _ = (pkg / "app.py").write_text(
        """from herdsman.core import Thing


def run() -> str:
    return Thing().ping()
""",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    _ = (tests / "test_core.py").write_text(
        """from herdsman.core import Thing


def test_thing() -> None:
    assert Thing().ping() == "pong"
""",
        encoding="utf-8",
    )
    return tmp_path


def test_valid_references_pass(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = (
        "from herdsman.core import Thing, use, CONSTANT\n"
        "\n"
        "\n"
        "def check() -> str:\n"
        "    marker = 'x'\n"
        "    return Thing().ping() + marker + str(CONSTANT) + str(len(use()))\n"
    )
    report = verify_proposed(root, source)
    assert report.verdict == "PASS"
    assert report.error is None
    assert report.repairs == []
    assert report.blast_radius is None
    resolved = {ref.name: ref.detail for ref in report.references if ref.status == "resolved"}
    assert resolved["Thing"] == "herdsman.core:Thing"
    assert resolved["CONSTANT"] == "herdsman.core:CONSTANT"
    assert resolved["use"] == "herdsman.core:use"
    again = verify_proposed(root, source)
    assert again.to_dict() == report.to_dict()  # deterministic


def test_phantom_symbol_import_and_repair(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = (
        "from herdsman.core import Thingy\n"
        "import herdsman.nope\n"
        "import definitely_missing_pkg\n"
        "\n"
        "\n"
        "def check() -> None:\n"
        "    Ghost()\n"
    )
    report = Verifier(root).verify(source)
    assert report.verdict == "BLOCK"
    phantoms = {ref.name: ref.kind for ref in report.references if ref.status == "phantom"}
    assert phantoms == {
        "herdsman.core:Thingy": "from_import",
        "herdsman.nope": "import",
        "definitely_missing_pkg": "import",
        "Ghost": "name",
    }
    repairs = {repair.phantom: repair.suggestions for repair in report.repairs}
    assert "herdsman.core:Thing" in repairs["herdsman.core:Thingy"]
    assert repairs["definitely_missing_pkg"] == ()


def test_repo_module_attribute_chains(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = (
        "import herdsman.core\n"
        "\n"
        "\n"
        "def check() -> str:\n"
        "    return herdsman.core.Thing().ping() + str(herdsman.core.CONSTANT)\n"
    )
    report = Verifier(root).verify(source)
    assert report.verdict == "PASS"
    attributes = {ref.name: ref.status for ref in report.references if ref.kind == "attribute"}
    assert attributes == {"herdsman.core:Thing": "resolved", "herdsman.core:CONSTANT": "resolved"}

    phantom = Verifier(root).verify(
        "import herdsman.core\n_ = herdsman.core.Nope\n"
    )
    assert phantom.verdict == "BLOCK"
    ref = next(item for item in phantom.references if item.status == "phantom")
    assert (ref.kind, ref.name) == ("attribute", "herdsman.core:Nope")


def test_unknown_dynamic_never_phantom(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = (
        "from herdsman.core import Thing\n"
        "\n"
        "\n"
        "def weird(obj: object) -> object:\n"
        "    _ = getattr(obj, 'ghost')\n"
        "    exec('pass')\n"
        "    return obj\n"
    )
    report = Verifier(root).verify(source)
    assert report.verdict == "WARN"
    unknown = {(ref.kind, ref.name) for ref in report.references if ref.status == "unknown"}
    assert ("dynamic", "getattr('ghost')") in unknown
    assert ("dynamic", "exec") in unknown
    assert not [ref for ref in report.references if ref.status == "phantom"]

    # A getattr literal that uniquely matches a repo symbol resolves by name.
    resolved = Verifier(root).verify("from herdsman.core import Thing\n_ = getattr(Thing, 'ping')\n")
    ref = next(item for item in resolved.references if item.kind == "dynamic")
    assert ref.status == "resolved"
    assert ref.detail.startswith("herdsman.core:Thing.ping")

    # A wildcard import makes bare names unknown, never phantom.
    star = Verifier(root).verify("from herdsman.core import *\n_ = Thing\n")
    assert star.verdict == "WARN"
    thing_ref = next(item for item in star.references if item.name == "Thing")
    assert thing_ref.status == "unknown"
    assert not [item for item in star.references if item.status == "phantom"]


def test_external_and_stdlib_imports_not_phantom(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = (
        "import json\n"
        "import pydantic\n"
        "from pydantic import BaseModel\n"
        "\n"
        "\n"
        "def build() -> object:\n"
        "    _ = json.dumps({})\n"
        "    return BaseModel()\n"
    )
    report = Verifier(root).verify(source)
    assert not [ref for ref in report.references if ref.status == "phantom"]
    assert report.verdict == "PASS"
    model_ref = next(ref for ref in report.references if ref.name == "pydantic:BaseModel")
    assert model_ref.status == "resolved"
    assert "external" in model_ref.detail


def test_relative_import_needs_target(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    source = "from .core import Thing\n\n\ndef poke() -> str:\n    return Thing().ping()\n"
    adrift = Verifier(root).verify(source)
    assert adrift.verdict == "WARN"
    ref = next(item for item in adrift.references if item.kind == "from_import")
    assert ref.status == "unknown"

    anchored = Verifier(root).verify(source, target="herdsman/extra.py")
    assert anchored.verdict == "PASS"
    ref = next(item for item in anchored.references if item.name == "herdsman.core:Thing")
    assert ref.status == "resolved"
    assert anchored.blast_radius is not None
    assert anchored.blast_radius.affected_paths == ()  # new module, no dependents


def test_blast_radius_dependents(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    replacement = (
        "class Thing:\n"
        "    def ping(self) -> str:\n"
        "        return 'pong'\n"
        "\n"
        "\n"
        "def use() -> str:\n"
        "    return Thing().ping()\n"
    )
    report = Verifier(root).verify(replacement, target="herdsman/core.py")
    assert report.verdict == "WARN"
    blast = report.blast_radius
    assert blast is not None
    assert blast.target == "herdsman/core.py"
    assert blast.modified == ("Thing", "use")
    assert blast.affected_paths == ("herdsman/app.py", "tests/test_core.py")
    assert "herdsman.app:run" in blast.affected_symbols

    breaking = Verifier(root).verify(
        replacement + "\n\n\ndef gone() -> None:\n    Missing()\n", target="herdsman/core.py"
    )
    assert breaking.verdict == "BLOCK"  # phantoms outrank the dependents warning
    assert breaking.blast_radius is not None
    assert breaking.blast_radius.affected_paths

    # to_dict serializes a populated blast radius (contract-check shape).
    payload = report.to_dict()
    blast_dict = cast("dict[str, object] | None", payload["blast_radius"])
    assert blast_dict is not None
    assert blast_dict["affected_paths"] == ["herdsman/app.py", "tests/test_core.py"]


def test_target_context_binds_existing_names(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fragment = "def poke() -> str:\n    return Thing().ping()\n"
    report = Verifier(root).verify(fragment, target="herdsman/core.py")
    assert not [ref for ref in report.references if ref.status == "phantom"]
    resolved = {ref.name for ref in report.references if ref.status == "resolved"}
    assert "Thing" in resolved
    assert report.verdict == "WARN"  # dependents of core.py, not phantoms

    adrift = Verifier(root).verify(fragment)
    assert any(ref.name == "Thing" and ref.status == "phantom" for ref in adrift.references)


def test_unparsable_source_blocks(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    report = Verifier(root).verify("def broken(:\n")
    assert report.verdict == "BLOCK"
    assert report.error is not None


def test_oversized_source_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(verifier, "MAX_SOURCE_CHARS", 16)
    report = Verifier(root).verify("x = '" + "a" * 32 + "'\n")
    assert report.verdict == "BLOCK"
    assert "characters" in (report.error or "")


def test_relative_target_validation(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(ValueError, match="repository-relative"):
        _ = Verifier(root).verify("x = 1\n", target="../outside/herdsman/core.py")
