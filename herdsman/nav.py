"""Offline code navigation for this repository: index, tour, flow, symbol, guide.

Extraction is stdlib ``ast`` over the working tree, in memory, on every
invocation, so read commands can never serve stale data; the only staleness
surface is the written guide, guarded by a content fingerprint. The default
path needs no daemon, no model call, and no network. The optional codegraph
index is probed only under ``nav guide --deep`` — read-only, resolved from an
already-installed copy (``npx --no-install``: fail fast, never download) — and
is never mutated.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

GUIDE_PATH = Path(".herdsman/nav/guide.md")
_CODEGRAPH_CMD = ("npx", "--no-install", "@colbymchenry/codegraph")
_BUILTINS = frozenset(dir(builtins))
_MAX_TEXT_UNRESOLVED = 20


class NavError(Exception):
    """A user-facing navigation error; the CLI maps it to typer.BadParameter."""


# ---------------------------------------------------------------------------
# Index model
# ---------------------------------------------------------------------------


@dataclass
class _EntryPoints:
    """Typed entry-point inventory; ``to_dict`` feeds ``--json``."""

    console_script: dict[str, object] | None = None
    cli: list[dict[str, object]] = field(default_factory=list)
    routes: list[dict[str, object]] = field(default_factory=list)
    tests: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "console_script": self.console_script,
            "cli": self.cli,
            "routes": self.routes,
            "tests": self.tests,
        }


class NavIndex:
    """The in-memory navigation index; ``to_dict`` is what ``--json`` prints."""

    root: Path
    repo_ref: str | None
    fingerprint: str
    files: list[dict[str, object]]
    symbols: list[dict[str, object]]
    edges: list[dict[str, object]]
    entry_points: _EntryPoints
    unresolved: list[dict[str, object]]
    coverage: dict[str, object]

    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo_ref = None
        self.fingerprint = ""
        self.files = []
        self.symbols = []
        self.edges = []
        self.entry_points = _EntryPoints()
        self.unresolved = []
        self.coverage = {
            "languages": ["python"],
            "excluded": ["ui/ (stub, no source)", "assets/ (prose)"],
            "deep": False,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "repo_ref": self.repo_ref,
            "fingerprint": self.fingerprint,
            "coverage": self.coverage,
            "files": self.files,
            "symbols": self.symbols,
            "edges": self.edges,
            "entry_points": self.entry_points.to_dict(),
            "unresolved": self.unresolved,
        }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def discover_files(root: Path) -> list[Path]:
    """The indexed tree: herdsman/**/*.py, tests/**/*.py, pyproject.toml."""
    found: set[Path] = set()
    for package in ("herdsman", "tests"):
        base = root / package
        if base.is_dir():
            found.update(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        found.add(pyproject)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def fingerprint(root: Path) -> str:
    """Aggregate content fingerprint over the discovered files."""
    aggregate = hashlib.sha256()
    for path in discover_files(root):
        rel = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        aggregate.update(f"{rel}:{digest}\n".encode())
    return f"sha256:{aggregate.hexdigest()}"


def repo_ref(root: Path) -> str | None:
    """``branch@commit`` for the working tree; None outside a git repo."""
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{branch}@{commit}" if branch and commit else None


def _module_name(rel: str) -> str:
    parts = rel[: -len(".py")].split("/") if rel.endswith(".py") else rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _in_repo(module: str) -> bool:
    return module in ("herdsman", "tests") or module.startswith(("herdsman.", "tests."))


class _Lookup:
    """Cross-file lookup structures built after the symbol pass."""

    def __init__(self) -> None:
        self.by_path: dict[str, dict[str, object]] = {}
        self.by_line: dict[tuple[str, int], str] = {}
        self.by_simple: dict[str, list[str]] = {}
        self.classes: set[str] = set()
        self.methods: dict[str, dict[str, str]] = {}
        self.bases: dict[str, list[str]] = {}

    def _candidates(
        self, name: str, module: str, imports: dict[str, tuple[str, str | None]]
    ) -> list[str]:
        imported = imports.get(name)
        if imported is not None:
            target = f"{imported[0]}:{imported[1] or name}"
            if target in self.by_path:
                return [target]
        same = f"{module}:{name}"
        if same in self.by_path:
            return [same]
        return sorted(self.by_simple.get(name, []))

    def resolve_class(
        self, name: str, module: str, imports: dict[str, tuple[str, str | None]]
    ) -> str | None:
        candidates = [
            path for path in self._candidates(name, module, imports) if path in self.classes
        ]
        return candidates[0] if len(candidates) == 1 else None

    def resolve_any(
        self, name: str, module: str, imports: dict[str, tuple[str, str | None]]
    ) -> str | None:
        candidates = self._candidates(name, module, imports)
        return candidates[0] if len(candidates) == 1 else None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(base) for base in node.bases]
        return f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args = ast.unparse(node.args)
    except Exception:  # pragma: no cover - unparse covers every valid arguments node
        args = "..."
    return f"{prefix} {node.name}({args})"


def _doc_first_line(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    doc = ast.get_docstring(node)
    return doc.splitlines()[0] if doc else ""


def _extract_all_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):  # __all__: list[str] = [...]
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(value, ast.List | ast.Tuple):
                    for elt in value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            names.add(elt.value)
    return names


class _Pass1:
    """Symbol pass: symbols, containment, class metadata, entry points."""

    def __init__(self, rel: str, init_all: set[str]) -> None:
        self.rel: str = rel
        self.module: str = _module_name(rel)
        self.init_all: set[str] = init_all
        self.symbols: list[dict[str, object]] = []
        self.contains: list[dict[str, object]] = []
        self.cli_commands: list[dict[str, object]] = []
        self.routes: list[dict[str, object]] = []
        self._paths: set[str] = set()

    def run(self, tree: ast.Module) -> None:
        module_all = _extract_all_names(tree)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._add(node, "class", node.name, None, module_all)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                self._add(node, "function", node.name, None, module_all)
        self._entry_points(tree)

    def _add(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        kind: str,
        qualname: str,
        parent: str | None,
        module_all: set[str],
    ) -> None:
        path = f"{self.module}:{qualname}"
        if path in self._paths:
            return
        self._paths.add(path)
        simple = qualname.split(".")[-1]
        self.symbols.append(
            {
                "name": qualname,
                "kind": kind,
                "module": self.module,
                "file": self.rel,
                "line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
                "signature": _signature(node),
                "bases": [ast.unparse(base) for base in node.bases]
                if isinstance(node, ast.ClassDef)
                else [],
                "returns": ast.unparse(node.returns)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.returns is not None
                else "",
                "exported": simple in module_all or simple in self.init_all,
                "doc": _doc_first_line(node),
            }
        )
        if parent is not None:
            self.contains.append(
                {
                    "kind": "contains",
                    "src": f"{self.module}:{parent}",
                    "dst": path,
                    "file": self.rel,
                    "line": node.lineno,
                    "resolution": "static",
                }
            )
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    self._add(stmt, "method", f"{qualname}.{stmt.name}", qualname, module_all)
        else:
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                    # One-level helpers (e.g. route handlers) keep their own
                    # name, matching the design's `herdsman.daemon:create`;
                    # collisions get the parent prefix.
                    qualifier = stmt.name
                    if f"{self.module}:{qualifier}" in self._paths:
                        qualifier = f"{qualname}.{stmt.name}"
                    self._add(stmt, "function", qualifier, qualname, module_all)

    def _entry_points(self, tree: ast.Module) -> None:
        # ponytail: decorators literally named `.command` match typer's API here;
        # a non-typer decorator using the same attribute would be miscounted.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for decorator in node.decorator_list:
                    func = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if isinstance(func, ast.Attribute) and func.attr == "command":
                        name = node.name
                        if isinstance(decorator, ast.Call):
                            for keyword in decorator.keywords:
                                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                                    name = str(keyword.value.value)
                        self.cli_commands.append(
                            {"command": name, "file": self.rel, "line": node.lineno}
                        )
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add_api_route"):
                continue
            if len(node.args) < 2:
                continue
            path_arg, handler = node.args[0], node.args[1]
            if not (isinstance(path_arg, ast.Constant) and isinstance(path_arg.value, str)):
                continue
            methods: list[str] = []
            for keyword in node.keywords:
                if keyword.arg == "methods" and isinstance(keyword.value, ast.List):
                    methods = [
                        str(elt.value)
                        for elt in keyword.value.elts
                        if isinstance(elt, ast.Constant)
                    ]
            self.routes.append(
                {
                    "method": methods[0] if methods else "?",
                    "path": path_arg.value,
                    "handler": ast.unparse(handler),
                    "file": self.rel,
                    "line": node.lineno,
                }
            )


class _EdgeScanner:
    """Second pass: imports, calls, instantiations, references, unresolved.

    Only what the AST proves becomes a ``static`` edge: direct names,
    ``self``/``cls`` methods of the enclosing class, and ``module.attr`` where
    the import binding is in-repo. Everything else is one honest lower-tier
    label: ``dynamic`` (unproven receiver resolved by name match or bare attr,
    or a getattr string literal), ``external``, or ``unresolved``.
    """

    def __init__(self, rel: str, lookup: _Lookup, tree: ast.Module) -> None:
        self.rel: str = rel
        self.module: str = _module_name(rel)
        self.lookup: _Lookup = lookup
        self.tree: ast.Module = tree
        self.imports: dict[str, tuple[str, str | None]] = {}
        self.edges: list[dict[str, object]] = []
        self.unresolved: list[dict[str, object]] = []
        self._seen: set[tuple[str, str, str, str]] = set()

    def run(self) -> None:
        self._collect_imports()
        scope = _Scope()
        for node in self.tree.body:
            self._visit(node, scope)

    # -- imports -----------------------------------------------------------

    def _collect_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    binding = alias.asname or alias.name.split(".")[0]
                    self.imports[binding] = (alias.name if alias.asname else binding, None)
                    self._import_edge(alias.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                package = (
                    self.module.split(".")
                    if self.rel.endswith("__init__.py")
                    else self.module.split(".")[:-1]
                )
                base = (
                    package[: len(package) - (node.level - 1)]
                    if node.level > 1
                    else package
                )
                if node.module:
                    target = (
                        ".".join(base + node.module.split("."))
                        if node.level
                        else node.module
                    )
                    for alias in node.names:
                        if alias.name != "*":
                            self.imports[alias.asname or alias.name] = (target, alias.name)
                    self._import_edge(target, node.lineno)
                else:  # `from . import submodule`
                    for alias in node.names:
                        sub = ".".join(base + [alias.name])
                        self.imports[alias.asname or alias.name] = (sub, None)
                        self._import_edge(sub, node.lineno)

    def _import_edge(self, dst: str, line: int) -> None:
        self._edge(
            "imports",
            self.module,
            dst,
            line,
            "static" if _in_repo(dst) else "external",
        )

    # -- visitors ----------------------------------------------------------

    def _visit(self, node: ast.AST, scope: _Scope) -> None:
        if isinstance(node, ast.Call):
            self._visit_call(node, scope)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            self._visit_function(node, scope)
        elif isinstance(node, ast.ClassDef):
            cls = self.lookup.by_line.get((self.rel, node.lineno)) or scope.self_class
            inner = _Scope(symbol=scope.symbol, self_class=cls)
            for stmt in node.body:
                self._visit(stmt, inner)
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self._reference(node, scope)
        else:
            for child in ast.iter_child_nodes(node):
                self._visit(child, scope)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, scope: _Scope) -> None:
        path = self.lookup.by_line.get((self.rel, node.lineno))
        inner = _Scope(symbol=path or scope.symbol, self_class=scope.self_class)
        for decorator in node.decorator_list:
            self._visit(decorator, scope)
        for stmt in node.body:
            self._visit(stmt, inner)

    def _visit_call(self, node: ast.Call, scope: _Scope) -> None:
        src = scope.symbol or self.module
        func = node.func
        if isinstance(func, ast.Name):
            self._call_name(node, func.id, src, node.lineno, scope)
        elif isinstance(func, ast.Attribute):
            self._call_attr(node, func.attr, src, node.lineno, scope)
            self._visit(func.value, scope)
        for arg in node.args:
            self._visit(arg, scope)
        for keyword in node.keywords:
            self._visit(keyword.value, scope)

    # -- call resolution ---------------------------------------------------

    def _call_name(
        self, node: ast.Call, name: str, src: str, line: int, scope: _Scope
    ) -> None:
        args = node.args
        if (
            name == "getattr"
            and len(args) >= 2
            and isinstance(args[1], ast.Constant)
            and isinstance(args[1].value, str)
        ):
            lit = str(args[1].value)
            matched = self.lookup.resolve_any(lit, self.module, self.imports)
            self._edge("calls", src, matched or lit, line, "dynamic")
            return
        if name == "cls" and scope.self_class:
            self._edge("instantiates", src, scope.self_class, line, "static")
            return
        imported = self.imports.get(name)
        if imported is not None:
            mod, attr = imported
            if _in_repo(mod):
                if attr:
                    target = f"{mod}:{attr}"
                    if target in self.lookup.by_path:
                        kind = "instantiates" if target in self.lookup.classes else "calls"
                        self._edge(kind, src, target, line, "static")
                return
            self._edge("calls", src, f"{mod}.{attr}" if attr else mod, line, "external")
            return
        target = self.lookup.resolve_class(name, self.module, self.imports)
        if target is not None:
            self._edge("instantiates", src, target, line, "static")
            return
        target = self.lookup.resolve_any(name, self.module, self.imports)
        if target is not None:
            self._edge("calls", src, target, line, "static")
            return
        if name not in _BUILTINS:
            self._unresolved(name, src, line)

    def _call_attr(self, node: ast.Call, attr: str, src: str, line: int, scope: _Scope) -> None:
        assert isinstance(node.func, ast.Attribute)
        value = node.func.value
        if isinstance(value, ast.Name):
            name = value.id
            if name in ("self", "cls") and scope.self_class:
                method = self.lookup.methods.get(scope.self_class, {}).get(attr)
                if method is not None:
                    self._edge("calls", src, method, line, "static")
                elif "BaseModel" in self.lookup.bases.get(scope.self_class, []):
                    self._edge("calls", src, f"pydantic.{attr}", line, "external")
                elif not attr.startswith("__"):
                    self._unresolved(attr, src, line)
                return
            imported = self.imports.get(name)
            if imported is not None:
                mod, bound = imported
                if _in_repo(mod):
                    target = f"{mod}:{bound or attr}"
                    if target in self.lookup.by_path:
                        kind = "instantiates" if target in self.lookup.classes else "calls"
                        self._edge(kind, src, target, line, "static")
                    elif not attr.startswith("__"):
                        self._unresolved(attr, src, line)
                else:
                    self._edge("calls", src, f"{mod}.{attr}", line, "external")
                return
        # Unproven receiver (parameter, local, chained attribute): resolve the
        # attribute by name when uniquely plausible, label it dynamic.
        matched = self.lookup.resolve_any(attr, self.module, self.imports)
        self._edge("calls", src, matched or attr, line, "dynamic")

    # -- references / bookkeeping ------------------------------------------

    def _reference(self, node: ast.Name, scope: _Scope) -> None:
        name = node.id
        src = scope.symbol or self.module
        imported = self.imports.get(name)
        if imported is not None:
            mod, attr = imported
            if _in_repo(mod):
                target = f"{mod}:{attr or name}"
                if target in self.lookup.by_path:
                    self._ref(src, target, node.lineno)
            return
        target = self.lookup.resolve_any(name, self.module, self.imports)
        if target is not None:
            self._ref(src, target, node.lineno)

    def _edge(self, kind: str, src: str, dst: str, line: int, resolution: str) -> None:
        key = (kind, src, dst, resolution)
        if key in self._seen:
            return
        self._seen.add(key)
        self.edges.append(
            {
                "kind": kind,
                "src": src,
                "dst": dst,
                "file": self.rel,
                "line": line,
                "resolution": resolution,
            }
        )

    def _ref(self, src: str, dst: str, line: int) -> None:
        self._edge("references", src, dst, line, "static")

    def _unresolved(self, name: str, src: str, line: int) -> None:
        # One entry per (symbol, name): the list is a vocabulary of
        # unresolvable calls per site, not every call site.
        for item in self.unresolved:
            if item["src"] == src and item["name"] == name:
                return
        self.unresolved.append(
            {"kind": "calls", "src": src, "name": name, "file": self.rel, "line": line}
        )


@dataclass
class _Scope:
    symbol: str | None = None
    self_class: str | None = None


def _console_scripts(root: Path) -> list[dict[str, object]]:
    pyproject = root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = cast("dict[str, object]", tomllib.loads(text))
    except tomllib.TOMLDecodeError:
        return []
    project = cast("dict[str, object] | None", data.get("project"))
    raw_scripts = project.get("scripts") if project is not None else None
    scripts = cast("dict[str, str]", raw_scripts) if isinstance(raw_scripts, dict) else None
    if scripts is None:
        return []
    entries: list[dict[str, object]] = []
    for name, target in scripts.items():
        line = next(
            (
                number
                for number, text_line in enumerate(text.splitlines(), 1)
                if target in text_line
            ),
            None,
        )
        entries.append({"name": name, "target": target, "file": "pyproject.toml", "line": line})
    return entries


def build_index(root: Path) -> NavIndex:
    """Two passes over the working tree: symbols first, then edges.

    Reads never fork git or hash the tree; the fingerprint is a
    written-guide concern, computed lazily by the guide commands.
    """
    index = NavIndex(root)
    trees: dict[str, ast.Module] = {}
    for path in discover_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        index.files.append({"path": rel, "loc": len(text.splitlines())})
        if path.suffix == ".py":
            try:
                trees[rel] = ast.parse(text, filename=rel)
            except SyntaxError:
                continue

    init_all = (
        _extract_all_names(trees["herdsman/__init__.py"])
        if "herdsman/__init__.py" in trees
        else set[str]()
    )
    passes: list[_Pass1] = []
    for rel, tree in sorted(trees.items()):
        pass1 = _Pass1(rel, init_all)
        pass1.run(tree)
        passes.append(pass1)

    lookup = _Lookup()
    for pass1 in passes:
        for symbol in pass1.symbols:
            path = f"{symbol['module']}:{symbol['name']}"
            lookup.by_path[path] = symbol
            lookup.by_line[(str(symbol["file"]), int(str(symbol["line"])))] = path
            lookup.by_simple.setdefault(str(symbol["name"]).split(".")[-1], []).append(path)
            if symbol["kind"] == "class":
                lookup.classes.add(path)
                lookup.bases[path] = [
                    str(base) for base in cast("list[object]", symbol["bases"])
                ]
    for pass1 in passes:
        index.edges.extend(pass1.contains)
    for path, symbol in lookup.by_path.items():
        if symbol["kind"] == "method":
            cls = path.rsplit(".", 1)[0]
            lookup.methods.setdefault(cls, {})[str(symbol["name"]).split(".")[-1]] = path

    scripts = _console_scripts(root)
    index.entry_points = _EntryPoints(
        console_script=scripts[0] if scripts else None,
        cli=[c for pass1 in passes for c in pass1.cli_commands],
        routes=[r for pass1 in passes for r in pass1.routes],
        tests=[
            {
                "node": f"{pass1.rel}::{symbol['name']}",
                "file": pass1.rel,
                "line": int(str(symbol["line"])),
            }
            for pass1 in passes
            if pass1.rel.startswith("tests/")
            for symbol in pass1.symbols
            if symbol["kind"] == "function"
            and "." not in str(symbol["name"])
            and str(symbol["name"]).startswith("test")
        ],
    )
    index.symbols = [symbol for pass1 in passes for symbol in pass1.symbols]
    index.symbols.sort(key=lambda s: (str(s["file"]), int(str(s["line"]))))

    for rel, tree in sorted(trees.items()):
        scanner = _EdgeScanner(rel, lookup, tree)
        scanner.run()
        index.edges.extend(scanner.edges)
        index.unresolved.extend(scanner.unresolved)
    index.edges.sort(key=lambda e: (str(e["file"]), int(str(e["line"]))))
    index.unresolved.sort(key=lambda u: (str(u["file"]), int(str(u["line"]))))
    return index


# ---------------------------------------------------------------------------
# Optional codegraph deep mode (probe only; never init/index/sync/uninit/unlock)
# ---------------------------------------------------------------------------


def _codegraph_probe(root: Path) -> bool:
    """True when the optional codegraph index exists and reports up to date.

    ``--no-install`` makes npx fail fast when the package is not already
    cached/installed, so probing never downloads and never touches the network.
    """
    try:
        proc = subprocess.run(
            [*_CODEGRAPH_CMD, "status", "."],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "up to date" in (proc.stdout + proc.stderr).lower()


def _codegraph_neighbors(root: Path, command: str, symbol: str) -> list[dict[str, object]]:
    try:
        proc = subprocess.run(
            [*_CODEGRAPH_CMD, command, symbol, "--json"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    try:
        parsed = cast("dict[str, object]", json.loads(proc.stdout))
    except ValueError:
        return []
    rows = parsed.get(command)
    if not isinstance(rows, list):
        return []
    result: list[dict[str, object]] = []
    for row in cast("list[object]", rows):
        if isinstance(row, dict):
            result.append(cast("dict[str, object]", row))
    return result


_DEEP_SYMBOLS = (
    "herdsman.daemon:Daemon.create_plan",
    "herdsman.daemon:Daemon.approve_plan",
    "herdsman.daemon:Daemon.run_initiative",
    "herdsman.daemon:Daemon.run_and_settle",
    "herdsman.runtime:PiFrontierPlanner.propose",
    "herdsman.checkpoint:GitCheckpointCollector.capture_base",
)


def _deep_crosscheck(root: Path, index: NavIndex) -> list[dict[str, str]]:
    """Caller/callee cross-check for curated symbols.

    Policy: conflicts are annotated, never merged — a codegraph-only edge is
    surfaced as a conflict line and never downgrades an AST dynamic/unresolved
    label. Rows from other modules with the same short name are ignored, so a
    same-named symbol elsewhere cannot be attributed to this one.
    """
    paths = {f"{sym['module']}:{sym['name']}" for sym in index.symbols}
    incoming: dict[str, set[str]] = {}
    outgoing: dict[str, set[str]] = {}
    for edge in index.edges:
        if edge["kind"] not in ("calls", "instantiates") or edge["resolution"] != "static":
            continue
        dst, src = str(edge["dst"]), str(edge["src"])
        incoming.setdefault(dst, set()).add(src)
        outgoing.setdefault(src, set()).add(dst)
    conflicts: list[dict[str, str]] = []
    for ref in _DEEP_SYMBOLS:
        if ref not in paths:
            continue
        module = ref.split(":", 1)[0]
        simple = ref.split(":")[-1].split(".")[-1]
        for command, direction, mine in (
            ("callers", "caller", incoming.get(ref, set())),
            ("callees", "callee", outgoing.get(ref, set())),
        ):
            for row in _codegraph_neighbors(root, command, simple):
                name = str(row.get("name", ""))
                file = str(row.get("filePath", ""))
                if _module_name(file) != module:
                    continue
                known = any(
                    src.split(":")[-1].split(".")[-1] == name
                    and src.split(":", 1)[0] == module
                    for src in mine
                )
                if not known:
                    conflicts.append(
                        {
                            "symbol": ref,
                            "direction": direction,
                            "edge": f"{name} ({file}:{row.get('startLine')})",
                            "note": "codegraph-only; not a static AST edge (likely dynamic or unresolved)",
                        }
                    )
    return conflicts


# ---------------------------------------------------------------------------
# Curated content — every reference is a name-referenced symbol path, resolved
# at render time against the live index, so drift degrades to a visible
# "unresolved (moved?)" instead of a silently wrong citation.
# ---------------------------------------------------------------------------


@dataclass
class _Facet:
    title: str
    symbols: list[str]
    definition: str
    exports: str
    importers: str
    construction: str
    serialization: str


@dataclass
class _FlowStep:
    label: str
    refs: list[str]
    note: str


@dataclass
class _Flow:
    title: str
    steps: list[_FlowStep]
    absent: list[str]


@dataclass
class _TourStep:
    title: str
    refs: list[str]
    body: str
    checkpoint: str


MODULE_BLURBS: dict[str, str] = {
    "herdsman/cli.py": "Fact: typer CLI; mutation commands (create/approve/run/run-plan/settle/discard) are thin HTTP clients to the daemon, read commands (plan/review/graph/risk/events) project the local event store directly.",
    "herdsman/daemon.py": "Fact: in-process daemon; FastAPI routes registered with string-literal add_api_route calls; Daemon is the event store's single writer with live SSE fan-out. Interpretation: the HTTP API is the only write path.",
    "herdsman/classes.py": "Fact: canonical pydantic domain models; only Ev subclasses persist, everything under Plan is a projection rebuilt by Plan.step/Plan.fold; third-party types never appear here.",
    "herdsman/store.py": "Fact: project-local SQLite append-only event log; append folds before it writes, WAL journal with synchronous=FULL; no domain rule lives here.",
    "herdsman/graph.py": "Fact: pure NetworkX projections over an already-folded Plan — DAG, critical path, contention, risk, overhead; nothing mutates state or persists.",
    "herdsman/runtime.py": "Fact: boundaries for planner output, executor task packets, and completion evidence, plus project-local luna/models config resolution. Interpretation: the model boundary — a pi planner in, a luna executor out.",
    "herdsman/checkpoint.py": "Fact: mechanical checkpoint collection — git head/changed-paths/patch, configured check commands, completion parsing; evidence only, no settlement policy.",
    "herdsman/herdr.py": "Fact: narrow adapter over herdr's JSON-lines socket; herdr objects stay opaque and cross into the domain only as RuntimeFact values converted by to_runtime_observed.",
    "herdsman/nav.py": "Fact: offline navigation — stdlib-ast index rebuilt in memory on every read; the optional codegraph probe runs only under guide --deep, read-only; the written guide under .herdsman/ is the only artifact.",
    "herdsman/__init__.py": "Fact: package surface — re-exports the herdr adapter names via __all__.",
}

CLASS_FACETS: list[_Facet] = [
    _Facet(
        title="Domain events",
        symbols=[
            "herdsman.classes:Ev",
            "herdsman.classes:PlanCreated",
            "herdsman.classes:PlanProposed",
            "herdsman.classes:PlanApproved",
            "herdsman.classes:AttemptStarted",
            "herdsman.classes:AttemptProvisioned",
            "herdsman.classes:RuntimeObserved",
            "herdsman.classes:CheckpointRecorded",
            "herdsman.classes:InitiativeSettled",
            "herdsman.classes:InitiativeFailed",
        ],
        definition="Fact: frozen pydantic event models; the only things ever persisted, appended by the daemon and folded by Plan.step/Plan.fold.",
        exports="Interpretation: classes.py declares no __all__, so these are not a declared consumer API — consumer intent unknown beyond internal use by store/daemon/tests.",
        importers="herdsman.store, herdsman.daemon, herdsman.runtime, herdsman.checkpoint, herdsman.herdr, herdsman.cli, and most tests.",
        construction="Fact: Daemon.append is the production write path (single writer); tests construct fixtures directly.",
        serialization="Fact: persisted as one JSON row per event (EventStore.append, model_dump_json) and re-emitted live as SSE data (daemon.sse).",
    ),
    _Facet(
        title="Projections",
        symbols=[
            "herdsman.classes:Plan",
            "herdsman.classes:Initiative",
            "herdsman.classes:Attempt",
            "herdsman.classes:Subtask",
        ],
        definition="Fact: rebuilt state — never persisted; Plan.step folds one event at a time, Plan.fold rebuilds from the stream, and Plan.ready computes readiness on demand.",
        exports="Interpretation: no __all__ declaration in classes.py — consumer intent unknown beyond internal projections.",
        importers="herdsman.store (fold), herdsman.daemon, herdsman.graph, herdsman.cli, tests.",
        construction="Fact: constructed only inside Plan.step while folding; nothing else builds a Plan in production paths.",
        serialization="Fact: daemon routes return plan projections as JSON on read (model_dump); projections are never written to the store.",
    ),
    _Facet(
        title="Value objects",
        symbols=[
            "herdsman.classes:Assignment",
            "herdsman.classes:Routes",
            "herdsman.classes:Usage",
            "herdsman.classes:CheckResult",
            "herdsman.classes:Checkpoint",
            "herdsman.classes:ArtifactRef",
            "herdsman.classes:InitiativeSpec",
        ],
        definition="Fact: FrozenModel value objects embedded in events and projections; Checkpoint is the mechanical evidence manifest produced by GitCheckpointCollector.",
        exports="Interpretation: no __all__ in classes.py — intent unknown; they cross the store/API boundary as embedded payload.",
        importers="herdsman.daemon, herdsman.runtime, herdsman.checkpoint, herdsman.graph, herdsman.herdr, herdsman.cli, tests.",
        construction="Fact: Checkpoint/CheckResult are built by the collector from git and check evidence; Assignment/Routes come from planner proposals and assignments.",
        serialization="Fact: model_dump_json inside event payloads, checkpoints, and API responses.",
    ),
    _Facet(
        title="Graph models",
        symbols=[
            "herdsman.graph:PlanGraph",
            "herdsman.graph:NodeStatus",
            "herdsman.graph:RiskReport",
            "herdsman.graph:NodeRisk",
            "herdsman.graph:Contention",
            "herdsman.graph:Overhead",
            "herdsman.graph:ScopeTrie",
        ],
        definition="Fact: read-only projection models over a folded Plan — running graph, per-node risk, contention, overhead.",
        exports="Fact: listed in graph.py's __all__ (ScopeTrie included) — a declared internal surface; consumer contract beyond it: intent unknown.",
        importers="herdsman.daemon (route responses), herdsman.cli (graph/risk commands).",
        construction="Fact: built by the pure functions plan_graph/risk_report/overhead; ScopeTrie is an internal write-scope matcher.",
        serialization="Fact: returned as JSON by daemon routes /plans/{id}/graph and /risk and printed by CLI graph/risk.",
    ),
    _Facet(
        title="Runtime boundary (planner/executor)",
        symbols=[
            "herdsman.runtime:TaskPacket",
            "herdsman.runtime:PiFrontierPlanner",
            "herdsman.runtime:PlannerError",
            "herdsman.runtime:CompletionError",
            "herdsman.runtime:LunaConfigError",
        ],
        definition="Fact: TaskPacket is the only context an executor receives; PiFrontierPlanner makes one bounded pi call returning a validated proposal.",
        exports="Fact: listed in runtime.py's __all__; consumer contract beyond the daemon/cli importers: intent unknown.",
        importers="herdsman.daemon, herdsman.cli (resolve_model_tiers, LunaConfigError).",
        construction="Fact: Daemon.create_plan builds the planner; Daemon.run_initiative compiles the packet from the initiative spec and settled upstream inputs.",
        serialization="Fact: packet.json() feeds token estimation; proposal_from_result parses the planner's JSON into a PlanProposed event.",
    ),
    _Facet(
        title="herdr adapter",
        symbols=[
            "herdsman.herdr:HerdrAdapter",
            "herdsman.herdr:HerdrConfig",
            "herdsman.herdr:RuntimeFact",
            "herdsman.herdr:HerdrError",
        ],
        definition="Fact: the only module that speaks herdr's JSON-lines socket protocol; worktrees, panes, and event streams become opaque refs and RuntimeFact values.",
        exports="Fact: listed in herdr.py's __all__ and re-exported from herdsman/__init__.py; consumer contract beyond the re-export: intent unknown (no docstring claims a public API).",
        importers="herdsman.daemon (runtime default), herdsman/__init__ (re-export).",
        construction="Fact: Daemon.run_initiative and discard_initiative build HerdrAdapter as the default Runtime when none is injected.",
        serialization="Fact: to_runtime_observed converts a RuntimeFact into the RuntimeObserved domain event the daemon can append.",
    ),
    _Facet(
        title="Runtime/Collector protocols",
        symbols=["herdsman.daemon:Runtime", "herdsman.daemon:Collector"],
        definition="Fact: structural typing.Protocol contracts for the injected runtime and checkpoint collector; HerdrAdapter and GitCheckpointCollector satisfy them, as do test fakes.",
        exports="Interpretation: not in daemon.py's __all__ — internal seams, consumer intent unknown.",
        importers="used only inside herdsman.daemon (parameter annotations) — no module imports them.",
        construction="Fact: never constructed; implementations are injected as parameters (test fakes included).",
        serialization="Fact: not applicable — call contracts only; results cross as domain events.",
    ),
]

FLOWS: dict[str, _Flow] = {
    "create-approve-run-settle": _Flow(
        title="Create → approve → run → settle (the golden thread)",
        steps=[
            _FlowStep(
                label="CLI create posts the brief",
                refs=["herdsman.cli:create"],
                note="Fact: the CLI is a thin HTTP client; the daemon API is the only write path.",
            ),
            _FlowStep(
                label="Daemon route POST /plans",
                refs=[
                    "herdsman.daemon:create_app",
                    "herdsman.daemon:create",
                    "herdsman.daemon:Daemon.create_plan",
                ],
                note="Fact: routes are registered with string-literal add_api_route calls; the handler is a local function in create_app.",
            ),
            _FlowStep(
                label="Planner proposal",
                refs=[
                    "herdsman.daemon:_planner_call",
                    "herdsman.runtime:PiFrontierPlanner.propose",
                    "herdsman.runtime:proposal_from_result",
                    "herdsman.classes:PlanProposed",
                ],
                note='Fact: the planner call is a dynamic edge — _planner_call reaches propose through getattr(planner, "propose"); the planner itself runs as an external pi subprocess. Interpretation: this is the one model call in the create leg.',
            ),
            _FlowStep(
                label="Approve",
                refs=[
                    "herdsman.cli:approve",
                    "herdsman.daemon:Daemon.approve_plan",
                    "herdsman.classes:PlanApproved",
                ],
                note="Fact: approval is persisted as an explicit PlanApproved event; the fold refuses attempts before approval.",
            ),
            _FlowStep(
                label="Fold gates",
                refs=["herdsman.classes:Plan.step", "herdsman.store:EventStore.append"],
                note="Fact: EventStore.append folds before it writes — an event the projection rejects is never persisted; the fold enforces the approval/version match.",
            ),
            _FlowStep(
                label="Run one initiative",
                refs=[
                    "herdsman.daemon:Daemon.run_and_settle",
                    "herdsman.daemon:Daemon.run_initiative",
                    "herdsman.runtime:compile_task_packet",
                    "herdsman.classes:AttemptStarted",
                ],
                note="Fact: run_and_settle applies the one settlement policy; run_initiative reserves the attempt before provisioning anything.",
            ),
            _FlowStep(
                label="Runtime and checkpoint evidence",
                refs=[
                    "herdsman.herdr:HerdrAdapter",
                    "herdsman.daemon:Runtime",
                    "herdsman.daemon:Collector",
                    "herdsman.checkpoint:GitCheckpointCollector.capture_base",
                    "herdsman.classes:CheckpointRecorded",
                ],
                note="Fact: the runtime and collector arrive as Protocol-typed injected parameters — dynamic edges; herdr is reached over a Unix socket and git/checks run as subprocesses — external boundaries. Interpretation: completion evidence arrives via RuntimeObserved.detail, which is untyped dict flow.",
            ),
            _FlowStep(
                label="Settle and downstream readiness",
                refs=[
                    "herdsman.daemon:Daemon.settle_initiative",
                    "herdsman.classes:InitiativeSettled",
                    "herdsman.classes:Plan.ready",
                ],
                note="Fact: a clean checkpoint settles automatically; downstream readiness is computed by Plan.ready, never stored.",
            ),
        ],
        absent=[
            "UI → daemon — no UI is implemented (ui/ is a stub); machine-readable status = GET /plans/{plan_id}/graph and SSE /plans/{plan_id}/events.",
        ],
    ),
}

_CLASS_FACET_BY_NAME: dict[str, _Facet] = {
    ref.split(":")[-1].split(".")[-1]: facet for facet in CLASS_FACETS for ref in facet.symbols
}

TOUR_STEPS: list[_TourStep] = [
    _TourStep(
        title="The single writer",
        refs=[
            "herdsman.daemon:Daemon",
            "herdsman.daemon:Daemon.create_plan",
            "herdsman.daemon:Daemon.approve_plan",
            "herdsman.store:EventStore.append",
        ],
        body="Fact: Daemon calls itself the event store's single writer and fans persisted events out to live subscribers; EventStore.append folds before it writes, so an event the projection rejects is never on disk.",
        checkpoint="You should now be able to say what single writer means and where it lives.",
    ),
    _TourStep(
        title="Events and the fold",
        refs=[
            "herdsman.classes:Plan",
            "herdsman.classes:Plan.step",
            "herdsman.classes:PlanCreated",
        ],
        body="Fact: only the Ev subclasses persist; Plan.step folds one event at a time, and readiness is computed by Plan.ready rather than stored. Interpretation: anything derivable is deliberately not state.",
        checkpoint="You should now be able to name which events persist and which states are derived.",
    ),
    _TourStep(
        title="The run leg",
        refs=[
            "herdsman.daemon:Daemon.run_and_settle",
            "herdsman.daemon:Daemon.run_initiative",
            "herdsman.runtime:TaskPacket",
            "herdsman.runtime:compile_task_packet",
        ],
        body="Fact: run_and_settle wraps run_initiative — the primitive records evidence without judging; the executor receives only its compiled TaskPacket.",
        checkpoint="You should now be able to say what the executor receives and what evidence settles an initiative.",
    ),
    _TourStep(
        title="The CLI map",
        refs=[
            "herdsman.cli:create",
            "herdsman.cli:approve",
            "herdsman.cli:run_plan",
            "herdsman.cli:graph",
        ],
        body="Fact: mutation commands post to the daemon over HTTP; read commands open the local event store and project directly — no daemon needed.",
        checkpoint="You should now be able to say which CLI commands write state and why they go through HTTP.",
    ),
    _TourStep(
        title="Boundaries and drill-down",
        refs=[],
        body="Drill down: run `herdsman nav symbol Daemon.run_and_settle` (callers, callees, linked tests, labeled edges) and `herdsman nav flow create-approve-run-settle` (the full cross-module trace).",
        checkpoint="You should now be able to trace one run from create to settled with file:line citations.",
    ),
]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _symbol_by_ref(index: NavIndex, ref: str) -> dict[str, object] | None:
    matches = [
        sym
        for sym in index.symbols
        if f"{sym['module']}:{sym['name']}" == ref or sym["name"] == ref.split(":")[-1]
    ]
    if len(matches) == 1:
        return matches[0]
    for sym in matches:
        if f"{sym['module']}:{sym['name']}" == ref:
            return sym
    return None


def _cite(index: NavIndex, ref: str) -> str:
    """Resolve a curated symbol-path reference against the live index."""
    sym = _symbol_by_ref(index, ref)
    if sym is None:
        return f"`{ref}` — unresolved (moved?)"
    return f"`{sym['file']}:{sym['line']}` (`{ref}`)"


def coverage_line(index: NavIndex) -> str:
    base = "coverage: python (herdsman/, tests/); ui/ stub and assets/ excluded"
    note = index.coverage.get("deep_note")
    if index.coverage.get("deep") or note:
        return f"{base} — {note}"
    return base


def codemap_text(index: NavIndex) -> str:
    lines = [
        "Herdsman codemap — current working tree (reads always reflect live source)",
        coverage_line(index),
        "",
        "Modules — role notes are curated: Fact: = source/docstring fact, Interpretation: = inference.",
    ]
    for file in index.files:
        rel = str(file["path"])
        if not rel.endswith(".py"):
            continue
        lines.append(f"  {rel}  {file['loc']} LOC")
        lines.append(f"    {MODULE_BLURBS.get(rel, '(no curated note)')}")
        owned = [sym for sym in index.symbols if sym["file"] == rel]
        if owned:
            top = " · ".join(f"{sym['name']} ({sym['line']})" for sym in owned[:6])
            more = f" (+{len(owned) - 6} more)" if len(owned) > 6 else ""
            lines.append(f"    symbols: {top}{more}")
    lines += ["", "Entry points"]
    script = index.entry_points.console_script
    if script is not None:
        lines.append(
            f"  console script: {script['name']} → {script['target']} ({script['file']}:{script['line']})"
        )
    commands = index.entry_points.cli
    shown = " · ".join(
        f"{c['command']} ({c['file']}:{c['line']})" for c in commands[:12]
    )
    more = f" · (+{len(commands) - 12} more)" if len(commands) > 12 else ""
    lines.append(f"  cli commands ({len(commands)}): {shown}{more}")
    routes = index.entry_points.routes
    shown = " · ".join(
        f"{r['method']} {r['path']} → {r['handler']} ({r['file']}:{r['line']})"
        for r in routes[:12]
    )
    more = f" · (+{len(routes) - 12} more)" if len(routes) > 12 else ""
    lines.append(f"  daemon routes ({len(routes)}): {shown}{more}")
    tests = index.entry_points.tests
    files = {str(t["file"]) for t in tests}
    lines.append(
        f"  test entry points ({len(tests)} across {len(files)} files — full list via `herdsman nav codemap --json`)"
    )
    lines += [
        "",
        f"Unresolved edges ({len(index.unresolved)}) — unknown edges stay visible, never dropped",
    ]
    for item in index.unresolved[:_MAX_TEXT_UNRESOLVED]:
        lines.append(f"  calls {item['src']} → {item['name']} ({item['file']}:{item['line']})")
    if len(index.unresolved) > _MAX_TEXT_UNRESOLVED:
        lines.append(
            f"  …(+{len(index.unresolved) - _MAX_TEXT_UNRESOLVED} more; full list via `herdsman nav codemap --json`)"
        )
    return "\n".join(lines)


def codemap_json(index: NavIndex) -> str:
    return json.dumps(index.to_dict(), indent=1)


def tour_text(index: NavIndex) -> str:
    lines = ["Guided tour — ordered path through the source with checkpoints", ""]
    for number, step in enumerate(TOUR_STEPS, 1):
        lines.append(f"{number}. {step.title}")
        for ref in step.refs:
            lines.append(f"   {_cite(index, ref)}")
        lines.append(f"   {step.body}")
        lines.append(f"   Checkpoint: {step.checkpoint}")
        lines.append("")
    return "\n".join(lines)


def flow_text(index: NavIndex, name: str) -> str:
    flow = FLOWS.get(name)
    if flow is None:
        raise NavError(f"unknown flow {name!r}; known flows: {', '.join(sorted(FLOWS))}")
    lines = [f"Flow: {name} — {flow.title}", ""]
    for number, step in enumerate(flow.steps, 1):
        lines.append(f"{number}. {step.label}")
        for ref in step.refs:
            lines.append(f"   {_cite(index, ref)}")
        lines.append(f"   {step.note}")
    for absent in flow.absent:
        lines.append(f"   [absent] {absent}")
    lines += [
        "",
        "Edge labels: static = resolved in this index · dynamic = unproven receiver (parameter, local, chained attribute) or getattr string literal · external = stdlib, third-party, or subprocess boundary · unresolved = listed, never dropped.",
    ]
    return "\n".join(lines)


def _resolve_symbol_arg(index: NavIndex, name: str) -> dict[str, object]:
    exact = [
        sym
        for sym in index.symbols
        if f"{sym['module']}:{sym['name']}" == name or sym["name"] == name
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        paths = "\n".join(f"  {sym['module']}:{sym['name']}" for sym in exact)
        raise NavError(f"ambiguous symbol {name!r}; full paths:\n{paths}")
    tail = name.split(".")[-1]
    same_tail = [sym for sym in index.symbols if str(sym["name"]).split(".")[-1] == tail]
    if len(same_tail) == 1:
        return same_tail[0]
    if len(same_tail) > 1:
        paths = "\n".join(f"  {sym['module']}:{sym['name']}" for sym in same_tail)
        raise NavError(f"ambiguous symbol {name!r}; full paths:\n{paths}")
    raise NavError(f"unknown symbol {name!r}")


def _group_edges(edges: list[dict[str, object]], *, outgoing: bool) -> list[tuple[str, str, str]]:
    """(other end, resolution, first file:line), deduped by (other, resolution)."""
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for edge in edges:
        other = str(edge["dst"] if outgoing else edge["src"])
        key = (other, str(edge["resolution"]))
        if key not in grouped or int(str(edge["line"])) < int(str(grouped[key]["line"])):
            grouped[key] = edge
    return [
        (other, resolution, f"{edge['file']}:{edge['line']}")
        for (other, resolution), edge in sorted(
            grouped.items(),
            key=lambda kv: (str(kv[1]["file"]), int(str(kv[1]["line"]))),
        )
    ]


def symbol_text(index: NavIndex, name: str) -> str:
    sym = _resolve_symbol_arg(index, name)
    ref = f"{sym['module']}:{sym['name']}"
    lines = [f"{sym['name']} — {sym['kind']} in {sym['module']}"]
    lines.append(f"  {sym['signature']}")
    exported = "yes — listed in module __all__" if sym["exported"] else "no — not in module __all__"
    note = "; consumer intent unknown (no docstring claims a public API)" if sym["kind"] == "class" else ""
    lines.append(f"  `{sym['file']}:{sym['line']}` · exported: {exported}{note}")
    if sym["doc"]:
        lines.append(f"  docstring: {sym['doc']}")

    callers = _incoming(index, ref, ("calls",))
    if callers:
        lines += ["", "Callers"]
        lines += [
            f"  ← {other} ({resolution}) `{site}`"
            for other, resolution, site in _group_edges(callers, outgoing=False)
        ]
    callees = _outgoing(index, ref, ("calls", "instantiates"))
    if callees:
        lines += ["", "Callees"]
        lines += [
            f"  → {other} ({resolution}) `{site}`"
            for other, resolution, site in _group_edges(callees, outgoing=True)
        ]

    importers = sorted(
        {
            str(e["src"])
            for e in index.edges
            if e["kind"] == "imports" and e["dst"] == sym["module"]
        }
        - {str(sym["module"])}
    )
    if importers:
        lines += ["", f"Importers of {sym['module']}"]
        lines += [f"  {module}" for module in importers]
    references = _incoming(index, ref, ("references",))
    if references:
        lines += ["", "Referenced by"]
        lines += [
            f"  {other} `{site}`"
            for other, _resolution, site in _group_edges(references, outgoing=False)
        ]

    tests = sorted(
        {
            f"{e['file']}:{e['line']}"
            for e in index.edges
            if e["kind"] in ("calls", "instantiates", "references")
            and str(e["src"]).split(":", 1)[0].startswith("tests.")
            and (e["dst"] == ref or str(e["dst"]).startswith(ref + "."))
        }
    )
    if tests:
        lines += ["", "Linked tests"]
        lines += [f"  `{site}`" for site in tests]

    constructed = _incoming(index, ref, ("instantiates",))
    if sym["kind"] == "class" and constructed:
        lines += ["", "Constructed by"]
        lines += [
            f"  {other} `{site}`"
            for other, _resolution, site in _group_edges(constructed, outgoing=False)
        ]

    facet = (
        _CLASS_FACET_BY_NAME.get(str(sym["name"]).split(".")[-1])
        if sym["kind"] == "class"
        else None
    )
    if facet is not None:
        lines += ["", f"Facet — {facet.title}"]
        refs = " · ".join(_cite(index, ref) for ref in facet.symbols)
        lines.append(f"  symbols: {refs}")
        for field_name in ("definition", "exports", "importers", "construction", "serialization"):
            lines.append(f"  {field_name.capitalize()} — {getattr(facet, field_name)}")
    return "\n".join(lines)


def _incoming(index: NavIndex, ref: str, kinds: tuple[str, ...]) -> list[dict[str, object]]:
    return [e for e in index.edges if e["kind"] in kinds and e["dst"] == ref]


def _outgoing(index: NavIndex, ref: str, kinds: tuple[str, ...]) -> list[dict[str, object]]:
    return [e for e in index.edges if e["kind"] in kinds and e["src"] == ref]


# ---------------------------------------------------------------------------
# The generated guide
# ---------------------------------------------------------------------------


def render_guide(index: NavIndex) -> str:
    extractor = "stdlib-ast+codegraph (deep)" if index.coverage.get("deep") else "stdlib-ast"
    meta = {
        "repo_ref": index.repo_ref or "unknown",
        "fingerprint": index.fingerprint,
        "extractor": extractor,
        "coverage": coverage_line(index).removeprefix("coverage: "),
        "freshness": "regenerate with: herdsman nav guide --refresh; check with: herdsman nav guide",
        "files": str(len(index.files)),
    }
    header = (
        "<!-- herdsman nav guide\n"
        + "\n".join(f"{key}: {value}" for key, value in meta.items())
        + "\n-->"
    )

    start_here = [
        "## 1. Start here",
        "",
        "Fact: Herdsman's daemon is the single writer to an append-only event store; every",
        "other module either produces events, folds them into projections, or reads them.",
        "Begin with the writer, the fold, and one run leg:",
        "",
    ]
    for number, step in enumerate(TOUR_STEPS[:3], 1):
        start_here.append(f"{number}. {step.title}")
        for ref in step.refs:
            start_here.append(f"   {_cite(index, ref)}")
        start_here.append(f"   {step.body}")
        start_here.append(f"   Checkpoint: {step.checkpoint}")
        start_here.append("")
    start_here += [
        "Why this order: the writer explains where truth lives, the fold explains what is",
        "derived from it, and the run leg is the representative cross-module flow.",
    ]

    facets: list[str] = ["## 3. Class/type surface", ""]
    for facet in CLASS_FACETS:
        facets.append(f"### {facet.title}")
        facets.append("symbols: " + " · ".join(_cite(index, ref) for ref in facet.symbols))
        for field_name in ("definition", "exports", "importers", "construction", "serialization"):
            facets.append(f"- {field_name.capitalize()}: {getattr(facet, field_name)}")
        facets.append("")

    boundaries = [
        "## 5. Boundaries — edge labels",
        "",
        "- **static**: dst resolves to an indexed symbol (direct name, `self.X`/`cls.X` of the enclosing class, or `module.attr` where the import binding is in-repo).",
        "- **dynamic**: the receiver is not statically proven — a parameter, local, or chained attribute — or the call goes through `getattr` with a string literal; dst is the unique same-named indexed symbol when one exists, else the bare attribute name.",
        "- **external**: stdlib/third-party/subprocess boundary (`typer`, `pydantic`, `asyncio.open_unix_connection`, `subprocess.run`, …).",
        "- **unresolved**: callable-looking name that matched nothing — listed below, never dropped.",
        "",
        "Resident examples at this ref: `propose` via getattr (dynamic); receivers like the injected runtime/collector resolving by method name (dynamic); git and check commands (external subprocess).",
        "",
        f"Unresolved edges ({len(index.unresolved)} total)",
    ]
    for item in index.unresolved[:_MAX_TEXT_UNRESOLVED]:
        boundaries.append(f"- calls {item['src']} → {item['name']} (`{item['file']}:{item['line']}`)")
    if len(index.unresolved) > _MAX_TEXT_UNRESOLVED:
        boundaries.append(
            f"- …(+{len(index.unresolved) - _MAX_TEXT_UNRESOLVED} more; full list via `herdsman nav codemap --json`)"
        )
    conflicts = index.coverage.get("deep_conflicts")
    if isinstance(conflicts, list) and conflicts:
        boundaries += [
            "",
            f"Codegraph cross-check (--deep): {len(cast('list[object]', conflicts))} conflicts — annotated, never merged",
        ]
        for conflict in cast("list[dict[str, str]]", conflicts)[:10]:
            boundaries.append(
                f"- {conflict['symbol']} {conflict['direction']}: {conflict['edge']} — {conflict['note']}"
            )

    limits = [
        "## 6. Coverage & limits",
        "",
        f"- extractor: {extractor}; python only — ui/ (stub, no source) and assets/ (prose) are excluded from the index.",
        "- Interpretation: resolution is AST-structural, not type-inferred; receivers without import/class/self evidence are dynamic name-matches, and callable names matching nothing stay unresolved by design.",
        "- Interpretation: value flow through RuntimeObserved.detail (dict[str, object]) is not extracted; the flow notes mark it instead.",
        "- Interpretation: graph contention checks rebuild the scope trie per admission check — fine at current plan scale.",
        "- The header fingerprint gates staleness of this guide; read commands always see current source.",
    ]
    deep_note = index.coverage.get("deep_note")
    if deep_note:
        limits.insert(1, f"- deep mode: {deep_note}")

    sections = [
        header,
        "# Herdsman architecture guide",
        "",
        *start_here,
        "",
        "## 2. Repo map",
        "",
        "```text",
        codemap_text(index),
        "```",
        "",
        *facets,
        "## 4. Flows",
        "",
        flow_text(index, "create-approve-run-settle"),
        "",
        *boundaries,
        "",
        *limits,
        "",
    ]
    return "\n".join(sections)


def guide_status(root: Path, out: Path) -> tuple[str, str | None]:
    """(status, recorded fingerprint) for a written guide."""
    try:
        text = out.read_text(encoding="utf-8")
    except OSError:
        return "missing", None
    recorded: str | None = None
    for line in text.split("-->", 1)[0].splitlines():
        if line.startswith("fingerprint:"):
            recorded = line.split(":", 1)[1].strip()
    if recorded is None:
        return "stale", None
    return ("fresh" if recorded == fingerprint(root) else "stale"), recorded


def refresh_guide(root: Path, out: Path, deep: bool) -> NavIndex:
    """Build the index in memory and write the guide; --deep never fails."""
    index = build_index(root)
    index.repo_ref = repo_ref(root)
    index.fingerprint = fingerprint(root)
    if deep:
        try:
            probe_ok = _codegraph_probe(root)
        except Exception:  # an optional tool must never fail the command
            probe_ok = False
        if probe_ok:
            index.coverage["deep"] = True
            index.coverage["deep_note"] = (
                "codegraph index verified current; caller/callee cross-check over curated symbols"
            )
            index.coverage["deep_conflicts"] = _deep_crosscheck(root, index)
        else:
            index.coverage["deep_note"] = "degraded (codegraph unavailable/stale — AST-only)"
    try:
        _ = out.parent.mkdir(parents=True, exist_ok=True)
        _ = out.write_text(render_guide(index), encoding="utf-8")
    except OSError as exc:
        raise NavError(f"cannot write guide to {out}: {exc}") from exc
    return index
