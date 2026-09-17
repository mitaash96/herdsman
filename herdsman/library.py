"""Project-local Library: the authorable assets a plan freezes at approval.

Six kinds -- role, contract, skill, agent, checkpoint-template, memory-leaf --
each one Markdown with a small frontmatter head. Bundled assets ship with the
package and are read-only; editing one copies it into
``<project>/.herdsman/library/`` and every later read prefers that copy. That
is the whole override rule: no global harness path is read or written, ever.

Memory leaves are not a second memory system. The ``memory-leaf`` shelf is a
projection of `MemoryFileStore`, and every shelf write goes back through that
store -- so a shelf edit and Sprint 6-B delivery read the same bytes, keep the
same evidence requirement, and share the stale/conflicted vocabulary.

Nothing here schedules, spawns a process, or watches a file.

## The interface the REST/CLI/editor lane calls

`Daemon.library()` returns a `Library` bound to the project and its memory
store; the daemon holds no Library state, so every call reads what is on disk
and a terminal edit needs nothing invalidated.

    browse(kind, status, query)    the shelf, with the memory filters
    show(ref) / find(ref)          one asset, project copy over bundled
    create / edit / copy / rename / archive / unarchive
    checkout(ref) -> Path          the file an $EDITOR opens (copy-on-edit)
    revision -> str                a file watcher's change token
    resolve(refs) / validate(refs) reference closure and its findings

`edit` takes `expect_digest`, the stale-write precondition -- pass the revision
you read and a concurrent terminal edit is refused instead of overwritten.
Every failure is a `LibraryError`, which is a `ValueError`, so the existing
route error mapping already turns it into a 400/409.

## The two seams the core already owns

Approval freezes the Library: `Daemon.approve_plan` calls `snapshot_for`, which
resolves each initiative's declared `InitiativeSpec.assets` to its reference
closure and writes the exact bytes into `PlanApproved.assets`. A missing,
archived, or cyclic reference refuses the approval; size and staleness ride
along as recorded warnings. `Plan.asset_snapshots` is write-once per version,
so an approved version keeps its bytes no matter what the shelf does next.

Injection stays narrow: `Daemon.run_initiative` passes
`Plan.initiative_assets(initiative_id)` -- that node's closure and nothing else
-- into `compile_task_packet`, and an initiative that declared no assets
carries no packet section at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, cast

from pydantic import Field, model_validator

from .classes import (
    AssetKind,
    AssetOrigin,
    AssetSnapshot,
    AssetStatus,
    Contract,
    FrozenModel,
    LibraryIssue,
    LibrarySnapshot,
    MemoryLeaf,
    Plan,
)
from .memory import MemoryFileStore, token_count

LIBRARY_DIR = ".herdsman/library"
MAX_CONTEXT_TOKENS = 2000
"""Effective-context warning threshold for one initiative's asset closure.

A warning, never a refusal: the owner decides what a big role document is
worth. Counted with `memory.token_count`, the same conservative word count
the memory budgets use, so one number means one thing across the product."""

KIND_DIRS: dict[AssetKind, str] = {
    "role": "roles",
    "contract": "contracts",
    "skill": "skills",
    "agent": "agents",
    "checkpoint-template": "checkpoint-templates",
    "memory-leaf": "memory",
}
"""Kind to on-disk folder. ``memory-leaf`` never uses its folder -- those
leaves live in `MemoryFileStore`'s directory -- but the mapping keeps the
kind set and the layout in one table."""

MEMORY_KIND: AssetKind = "memory-leaf"

CONTRACT_ASSET_KIND: AssetKind = "contract"

_CONTRACT_KEYS = {
    "role", "required_checks", "required_paths", "require_patch",
    "allow_writes", "allowed_commands",
}
_GENERIC_KEYS = {"kind", "name", "title", "description", "references", "status"}

# The contract asset schema, one deterministic shape:
#
#   ---
#   kind: contract
#   name: gated
#   title: Optional human label
#   role: implementer            # optional, default "implementer"
#   required_checks:             # optional list of exact check names
#     - uv run pytest -q
#   required_paths:              # optional list of exact artifact paths
#   require_patch: true          # optional bool, default false
#   allow_writes: false          # optional bool, default true
#   allowed_commands:            # optional list; absent means unrestricted
#   ---
#   Prose for the executor's context; enforcement is the typed model.
#
# Frontmatter fields compile into the typed `Contract`; the body is guidance
# the packet carries, never enforcement. Unknown frontmatter keys are refused
# on a contract asset -- a typo'd gate (`required_check:`) silently dropping a
# requirement would be the worst kind of ambiguity. Compiled and validated
# when the asset is written and again at approval.

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_TITLE_ALIASES = ("title", "description")
_FOLD_MARKERS = {">", ">-", ">+", "|", "|-", "|+"}
_STATUSES = {"active", "stale", "conflicted", "retired"}


class LibraryError(ValueError):
    """An asset is unknown, malformed, read-only, stale, or invalid to snapshot."""


def parse_ref(ref: str) -> tuple[AssetKind, str]:
    """Split ``kind/name`` into its parts, rejecting anything else."""
    kind, _, name = ref.partition("/")
    if kind not in KIND_DIRS:
        raise LibraryError(
            f"unknown asset kind in {ref!r}; expected one of "
            + ", ".join(sorted(KIND_DIRS))
        )
    if not _NAME.fullmatch(name):
        raise LibraryError(
            f"asset name in {ref!r} must be a lowercase safe slug"
        )
    return kind, name


class Asset(FrozenModel):
    """One Library document: its identity, its references, and its text.

    `digest` is the revision and excludes `origin`, so copying a bundled asset
    into the project unchanged produces the same revision -- copy-on-edit only
    moves bytes, and only an actual edit changes identity.
    """

    kind: AssetKind
    name: str
    title: str = ""
    references: list[str] = []
    """Other assets this one needs, as ``kind/name``.

    For a ``memory-leaf`` this carries the leaf's evidence refs instead, which
    is what `MemoryFileStore` already requires and validates."""
    fields: dict[str, object] = {}
    """The parsed frontmatter, beyond the structured fields above.

    Semantic for a ``contract`` asset (its gates live here); identity noise
    for every other kind. Part of `digest` so any frontmatter change is a new
    revision."""
    body: str = ""
    origin: AssetOrigin = "project"
    status: AssetStatus = "active"
    """`archive` sets ``retired``; the memory shelf also reports ``stale`` and
    ``conflicted`` from the leaf itself."""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not _NAME.fullmatch(self.name):
            raise LibraryError(
                f"asset name {self.name!r} must be a lowercase safe slug"
            )
        if self.kind != MEMORY_KIND:
            for ref in self.references:
                _ = parse_ref(ref)
        return self

    @property
    def ref(self) -> str:
        return f"{self.kind}/{self.name}"

    @property
    def digest(self) -> str:
        """Content-addressed revision. Derived, never stored."""
        material = json.dumps(
            {
                "kind": self.kind,
                "name": self.name,
                "title": self.title,
                "references": list(self.references),
                "fields": self.fields,
                "body": self.body,
                "status": self.status,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    @property
    def tokens(self) -> int:
        """Effective context cost, counted like every other memory budget."""
        return token_count(f"{self.title}\n{self.body}")

    def snapshot(self, *, contract: Contract | None = None) -> AssetSnapshot:
        """Freeze this asset's exact contents and revision for an approval.

        A ``contract`` asset travels with the typed `Contract` compiled from
        its frontmatter at approval time, so replay enforces the approved
        form without the shelf."""
        return AssetSnapshot(
            ref=self.ref,
            kind=self.kind,
            name=self.name,
            origin=self.origin,
            title=self.title,
            references=list(self.references),
            body=self.body,
            digest=self.digest,
            tokens=self.tokens,
            contract=contract,
        )


class AssetSummary(FrozenModel):
    """One browse row: everything a list surface needs, without the body."""

    ref: str
    kind: AssetKind
    name: str
    title: str = ""
    origin: AssetOrigin = "project"
    status: AssetStatus = "active"
    digest: str
    tokens: int = Field(default=0, ge=0)
    references: list[str] = []
    shadows_bundled: bool = False
    """True when a project copy overrides a bundled asset of the same ref."""


# --- serialization -----------------------------------------------------------


def serialize_asset(asset: Asset) -> str:
    """Emit the same intentionally small frontmatter subset memory leaves use.

    A ``contract`` asset's gate fields ride along so a file round-trip keeps
    the compiled gates; every other kind's raw frontmatter is deliberately
    dropped, since only `fields` consumed by a compiler is semantic."""
    lines = ["---", f"kind: {asset.kind}", f"name: {asset.name}"]
    if asset.title:
        lines.append(f"title: {asset.title}")
    if asset.references:
        lines.append("references:")
        lines.extend(f"  - {ref}" for ref in asset.references)
    if asset.status != "active":
        lines.append(f"status: {asset.status}")
    if asset.kind == CONTRACT_ASSET_KIND:
        for key in sorted(_CONTRACT_KEYS):
            value = asset.fields.get(key)
            if value is None:
                continue
            if isinstance(value, bool):
                lines.append(f"{key}: {'true' if value else 'false'}")
            elif isinstance(value, list):
                lines.append(f"{key}:")
                lines.extend(
                    f"  - {item}" for item in cast(list[object], value)
                )
            else:
                lines.append(f"{key}: {value}")
    lines.extend(["---", asset.body.rstrip(), ""])
    return "\n".join(lines)


def parse_asset(
    text: str, *, kind: AssetKind, name: str, origin: AssetOrigin
) -> Asset:
    """Read one asset file.

    Frontmatter is optional and unknown keys are ignored, so a bundled document
    written for another tool -- a ``SKILL.md`` with its own ``name``/
    ``description`` head -- loads as an asset without being rewritten. The
    path is authoritative for `kind` and `name`; a frontmatter that disagrees
    is a conflict, not a silent rename.
    """
    values: dict[str, object] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise LibraryError(f"asset {kind}/{name}: frontmatter is not closed")
        front, body = text[4:end], text[end + 5 :]
        current: str | None = None
        for line in front.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if line[:1].isspace() or stripped.startswith("- "):
                # A continuation: either a list item, or another line of a
                # folded scalar. Bundled documents written for other harnesses
                # wrap long descriptions that way, and rejecting them would
                # make a perfectly good shipped skill unreadable.
                if current is None:
                    raise LibraryError(
                        f"asset {kind}/{name}: frontmatter continuation has no field"
                    )
                held = values.get(current)
                if stripped.startswith("- ") and isinstance(held, list):
                    cast(list[object], held).append(stripped[2:].strip())
                elif isinstance(held, str):
                    values[current] = f"{held} {stripped}".strip()
                continue
            if ":" not in line:
                raise LibraryError(f"asset {kind}/{name}: invalid frontmatter")
            key, raw = line.split(":", 1)
            current = key.strip()
            value = raw.strip()
            # An empty value opens a list; a YAML block marker opens a folded
            # scalar. Everything else is the value itself.
            values[current] = (
                [] if not value else "" if value in _FOLD_MARKERS else value
            )
    declared_kind = values.get("kind")
    if isinstance(declared_kind, str) and declared_kind != kind:
        raise LibraryError(
            f"asset {kind}/{name} declares kind {declared_kind!r}; the path decides"
        )
    declared_name = values.get("name")
    if isinstance(declared_name, str) and declared_name != name:
        raise LibraryError(
            f"asset {kind}/{name} declares name {declared_name!r}; the path decides"
        )
    title = ""
    for alias in _TITLE_ALIASES:
        candidate = values.get(alias)
        if isinstance(candidate, str) and candidate:
            title = candidate
            break
    references = values.get("references")
    status = values.get("status")
    if isinstance(status, str) and status not in _STATUSES:
        raise LibraryError(
            f"asset {kind}/{name} declares status {status!r}; expected one of "
            + ", ".join(sorted(_STATUSES))
        )
    if kind == CONTRACT_ASSET_KIND:
        unknown = sorted(set(values) - _GENERIC_KEYS - _CONTRACT_KEYS)
        if unknown:
            raise LibraryError(
                f"asset {kind}/{name} declares unknown contract key(s) "
                + ", ".join(unknown)
                + "; the compiled schema is kind/name/title/status, references, "
                + "and " + ", ".join(sorted(_CONTRACT_KEYS))
            )
    return Asset(
        kind=kind,
        name=name,
        title=title,
        references=[str(ref) for ref in cast(list[object], references)]
        if isinstance(references, list)
        else [],
        body=body.rstrip("\n"),
        origin=origin,
        status=cast(AssetStatus, status) if isinstance(status, str) else "active",
        # Only the unstructured remainder is identity-bearing; the structured
        # keys above already carry their values, and a round-trip through
        # serialize/parse must reproduce the same asset byte for byte.
        fields={
            key: value for key, value in values.items() if key not in _GENERIC_KEYS
        },
    )


def compile_contract(asset: Asset) -> Contract:
    """Compile a contract asset's frontmatter into the typed `Contract` model.

    The schema is the one documented above `CONTRACT_ASSET_KIND`: the id is the
    asset name, and each optional gate maps 1:1 to a `Contract` field. Anything
    not representable in the typed model -- a mistyped boolean, a non-string
    list item -- is a `LibraryError` at the write or the approval that reads it,
    never a silently weaker contract.
    """
    if asset.kind != CONTRACT_ASSET_KIND:
        raise LibraryError(f"asset {asset.ref} is not a contract")
    unknown = sorted(set(asset.fields) - _CONTRACT_KEYS)
    if unknown:
        raise LibraryError(
            f"contract {asset.ref}: unknown frontmatter key(s) "
            + ", ".join(unknown)
            + "; the compiled schema is " + ", ".join(sorted(_CONTRACT_KEYS))
            + " -- a typo'd gate must refuse, not silently weaken the contract"
        )

    def strings(key: str) -> list[str]:
        raw = asset.fields.get(key, [])
        if not isinstance(raw, list) or not all(
            isinstance(item, str) for item in cast(list[object], raw)
        ):
            raise LibraryError(
                f"contract {asset.ref}: {key} must be a list of strings"
            )
        return [str(item) for item in cast(list[object], raw)]

    def boolean(key: str) -> bool | None:
        raw = asset.fields.get(key)
        if raw is None:
            return None
        if isinstance(raw, str) and raw.strip().lower() in {"true", "false"}:
            return raw.strip().lower() == "true"
        raise LibraryError(
            f"contract {asset.ref}: {key} must be true or false, not {raw!r}"
        )

    role = asset.fields.get("role", "implementer")
    if not isinstance(role, str) or not role.strip():
        raise LibraryError(
            f"contract {asset.ref}: role must be a non-empty string"
        )
    patch = boolean("require_patch")
    allow_writes = boolean("allow_writes")
    gates = Contract(
        id=asset.name,
        role=role,
        required_checks=strings("required_checks") if "required_checks" in asset.fields else [],
        required_paths=strings("required_paths") if "required_paths" in asset.fields else [],
        allowed_commands=(
            strings("allowed_commands")
            if "allowed_commands" in asset.fields
            else None
        ),
        require_patch=patch if patch is not None else False,
        allow_writes=allow_writes if allow_writes is not None else True,
    )
    return gates


# --- the library -------------------------------------------------------------


class Library:
    """Bundled read-only assets, project-local overrides, and the memory shelf.

    Reads combine the two roots -- a project file of the same ref shadows the
    bundled one. Writes only ever land under ``<project>/.herdsman/library/``
    or, for memory leaves, in `MemoryFileStore`'s directory.
    """

    project_root: Path
    directory: Path
    bundled_root: Path
    memory: MemoryFileStore
    context_budget: int

    def __init__(
        self,
        project_root: str | os.PathLike[str] = ".",
        *,
        memory_store: MemoryFileStore | None = None,
        bundled_root: str | os.PathLike[str] | None = None,
        context_budget: int = MAX_CONTEXT_TOKENS,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.directory = self.project_root / LIBRARY_DIR
        self.bundled_root = (
            Path(bundled_root).expanduser().resolve()
            if bundled_root is not None
            else _bundled_root()
        )
        self.memory = memory_store or MemoryFileStore(self.project_root)
        self.context_budget = context_budget

    # -- reading --------------------------------------------------------------

    def browse(
        self,
        kind: AssetKind | None = None,
        *,
        status: AssetStatus | Iterable[AssetStatus] | None = "active",
        query: str | None = None,
    ) -> list[AssetSummary]:
        """List assets, newest layout first: project overrides then bundled.

        `status` defaults to ``active`` so retired assets stay off the shelf;
        pass ``None`` for everything, or ``"stale"``/``"conflicted"`` for the
        memory shelf's filters. `query` matches ref or title, case-folded.
        """
        wanted = (
            None
            if status is None
            else {status} if isinstance(status, str) else set(status)
        )
        effective, bundled = self._load()
        rows: list[AssetSummary] = []
        for asset in effective.values():
            if kind is not None and asset.kind != kind:
                continue
            if wanted is not None and asset.status not in wanted:
                continue
            if query is not None and query.casefold() not in (
                f"{asset.ref} {asset.title}".casefold()
            ):
                continue
            rows.append(
                AssetSummary(
                    ref=asset.ref,
                    kind=asset.kind,
                    name=asset.name,
                    title=asset.title,
                    origin=asset.origin,
                    status=asset.status,
                    digest=asset.digest,
                    tokens=asset.tokens,
                    references=list(asset.references),
                    shadows_bundled=asset.origin == "project"
                    and asset.ref in bundled,
                )
            )
        return sorted(rows, key=lambda row: row.ref)

    def show(self, ref: str) -> Asset:
        """The effective asset for one ref, project copy winning over bundled."""
        asset = self._effective().get(ref)
        if asset is None:
            raise LibraryError(f"unknown asset {ref}")
        return asset

    def find(self, ref: str) -> Asset | None:
        """`show` without the raise, for callers collecting issues."""
        return self._effective().get(ref)

    @property
    def revision(self) -> str:
        """Digest over every effective asset's identity and revision.

        The file-watching lane's change token: a terminal edit, a create, or a
        retire moves it, and nothing else does. Recomputed on read, so it never
        disagrees with what is on disk.
        """
        material = "\n".join(
            f"{ref}:{asset.digest}" for ref, asset in sorted(self._effective().items())
        )
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def checkout(self, ref: str) -> Path:
        """Materialize a project-local copy if needed and return its path.

        This is the ``$EDITOR`` seam: the later lane opens what this returns,
        and the copy-on-edit of a bundled asset has already happened by then.
        Memory leaves are already project files, so their path is returned
        as-is.
        """
        asset = self.show(ref)
        if asset.kind == MEMORY_KIND:
            return self.memory.directory / f"{asset.name}.md"
        path = self._path(asset.kind, asset.name)
        if not path.is_file():
            _ = self._write(asset.model_copy(update={"origin": "project"}))
        return path

    # -- writing --------------------------------------------------------------

    def create(
        self,
        kind: AssetKind,
        name: str,
        *,
        title: str = "",
        body: str = "",
        references: Sequence[str] = (),
        fields: Mapping[str, object] | None = None,
        resolver: Callable[[str], bool] | None = None,
    ) -> Asset:
        """Author a new project-local asset. Refuses to overwrite an existing one.

        `fields` carries raw frontmatter values; for a ``contract`` asset this
        is where the gates live, and a malformed set is refused here.
        `resolver` only matters for a ``memory-leaf``: `MemoryFileStore`
        resolves ``path@sha256`` evidence itself, and anything else
        (``check:``, ``checkpoint:``, ``decision:``) needs the caller that
        knows which run it came from to confirm it exists.
        """
        _ = parse_ref(f"{kind}/{name}")
        if kind == MEMORY_KIND:
            return self._create_leaf(
                name, title=title, body=body, evidence=references, resolver=resolver
            )
        if self._path(kind, name).is_file():
            raise LibraryError(f"asset {kind}/{name} already exists")
        asset = Asset(
            kind=kind,
            name=name,
            title=title,
            body=body,
            references=list(references),
            fields=dict(fields or {}),
            origin="project",
        )
        if kind == CONTRACT_ASSET_KIND:
            _ = compile_contract(asset)
        return self._write(asset)

    def edit(
        self,
        ref: str,
        *,
        title: str | None = None,
        body: str | None = None,
        references: Sequence[str] | None = None,
        fields: Mapping[str, object] | None = None,
        status: AssetStatus | None = None,
        expect_digest: str | None = None,
    ) -> Asset:
        """Apply changes, copying a bundled asset into the project on the way.

        `fields` replaces the parsed frontmatter wholesale; a ``contract``
        asset's gates live there and are recompiled on every edit.
        `expect_digest` is the stale-write precondition: pass the revision you
        read and a concurrent write is refused instead of overwritten. A
        terminal editor session legitimately moved the disk before it
        records, so the precondition also accepts a disk that already holds
        exactly the content being submitted -- any other disk state is a
        concurrent edit and refuses.
        """
        current = self.show(ref)

        def stale_refusal() -> LibraryError:
            return LibraryError(
                f"asset {ref} changed since it was read (revision "
                + f"{current.digest}, expected {expect_digest}); reload and reapply"
            )

        if current.kind == MEMORY_KIND:
            if references is not None:
                raise LibraryError(
                    "a memory leaf's references are its evidence; rewrite them "
                    + "through the memory store, not the shelf"
                )
            target = current.model_copy(
                update={
                    **({} if title is None else {"title": title}),
                    **({} if body is None else {"body": body}),
                    **({} if status is None else {"status": status}),
                }
            )
            if (
                expect_digest is not None
                and current.digest != expect_digest
                and current.digest != target.digest
            ):
                raise stale_refusal()
            return self._edit_leaf(current.name, title=title, body=body, status=status)
        updated = current.model_copy(
            update={
                "origin": "project",
                **({} if title is None else {"title": title}),
                **({} if body is None else {"body": body}),
                **({} if references is None else {"references": list(references)}),
                **({} if fields is None else {"fields": dict(fields)}),
                **({} if status is None else {"status": status}),
            }
        )
        if (
            expect_digest is not None
            and current.digest != expect_digest
            and current.digest != updated.digest
        ):
            raise stale_refusal()
        if updated.kind == CONTRACT_ASSET_KIND:
            _ = compile_contract(updated)
        return self._write(updated)

    def copy(self, ref: str, new_name: str) -> Asset:
        """Duplicate an asset under a new name in the project. Bundled included."""
        source = self.show(ref)
        return self.create(
            source.kind,
            new_name,
            title=source.title,
            body=source.body,
            references=list(source.references),
            fields=source.fields,
        )

    def rename(self, ref: str, new_name: str) -> Asset:
        """Move a project-local asset to a new name.

        A bundled asset cannot be renamed -- Herdsman never mutates what it
        ships -- so `copy` it instead and archive the copy you no longer want.
        """
        source = self.show(ref)
        if source.origin == "bundled":
            raise LibraryError(
                f"asset {ref} is bundled and read-only; copy it under the new "
                + "name instead of renaming it"
            )
        if source.kind == MEMORY_KIND:
            raise LibraryError(
                "memory leaves are addressed by their id; retire the leaf and "
                + "author a new one instead of renaming it"
            )
        _ = parse_ref(f"{source.kind}/{new_name}")
        if self._path(source.kind, new_name).is_file():
            raise LibraryError(f"asset {source.kind}/{new_name} already exists")
        moved = self._write(source.model_copy(update={"name": new_name}))
        self._path(source.kind, source.name).unlink(missing_ok=True)
        return moved

    def archive(self, ref: str) -> Asset:
        """Retire an asset. A bundled one is shadowed by a retired project copy.

        A memory leaf is retired through its own store, the same status and
        version bump `MemoryFileStore.retire` makes -- but without re-resolving
        its evidence, because a leaf whose evidence has drifted is precisely
        the one the owner needs to be able to take off the shelf.
        """
        return self.edit(ref, status="retired")

    def unarchive(self, ref: str) -> Asset:
        """Bring a retired asset back to the shelf."""
        return self.edit(ref, status="active")

    # -- resolution and validation --------------------------------------------

    def resolve(
        self, refs: Sequence[str], *, issues: list[LibraryIssue] | None = None
    ) -> list[Asset]:
        """The declared assets plus everything they reference, declared order first.

        Depth-first from each declared ref, each asset emitted once, cycles
        broken and reported rather than followed. A ``memory-leaf``'s
        references are its evidence, so the walk stops there.
        """
        found: list[Asset] = []
        seen: set[str] = set()
        collected = issues if issues is not None else []
        # One consistent view for the whole closure: resolving a reference
        # graph against a shelf being edited underneath it would be neither
        # faster nor truthful.
        index = self._effective()

        def walk(ref: str, trail: tuple[str, ...]) -> None:
            if ref in trail:
                collected.append(
                    LibraryIssue(
                        code="reference-cycle",
                        severity="error",
                        ref=ref,
                        message="asset references itself through a cycle",
                        detail=" -> ".join([*trail, ref]),
                    )
                )
                return
            if ref in seen:
                return
            asset = index.get(ref)
            if asset is None:
                collected.append(
                    LibraryIssue(
                        code="reference-missing",
                        severity="error",
                        ref=ref,
                        message="referenced asset is not in the Library",
                        detail=" -> ".join(trail) if trail else "",
                    )
                )
                return
            seen.add(ref)
            if asset.status == "retired":
                collected.append(
                    LibraryIssue(
                        code="reference-retired",
                        severity="error",
                        ref=ref,
                        message="referenced asset is archived",
                        detail=" -> ".join(trail) if trail else "",
                    )
                )
            elif asset.status == "stale" or asset.status == "conflicted":
                collected.append(
                    LibraryIssue(
                        code=(
                            "memory-stale"
                            if asset.status == "stale"
                            else "memory-conflicted"
                        ),
                        severity="warning",
                        ref=ref,
                        message=f"asset is marked {asset.status}",
                    )
                )
            found.append(asset)
            if asset.kind != MEMORY_KIND:
                for child in asset.references:
                    walk(child, (*trail, ref))

        for ref in refs:
            walk(ref, ())
        return found

    def validate(self, refs: Sequence[str], *, owner: str = "") -> list[LibraryIssue]:
        """Every reference, conflict, and effective-context finding for one set."""
        issues: list[LibraryIssue] = []
        closure = self.resolve(refs, issues=issues)
        issues.extend(self.validate_size(owner or ", ".join(refs), closure))
        return issues

    def snapshot_for(self, plan: Plan) -> LibrarySnapshot:
        """Freeze exactly what each initiative declared, for one approval.

        Errors -- a missing, archived, or cyclic reference, an ambiguous
        contract, or a contract asset that does not compile into the typed
        model -- raise, because an approval that silently dropped an asset or
        weakened a gate would launch executors without what their briefs
        name. Warnings ride along inside the snapshot, so the reason a big
        packet was accepted stays in the event log.

        Contract binding: at most one ``contract`` asset may sit in an
        initiative's effective closure, and never beside an inline
        `InitiativeSpec.contract` -- two enforcement sources are refused, not
        merged. The single one is compiled here and frozen into the snapshot,
        which is what replay binds enforcement to.
        """
        assets: dict[str, Asset] = {}
        by_initiative: dict[str, list[str]] = {}
        compiled: dict[str, Contract] = {}
        issues: list[LibraryIssue] = []
        for initiative_id in sorted(plan.initiatives):
            spec = plan.initiatives[initiative_id].spec
            declared = list(spec.assets)
            if not declared:
                # Nothing declared, nothing to bind; an inline contract stands
                # alone and needs no Library resolution.
                continue
            closure = self.resolve(declared, issues=issues)
            issues.extend(self.validate_size(initiative_id, closure))
            by_initiative[initiative_id] = [asset.ref for asset in closure]
            contract_assets = [a for a in closure if a.kind == CONTRACT_ASSET_KIND]
            if len(contract_assets) > 1:
                issues.append(
                    LibraryIssue(
                        code="contract-ambiguous",
                        severity="error",
                        ref=initiative_id,
                        message=(
                            "initiative resolves to multiple contract assets: "
                            + ", ".join(a.ref for a in contract_assets)
                        ),
                    )
                )
            elif contract_assets and spec.contract is not None:
                issues.append(
                    LibraryIssue(
                        code="contract-conflict",
                        severity="error",
                        ref=contract_assets[0].ref,
                        message=(
                            f"initiative {initiative_id} declares an inline "
                            + "contract and a Library contract asset; only one "
                            + "source of enforcement is allowed"
                        ),
                    )
                )
            elif contract_assets:
                asset = contract_assets[0]
                compiled[asset.ref] = compile_contract(asset)
            assets.update({asset.ref: asset for asset in closure})
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            raise LibraryError(
                "plan cannot be approved: "
                + "; ".join(f"{issue.ref}: {issue.message}" for issue in errors)
            )
        return LibrarySnapshot(
            assets=[
                assets[ref].snapshot(contract=compiled.get(ref))
                for ref in sorted(assets)
            ],
            by_initiative=by_initiative,
            issues=issues,
        )

    def validate_size(
        self, owner: str, closure: Sequence[Asset]
    ) -> list[LibraryIssue]:
        """The effective-context warning for one already-resolved closure."""
        total = sum(asset.tokens for asset in closure)
        if total <= self.context_budget:
            return []
        return [
            LibraryIssue(
                code="context-size",
                severity="warning",
                ref=owner,
                message=(
                    f"effective context is {total} tokens, over the "
                    + f"{self.context_budget}-token budget"
                ),
                detail=", ".join(
                    f"{asset.ref}={asset.tokens}"
                    for asset in sorted(closure, key=lambda a: (-a.tokens, a.ref))[:5]
                ),
            )
        ]

    # -- storage --------------------------------------------------------------

    def _path(self, kind: AssetKind, name: str) -> Path:
        return self.directory / KIND_DIRS[kind] / f"{name}.md"

    def _write(self, asset: Asset) -> Asset:
        """Atomically replace one project-local asset file."""
        path = self._path(asset.kind, asset.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = serialize_asset(asset)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{asset.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                _ = stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink(missing_ok=True)
        return asset.model_copy(update={"origin": "project"})

    def _load(self) -> tuple[dict[str, Asset], frozenset[str]]:
        """One disk pass: the effective assets, and which refs ship bundled.

        Read fresh every time on purpose. A terminal edit has to be visible to
        the next call with nothing to invalidate, which is what makes the
        ``$EDITOR`` path and `revision` work; callers that walk many refs take
        this index once and look up inside it instead of rescanning per ref.
        """
        bundled = self._scan(self.bundled_root, "bundled")
        found = dict(bundled)
        found.update(self._scan(self.directory, "project"))
        found.update(
            {asset.ref: asset for asset in map(_asset_from_leaf, self.memory.list())}
        )
        return found, frozenset(bundled)

    def _effective(self) -> dict[str, Asset]:
        """Every readable asset by ref, project copies shadowing bundled ones."""
        return self._load()[0]

    def _scan(self, root: Path, origin: AssetOrigin) -> dict[str, Asset]:
        found: dict[str, Asset] = {}
        if not root.is_dir():
            return found
        for kind, folder in KIND_DIRS.items():
            if kind == MEMORY_KIND:
                continue
            directory = root / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                # A skill shipped for another harness is a folder holding
                # SKILL.md; a Herdsman-authored one is a flat file. Both read.
                if path.is_dir():
                    path = path / "SKILL.md"
                    if not path.is_file():
                        continue
                    name = path.parent.name
                elif path.suffix == ".md":
                    name = path.stem
                else:
                    continue
                if not _NAME.fullmatch(name):
                    continue
                asset = parse_asset(
                    path.read_text(encoding="utf-8"),
                    kind=kind,
                    name=name,
                    origin=origin,
                )
                found[asset.ref] = asset
        return found

    # -- the memory shelf, through MemoryFileStore -----------------------------

    def _create_leaf(
        self,
        name: str,
        *,
        title: str,
        body: str,
        evidence: Sequence[str],
        resolver: Callable[[str], bool] | None = None,
    ) -> Asset:
        """Author a project memory leaf from shelf fields.

        `title` is the leaf subject and the first non-empty body line is its
        one-line claim -- the shelf collects prose, and `MemoryFileStore` keeps
        its own shape. Evidence stays required: it is the trust boundary that
        makes a leaf usable, not shelf decoration.
        """
        claim = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if not title.strip() or not claim:
            raise LibraryError(
                "a memory leaf needs a title (its subject) and a body whose "
                + "first line is its one-line claim"
            )
        leaf = MemoryLeaf(
            id=name,
            subject=title,
            claim=claim,
            origin="operator",
            by="operator",
            at=datetime.now(UTC),
            evidence=list(evidence),
            lifetime="project",
        )
        return _asset_from_leaf(self.memory.write(leaf, resolver=resolver))

    def _edit_leaf(
        self,
        name: str,
        *,
        title: str | None,
        body: str | None,
        status: AssetStatus | None,
    ) -> Asset:
        leaf = self.memory.get(name)
        if leaf is None:
            raise LibraryError(f"unknown memory leaf {name}")
        update: dict[str, object] = {"version": leaf.version + 1}
        if title is not None:
            update["subject"] = title
        if body is not None:
            update["body"] = body
        if status is not None:
            update["status"] = status
        # Evidence *presence* is still required by `validate_leaf`; only the
        # hash/path resolution is skipped, because drifted evidence is exactly
        # what the owner opens the shelf to correct.
        return _asset_from_leaf(
            self.memory.write(
                leaf.model_copy(update=update), overwrite=True, check_evidence=False
            )
        )


def _asset_from_leaf(leaf: MemoryLeaf) -> Asset:
    """Project one memory leaf onto the shelf without copying it anywhere."""
    return Asset(
        kind=MEMORY_KIND,
        name=leaf.id,
        title=leaf.subject,
        references=list(leaf.evidence),
        body=leaf.body or leaf.claim,
        origin="project",
        status=leaf.status,
    )


def _bundled_root() -> Path:
    """The shipped asset tree: packaged beside the module, else the repo's."""
    here = Path(__file__).resolve().parent
    packaged = here / "assets"
    return packaged if packaged.is_dir() else here.parent / "assets"


__all__ = [
    "Asset",
    "AssetSummary",
    "CONTRACT_ASSET_KIND",
    "KIND_DIRS",
    "LIBRARY_DIR",
    "Library",
    "LibraryError",
    "MAX_CONTEXT_TOKENS",
    "MEMORY_KIND",
    "compile_contract",
    "parse_asset",
    "parse_ref",
    "serialize_asset",
]
