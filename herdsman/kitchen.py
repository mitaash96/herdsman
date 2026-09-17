"""Project-local, inspectable Kitchen configuration: adapters, models, defaults.

Deterministic data only. Nothing here spawns a process, probes a harness, or
touches a path outside ``<project>/.herdsman/`` -- live discovery facts arrive
as `HarnessFacts` from the discovery lane and are *combined* here, never
gathered here.

Unknown is a value. A capability, price, or tier nobody declared stays
``"unknown"``/``None``; it is never inferred from a harness or model name, and
Herdsman ships no bundled model, ranking, or pricing database.

The canonical document is ``.herdsman/kitchen.json``. The older
``luna.json``/``harnesses.json``/``models.json``/``memory.json`` files are read
as *legacy inputs* for anything the canonical document does not declare; they
are never rewritten, deleted, or migrated.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Literal, Self, cast

import networkx as nx
from pydantic import Field, ValidationError, model_validator

from .classes import Assignment, EXECUTOR_HARNESS, FrozenModel, Model
from .library import MAX_CONTEXT_TOKENS

CapabilityState = Literal["supported", "unsupported", "unknown"]
"""Three states, never two. ``unsupported`` is a declaration; ``unknown`` is not."""

HealthState = Literal["healthy", "unhealthy", "unknown"]
MemoryClass = Literal["A", "B", "C"]
"""Sprint 6-B memory classes: A pointer+pull, B A+project sugar, C budgeted inline."""

ReadinessState = Literal["ready", "degraded", "unavailable", "unknown", "unconfigured"]
Provenance = Literal["declared", "discovered", "legacy"]

KITCHEN_FILE = "kitchen.json"
KITCHEN_DIR = ".herdsman"
KITCHEN_VERSION = 1
PROMPT_PLACEHOLDER = "{prompt}"
"""The one packet placeholder an adapter's argv template must hold, exactly once."""

LUNA_ARGV: tuple[str, ...] = (
    "--no-session", "--mode", "text", "--print", PROMPT_PLACEHOLDER,
)
"""Argv tail the runtime has always used for Luna; the binary is prepended."""

_LEGACY_LUNA = "luna.json"
_LEGACY_HARNESSES = "harnesses.json"
_LEGACY_TIERS = "models.json"
_LEGACY_MEMORY = "memory.json"


class KitchenConfigError(ValueError):
    """A Kitchen declaration is missing a file it names, malformed, or contradictory."""


# --- value objects -----------------------------------------------------------


class Capabilities(FrozenModel):
    """What one adapter can do. Every field defaults to an explicit unknown."""

    structured_output: CapabilityState = "unknown"
    """Machine-readable result output, as opposed to scraped terminal text."""
    resume: CapabilityState = "unknown"
    """Continuing a prior session instead of restarting cold."""
    usage: CapabilityState = "unknown"
    """Reporting provider token usage Herdsman can attribute."""
    pty: CapabilityState = "unknown"
    """``supported`` here means *needs* a PTY -- herdr owns supplying one."""
    memory: MemoryClass | None = None
    """Sprint 6-B class. ``None`` is undeclared, and is never guessed."""


class Adapter(FrozenModel):
    """One harness's launch and configuration identity plus its capabilities."""

    name: str
    argv: list[str]
    """Launch template holding exactly one ``{prompt}`` element."""
    model_argv: list[str] = []
    """Flag(s) the model name is appended to, e.g. ``["--model"]``."""
    capabilities: Capabilities = Capabilities()
    source: Provenance = "declared"

    @model_validator(mode="after")
    def _check_argv(self) -> Self:
        if not self.name.strip():
            raise ValueError("adapter name cannot be empty")
        if len(self.argv) < 2:
            raise ValueError(
                f"adapter {self.name!r} argv needs at least the executable and "
                + f"the {PROMPT_PLACEHOLDER} element"
            )
        placeholders = 0
        for element in [*self.argv, *self.model_argv]:
            if not element.strip():
                raise ValueError(f"adapter {self.name!r} argv elements must be non-empty")
            if element == PROMPT_PLACEHOLDER:
                placeholders += 1
            elif "{" in element or "}" in element:
                raise ValueError(
                    f"adapter {self.name!r} argv element {element!r} holds an "
                    + f"unknown placeholder; the only one read is {PROMPT_PLACEHOLDER}"
                )
        if placeholders != 1:
            raise ValueError(
                f"adapter {self.name!r} argv must hold exactly one "
                + f"{PROMPT_PLACEHOLDER} element, got {placeholders}"
            )
        return self


class Price(FrozenModel):
    """Explicit price facts only. An absent side stays unknown, never zero."""

    input_per_mtok: float | None = None
    output_per_mtok: float | None = None
    currency: str = "USD"


class ModelEntry(FrozenModel):
    """One model *on one harness*. Identity is the pair, so equal labels on two
    harnesses never collapse into one catalog row."""

    harness: str
    model: str
    source: Provenance = "declared"
    usage: CapabilityState = "unknown"
    """Whether this model's usage is reported back through its harness."""
    counting: CapabilityState = "unknown"
    """Whether a preflight token count is available for it."""
    price: Price | None = None
    tier: str | None = None
    """Resolved from `Kitchen.tiers` in the projection. Declaring it here is refused."""

    @property
    def key(self) -> tuple[str, str]:
        return (self.harness, self.model)


class FallbackChain(FrozenModel):
    """The ordered, explicit candidates for one primary assignment. Nothing
    outside `candidates` is ever eligible, so escalation cannot be silent."""

    primary: Assignment
    candidates: list[Assignment]


class Defaults(FrozenModel):
    """Assignment defaults. Planner and executor are separate on purpose: one
    harness can serve both, and two harnesses can split frontier from execution."""

    planner: Assignment | None = None
    """Frontier planning assignment."""
    initiative: Assignment | None = None
    """Executor default for an initiative with no role or plan-scoped choice."""
    roles: dict[str, Assignment] = {}


class HarnessFacts(Model):
    """Read-only discovery output, supplied by the discovery lane. Observed
    facts only -- declarations live on `Adapter`."""

    harness: str
    executable: str | None = None
    version: str | None = None
    health: HealthState = "unknown"
    detail: str = ""


class Readiness(FrozenModel):
    """One adapter's readiness plus the single corrective action that clears it."""

    harness: str
    state: ReadinessState
    reason: str
    action: str = ""
    version: str | None = None


class Resolution(FrozenModel):
    """A resolved assignment and the declaration that won, for attribution."""

    assignment: Assignment
    source: str
    """e.g. ``"defaults.roles['reviewer']"`` or ``"plan-override"``."""
    tier: str | None = None


class KitchenProjection(FrozenModel):
    """The whole serializable Kitchen: declarations, resolved catalog, readiness."""

    version: int
    configured: bool
    ready: bool
    revision: str
    adapters: list[Adapter]
    models: list[ModelEntry]
    tiers: dict[str, str]
    frontier_tiers: list[str]
    defaults: Defaults
    fallbacks: list[FallbackChain]
    context_warning_tokens: int
    readiness: list[Readiness]
    blockers: list[str]
    """Why `ready` is false. Empty when ready."""
    notes: list[str]
    """Non-fatal observations: legacy sources used, declarations shadowed."""


# --- configuration document --------------------------------------------------


class Kitchen(Model):
    """The canonical project-local declarations, and the whole read API over them.

    One class: what is on disk *is* this model's serialization, so there is no
    second place for effective configuration to disagree with stored state.
    """

    version: int = KITCHEN_VERSION
    adapters: list[Adapter] = []
    models: list[ModelEntry] = []
    tiers: dict[str, str] = {}
    """``"model"`` or ``"harness/model"`` to a project-local tier name."""
    frontier_tiers: list[str] = Field(default_factory=lambda: ["frontier"])
    """Which tier names count as frontier for the no-silent-escalation rule."""
    defaults: Defaults = Defaults()
    fallbacks: list[FallbackChain] = []
    context_warning_tokens: int = Field(default=MAX_CONTEXT_TOKENS, ge=1)
    """The Library's effective-context warning threshold, project-local.

    Per-initiative asset closures over this many tokens are warned about at
    approval (recorded, never refused). The default is the historical
    2,000-token value, so older kitchen documents without the field keep
    loading unchanged."""
    notes: list[str] = []
    """Provenance remarks accumulated while loading; not part of the digest."""

    # -- validation -----------------------------------------------------------

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.version != KITCHEN_VERSION:
            raise ValueError(
                f"kitchen version {self.version} is not readable by this Herdsman "
                + f"(expected {KITCHEN_VERSION})"
            )
        names = [adapter.name for adapter in self.adapters]
        _reject_duplicates(names, "adapters")
        known = set(names)
        catalog: set[tuple[str, str]] = set()
        for entry in self.models:
            if entry.tier is not None:
                raise ValueError(
                    f"models[{entry.harness}/{entry.model}].tier is resolved from "
                    + "the project-local tiers map; declare it under tiers instead"
                )
            if entry.harness not in known:
                raise ValueError(
                    f"model {entry.model!r} names undeclared adapter "
                    + f"{entry.harness!r}"
                )
            if entry.key in catalog:
                raise ValueError(f"duplicate model {entry.harness}/{entry.model}")
            catalog.add(entry.key)
        for label, assignment in self._declared_assignments():
            if (assignment.harness, assignment.model) not in catalog:
                raise ValueError(
                    f"{label} names {assignment.harness}/{assignment.model}, which "
                    + "is not in the model catalog"
                )
        self._check_fallbacks()
        return self

    def _declared_assignments(self) -> list[tuple[str, Assignment]]:
        found: list[tuple[str, Assignment]] = []
        if self.defaults.planner is not None:
            found.append(("defaults.planner", self.defaults.planner))
        if self.defaults.initiative is not None:
            found.append(("defaults.initiative", self.defaults.initiative))
        for role, assignment in self.defaults.roles.items():
            found.append((f"defaults.roles[{role!r}]", assignment))
        for chain in self.fallbacks:
            label = f"fallbacks[{chain.primary.harness}/{chain.primary.model}]"
            found.append((f"{label}.primary", chain.primary))
            for candidate in chain.candidates:
                found.append((label, candidate))
        return found

    def _check_fallbacks(self) -> None:
        graph: nx.DiGraph[tuple[str, str]] = nx.DiGraph()
        primaries = [
            f"{chain.primary.harness}/{chain.primary.model}" for chain in self.fallbacks
        ]
        _reject_duplicates(primaries, "fallback primaries")
        for chain in self.fallbacks:
            primary = (chain.primary.harness, chain.primary.model)
            label = f"fallbacks[{primary[0]}/{primary[1]}]"
            if not chain.candidates:
                raise ValueError(f"{label} declares no candidates; remove it instead")
            seen: list[str] = []
            for candidate in chain.candidates:
                key = (candidate.harness, candidate.model)
                if key == primary:
                    raise ValueError(f"{label} lists its own primary as a candidate")
                seen.append(f"{key[0]}/{key[1]}")
                if self._is_frontier(key) and not self._is_frontier(primary):
                    raise ValueError(
                        f"{label} candidate {key[0]}/{key[1]} is tier "
                        + f"{self.tier_of(*key)!r}; a fallback may not escalate a "
                        + "non-frontier primary to a frontier tier"
                    )
                _ = graph.add_edge(primary, key)
            _reject_duplicates(seen, f"{label} candidates")
        simple_cycles = cast(
            "Callable[[nx.DiGraph[tuple[str, str]]], Iterator[list[tuple[str, str]]]]",
            nx.simple_cycles,
        )
        for cycle in simple_cycles(graph):
            path = " -> ".join(f"{harness}/{model}" for harness, model in cycle)
            raise ValueError(f"fallback cycle {path}")

    # -- catalog and tiers ----------------------------------------------------

    def tier_of(self, harness: str, model: str) -> str | None:
        """Tier from the project-local map only. No name heuristics, ever."""
        return self.tiers.get(f"{harness}/{model}") or self.tiers.get(model)

    def _is_frontier(self, key: tuple[str, str]) -> bool:
        tier = self.tier_of(*key)
        return tier is not None and tier in self.frontier_tiers

    def catalog(
        self, discovered: Iterable[ModelEntry] = ()
    ) -> list[ModelEntry]:
        """Declared entries plus discovery's, each carrying its resolved tier.

        A declared entry wins over a discovered one for the same harness/model;
        nothing else can enter the catalog.
        """
        merged: dict[tuple[str, str], ModelEntry] = {
            entry.key: entry for entry in discovered
        }
        merged.update({entry.key: entry for entry in self.models})
        return [
            entry.model_copy(update={"tier": self.tier_of(*entry.key)})
            for entry in sorted(merged.values(), key=lambda entry: entry.key)
        ]

    def adapter(self, name: str) -> Adapter | None:
        return next((item for item in self.adapters if item.name == name), None)

    # -- assignment resolution ------------------------------------------------

    def resolve_planner(self) -> Resolution:
        """The frontier planning assignment."""
        if self.defaults.planner is None:
            raise KitchenConfigError(
                "no frontier planner is configured; set defaults.planner in "
                + f"{KITCHEN_DIR}/{KITCHEN_FILE}"
            )
        return self._resolution(self.defaults.planner, "defaults.planner")

    def resolve_assignment(
        self, *, role: str | None = None, override: Assignment | None = None
    ) -> Resolution:
        """Executor assignment: plan-scoped override, then role, then initiative
        default. An approved plan's own assignment is immutable and never reaches
        here -- callers pass it as `override` only when it is the live choice."""
        if override is not None:
            return self._resolution(override, "plan-override")
        if role is not None:
            assignment = self.defaults.roles.get(role)
            if assignment is not None:
                return self._resolution(assignment, f"defaults.roles[{role!r}]")
        if self.defaults.initiative is None:
            named = f" for role {role!r}" if role else ""
            raise KitchenConfigError(
                f"no executor assignment is configured{named}; set "
                + f"defaults.initiative in {KITCHEN_DIR}/{KITCHEN_FILE}"
            )
        return self._resolution(self.defaults.initiative, "defaults.initiative")

    def _resolution(self, assignment: Assignment, source: str) -> Resolution:
        return Resolution(
            assignment=assignment,
            source=source,
            tier=self.tier_of(assignment.harness, assignment.model),
        )

    def fallback_candidates(self, assignment: Assignment) -> list[Assignment]:
        """The explicit ordered candidates for one assignment, or nothing.

        An empty result is a refusal to continue, not an invitation to pick
        something else: validation has already proved no candidate escalates a
        non-frontier primary to a frontier tier.
        """
        for chain in self.fallbacks:
            if chain.primary == assignment:
                return list(chain.candidates)
        return []

    # -- readiness ------------------------------------------------------------

    def readiness(self, facts: Iterable[HarnessFacts] = ()) -> list[Readiness]:
        """Combine declarations with live discovery into one state per harness."""
        observed = {fact.harness: fact for fact in facts}
        results: list[Readiness] = []
        for adapter in self.adapters:
            fact = observed.pop(adapter.name, None)
            if fact is None:
                results.append(Readiness(
                    harness=adapter.name, state="unknown",
                    reason="no discovery facts for this adapter",
                    action="refresh Kitchen discovery",
                ))
            elif fact.executable is None:
                results.append(Readiness(
                    harness=adapter.name, state="unavailable",
                    reason=fact.detail or "executable not found",
                    action=f"install {adapter.name} or correct its argv executable",
                ))
            elif fact.health == "unhealthy":
                results.append(Readiness(
                    harness=adapter.name, state="unavailable",
                    reason=fact.detail or "health check failed",
                    action=f"repair the {adapter.name} installation",
                    version=fact.version,
                ))
            elif fact.health == "unknown":
                results.append(Readiness(
                    harness=adapter.name, state="degraded",
                    reason=fact.detail or "health is unknown",
                    action=f"run a {adapter.name} health check",
                    version=fact.version,
                ))
            else:
                results.append(Readiness(
                    harness=adapter.name, state="ready", reason="",
                    version=fact.version,
                ))
        for name in sorted(observed):
            results.append(Readiness(
                harness=name, state="unconfigured",
                reason="installed but not declared in this project",
                action=f"declare {name} as a Kitchen adapter",
                version=observed[name].version,
            ))
        return results

    @property
    def configured(self) -> bool:
        return bool(self.adapters)

    @property
    def revision(self) -> str:
        """Digest of the declarations, for stale-write preconditions."""
        payload = self.model_dump(mode="json", exclude={"notes"})
        material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def projection(
        self,
        *,
        facts: Iterable[HarnessFacts] = (),
        discovered: Iterable[ModelEntry] = (),
    ) -> KitchenProjection:
        """The whole serializable Kitchen, including the unconfigured case.

        An unconfigured project projects cleanly -- ``configured`` false with
        named blockers -- instead of raising, so the setup surfaces have
        something to render.
        """
        readiness = self.readiness(facts)
        usable = {
            item.harness for item in readiness if item.state in {"ready", "degraded"}
        }
        blockers: list[str] = []
        if not self.adapters:
            blockers.append(
                f"no adapter is declared; add one to {KITCHEN_DIR}/{KITCHEN_FILE}"
            )
        for label, assignment in (
            ("frontier planner", self.defaults.planner),
            ("initiative executor", self.defaults.initiative),
        ):
            if assignment is None:
                blockers.append(f"no {label} assignment is configured")
            elif assignment.harness not in usable:
                blockers.append(
                    f"{label} harness {assignment.harness!r} is not ready"
                )
        return KitchenProjection(
            version=self.version,
            configured=self.configured,
            ready=not blockers,
            revision=self.revision,
            adapters=list(self.adapters),
            models=self.catalog(discovered),
            tiers=dict(self.tiers),
            frontier_tiers=list(self.frontier_tiers),
            defaults=self.defaults,
            fallbacks=list(self.fallbacks),
            context_warning_tokens=self.context_warning_tokens,
            readiness=readiness,
            blockers=blockers,
            notes=list(self.notes),
        )

    # -- persistence ----------------------------------------------------------

    @classmethod
    def load(cls, project_root: str | os.PathLike[str] = ".") -> "Kitchen":
        """Read the canonical document, filling gaps from the legacy files.

        A project with nothing configured loads as an empty, inspectable Kitchen
        rather than an error; malformed declarations raise `KitchenConfigError`
        naming the offending path.
        """
        directory = _config_dir(project_root)
        path = directory / KITCHEN_FILE
        raw = _read_json(path, required=False)
        notes: list[str] = []
        payload: dict[str, object] = {}
        if raw is not None:
            if not isinstance(raw, dict):
                raise KitchenConfigError(f"{path} must be a JSON object")
            payload = dict(cast(dict[str, object], raw))
        legacy = _load_legacy(directory, notes)
        for field, value in legacy.items():
            if field in payload:
                notes.append(f"{field}: legacy declarations shadowed by {path.name}")
            else:
                payload[field] = value
        _ = payload.pop("notes", None)
        try:
            kitchen = cls.model_validate(payload)
        except ValidationError as exc:
            raise KitchenConfigError(_explain(path, exc)) from exc
        return kitchen.model_copy(update={"notes": notes})

    def save(
        self,
        project_root: str | os.PathLike[str] = ".",
        *,
        expect_revision: str | None = None,
    ) -> str:
        """Write the canonical document and return its new revision.

        `expect_revision` is the stale-write precondition: pass the revision the
        caller read, and a concurrent edit is refused instead of overwritten.
        Writes never leave ``<project>/.herdsman/``.
        """
        directory = _config_dir(project_root)
        path = directory / KITCHEN_FILE
        if expect_revision is not None:
            current = type(self).load(project_root).revision if path.exists() else ""
            if current != expect_revision:
                raise KitchenConfigError(
                    f"{path} changed since it was read (revision "
                    + f"{current or 'absent'}, expected {expect_revision}); "
                    + "reload and reapply"
                )
        directory.mkdir(parents=True, exist_ok=True)
        body: str = json.dumps(
            self.model_dump(mode="json", exclude={"notes"}), indent=2, sort_keys=True
        )
        temporary = path.with_suffix(".json.tmp")
        _ = temporary.write_text(body + "\n", encoding="utf-8")
        _ = temporary.replace(path)
        return self.revision


# --- loading helpers ---------------------------------------------------------


def _config_dir(project_root: str | os.PathLike[str]) -> Path:
    return Path(project_root).expanduser().resolve() / KITCHEN_DIR


def _reject_duplicates(values: Sequence[str], what: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {what} entry {value!r}")
        seen.add(value)


def _explain(path: Path, exc: ValidationError) -> str:
    lines = [
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
        for error in exc.errors()
    ]
    return f"invalid Kitchen configuration in {path}:\n  " + "\n  ".join(lines)


def _read_json(path: Path, *, required: bool) -> object | None:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        if required:
            raise KitchenConfigError(f"{path} is missing") from None
        return None
    except OSError as exc:
        raise KitchenConfigError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise KitchenConfigError(f"invalid JSON in {path}: {exc}") from exc


def _load_legacy(directory: Path, notes: list[str]) -> dict[str, object]:
    """Read the pre-Kitchen mapping files. Read-only, and never authoritative
    over the canonical document -- the caller drops anything already declared."""
    adapters: list[dict[str, object]] = []
    memory = _legacy_memory(directory)
    luna = _read_json(directory / _LEGACY_LUNA, required=False)
    if isinstance(luna, dict):
        binary = cast(dict[str, object], luna).get("binary")
        if not isinstance(binary, str) or not binary.strip():
            raise KitchenConfigError(
                f"{directory / _LEGACY_LUNA} field binary must be a non-empty string"
            )
        adapters.append({
            "name": EXECUTOR_HARNESS,
            "argv": [binary, *LUNA_ARGV],
            "model_argv": ["--model"],
            "source": "legacy",
            "capabilities": {"pty": "supported", "memory": memory.get(EXECUTOR_HARNESS)},
        })
        notes.append(f"adapters: {EXECUTOR_HARNESS} read from {_LEGACY_LUNA}")
    harnesses = _read_json(directory / _LEGACY_HARNESSES, required=False)
    if isinstance(harnesses, dict):
        for name, entry in cast(dict[str, object], harnesses).items():
            if not isinstance(entry, dict):
                raise KitchenConfigError(
                    f"harness {name!r} in {directory / _LEGACY_HARNESSES} must be an object"
                )
            fields = cast(dict[str, object], entry)
            adapters.append({
                "name": name,
                "argv": fields.get("argv", []),
                "model_argv": fields.get("model_argv", []),
                "source": "legacy",
                "capabilities": {"memory": memory.get(name)},
            })
        if harnesses:
            notes.append(f"adapters: read from {_LEGACY_HARNESSES}")
    legacy: dict[str, object] = {}
    if adapters:
        legacy["adapters"] = adapters
    tiers = _read_json(directory / _LEGACY_TIERS, required=False)
    if tiers is not None:
        if not isinstance(tiers, dict):
            raise KitchenConfigError(f"{directory / _LEGACY_TIERS} must be an object")
        for model, tier in cast(dict[str, object], tiers).items():
            if not isinstance(tier, str) or not tier.strip():
                raise KitchenConfigError(
                    f"{directory / _LEGACY_TIERS} value for {model!r} must be a string"
                )
        legacy["tiers"] = dict(cast(dict[str, str], tiers))
        notes.append(f"tiers: read from {_LEGACY_TIERS}")
    return legacy


def _legacy_memory(directory: Path) -> Mapping[str, MemoryClass]:
    """Per-harness Sprint 6-B class from `memory.json`, in either accepted shape."""
    raw = _read_json(directory / _LEGACY_MEMORY, required=False)
    if not isinstance(raw, dict):
        return {}
    root = cast(dict[str, object], raw)
    declared = root.get("harnesses") or root.get("adapters") or root
    if not isinstance(declared, dict):
        return {}
    classes: dict[str, MemoryClass] = {}
    for name, value in cast(dict[str, object], declared).items():
        if isinstance(value, dict):
            value = cast(dict[str, object], value).get("class")
        if isinstance(value, str) and value in {"A", "B", "C"}:
            classes[name] = cast(MemoryClass, value)
    return classes


__all__ = [
    "Adapter", "Capabilities", "CapabilityState", "Defaults", "FallbackChain",
    "HarnessFacts", "HealthState", "Kitchen", "KITCHEN_DIR", "KITCHEN_FILE",
    "KITCHEN_VERSION", "KitchenConfigError", "KitchenProjection", "LUNA_ARGV",
    "MemoryClass", "ModelEntry", "PROMPT_PLACEHOLDER", "Price", "Provenance",
    "Readiness", "ReadinessState", "Resolution",
]
