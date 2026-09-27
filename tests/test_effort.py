"""Reasoning-effort discovery: local catalog readers, pools, and launch argv."""

import json
import os
from pathlib import Path
from typing import cast

import pytest

from herdsman.classes import Assignment
from herdsman.effort import (
    EFFORT_PROVIDERS,
    effective_effort,
    effort_argv,
    effort_levels,
    provider_of,
    validate_effort,
)
from herdsman.kitchen import Adapter, Kitchen, ModelEntry


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def pi_home(root: Path) -> Path:
    """A fake home whose pi store exercises the level-map rule."""
    home = root / "home"
    _ = write_json(
        home / ".pi" / "agent" / "models-store.json",
        {
            "openai-codex": {
                "models": [
                    {
                        "id": "gpt-5.6-luna",
                        "provider": "openai-codex",
                        "reasoning": True,
                        "thinkingLevelMap": {"xhigh": "xhigh", "max": "max", "minimal": "low"},
                    },
                    {
                        "id": "gpt-6-astra",
                        "provider": "openai-codex",
                        "reasoning": True,
                        "thinkingLevelMap": {
                            "off": None, "minimal": "low", "low": "low",
                            "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max",
                        },
                    },
                    {"id": "plain", "provider": "openai-codex", "reasoning": True},
                    {"id": "dormant", "provider": "openai-codex", "reasoning": False},
                ]
            }
        },
    )
    return home


def test_pi_reader_follows_the_thinking_level_map(tmp_path: Path) -> None:
    home = pi_home(tmp_path)
    levels = EFFORT_PROVIDERS["pi"][0](home)

    # xhigh/max appear only because the map names them; minimal maps to low.
    # "off" is unmapped, so it stays (only an explicit null removes a level).
    assert levels["openai-codex/gpt-5.6-luna"] == [
        "off", "minimal", "low", "medium", "high", "xhigh", "max",
    ]
    # A mapped null removes "off"; the rest stay in pi's own order.
    assert levels["openai-codex/gpt-6-astra"] == [
        "minimal", "low", "medium", "high", "xhigh", "max",
    ]
    # No map: the base set, including off.
    assert levels["openai-codex/plain"] == ["off", "minimal", "low", "medium", "high"]
    # Non-reasoning and off-only models have no effort to select.
    assert "openai-codex/dormant" not in levels


def test_pi_reader_merges_user_overrides(tmp_path: Path) -> None:
    home = pi_home(tmp_path)
    _ = write_json(
        home / ".pi" / "agent" / "models.json",
        {
            "providers": {
                "ollama": {
                    "models": [{"id": "local/thing", "reasoning": True}],
                }
            }
        },
    )
    levels = EFFORT_PROVIDERS["pi"][0](home)
    assert levels["ollama/local/thing"] == ["off", "minimal", "low", "medium", "high"]


def test_codex_reader_reads_the_cached_supported_levels(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cache: dict[str, object] = {
        "models": [
            {
                "slug": "gpt-6-astra",
                "supported_reasoning_levels": [
                    {"effort": "low"}, {"effort": "medium"}, {"effort": "ultra"},
                ],
            },
            {"slug": "no-levels", "supported_reasoning_levels": []},
        ]
    }
    _ = write_json(home / ".codex" / "models_cache.json", cache)
    levels = EFFORT_PROVIDERS["codex"][0](home)
    assert levels == {"gpt-6-astra": ["low", "medium", "ultra"]}


def test_claude_reader_uses_the_newest_cache_and_skips_no_effort_models(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    directory = home / ".claude" / "cache" / "model-catalog"
    old = write_json(
        directory / "old.json",
        cast(
            object,
            {"catalog": {"config": {"models": [{"id": "stale", "thinking": {"type": "effort", "effort_options": [{"id": "low"}]}}]}}},
        ),
    )
    newest = write_json(
        directory / "new.json",
        cast(
            object,
            {
                "catalog": {
                    "config": {
                        "models": [
                            {
                                "id": "claude-opus-5-5",
                                "thinking": {
                                    "type": "effort",
                                    "effort_options": [{"id": "low"}, {"id": "high"}],
                                },
                            },
                            {"id": "claude-haiku", "thinking": {"type": "none", "effort_options": []}},
                        ]
                    }
                }
            },
        ),
    )
    os.utime(old, (1, 1))
    os.utime(newest, (2, 2))

    levels = EFFORT_PROVIDERS["claude"][0](home)
    assert levels == {"claude-opus-5-5": ["low", "high"]}


def test_unknown_executable_and_missing_catalogs_are_absent(tmp_path: Path) -> None:
    assert provider_of("/usr/bin/luna") is None
    assert effort_argv("/usr/bin/luna", "high") == []
    assert EFFORT_PROVIDERS["codex"][0](tmp_path / "empty") == {}
    assert EFFORT_PROVIDERS["claude"][0](tmp_path / "empty") == {}


def test_effort_levels_keys_declared_pairs_by_executable_basename(tmp_path: Path) -> None:
    home = pi_home(tmp_path)
    # The adapter name is arbitrary; the executable basename picks the provider.
    kitchen = Kitchen(
        adapters=[Adapter(name="claude-code", argv=["/opt/bin/pi", "--print", "{prompt}"])],
        models=[
            ModelEntry(harness="claude-code", model="gpt-5.6-luna"),
            ModelEntry(harness="claude-code", model="unlisted"),
        ],
    )
    assert effort_levels(kitchen, home=home) == {
        "claude-code/gpt-5.6-luna": [
            "off", "minimal", "low", "medium", "high", "xhigh", "max",
        ],
    }


def test_effort_argv_templates_per_provider() -> None:
    assert effort_argv("/usr/bin/pi", "high") == ["--thinking", "high"]
    assert effort_argv("/usr/bin/codex", "ultra") == [
        "-c", "model_reasoning_effort=ultra",
    ]
    assert effort_argv("/usr/bin/claude", "max") == ["--effort", "max"]
    assert effort_argv("/usr/bin/pi", None) == []


def pool_kitchen() -> Kitchen:
    return Kitchen(
        adapters=[Adapter(name="pi", argv=["/usr/bin/pi", "--print", "{prompt}"])],
        models=[ModelEntry(harness="pi", model="m")],
        efforts={"pi/m": ["low", "high"]},
    )


def test_effective_effort_prefers_the_assignment_then_the_pools_highest() -> None:
    kitchen = pool_kitchen()
    assert effective_effort(kitchen, Assignment(harness="pi", model="m")) == "high"
    assert (
        effective_effort(kitchen, Assignment(harness="pi", model="m", effort="low"))
        == "low"
    )
    assert effective_effort(Kitchen(), Assignment(harness="pi", model="m")) is None


def test_validate_effort_refuses_outside_the_pool_and_unknown_pairs() -> None:
    kitchen = pool_kitchen()
    validate_effort(kitchen, "pi", "m", None)  # unspecified is always allowed
    validate_effort(kitchen, "pi", "m", "low")
    with pytest.raises(ValueError, match=r"pi/m does not support effort 'ultra'.*low, high"):
        validate_effort(kitchen, "pi", "m", "ultra")

    # No selected pool and no discovered levels: any explicit level is refused.
    bare = Kitchen(
        adapters=[Adapter(name="pi", argv=["/usr/bin/pi", "--print", "{prompt}"])],
        models=[ModelEntry(harness="pi", model="m")],
    )
    with pytest.raises(ValueError, match="no reasoning effort levels are known for pi/m"):
        validate_effort(bare, "pi", "m", "high")
