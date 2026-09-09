"""Bounded, deterministic pre-write verification of proposed Python source.

Sprint 3 seam (follow-on to Effort A): parse a proposal, resolve every
reference the AST can prove against the real repository symbol table, and
return a PASS/WARN/BLOCK verdict with repair suggestions for phantom
references and the blast radius (static dependents) when the proposal would
change an existing module.

Verdict contract — unknowns are never called valid and never phantom:
- BLOCK: a reference inside supported coverage does not exist — an import of
  a missing module, a missing from-import name from a repo module, or an
  unbound bare name.
- WARN: no phantoms, but unknown-coverage constructs are present (wildcard
  imports, unresolved getattr literals, exec/eval, relative imports without a
  target module), or the proposal would change a module that static
  dependents still reference.
- PASS: every supported reference resolved, no unknowns, no dependents.

Deterministic: stdlib ``ast``/``difflib`` only, sorted outputs, no model
calls, no network, no mutation. Same tree + same proposal + same environment
→ same report. The repository symbol table is rebuilt from the working tree
on construction, so it can never serve stale bindings; the one environment
dependency (installed-package checks) is stated in the coverage limits.
"""

from __future__ import annotations

import ast
import builtins
import difflib
import importlib.util
import sys
from collections.abc import Set
from dataclasses import dataclass, field
from pathlib import Path

from herdsman import nav

MAX_SOURCE_CHARS = 200_000

BUILTINS: frozenset[str] = frozenset(dir(builtins))
_MODULE_DUNDERS: frozenset[str] = frozenset(
    {
        "__all__",
        "__annotations__",
        "__builtins__",
        "__class__",
        "__debug__",
        "__dict__",
        "__doc__",
        "__file__",
        "__loader__",
        "__module__",
        "__name__",
        "__package__",
        "__path__",
        "__qualname__",
        "__spec__",
    }
)

_COVERAGE_LIMITS: tuple[str, ...] = (
    "supported coverage — enforced, phantom → BLOCK: import statements, from-import names from repository modules, bare names, and attribute chains rooted at an imported repository module",
    "unknown coverage — recorded, never valid, never phantom: wildcard imports, getattr/importlib.import_module literals without a unique repo match, exec/eval, relative imports without a target module, repository attribute chains that leave the module surface",
    "stdlib and third-party from-import attributes are outside the repository symbol table: the import is marked resolved with detail '(external, attribute not verified)' and never gates the verdict",
    "attributes on unproven receivers (locals, parameters, self) are not verified — that is runtime typing, not static resolution",
    "name analysis is flat: use-before-definition, conditional rebinding, and shadowing are not modeled",
    "string annotations are not resolved",
    "stdlib and third-party imports are verified at their top-level package only; third-party installation is checked via importlib.util.find_spec against the current environment, never for attributes",
    "blast radius counts static AST dependents only; dynamic or unresolved repository references are not counted",
)

_DEPENDENT_KINDS = frozenset({"imports", "calls", "instantiates", "references"})


@dataclass(frozen=True)
class Reference:
    """One cross-boundary reference the proposal makes, with its status."""

    kind: str  # import | from_import | name | attribute | dynamic
    name: str
    line: int
    status: str  # resolved | phantom | unknown
    detail: str = ""


@dataclass(frozen=True)
class Repair:
    """A phantom reference mapped to the close real project symbols."""

    phantom: str
    kind: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlastRadius:
    """Static dependents affected by changing an existing target module."""

    target: str
    modified: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    affected_symbols: tuple[str, ...] = ()


@dataclass
class VerifyReport:
    """The verdict plus every reference, repair, and coverage statement."""

    verdict: str  # PASS | WARN | BLOCK
    references: list[Reference] = field(default_factory=list)
    repairs: list[Repair] = field(default_factory=list)
    blast_radius: BlastRadius | None = None
    limits: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-ready shape for a contract check; key order is fixed."""
        return {
            "verdict": self.verdict,
            "error": self.error,
            "references": [
                {
                    "kind": ref.kind,
                    "name": ref.name,
                    "line": ref.line,
                    "status": ref.status,
                    "detail": ref.detail,
                }
                for ref in self.references
            ],
            "repairs": [
                {
                    "phantom": repair.phantom,
                    "kind": repair.kind,
                    "suggestions": list(repair.suggestions),
                }
                for repair in self.repairs
            ],
            "blast_radius": (
                {
                    "target": blast.target,
                    "modified": list(blast.modified),
                    "affected_paths": list(blast.affected_paths),
                    "affected_symbols": list(blast.affected_symbols),
                }
                if (blast := self.blast_radius) is not None
                else None
            ),
            "limits": list(self.limits),
        }


@dataclass
class _ModuleInfo:
    """Top-level name surface of one repository module."""

    bindings: set[str] = field(default_factory=set)
    submodules: set[str] = field(default_factory=set)
    star_imports: bool = False
    dynamic: bool = False  # module-level __getattr__


def _rel_module(rel: str) -> str:
    """Repository-relative path → dotted module name (nav's convention)."""
    parts = rel[: -len(".py")].split("/") if rel.endswith(".py") else rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _bind_names(info: _ModuleInfo, stmts: list[ast.stmt]) -> None:
    """Collect the names these statements bind, one compound level deep."""
    for node in stmts:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            info.bindings.add(node.name)
            if node.name == "__getattr__":
                info.dynamic = True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    info.bindings.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                info.bindings.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                info.bindings.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    info.star_imports = True
                else:
                    info.bindings.add(alias.asname or alias.name)
        elif isinstance(node, ast.If | ast.While | ast.For | ast.AsyncFor):
            for block in (node.body, node.orelse):
                _bind_names(info, block)
        elif isinstance(node, ast.With | ast.AsyncWith):
            _bind_names(info, node.body)
        elif isinstance(node, ast.Try | ast.TryStar):
            blocks = [node.body, node.orelse, node.finalbody]
            blocks.extend(handler.body for handler in node.handlers)
            for block in blocks:
                _bind_names(info, block)
        elif isinstance(node, ast.Match):
            for case in node.cases:
                _bind_names(info, case.body)


def _child_modules(package_dir: Path) -> set[str]:
    """Direct python children of a package directory."""
    found: set[str] = set()
    try:
        children = list(package_dir.iterdir())
    except OSError:
        return found
    for child in children:
        if child.is_file() and child.suffix == ".py" and child.stem != "__init__":
            found.add(child.stem)
        elif child.is_dir() and (child / "__init__.py").is_file():
            found.add(child.name)
    return found


class Verifier:
    """Reusable verifier over one repository tree; build once, verify many.

    The symbol table covers every top-level binding (defs, classes, module
    constants, import re-exports) — nav's public index only lists
    def/class/method symbols, so using it for resolution would raise false
    phantoms on module constants. nav's index is still reused, lazily, for
    blast radius: its static edges are exactly the dependent set.
    """

    def __init__(self, root: Path) -> None:
        self.root: Path = root
        self._modules: dict[str, _ModuleInfo] = {}
        self._module_names: list[str] = []
        self._by_simple: dict[str, list[str]] = {}
        self._index: nav.NavIndex | None = None
        for path in nav.discover_files(root):
            if path.suffix != ".py":
                continue
            rel = path.relative_to(root).as_posix()
            module = _rel_module(rel)
            self._module_names.append(module)
            info = self._modules.setdefault(module, _ModuleInfo())
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (OSError, SyntaxError, ValueError, RecursionError):
                continue
            _bind_names(info, tree.body)
            if rel.endswith("__init__.py"):
                info.submodules |= _child_modules(path.parent)
            self._collect_symbols(module, tree)
        self._module_names.sort()
        for paths in self._by_simple.values():
            paths.sort()

    def _collect_symbols(self, module: str, tree: ast.Module) -> None:
        """Def/class/method paths keyed by simple name, for repairs/getattr."""
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            self._by_simple.setdefault(node.name, []).append(f"{module}:{node.name}")
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                        self._by_simple.setdefault(stmt.name, []).append(
                            f"{module}:{node.name}.{stmt.name}"
                        )

    # -- module-level resolution -------------------------------------------

    def _resolve_module(self, dotted: str) -> tuple[str, str]:
        """(status, detail) for importing the module ``dotted``."""
        first = dotted.split(".")[0]
        if dotted in self._modules:
            return "resolved", dotted
        if first in self._modules:
            return "phantom", f"{dotted} is not a module in this repository"
        if first in sys.stdlib_module_names:
            return "resolved", f"{first} (stdlib, top-level verified)"
        try:
            installed = importlib.util.find_spec(first) is not None
        except (ImportError, ValueError, OSError):
            return "unknown", f"{dotted} could not be verified against this environment"
        if installed:
            return "resolved", f"{first} (external, installed)"
        return (
            "phantom",
            f"{first} is neither a repository module, stdlib, nor installed",
        )

    def _resolve_from_name(self, dotted: str, name: str) -> tuple[str, str]:
        """(status, detail) for ``from dotted import name`` on a repo module."""
        info = self._modules[dotted]
        if name in info.bindings or name in info.submodules:
            return "resolved", f"{dotted}:{name}"
        if info.star_imports or info.dynamic:
            return "unknown", f"{dotted} re-exports dynamically; {name!r} cannot be verified"
        return "phantom", f"{dotted} has no top-level {name!r}"

    # -- proposal scanning ---------------------------------------------------

    def verify(
        self,
        source: str,
        target: str | None = None,
        extra_names: Set[str] | None = None,
    ) -> VerifyReport:
        """Verify one proposed source text against the repository tables.

        ``target`` is the repository-relative path the proposal would live at;
        it provides the module context for relative imports, binds the
        existing module's top-level names into the proposal, and enables the
        blast-radius computation. ``extra_names`` are names the caller knows
        are bound at the insertion point (e.g. surrounding function scope).
        """
        if len(source) > MAX_SOURCE_CHARS:
            return VerifyReport(
                "BLOCK",
                limits=_COVERAGE_LIMITS,
                error=f"proposed source exceeds {MAX_SOURCE_CHARS} characters",
            )
        if target is not None:
            parts = Path(target).parts
            if Path(target).is_absolute() or ".." in parts:
                raise ValueError("target must be a repository-relative path")
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
            return VerifyReport(
                "BLOCK", limits=_COVERAGE_LIMITS, error=f"proposed source does not parse: {exc}"
            )
        try:
            module = _rel_module(target) if target is not None else None
            context_bindings: set[str] = set()
            if module is not None:
                context_bindings = self._modules.get(module, _ModuleInfo()).bindings
            context = context_bindings | set(extra_names or frozenset())
            imports: dict[str, tuple[str, str]] = {}
            raw: list[Reference] = []
            snippet_star = False

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        bound = alias.asname or alias.name.split(".")[0]
                        status, detail = self._resolve_module(alias.name)
                        raw.append(Reference("import", alias.name, node.lineno, status, detail))
                        if status == "resolved":
                            # The runtime object the binding names is the asname,
                            # or the top package for a plain dotted import.
                            obj = alias.name if alias.asname else bound
                            kind = "module" if obj in self._modules else "external"
                            imports[bound] = (kind, obj)
                elif isinstance(node, ast.ImportFrom):
                    snippet_star |= self._scan_from_import(node, target, module, imports, raw)

            proposal_bound = self._collect_bound_names(tree)
            bound = proposal_bound | context

            parents: dict[int, ast.AST] = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[id(child)] = parent

            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    self._check_name(
                        node,
                        imports,
                        proposal_bound,
                        context,
                        context_bindings,
                        module,
                        snippet_star,
                        raw,
                    )
                elif (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.ctx, ast.Load)
                    and not isinstance(parents.get(id(node)), ast.Attribute)
                ):
                    self._check_chain(node, imports, raw)
                elif isinstance(node, ast.Call):
                    self._check_dynamic_call(node, raw)

            ordered = self._merge(raw)
            phantoms = [ref for ref in ordered if ref.status == "phantom"]
            has_unknown = any(ref.status == "unknown" for ref in ordered)
            blast = self._blast_radius(target, tree)
        except RecursionError:
            return VerifyReport(
                "BLOCK", limits=_COVERAGE_LIMITS, error="proposed source is too deeply nested"
            )
        if phantoms:
            verdict = "BLOCK"
        elif has_unknown or (blast is not None and (blast.affected_paths or blast.affected_symbols)):
            verdict = "WARN"
        else:
            verdict = "PASS"
        return VerifyReport(
            verdict,
            ordered,
            [self._repair(ref) for ref in phantoms],
            blast,
            _COVERAGE_LIMITS,
        )

    def _scan_from_import(
        self,
        node: ast.ImportFrom,
        target: str | None,
        module: str | None,
        imports: dict[str, tuple[str, str]],
        raw: list[Reference],
    ) -> bool:
        """Record one ImportFrom; returns True when it uses a wildcard."""
        prefix: list[str] = []
        if node.level:
            if module is None:
                raw.append(
                    Reference(
                        "from_import",
                        "." * node.level + (node.module or ""),
                        node.lineno,
                        "unknown",
                        "relative import without a target module context",
                    )
                )
                star = False
                for alias in node.names:
                    if alias.name == "*":
                        star = True
                        continue
                    imports[alias.asname or alias.name] = (
                        "unknown",
                        "unresolved relative import",
                    )
                return star
            assert target is not None
            parts = module.split(".")
            package = parts if target.endswith("__init__.py") else parts[:-1]
            prefix = package[: len(package) - (node.level - 1)] if node.level > 1 else package
        dotted = (
            ".".join([*prefix, *node.module.split(".")]) if node.module else ".".join(prefix)
        )
        star = False
        for alias in node.names:
            if alias.name == "*":
                star = True
                raw.append(
                    Reference(
                        "from_import",
                        f"{dotted}:*",
                        node.lineno,
                        "unknown",
                        "wildcard import: the imported names cannot be verified",
                    )
                )
                continue
            ref_name = f"{dotted}:{alias.name}"
            info = self._modules.get(dotted)
            if info is not None:
                status, detail = self._resolve_from_name(dotted, alias.name)
                raw.append(Reference("from_import", ref_name, node.lineno, status, detail))
                if status == "resolved":
                    binding = alias.asname or alias.name
                    if f"{dotted}.{alias.name}" in self._modules:
                        imports[binding] = ("module", f"{dotted}.{alias.name}")
                    else:
                        imports[binding] = ("symbol", f"{dotted}:{alias.name}")
                continue
            status, detail = self._resolve_module(dotted)
            if status == "phantom":
                raw.append(Reference("from_import", ref_name, node.lineno, status, detail))
                continue
            raw.append(
                Reference(
                    "from_import",
                    ref_name,
                    node.lineno,
                    "resolved",
                    f"{ref_name} (external, attribute not verified)",
                )
            )
            imports[alias.asname or alias.name] = ("external", ref_name)
        return star

    def _collect_bound_names(self, tree: ast.Module) -> set[str]:
        """Every name the proposal itself binds."""
        bound: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
                bound.add(node.id)
            elif isinstance(node, ast.arg):
                bound.add(node.arg)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                bound.add(node.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.Global | ast.Nonlocal):
                bound.update(node.names)
            elif isinstance(node, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple):
                bound.add(node.name)
            elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
                bound.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                bound.add(node.rest)
        return bound

    def _check_name(
        self,
        node: ast.Name,
        imports: dict[str, tuple[str, str]],
        proposal_bound: Set[str],
        context: Set[str],
        context_bindings: Set[str],
        module: str | None,
        snippet_star: bool,
        raw: list[Reference],
    ) -> None:
        name = node.id
        if name in proposal_bound:
            return
        if name in context:
            # Bound by the existing target module or the caller's insertion
            # scope: proven against the real file, recorded as evidence.
            if name in context_bindings and module is not None:
                detail = f"{module}:{name} (target context)"
            else:
                detail = "caller-provided name context"
            raw.append(Reference("name", name, node.lineno, "resolved", detail))
            return
        binding = imports.get(name)
        if binding is not None:
            # Symbol bindings are proven here; module bindings are proven
            # through their attribute chains; external bindings are outside
            # the repo table and already recorded at their import.
            if binding[0] == "symbol":
                raw.append(Reference("name", name, node.lineno, "resolved", binding[1]))
            elif binding[0] == "unknown":
                raw.append(
                    Reference(
                        "name",
                        name,
                        node.lineno,
                        "unknown",
                        "importer could not be resolved",
                    )
                )
            return
        if name in BUILTINS or name in _MODULE_DUNDERS:
            return
        if snippet_star:
            raw.append(
                Reference(
                    "name", name, node.lineno, "unknown", "name may come from a wildcard import"
                )
            )
            return
        raw.append(
            Reference(
                "name", name, node.lineno, "phantom", "no binding, import, builtin, or repo symbol"
            )
        )

    def _check_chain(
        self, node: ast.Attribute, imports: dict[str, tuple[str, str]], raw: list[Reference]
    ) -> None:
        """Verify ``module.attr`` chains; leave receiver attributes alone."""
        parts: list[str] = []
        cursor: ast.expr = node
        while isinstance(cursor, ast.Attribute):
            parts.append(cursor.attr)
            cursor = cursor.value
        parts.reverse()  # collected outermost-first; walk the chain inward
        if not isinstance(cursor, ast.Name):
            return
        binding = imports.get(cursor.id)
        if binding is None or binding[0] != "module":
            return
        current = binding[1]
        info = self._modules.get(current)
        if info is None:
            return  # stdlib/external module: attributes are outside coverage
        for position, part in enumerate(parts):
            last = position == len(parts) - 1
            deeper = f"{current}.{part}"
            if part in info.bindings or part in info.submodules:
                if deeper in self._modules:
                    if last:
                        raw.append(
                            Reference("attribute", f"{current}:{part}", node.lineno, "resolved", deeper)
                        )
                        return
                    current, info = deeper, self._modules[deeper]
                    continue
                if last:
                    raw.append(
                        Reference(
                            "attribute", f"{current}:{part}", node.lineno, "resolved", f"{current}:{part}"
                        )
                    )
                else:
                    raw.append(
                        Reference(
                            "attribute",
                            f"{current}:{part}",
                            node.lineno,
                            "unknown",
                            f"attributes of {current}:{part} are beyond the module surface",
                        )
                    )
                return
            if last:
                raw.append(
                    Reference(
                        "attribute",
                        f"{current}:{part}",
                        node.lineno,
                        "phantom",
                        f"{current} has no top-level {part!r}",
                    )
                )
            else:
                raw.append(
                    Reference(
                        "attribute",
                        f"{current}:{part}",
                        node.lineno,
                        "unknown",
                        f"{current}:{part} not found; deeper access unverifiable",
                    )
                )
            return

    def _check_dynamic_call(self, node: ast.Call, raw: list[Reference]) -> None:
        """getattr/import_module literals, exec/eval — unknown, never phantom."""
        func = node.func
        name = ""
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if (
            name == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            lit = str(node.args[1].value)
            # ponytail: name-only match; receiver types would need a type checker
            paths = self._by_simple.get(lit, [])
            if len(paths) == 1:
                raw.append(
                    Reference(
                        "dynamic",
                        f"getattr({lit!r})",
                        node.lineno,
                        "resolved",
                        f"{paths[0]} (name-only match; receiver not verified)",
                    )
                )
            elif lit not in BUILTINS:
                raw.append(
                    Reference(
                        "dynamic",
                        f"getattr({lit!r})",
                        node.lineno,
                        "unknown",
                        "no unique repository symbol matches this literal",
                    )
                )
        elif (
            name in ("__import__", "import_module")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            dotted = str(node.args[0].value)
            status, detail = self._resolve_module(dotted)
            raw.append(Reference("dynamic", f"import {dotted}", node.lineno, status, detail))
        elif name in ("exec", "eval"):
            raw.append(
                Reference(
                    "dynamic",
                    name,
                    node.lineno,
                    "unknown",
                    f"{name} content cannot be verified statically",
                )
            )

    # -- verdict inputs ------------------------------------------------------

    def _merge(self, raw: list[Reference]) -> list[Reference]:
        """Deduplicate on (kind, name, status), keep the first line, sort."""
        merged: dict[tuple[str, str, str], Reference] = {}
        for ref in raw:
            key = (ref.kind, ref.name, ref.status)
            existing = merged.get(key)
            if existing is None or ref.line < existing.line:
                merged[key] = ref
        return sorted(merged.values(), key=lambda ref: (ref.line, ref.kind, ref.name))

    def _blast_radius(self, target: str | None, tree: ast.Module) -> BlastRadius | None:
        """Static dependents of the target module and the symbols it replaces."""
        if target is None:
            return None
        module = _rel_module(target)
        existing = self._modules.get(module)
        proposed: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                proposed.add(node.name)
            elif isinstance(node, ast.Assign):
                for stmt_target in node.targets:
                    if isinstance(stmt_target, ast.Name):
                        proposed.add(stmt_target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                proposed.add(node.target.id)
        modified = sorted(proposed & existing.bindings) if existing is not None else []
        if self._index is None:
            self._index = nav.build_index(self.root)
        paths: set[str] = set()
        symbols: set[str] = set()
        for edge in self._index.edges:
            if edge["resolution"] != "static" or edge["kind"] not in _DEPENDENT_KINDS:
                continue
            dst = str(edge["dst"])
            if dst != module and not dst.startswith(module + ":"):
                continue
            if str(edge["file"]) == target:
                continue
            paths.add(str(edge["file"]))
            symbols.add(str(edge["src"]))
        return BlastRadius(target, tuple(modified), tuple(sorted(paths)), tuple(sorted(symbols)))

    def _repair(self, ref: Reference) -> Repair:
        """Map a phantom to the close real project symbols (difflib)."""
        if ref.kind == "import":
            matches = difflib.get_close_matches(ref.name, self._module_names, n=3, cutoff=0.6)
            return Repair(ref.name, ref.kind, tuple(matches))
        leaf = ref.name.split(":")[-1]
        suggestions: list[str] = []
        for match in difflib.get_close_matches(leaf, sorted(self._by_simple), n=3, cutoff=0.6):
            suggestions.extend(self._by_simple[match])
        if not suggestions:
            suggestions = [
                f"{module} (module)"
                for module in difflib.get_close_matches(leaf, self._module_names, n=3, cutoff=0.6)
            ]
        return Repair(ref.name, ref.kind, tuple(dict.fromkeys(suggestions))[:3])


def verify_proposed(
    root: Path,
    source: str,
    target: str | None = None,
    extra_names: Set[str] | None = None,
) -> VerifyReport:
    """One-shot convenience: build a Verifier for root and verify one proposal."""
    return Verifier(root).verify(source, target=target, extra_names=extra_names)
