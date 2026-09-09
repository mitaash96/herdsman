"""Focused checks for the pure diff-walkthrough seam."""

import random

from herdsman.walkthrough import walkthrough


def test_groups_by_top_level_directory():
    result = walkthrough(["herdsman/daemon.py", "tests/test_daemon.py", "pyproject.toml"])
    assert [(c.name, c.paths) for c in result.cohorts] == [
        ("(root)", ["pyproject.toml"]),
        ("herdsman", ["herdsman/daemon.py"]),
        ("tests", ["tests/test_daemon.py"]),
    ]
    assert result.total_files == 3


def test_stable_regardless_of_input_order():
    paths = [
        "herdsman/daemon.py",
        "tests/test_daemon.py",
        "pyproject.toml",
        "herdsman/cli.py",
        "ui/src/app.html",
    ]
    expected = walkthrough(paths)
    shuffled = list(paths)
    random.Random(42).shuffle(shuffled)
    assert walkthrough(shuffled) == expected
    assert walkthrough(paths + paths) == expected  # duplicates collapse


def test_mechanical_summaries_and_empty_input():
    result = walkthrough(["herdsman/a.py", "herdsman/b.py", "README.md"])
    assert [c.summary for c in result.cohorts] == [
        "1 file at repository root",
        "2 files under herdsman/",
    ]
    empty = walkthrough([])
    assert empty.cohorts == [] and empty.total_files == 0
