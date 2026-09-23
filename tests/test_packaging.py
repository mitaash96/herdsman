import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from herdsman.classes import (
    AttemptStarted,
    Checkpoint,
    CheckpointApproved,
    CheckpointRecorded,
    Event,
    InitiativeSettled,
    Plan,
    PlanApproved,
    PlanCreated,
    PlanProposed,
)
from herdsman.demo import demo_spec


ROOT = Path(__file__).parents[1]


def _build(project: Path, destination: Path) -> Path:
    completed = subprocess.run(
        ["uv", "build", "--offline", "--wheel", "--out-dir", str(destination)],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return next(destination.glob("*.whl"))


@pytest.fixture(scope="module")
def wheels(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("packaging")
    project = root / "project"
    project.mkdir()
    _ = shutil.copy2(ROOT / "pyproject.toml", project / "pyproject.toml")
    _ = shutil.copytree(ROOT / "herdsman", project / "herdsman")
    _ = shutil.copytree(ROOT / "assets", project / "assets")

    without_ui = _build(project, root / "without-ui")
    build = project / "ui" / "build"
    (build / "_app").mkdir(parents=True)
    _ = (build / "index.html").write_text("<main>Herdsman</main>", encoding="utf-8")
    _ = (build / "_app" / "app.js").write_text("export {};", encoding="utf-8")
    with_ui = _build(project, root / "with-ui")
    return without_ui, with_ui


def test_wheel_builds_without_ui_and_includes_assets(
    wheels: tuple[Path, Path],
) -> None:
    without_ui, _ = wheels
    with ZipFile(without_ui) as archive:
        names = set(archive.namelist())
    assert "herdsman/assets/README.md" in names
    assert "herdsman/assets/skills/herdsman-nav/SKILL.md" in names
    assert not any(name.startswith("herdsman/ui/") for name in names)


def test_demo_consumer_waits_for_both_checkpoint_approvals() -> None:
    at = datetime(2026, 9, 19, tzinfo=UTC)
    specs = demo_spec("pi", "test")
    events: list[Event] = [
        PlanCreated(plan_id="demo", at=at, brief="demo"),
        PlanProposed(plan_id="demo", at=at, version=1, initiatives=specs),
        PlanApproved(plan_id="demo", at=at, version=1),
    ]
    assert Plan.fold(events).ready() == ["D1", "D2"]

    for producer in ("D1", "D2"):
        checkpoint_id = f"cp-{producer}"
        assignment = next(spec.assignment for spec in specs if spec.id == producer)
        events.extend(
            [
                AttemptStarted(
                    plan_id="demo",
                    at=at,
                    attempt_id=f"attempt-{producer}",
                    initiative_id=producer,
                    assignment=assignment,
                ),
                CheckpointRecorded(
                    plan_id="demo",
                    at=at,
                    checkpoint=Checkpoint(
                        id=checkpoint_id,
                        attempt_id=f"attempt-{producer}",
                        exit_code=0,
                    ),
                ),
                CheckpointApproved(
                    plan_id="demo",
                    at=at,
                    checkpoint_id=checkpoint_id,
                    by="operator",
                    reason="demo gate",
                ),
                InitiativeSettled(
                    plan_id="demo",
                    at=at,
                    initiative_id=producer,
                    checkpoint_id=checkpoint_id,
                ),
            ]
        )
        if producer == "D1":
            assert "D3" not in Plan.fold(events).ready()
    assert Plan.fold(events).ready() == ["D3"]


def test_wheel_includes_ui_and_lifecycle_resolves_installed_bundle(
    wheels: tuple[Path, Path], tmp_path: Path
) -> None:
    _, with_ui = wheels
    with ZipFile(with_ui) as archive:
        names = set(archive.namelist())
        assert "herdsman/ui/index.html" in names
        assert "herdsman/ui/_app/app.js" in names
        archive.extractall(tmp_path / "site")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path / "site")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from herdsman.lifecycle import ui_bundle; print(ui_bundle())",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert Path(completed.stdout.strip()).resolve() == (
        tmp_path / "site" / "herdsman" / "ui"
    ).resolve()
