"""Focused checks for the pure diff-walkthrough seam."""

import random

from herdsman.walkthrough import walkthrough


def test_groups_into_logical_cohorts():
    result = walkthrough(["herdsman/daemon.py", "tests/test_daemon.py", "pyproject.toml"])
    assert [(c.name, c.paths) for c in result.cohorts] == [
        ("(root)", ["pyproject.toml"]),
        ("daemon core", ["herdsman/daemon.py"]),
        ("tests", ["tests/test_daemon.py"]),
    ]
    assert result.total_files == 3


def test_summaries_describe_the_cohorts_behavior():
    result = walkthrough(["herdsman/a.py", "herdsman/b.py", "ui/src/app.html", "README.md"])
    by_name = {c.name: c.summary for c in result.cohorts}
    assert by_name["daemon core"] == "2 files changed; daemon and CLI behavior changed"
    assert by_name["overlay UI"] == "1 file changed; the Svelte overlay changed"
    assert by_name["(root)"] == "1 file at repository root"


def test_unknown_paths_fall_back_to_their_top_level_directory():
    result = walkthrough(["tools/gen.py"])
    assert [(c.name, c.paths, c.summary) for c in result.cohorts] == [
        ("tools", ["tools/gen.py"], "1 file under tools/")
    ]


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


def test_empty_input():
    empty = walkthrough([])
    assert empty.cohorts == [] and empty.total_files == 0
