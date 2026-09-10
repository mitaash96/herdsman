"""Optional, replaceable Aider RepoMap adapter.

Only the structural map is exposed. Aider agent/chat/model functionality is
never imported or invoked, and an absent optional dependency is an explicit
unavailable result rather than a fabricated map.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, cast, final, runtime_checkable


@dataclass(frozen=True)
class RepoMapRequest:
    repo_root: Path
    scope: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    token_budget: int = 0
    counter: Callable[[str], int] | None = None


@dataclass(frozen=True)
class RepoMapResult:
    available: bool
    text: str = ""
    tokens: int | None = 0
    source: str = "unavailable"
    provenance: str = ""
    cache_key: str = ""
    token_count_exact: bool = False
    hard_token_bound: bool = False


@runtime_checkable
class RepoMapBackend(Protocol):
    def build(self, request: RepoMapRequest) -> str: ...


class RepoMapFactory(Protocol):
    def __call__(self, *, map_tokens: int, root: str) -> object: ...


@runtime_checkable
class AiderRepoMap(Protocol):
    def get_repo_map(self) -> object: ...


def _tree_digest(root: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    selected = sorted(paths)
    if not selected:
        try:
            selected = subprocess.check_output(
                ["git", "-C", str(root), "ls-files"], text=True, stderr=subprocess.DEVNULL
            ).splitlines()
        except (OSError, subprocess.CalledProcessError):
            selected = sorted(
                str(path.relative_to(root))
                for path in root.rglob("*.py")
                if ".git" not in path.parts and ".venv" not in path.parts
            )
    expanded: list[str] = []
    for relative in selected:
        path = root / relative
        if path.is_dir():
            expanded.extend(
                str(candidate.relative_to(root))
                for candidate in sorted(path.rglob("*"))
                if candidate.is_file() and ".git" not in candidate.parts
            )
        else:
            expanded.append(relative)
    for relative in sorted(set(expanded)):
        path = root / relative
        digest.update(relative.encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:24]


@final
class RepoMapAdapter:
    """One narrow adapter around the unstable Aider RepoMap implementation."""

    def __init__(
        self,
        backend: RepoMapBackend | RepoMapFactory | None = None,
        *,
        counter: Callable[[str], int] | None = None,
    ) -> None:
        self.backend = backend if backend is not None else self._load_backend()
        self.counter = counter

    @staticmethod
    def _load_backend() -> RepoMapFactory | None:
        try:
            module = importlib.import_module("aider.repomap")
        except ImportError:
            return None
        # The adapter does not assume a single Aider constructor. This lookup
        # is deliberately isolated so a future pin change touches one seam.
        return cast(RepoMapFactory | None, getattr(module, "RepoMap", None))

    def build(
        self,
        request: RepoMapRequest,
        *,
        counter: Callable[[str], int] | None = None,
    ) -> RepoMapResult:
        selected_counter = counter or request.counter or self.counter
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "tree": _tree_digest(
                        request.repo_root,
                        tuple(sorted(set(request.scope) | set(request.changed_paths))),
                    ),
                    "scope": sorted(request.scope),
                    "changed": sorted(request.changed_paths),
                    "budget": request.token_budget,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:24]
        if request.token_budget <= 0:
            return RepoMapResult(
                available=self.backend is not None,
                source="aider-repomap" if self.backend is not None else "unavailable",
                provenance="zero token budget" if self.backend is not None else "aider RepoMap optional dependency is absent",
                cache_key=cache_key,
                token_count_exact=selected_counter is not None,
                hard_token_bound=True,
            )
        if self.backend is None:
            return RepoMapResult(
                available=False,
                source="unavailable",
                provenance="aider RepoMap optional dependency is absent",
                cache_key=cache_key,
            )
        try:
            text = self._render(request)
        except (OSError, TypeError, ValueError, AttributeError, RuntimeError):
            return RepoMapResult(
                available=False,
                source="unavailable",
                provenance="compatible Aider RepoMap API unavailable",
                cache_key=cache_key,
            )
        if selected_counter is None:
            # A character truncation is deterministic but is not a token hard
            # bound. Keep the result explicitly labelled rather than claiming
            # the old four-characters-per-token heuristic is exact.
            text = text[: max(request.token_budget, 0) * 4]
            return RepoMapResult(
                available=True,
                text=text,
                tokens=len(text) // 4,
                source="aider-repomap",
                provenance="character-bounded estimate; explicit counter unavailable",
                cache_key=cache_key,
                token_count_exact=False,
                hard_token_bound=False,
            )
        limit = max(request.token_budget, 0)
        # Find the longest prefix accepted by the supplied authoritative counter.
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if max(selected_counter(text[:middle]), 0) <= limit:
                low = middle
            else:
                high = middle - 1
        text = text[:low]
        tokens = max(selected_counter(text), 0)
        return RepoMapResult(
            available=True,
            text=text,
            tokens=tokens,
            source="aider-repomap",
            provenance="aider RepoMap structural output; explicit token counter",
            cache_key=cache_key,
            token_count_exact=True,
            hard_token_bound=True,
        )

    def _render(self, request: RepoMapRequest) -> str:
        backend = self.backend
        if backend is None:
            raise AttributeError("Aider RepoMap backend is absent")
        if isinstance(backend, RepoMapBackend):
            return str(backend.build(request))
        # Compatible Aider classes are isolated behind this constructor seam;
        # no agent/chat methods are reachable here.
        instance = backend(
            map_tokens=request.token_budget,
            root=str(request.repo_root),
        )
        if isinstance(instance, AiderRepoMap):
            return str(instance.get_repo_map())
        if isinstance(instance, RepoMapBackend):
            return str(instance.build(request))
        raise AttributeError("Aider RepoMap backend has no structural map method")


__all__ = ["RepoMapAdapter", "RepoMapRequest", "RepoMapResult"]
