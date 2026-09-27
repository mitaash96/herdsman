"""Local, read-only reasoning-effort levels and their launch argv.

Reasoning levels are harness data: pi's ``off..max``, codex's extra ``ultra``,
claude's ``low..max`` are carried verbatim and are never narrowed to a Herdsman
enum. One table, keyed by the *executable basename* of a declared adapter -- the
adapter name is arbitrary, the basename is what will actually run -- pairs each
provider's on-disk catalog reader with the argv template that selects one level.

Readers touch only files the installed CLI already keeps under the home
directory. A missing file, a parse fault, or an unknown executable is *absent*
(no levels, no flag), never a guessed list, and nothing here spawns a process.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from .classes import Assignment
from .kitchen import Kitchen

__all__ = [
    "EFFORT_PLACEHOLDER",
    "EFFORT_PROVIDERS",
    "effective_effort",
    "effort_argv",
    "effort_levels",
    "effort_pool",
    "pair_key",
    "provider_of",
    "validate_effort",
]

EFFORT_PLACEHOLDER = "{effort}"
"""Substituted with the chosen level inside an effort argv template."""

Levels = Mapping[str, list[str]]
"""One provider catalog: model id -> supported levels, in the harness's order."""

Reader = Callable[[Path], Levels]
"""``home`` -> per-model levels for one provider."""

_PI_EXTENDED = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
"""pi's own level vocabulary, in its own order (`getSupportedThinkingLevels`)."""


def pair_key(harness: str, model: str) -> str:
    """The ``harness/model`` identity Kitchen uses for tiers and efforts."""
    return f"{harness}/{model}"


# --- readers -----------------------------------------------------------------


def _load_json(path: Path) -> object | None:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _pi_levels(model: Mapping[str, object]) -> list[str]:
    """pi's own supported-levels rule over one composed model entry.

    A non-reasoning model supports ``off`` only; ``xhigh``/``max`` count only
    when the map names them, and a mapped ``null`` removes a level outright.
    """
    if not model.get("reasoning"):
        return ["off"]
    mapping = model.get("thinkingLevelMap")
    declared: Mapping[str, object] = (
        cast(Mapping[str, object], mapping) if isinstance(mapping, dict) else {}
    )
    levels: list[str] = []
    for level in _PI_EXTENDED:
        if level in declared and declared[level] is None:
            continue
        if level in ("xhigh", "max") and level not in declared:
            continue
        levels.append(level)
    return levels


def _collect_pi_models(target: dict[str, object], providers: Mapping[str, object]) -> None:
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        entries = cast(dict[str, object], provider).get("models")
        if not isinstance(entries, list):
            continue
        for entry in cast(list[object], entries):
            if isinstance(entry, dict):
                model = cast(dict[str, object], entry)
                model_id = model.get("id")
                if isinstance(model_id, str) and model_id:
                    target[f"{name}/{model_id}"] = model


def _pi_reader(home: Path) -> Levels:
    """The composed cache, then the user's hand-written overrides on top."""
    models: dict[str, object] = {}
    store = _load_json(home / ".pi" / "agent" / "models-store.json")
    if isinstance(store, dict):
        _collect_pi_models(models, cast(Mapping[str, object], store))
    overrides = _load_json(home / ".pi" / "agent" / "models.json")
    if isinstance(overrides, dict):
        providers = cast(dict[str, object], overrides).get("providers")
        if isinstance(providers, dict):
            _collect_pi_models(models, cast(Mapping[str, object], providers))
    levels: dict[str, list[str]] = {}
    for key, model in models.items():
        supported = _pi_levels(cast(Mapping[str, object], model))
        # `off`-only means no reasoning effort to select: absent, not one chip.
        if supported and supported != ["off"]:
            levels[key] = supported
    return levels


def _codex_reader(home: Path) -> Levels:
    cache = _load_json(home / ".codex" / "models_cache.json")
    if not isinstance(cache, dict):
        return {}
    entries = cast(dict[str, object], cache).get("models")
    levels: dict[str, list[str]] = {}
    for entry in cast(list[object], entries) if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        model = cast(dict[str, object], entry)
        slug = model.get("slug")
        supported = model.get("supported_reasoning_levels")
        if not isinstance(slug, str) or not isinstance(supported, list):
            continue
        efforts = [
            cast(dict[str, object], item)["effort"]
            for item in cast(list[object], supported)
            if isinstance(item, dict)
            and isinstance(cast(dict[str, object], item).get("effort"), str)
        ]
        if efforts:
            levels[slug] = cast(list[str], efforts)
    return levels


def _claude_reader(home: Path) -> Levels:
    """The newest internal model-catalog cache, or nothing.

    The cache carries no alias mapping, so an alias such as ``opus`` stays
    absent rather than guessing which of several models it names.
    """
    directory = home / ".claude" / "cache" / "model-catalog"
    try:
        newest = max(directory.glob("*.json"), key=lambda path: path.stat().st_mtime)
    except (OSError, ValueError):
        return {}
    catalog = _load_json(newest)
    if not isinstance(catalog, dict):
        return {}
    config = cast(dict[str, object], catalog).get("catalog")
    if not isinstance(config, dict):
        return {}
    entries = cast(dict[str, object], config).get("config")
    models = cast(dict[str, object], entries).get("models") if isinstance(entries, dict) else None
    levels: dict[str, list[str]] = {}
    for entry in cast(list[object], models) if isinstance(models, list) else []:
        if not isinstance(entry, dict):
            continue
        model = cast(dict[str, object], entry)
        model_id = model.get("id")
        thinking = model.get("thinking")
        options = cast(dict[str, object], thinking).get("effort_options") if isinstance(thinking, dict) else None
        if not isinstance(model_id, str) or not isinstance(options, list):
            continue
        efforts = [
            cast(dict[str, object], option)["id"]
            for option in cast(list[object], options)
            if isinstance(option, dict)
            and isinstance(cast(dict[str, object], option).get("id"), str)
        ]
        if efforts:
            levels[model_id] = cast(list[str], efforts)
    return levels


EFFORT_PROVIDERS: dict[str, tuple[Reader, list[str]]] = {
    "pi": (_pi_reader, ["--thinking", EFFORT_PLACEHOLDER]),
    "codex": (_codex_reader, ["-c", f"model_reasoning_effort={EFFORT_PLACEHOLDER}"]),
    "claude": (_claude_reader, ["--effort", EFFORT_PLACEHOLDER]),
}
"""Executable basename -> (catalog reader, effort argv template). The one
source for both level discovery and the flag a launch appends."""


def provider_of(executable: str) -> tuple[Reader, list[str]] | None:
    return EFFORT_PROVIDERS.get(Path(executable).name)


def effort_argv(executable: str, effort: str | None) -> list[str]:
    """The effort flag for one executable, or nothing when unsupported/unset."""
    provider = provider_of(executable)
    if effort is None or provider is None:
        return []
    return [element.replace(EFFORT_PLACEHOLDER, effort) for element in provider[1]]


def _match(levels: Levels, label: str) -> list[str] | None:
    """A declared label to a provider id: exact, else a unique final segment.

    Kitchen models are project-local labels, so a pi id's namespaced form
    (``provider/id``) still matches the id it declares; anything ambiguous or
    unknown stays a miss.
    """
    if label in levels:
        return levels[label]
    suffix = [
        values for key, values in levels.items() if key.rsplit("/", 1)[-1] == label
    ]
    return suffix[0] if len(suffix) == 1 else None


def effort_levels(kitchen: Kitchen, *, home: str | Path | None = None) -> dict[str, list[str]]:
    """Discovered levels per declared pair, from local files only.

    Every declared model pair gets a key only when its adapter's executable
    provider is known and that model has at least one selectable level; a miss
    is absent, never a guessed list.
    """
    base = Path.home() if home is None else Path(home)
    catalogs: dict[str, Levels] = {}
    found: dict[str, list[str]] = {}
    for entry in kitchen.models:
        adapter = kitchen.adapter(entry.harness)
        if adapter is None or not adapter.argv:
            continue
        provider = provider_of(adapter.argv[0])
        if provider is None:
            continue
        reader, _ = provider
        if adapter.argv[0] not in catalogs:
            try:
                catalogs[adapter.argv[0]] = reader(base)
            except Exception:  # any read or parse fault is absence, not a guess
                catalogs[adapter.argv[0]] = {}
        levels = _match(catalogs[adapter.argv[0]], entry.model)
        if levels:
            found[pair_key(entry.harness, entry.model)] = list(levels)
    return found


def effort_pool(
    kitchen: Kitchen,
    harness: str,
    model: str,
    *,
    discovered: Mapping[str, list[str]] | None = None,
) -> list[str] | None:
    """A pair's allowed levels: the selected pool, else the discovered ones."""
    selected = kitchen.efforts.get(pair_key(harness, model))
    if selected:
        return list(selected)
    found = effort_levels(kitchen) if discovered is None else discovered
    levels = found.get(pair_key(harness, model))
    return list(levels) if levels else None


def validate_effort(
    kitchen: Kitchen,
    harness: str,
    model: str,
    effort: str | None,
    *,
    discovered: Mapping[str, list[str]] | None = None,
) -> None:
    """Refuse an explicit level outside a pair's pool, naming both."""
    if effort is None:
        return
    key = pair_key(harness, model)
    pool = effort_pool(kitchen, harness, model, discovered=discovered)
    if pool is None:
        raise ValueError(
            f"no reasoning effort levels are known for {key}; omit effort or "
            + "declare its model in a provider catalog"
        )
    if effort not in pool:
        raise ValueError(
            f"{key} does not support effort {effort!r}; choose one of "
            + ", ".join(pool)
        )


def effective_effort(kitchen: Kitchen, assignment: Assignment) -> str | None:
    """The level a launch uses: the assignment's own, else the pool's highest."""
    if assignment.effort:
        return assignment.effort
    pool = kitchen.efforts.get(pair_key(assignment.harness, assignment.model))
    return pool[-1] if pool else None
