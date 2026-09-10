"""Project-local, deterministic shared-memory storage and delivery helpers.

Markdown files are the canonical content.  The daemon is the only writer; this
module deliberately contains no model or executor integration.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, cast

from pydantic import BaseModel, ConfigDict, Field

from .classes import MemoryLeaf, ScopeTrie, path_segments

MEMORY_DIR = Path(".herdsman/memory")
CAPABILITY_FILE = Path(".herdsman/memory.json")
MAX_BODY_TOKENS = 300
MAX_POINTER_TOKENS = 40
MAX_INLINE_TOKENS = 200
_MAX_CLAIM_CHARS = 400
_MAX_SUBJECT_CHARS = 160
_ALLOWED_FRONTMATTER = {
    "id", "subject", "claim", "evidence", "scope", "origin", "lifetime",
    "status", "ttl", "ttl_days", "ttl_runs", "body", "by", "at", "owner_run",
    "version",
}


def normalize_subject(value: str) -> str:
    """Normalize a subject for exact matching and conflict indexing."""
    return " ".join(value.strip().casefold().split())


def token_count(value: str) -> int:
    """Conservative, deterministic token estimate used for hard budgets."""
    return len(re.findall(r"\S+", value))


def leaf_version(leaf: MemoryLeaf) -> str:
    """Return a stable version even for old event-projected leaves."""
    if leaf.version:
        return str(leaf.version)
    return hashlib.sha256(_leaf_material(leaf).encode()).hexdigest()[:12]


def _leaf_material(leaf: MemoryLeaf) -> str:
    return json.dumps(
        leaf.model_dump(mode="json", exclude={"version", "content_hash"}),
        sort_keys=True,
        separators=(",", ":"),
    )


def content_hash(leaf: MemoryLeaf) -> str:
    return hashlib.sha256(_leaf_material(leaf).encode()).hexdigest()


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
        raise ValueError("memory id must be a lowercase safe slug")
    return value


def _clean_line(value: str, name: str, limit: int) -> str:
    text = " ".join(value.strip().split())
    if not text:
        raise ValueError(f"memory {name} cannot be empty")
    if len(text) > limit:
        raise ValueError(f"memory {name} exceeds {limit} characters")
    return text


def validate_leaf(leaf: MemoryLeaf, *, require_evidence: bool = True) -> MemoryLeaf:
    """Validate model-authored content before it reaches the file protocol."""
    _safe_id(leaf.id)
    subject = normalize_subject(leaf.subject)
    if len(subject) > _MAX_SUBJECT_CHARS:
        raise ValueError("memory subject is too long")
    claim = _clean_line(leaf.claim, "claim", _MAX_CLAIM_CHARS)
    if "\n" in leaf.claim or "\r" in leaf.claim:
        raise ValueError("memory claim must be one line")
    if require_evidence and not leaf.evidence:
        raise ValueError("memory writes require evidence")
    if token_count(leaf.body) > MAX_BODY_TOKENS:
        raise ValueError(f"memory body exceeds {MAX_BODY_TOKENS} tokens")
    for scope in leaf.scope:
        if not scope.strip() or scope.startswith("/") or ".." in path_segments(scope):
            raise ValueError(f"invalid memory scope {scope!r}")
    if leaf.lifetime not in {"run", "project"}:
        raise ValueError("memory lifetime must be run or project")
    if leaf.lifetime == "project" and leaf.owner_run is not None:
        raise ValueError("project memory cannot have an owner run")
    return leaf.model_copy(update={"subject": subject, "claim": claim})


def _format_scalar(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        # Keep strings that the deliberately small parser would otherwise
        # coerce into booleans or integers.
        if not value or value in {"true", "false"} or re.fullmatch(r"-?\d+", value):
            return json.dumps(value)
    return str(value)


def serialize_leaf(leaf: MemoryLeaf) -> str:
    leaf = validate_leaf(leaf)
    fields: list[tuple[str, object]] = [
        ("id", leaf.id), ("subject", leaf.subject), ("claim", leaf.claim),
        ("evidence", list(leaf.evidence)), ("scope", list(leaf.scope)),
        ("origin", leaf.origin), ("lifetime", leaf.lifetime),
        ("status", leaf.status), ("ttl", leaf.ttl),
        ("ttl_days", leaf.ttl_days), ("ttl_runs", leaf.ttl_runs),
        ("by", leaf.by), ("at", leaf.at.isoformat()),
        ("owner_run", leaf.owner_run), ("version", leaf.version),
    ]
    lines = ["---"]
    for key, value in fields:
        if value is None or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {_format_scalar(item)}" for item in value)
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    lines.extend(["---", leaf.body.rstrip(), ""])
    return "\n".join(lines)


def _parse_scalar(value: str) -> object:
    if value == "":
        return None
    if value.startswith('"') and value.endswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, str):
                return parsed
    if value == "true":
        return True
    if value == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_leaf(text: str) -> MemoryLeaf:
    """Parse the intentionally small YAML subset emitted by ``serialize_leaf``."""
    if not text.startswith("---\n"):
        raise ValueError("memory file must start with frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("memory frontmatter is not closed")
    front, body = text[4:end], text[end + 5:]
    values: dict[str, object] = {}
    current: str | None = None
    for line in front.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - "):
            if current is None or not isinstance(values.get(current), list):
                raise ValueError("memory list item has no list field")
            cast(list[object], values[current]).append(_parse_scalar(line[4:].strip()))
            continue
        if ":" not in line or line.startswith(" "):
            raise ValueError("invalid memory frontmatter")
        key, raw = line.split(":", 1)
        if key not in _ALLOWED_FRONTMATTER or key in values:
            raise ValueError(f"unknown or duplicate memory field {key!r}")
        value = raw.strip()
        if not value:
            values[key] = []
            current = key
        else:
            values[key] = _parse_scalar(value)
            current = None
    values["body"] = body.rstrip("\n")
    try:
        if isinstance(values.get("at"), str):
            values["at"] = datetime.fromisoformat(cast(str, values["at"]))
        return validate_leaf(MemoryLeaf.model_validate(values))
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"invalid memory leaf: {exc}") from exc


class MemoryFileStore:
    """Atomic project-local Markdown leaf store."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.directory = self.project_root / MEMORY_DIR

    def list(self) -> list[MemoryLeaf]:
        if not self.directory.is_dir():
            return []
        leaves: list[MemoryLeaf] = []
        for path in sorted(self.directory.glob("*.md")):
            leaves.append(parse_leaf(path.read_text(encoding="utf-8")))
        return leaves

    def file_hash(self, leaf_id: str) -> str | None:
        _safe_id(leaf_id)
        path = self.directory / f"{leaf_id}.md"
        if not path.is_file():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def get(self, leaf_id: str) -> MemoryLeaf | None:
        _safe_id(leaf_id)
        path = self.directory / f"{leaf_id}.md"
        if not path.is_file():
            return None
        leaf = parse_leaf(path.read_text(encoding="utf-8"))
        if leaf.id != leaf_id:
            raise ValueError(f"memory file {path} has mismatched id")
        return leaf

    def _resolve(self, relative: str) -> Path:
        candidate = (self.project_root / relative).resolve()
        if candidate != self.project_root and self.project_root not in candidate.parents:
            raise ValueError(f"evidence path escapes project: {relative}")
        return candidate

    def validate_evidence(
        self, leaf: MemoryLeaf, resolver: Callable[[str], bool] | None = None
    ) -> None:
        for ref in leaf.evidence:
            if "@" in ref and not ref.startswith(("check:", "checkpoint:", "decision:")):
                relative, expected = ref.rsplit("@", 1)
                if not relative or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                    raise ValueError(f"evidence reference must use a SHA-256 hash: {ref}")
                expected = expected.casefold()
                path = self._resolve(relative)
                if not path.is_file():
                    raise ValueError(f"evidence path is missing: {relative}")
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != expected and not actual.startswith(expected):
                    raise ValueError(f"evidence hash mismatch: {relative}")
            elif resolver is None or not resolver(ref):
                raise ValueError(f"evidence reference is unresolved: {ref}")

    def write(
        self,
        leaf: MemoryLeaf,
        *,
        resolver: Callable[[str], bool] | None = None,
        overwrite: bool = False,
        check_evidence: bool = True,
    ) -> MemoryLeaf:
        leaf = validate_leaf(leaf)
        if leaf.lifetime != "project":
            raise ValueError("only project leaves are stored in Markdown")
        if check_evidence:
            self.validate_evidence(leaf, resolver)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{leaf.id}.md"
        if path.exists() and not overwrite:
            raise ValueError(f"memory leaf {leaf.id} already exists")
        payload = serialize_leaf(leaf)
        fd, temporary = tempfile.mkstemp(prefix=f".{leaf.id}.", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            with suppress_os_error():
                os.unlink(temporary)
        return leaf.model_copy(update={"content_hash": hashlib.sha256(payload.encode()).hexdigest()})

    def retire(self, leaf_id: str) -> MemoryLeaf:
        leaf = self.get(leaf_id)
        if leaf is None:
            raise ValueError(f"unknown memory leaf {leaf_id}")
        return self.write(leaf.model_copy(update={"status": "retired", "version": leaf.version + 1}), overwrite=True)

    def gitignore_opt_out(self, enabled: bool = True) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ".gitignore"
        if enabled:
            path.write_text("*.md\n", encoding="utf-8")
        elif path.exists():
            path.unlink()


class suppress_os_error:
    def __enter__(self) -> "suppress_os_error":
        return self
    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return isinstance(exc_type, type) and issubclass(exc_type, OSError)


class MemoryCapabilityError(ValueError):
    pass


@dataclass(frozen=True)
class MemoryCapabilities:
    harnesses: Mapping[str, str]
    author: Mapping[str, object] | None = None

    @classmethod
    def load(cls, project_root: str | Path = ".") -> "MemoryCapabilities":
        path = Path(project_root).expanduser().resolve() / CAPABILITY_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise MemoryCapabilityError(f"memory capability declaration is missing at {path}") from exc
        except json.JSONDecodeError as exc:
            raise MemoryCapabilityError(f"invalid memory capability declaration: {exc}") from exc
        root = raw if isinstance(raw, dict) else {}
        author_value = root.get("author") or root.get("memory_author")
        author: Mapping[str, object] | None = None
        if author_value is not None:
            if not isinstance(author_value, dict):
                raise MemoryCapabilityError("memory author declaration must be an object")
            author = dict(cast(dict[str, object], author_value))
            if not isinstance(author.get("model", "default"), str) or not isinstance(author.get("binary", "pi"), str):
                raise MemoryCapabilityError("memory author binary and model must be strings")
        if isinstance(root.get("harnesses"), dict):
            raw = root["harnesses"]
        elif isinstance(root.get("adapters"), dict):
            raw = root["adapters"]
        if not isinstance(raw, dict) or not raw:
            raise MemoryCapabilityError("memory capability declaration must name harnesses")
        result: dict[str, str] = {}
        for name, capability in raw.items():
            if isinstance(capability, dict):
                capability = capability.get("class")
            if not isinstance(name, str) or not isinstance(capability, str) or capability not in {"A", "B", "C"}:
                raise MemoryCapabilityError(f"unknown memory capability for {name!r}")
            result[name] = cast(str, capability)
        return cls(result, author=author)

    def for_harness(self, harness: str) -> str:
        try:
            return self.harnesses[harness]
        except KeyError as exc:
            raise MemoryCapabilityError(f"no memory capability declared for harness {harness!r}") from exc

    def ensure_sugar(
        self, project_root: str | Path, harness: str, attempt_id: str | None = None
    ) -> Path | None:
        if self.for_harness(harness) != "B":
            return None
        # Keep this project-root asset generic: attempt-specific commands belong
        # in the compiled packet, not in shared mutable state.
        del attempt_id
        path = Path(project_root).expanduser().resolve() / "skill" / "AGENTS.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        command = "herdsman agent memory --query <subject>"
        generated = "# Herdsman memory\n\nPull relevant project memory with `herdsman agent memory --query "
        if not path.exists() or (
            path.is_file()
            and path.read_text(encoding="utf-8").startswith(generated)
        ):
            path.write_text(
                f"# Herdsman memory\n\nPull relevant project memory with `{command}`.\n",
                encoding="utf-8",
            )
        return path


def _evidence_fresh(
    store: MemoryFileStore, leaf: MemoryLeaf,
    resolver: Callable[[str], bool] | None = None,
) -> bool:
    try:
        store.validate_evidence(leaf, resolver)
    except ValueError:
        return False
    return True


def _expired(leaf: MemoryLeaf, now: datetime, run_count: int | None) -> bool:
    if leaf.lifetime == "run":
        return False
    if leaf.ttl is None and leaf.ttl_days is None and leaf.ttl_runs is None:
        if now >= leaf.at + timedelta(days=30):
            return True
        if run_count is not None and run_count >= 20:
            return True
    if leaf.ttl_days is not None and now >= leaf.at + timedelta(days=leaf.ttl_days):
        return True
    if leaf.ttl_runs is not None and run_count is not None and run_count >= leaf.ttl_runs:
        return True
    if isinstance(leaf.ttl, int) and run_count is not None and run_count >= leaf.ttl:
        return True
    if isinstance(leaf.ttl, str):
        match = re.fullmatch(r"(\d+)([dr])", leaf.ttl.strip().casefold())
        if match and match[2] == "d" and now >= leaf.at + timedelta(days=int(match[1])):
            return True
        if match and match[2] == "r" and run_count is not None and run_count >= int(match[1]):
            return True
    return False


def eligible_memory(
    leaves: Iterable[MemoryLeaf],
    *,
    scopes: Iterable[str] = (),
    subject: str | None = None,
    store: MemoryFileStore | None = None,
    now: datetime | None = None,
    run_count: int | Callable[[MemoryLeaf], int] | None = None,
    owner_run: str | None = None,
    evidence_resolver: Callable[[str], bool] | None = None,
) -> list[MemoryLeaf]:
    """Return deterministic, mechanically eligible leaves.

    The caller supplies project and current-run leaves.  Run ownership is
    checked here so a sibling can never receive another run's intervention.
    """
    current = now or datetime.now(UTC)
    requested_subject = normalize_subject(subject) if subject is not None else None
    candidates: list[MemoryLeaf] = []
    for leaf in leaves:
        if leaf.status != "active" or leaf.lifetime == "project" and leaf.owner_run is not None:
            continue
        if leaf.lifetime == "run" and leaf.owner_run not in {None, owner_run}:
            continue
        if requested_subject is not None and normalize_subject(leaf.subject) != requested_subject:
            continue
        count = run_count(leaf) if callable(run_count) else run_count
        if _expired(leaf, current, count):
            continue
        if store is not None and leaf.lifetime == "project" and leaf.evidence and not _evidence_fresh(store, leaf, evidence_resolver):
            continue
        candidates.append(leaf)
    by_subject: dict[str, list[MemoryLeaf]] = {}
    for leaf in candidates:
        by_subject.setdefault(normalize_subject(leaf.subject), []).append(leaf)
    conflicted = {
        key for key, group in by_subject.items()
        if len({hashlib.sha256(leaf.claim.encode()).hexdigest() for leaf in group}) > 1
    }
    candidates = [leaf for leaf in candidates if normalize_subject(leaf.subject) not in conflicted]
    requested = tuple(path_segments(scope) for scope in scopes if scope.strip())
    if requested:
        trie = ScopeTrie()
        for leaf in candidates:
            for scope in leaf.scope:
                trie.insert(scope, leaf.id)
        touching = set().union(*(trie.touching(scope) for scope in scopes if scope.strip()))
        candidates = [leaf for leaf in candidates if not leaf.scope or leaf.id in touching]

    def rank(leaf: MemoryLeaf) -> tuple[int, float, str, str]:
        segments = [path_segments(scope) for scope in leaf.scope]
        depth = max(
            (len(a) for a in segments for b in requested if _overlap(a, b)),
            default=0,
        )
        # Empty scope is intentionally last; recency remains deterministic.
        if not leaf.scope:
            depth = -1
        timestamp = leaf.at.timestamp()
        return (-depth, -timestamp, leaf.id, leaf_version(leaf))

    return sorted(candidates, key=rank)


def _overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b:
        return True
    return a[: min(len(a), len(b))] == b[: min(len(a), len(b))]


@dataclass(frozen=True)
class MemoryDelivery:
    mode: str
    pointers: tuple[str, ...]
    inline: tuple[str, ...]
    leaf_ids: tuple[str, ...]
    versions: tuple[str, ...]
    tokens: int


def deliver_memory(leaves: Iterable[MemoryLeaf], capability: str) -> MemoryDelivery:
    if capability not in {"A", "B", "C"}:
        raise MemoryCapabilityError(f"unknown memory capability {capability!r}")
    selected = list(leaves)
    pointers: list[str] = []
    inline: list[str] = []
    carried: list[MemoryLeaf] = []
    budget = MAX_INLINE_TOKENS if capability == "C" else MAX_POINTER_TOKENS
    for leaf in selected:
        version = leaf_version(leaf)
        if capability == "C":
            spine = f"[{leaf.id}@{version}] {leaf.subject}: {leaf.claim}"
            if leaf.scope:
                spine += f" scope={','.join(leaf.scope)}"
            if leaf.evidence:
                spine += f" evidence={';'.join(leaf.evidence)}"
            body = " ".join(leaf.body.split())
            available = budget - token_count(" ".join(inline) + " " + spine)
            if body and available > 1:
                body = " ".join(body.split()[: available - 1])
                candidate = f"{spine} — {body}"
            else:
                candidate = spine
        else:
            candidate = f"[{leaf.id}@{version}] {leaf.claim}"
        if token_count(" ".join(inline if capability == "C" else pointers) + " " + candidate) > budget:
            continue
        (inline if capability == "C" else pointers).append(candidate)
        carried.append(leaf)
    payload = inline if capability == "C" else pointers
    return MemoryDelivery(
        mode="inline" if capability == "C" else "pointer",
        pointers=tuple(pointers), inline=tuple(inline),
        leaf_ids=tuple(leaf.id for leaf in carried),
        versions=tuple(leaf_version(leaf) for leaf in carried),
        tokens=token_count(" ".join(payload)),
    )


__all__ = [
    "CAPABILITY_FILE", "MAX_BODY_TOKENS", "MAX_INLINE_TOKENS", "MAX_POINTER_TOKENS",
    "MemoryCapabilities", "MemoryCapabilityError", "MemoryDelivery", "MemoryFileStore",
    "content_hash", "deliver_memory", "eligible_memory", "leaf_version", "normalize_subject",
    "parse_leaf", "serialize_leaf", "token_count", "validate_leaf",
]
