#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "tree-sitter>=0.23,<0.26",
#   "tree-sitter-python>=0.23",
# ]
# ///
"""Atlas index script: extract Python symbols/edges/references + compute the slice.

Invoked by the agent. Not a human-facing interface (PRD §12.1).

    uv run scripts/atlas_index.py --repo <path> --entry <file|symbol> [--hops 2] --out index.json

The output JSON slots directly into the artifact's repository/slice/index fields.
Calls that tree-sitter cannot resolve (dynamic dispatch, etc.) produce no edge and
are counted in stats.unresolved_calls — no speculative edges (PRD §16).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

PY_LANGUAGE = Language(tspython.language())

DEFAULT_EXCLUDES = [
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".mypy_cache", ".ruff_cache", "build", "dist", ".eggs",
]

DEFAULT_MAX_SOURCE_BYTES = 4 * 1024 * 1024

# Method names common on builtin types (dict/list/str). When the receiver type is
# unknown, name_match on these creates false-positive edges, so block them (precision first).
COMMON_BUILTIN_METHODS = frozenset({
    "get", "set", "items", "keys", "values", "append", "extend", "add",
    "pop", "update", "join", "split", "strip", "lstrip", "rstrip",
    "format", "read", "write", "close", "copy", "encode", "decode",
    "count", "index", "sort", "replace", "startswith", "endswith",
    "lower", "upper", "setdefault", "clear", "remove", "insert",
})


# ---------------------------------------------------------------- data model

@dataclass
class Symbol:
    symbol_id: str
    kind: str            # module | class | function | method
    name: str
    qualname: str        # dotted path within the module ("" for module)
    module: str
    path: str
    start: tuple[int, int]
    end: tuple[int, int]
    content_hash: str
    signature: str
    parent: str | None
    in_slice: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.module}.{self.qualname}" if self.qualname else self.module


@dataclass
class Edge:
    src: str
    dst: str
    kind: str            # calls | imports
    resolution: str      # exact | name_match
    site_path: str | None = None
    site_range: tuple[tuple[int, int], tuple[int, int]] | None = None


@dataclass
class Reference:
    path: str
    start: tuple[int, int]
    end: tuple[int, int]
    symbol_id: str


@dataclass
class FileIndex:
    path: str
    module: str
    source: str
    sha256: str
    symbols: list[Symbol] = field(default_factory=list)
    # alias -> imported full name ("pkg.mod" or "pkg.mod.attr")
    imports: dict[str, str] = field(default_factory=dict)
    # (enclosing Symbol, callee node)
    calls: list[tuple[Symbol, Node]] = field(default_factory=list)
    # class qualname -> {instance attr name -> unresolved type expression string}
    class_attrs: dict[str, dict[str, str]] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_range(node: Node) -> tuple[tuple[int, int], tuple[int, int]]:
    # tree-sitter rows are 0-based → convert to 1-based lines; characters stay 0-based.
    return (
        (node.start_point[0] + 1, node.start_point[1]),
        (node.end_point[0] + 1, node.end_point[1]),
    )


def node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def module_name(relpath: Path) -> str:
    parts = list(relpath.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else relpath.stem


def git_head(root: Path) -> str:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        sha = run("rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unversioned"
    try:
        dirty = run("status", "--porcelain")
    except (subprocess.CalledProcessError, FileNotFoundError):
        dirty = ""
    if dirty:
        patch = subprocess.run(
            ["git", "diff", "HEAD"], cwd=root, capture_output=True, check=False
        ).stdout
        return f"worktree:{sha}:{hashlib.sha256(patch).hexdigest()[:16]}"
    return sha


def git_changed_files(root: Path, base: str) -> tuple[str, dict[str, str]]:
    """Resolve base and list python files changed between base and the worktree.

    Returns (base sha, {relpath: added|modified|removed}).
    """
    sha = subprocess.run(
        ["git", "rev-parse", base], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    out = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", sha, "--", "*.py"],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    status_map = {"A": "added", "M": "modified", "D": "removed"}
    changed: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            changed[parts[-1]] = status_map.get(parts[0][0], "modified")
    return sha, changed


def base_file(root: Path, sha: str, relpath: str) -> tuple[dict[str, str], str | None]:
    """Parse the base revision of one file.

    Returns ({symbol_id: content_hash}, base source text). The source feeds the
    viewer's side-by-side diff.
    """
    proc = subprocess.run(
        ["git", "show", f"{sha}:{relpath}"], cwd=root, capture_output=True, check=False
    )
    if proc.returncode != 0:
        print(f"warn: cannot read {relpath} at base: skipped", file=sys.stderr)
        return {}, None
    parser = Parser(PY_LANGUAGE)
    tree = parser.parse(proc.stdout)
    fi = Extractor(relpath, module_name(Path(relpath)), proc.stdout).run(tree.root_node)
    symbols = {s.symbol_id: s.content_hash for s in fi.symbols if s.kind != "module"}
    return symbols, proc.stdout.decode("utf-8", errors="replace")


def is_excluded(relpath: Path, excludes: list[str]) -> bool:
    parts = set(relpath.parts)
    for pat in excludes:
        if pat in parts:
            return True
        if fnmatch.fnmatch(str(relpath), pat):
            return True
    return False


# ---------------------------------------------------------------- extraction

class Extractor:
    """Extract symbols/imports/calls from a single file."""

    def __init__(self, relpath: str, module: str, source: bytes):
        self.relpath = relpath
        self.module = module
        self.source = source
        self.file = FileIndex(
            path=relpath,
            module=module,
            source=source.decode("utf-8", errors="replace"),
            sha256=sha256_text(source.decode("utf-8", errors="replace")),
        )
        self.module_symbol = Symbol(
            symbol_id=f"python:{module}",
            kind="module",
            name=module.rsplit(".", 1)[-1],
            qualname="",
            module=module,
            path=relpath,
            start=(1, 0),
            end=(max(1, self.file.source.count("\n") + 1), 0),
            content_hash=self.file.sha256,
            signature=f"module {module}",
            parent=None,
        )
        self.file.symbols.append(self.module_symbol)

    def run(self, tree_root: Node) -> FileIndex:
        self._walk(tree_root, scope=[], enclosing=self.module_symbol)
        return self.file

    # scope: class/function nesting path. enclosing: innermost symbol that owns calls.
    def _walk(self, node: Node, scope: list[tuple[str, str]], enclosing: Symbol) -> None:
        for child in node.children:
            t = child.type
            if t == "decorated_definition":
                inner = child.child_by_field_name("definition")
                if inner is not None:
                    self._definition(inner, outer=child, scope=scope, enclosing=enclosing)
                continue
            if t in ("function_definition", "class_definition"):
                self._definition(child, outer=child, scope=scope, enclosing=enclosing)
                continue
            if t in ("import_statement", "import_from_statement"):
                self._import(child)
                # no need to descend into import statements
                continue
            if t == "call":
                self._call(child, enclosing)
                # keep walking for nested calls/lambdas inside arguments
            self._walk(child, scope, enclosing)

    def _definition(self, node: Node, outer: Node, scope: list[tuple[str, str]], enclosing: Symbol) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node, self.source)
        is_class = node.type == "class_definition"
        parent_kinds = [k for k, _ in scope]
        if is_class:
            kind = "class"
        elif parent_kinds and parent_kinds[-1] == "class":
            kind = "method"
        else:
            kind = "function"

        qualname = ".".join([n for _, n in scope] + [name])
        text = node_text(outer, self.source)
        first_line = text.split("\n", 1)[0].rstrip(":")
        sym = Symbol(
            symbol_id=f"python:{self.module}:{qualname}",
            kind=kind,
            name=name,
            qualname=qualname,
            module=self.module,
            path=self.relpath,
            start=node_range(outer)[0],
            end=node_range(outer)[1],
            content_hash=sha256_text(text),
            signature=first_line,
            parent=enclosing.symbol_id,
        )
        self.file.symbols.append(sym)

        body = node.child_by_field_name("body")
        if body is not None:
            child_scope = scope + [("class" if is_class else "function", name)]
            # calls in a class body run at class-definition time, so the class symbol owns them
            self._walk(body, child_scope, enclosing=sym)
            if is_class:
                self._collect_class_attrs(body, qualname)

    def _collect_class_attrs(self, class_body: Node, class_qualname: str) -> None:
        """Collect instance attr types from __init__ annotations and self.x = ... assignments."""
        init_node: Node | None = None
        for child in class_body.children:
            cand = child
            if child.type == "decorated_definition":
                cand = child.child_by_field_name("definition") or child
            if cand.type == "function_definition":
                nm = cand.child_by_field_name("name")
                if nm is not None and node_text(nm, self.source) == "__init__":
                    init_node = cand
                    break
        if init_node is None:
            return

        param_types: dict[str, str] = {}
        params = init_node.child_by_field_name("parameters")
        if params is not None:
            for p in params.children:
                if p.type in ("typed_parameter", "typed_default_parameter"):
                    ident = next((c for c in p.children if c.type == "identifier"), None)
                    tnode = p.child_by_field_name("type")
                    if ident is not None and tnode is not None:
                        param_types[node_text(ident, self.source)] = (
                            node_text(tnode, self.source).strip()
                        )

        attrs: dict[str, str] = {}

        def scan(node: Node) -> None:
            for child in node.children:
                if child.type == "assignment":
                    left = child.child_by_field_name("left")
                    right = child.child_by_field_name("right")
                    if (
                        left is not None and right is not None
                        and left.type == "attribute"
                    ):
                        obj = left.child_by_field_name("object")
                        attr = left.child_by_field_name("attribute")
                        if (
                            obj is not None and attr is not None
                            and obj.type == "identifier"
                            and node_text(obj, self.source) == "self"
                        ):
                            attr_name = node_text(attr, self.source)
                            if right.type == "identifier":
                                pname = node_text(right, self.source)
                                if pname in param_types:
                                    attrs[attr_name] = param_types[pname]
                            elif right.type == "call":
                                cf = right.child_by_field_name("function")
                                if cf is not None and cf.type in ("identifier", "attribute"):
                                    attrs[attr_name] = node_text(cf, self.source)
                scan(child)

        body = init_node.child_by_field_name("body")
        if body is not None:
            scan(body)
        if attrs:
            self.file.class_attrs[class_qualname] = attrs

    def _import(self, node: Node) -> None:
        src = node_text(node, self.source)
        if node.type == "import_statement":
            # import a.b.c [as x], ...
            for child in node.children:
                if child.type == "dotted_name":
                    target = node_text(child, self.source)
                    alias = target.split(".", 1)[0]
                    self.file.imports[alias] = target.split(".", 1)[0]
                    self.file.imports[target] = target
                elif child.type == "aliased_import":
                    dn = child.child_by_field_name("name")
                    al = child.child_by_field_name("alias")
                    if dn is not None and al is not None:
                        self.file.imports[node_text(al, self.source)] = node_text(dn, self.source)
        else:
            # from a.b import c [as d], ...
            mod_node = node.child_by_field_name("module_name")
            if mod_node is None:
                return
            base = node_text(mod_node, self.source)
            if base.startswith("."):
                # relative import: resolve against the current module
                level = len(base) - len(base.lstrip("."))
                remainder = base.lstrip(".")
                parts = self.module.split(".")
                anchor = parts[: len(parts) - level] if level <= len(parts) else []
                base = ".".join(anchor + ([remainder] if remainder else []))
            seen_module_name = False
            for child in node.children:
                if child.type == "dotted_name":
                    if not seen_module_name and child == mod_node:
                        seen_module_name = True
                        continue
                    name = node_text(child, self.source)
                    self.file.imports[name] = f"{base}.{name}" if base else name
                elif child.type == "aliased_import":
                    dn = child.child_by_field_name("name")
                    al = child.child_by_field_name("alias")
                    if dn is not None and al is not None:
                        name = node_text(dn, self.source)
                        self.file.imports[node_text(al, self.source)] = (
                            f"{base}.{name}" if base else name
                        )
                elif child.type == "wildcard_import":
                    pass  # from x import * is not resolved (left as unresolved)
        _ = src

    def _call(self, node: Node, enclosing: Symbol) -> None:
        fn = node.child_by_field_name("function")
        if fn is not None:
            self.file.calls.append((enclosing, fn))


# ---------------------------------------------------------------- resolution

class Resolver:
    def __init__(self, files: list[FileIndex]):
        self.files = files
        self.by_full: dict[str, Symbol] = {}
        self.by_id: dict[str, Symbol] = {}
        self.by_name: dict[str, list[Symbol]] = {}
        self.modules: dict[str, Symbol] = {}
        for f in files:
            for s in f.symbols:
                self.by_full[s.full_name] = s
                self.by_id[s.symbol_id] = s
                self.by_name.setdefault(s.name, []).append(s)
                if s.kind == "module":
                    self.modules[s.module] = s

        # class symbol_id -> {instance attr -> type class Symbol}
        self.attr_types: dict[str, dict[str, Symbol]] = {}
        for f in files:
            for cq, attrs in f.class_attrs.items():
                resolved: dict[str, Symbol] = {}
                for attr, texpr in attrs.items():
                    t = self._resolve_type_expr(f, texpr)
                    if t is not None and t.kind == "class":
                        resolved[attr] = t
                if resolved:
                    self.attr_types[f"python:{f.module}:{cq}"] = resolved

        self.edges: list[Edge] = []
        self.references: list[Reference] = []
        self.unresolved_calls = 0

    def _resolve_type_expr(self, f: FileIndex, texpr: str) -> Symbol | None:
        # take the first concrete type from "X | None", "Optional[X]", quoted forward refs
        t = texpr.strip().strip("'\"")
        if t.startswith("Optional[") and t.endswith("]"):
            t = t[len("Optional[") : -1]
        t = t.split("|", 1)[0].strip()
        if not t or "[" in t:
            return None
        if t in f.imports:
            return self.by_full.get(f.imports[t])
        head = t.split(".", 1)[0]
        if head in f.imports and "." in t:
            return self.by_full.get(f.imports[head] + t[len(head):])
        return self.by_full.get(f"{f.module}.{t}") or self.by_full.get(t)

    def run(self) -> None:
        for f in self.files:
            self._import_edges(f)
            for enclosing, fn_node in f.calls:
                self._resolve_call(f, enclosing, fn_node)

    def _import_edges(self, f: FileIndex) -> None:
        seen_dst: set[str] = set()
        for target in f.imports.values():
            # create an edge when target is a project module or a symbol in one
            sym = self.by_full.get(target)
            if sym is None:
                # search for a module prefix in "pkg.mod.attr"
                parts = target.split(".")
                for i in range(len(parts), 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in self.modules:
                        sym = self.modules[cand]
                        break
            if sym is None:
                continue
            dst = sym.symbol_id if sym.kind == "module" else f"python:{sym.module}"
            if dst in seen_dst or dst == f"python:{f.module}":
                continue
            seen_dst.add(dst)
            self.edges.append(
                Edge(src=f"python:{f.module}", dst=dst, kind="imports", resolution="exact")
            )

    def _resolve_call(self, f: FileIndex, enclosing: Symbol, fn: Node) -> None:
        source = f.source.encode("utf-8")

        def text(n: Node) -> str:
            return source[n.start_byte : n.end_byte].decode("utf-8", errors="replace")

        target: Symbol | None = None
        resolution = "exact"
        click_node: Node = fn

        if fn.type == "identifier":
            name = text(fn)
            target = self._lookup_local(f, enclosing, name) or self._lookup_import(f, name)
            if target is None:
                target, resolution = self._name_match(name)
        elif fn.type == "attribute":
            obj = fn.child_by_field_name("object")
            attr = fn.child_by_field_name("attribute")
            if obj is None or attr is None:
                return
            click_node = attr
            attr_name = text(attr)
            if obj.type == "identifier":
                obj_name = text(obj)
                if obj_name in ("self", "cls"):
                    target = self._lookup_in_class(enclosing, attr_name)
                elif obj_name in f.imports:
                    full = f"{f.imports[obj_name]}.{attr_name}"
                    target = self.by_full.get(full)
                if target is None:
                    target, resolution = self._name_match(attr_name, attribute_call=True)
            elif obj.type == "attribute":
                # self.repo.get(...) — type resolution via __init__ annotations
                inner_obj = obj.child_by_field_name("object")
                inner_attr = obj.child_by_field_name("attribute")
                if (
                    inner_obj is not None and inner_attr is not None
                    and inner_obj.type == "identifier"
                    and text(inner_obj) in ("self", "cls")
                ):
                    target = self._lookup_via_attr_type(
                        enclosing, text(inner_attr), attr_name
                    )
                if target is None:
                    target, resolution = self._name_match(attr_name, attribute_call=True)
            else:
                target, resolution = self._name_match(attr_name, attribute_call=True)
        else:
            return  # subscript calls etc. are not resolved

        if target is None:
            self.unresolved_calls += 1
            return
        if target.symbol_id == enclosing.symbol_id:
            pass  # recursive calls also get an edge
        rng = node_range(click_node)
        self.edges.append(
            Edge(
                src=enclosing.symbol_id,
                dst=target.symbol_id,
                kind="calls",
                resolution=resolution,
                site_path=f.path,
                site_range=rng,
            )
        )
        self.references.append(
            Reference(path=f.path, start=rng[0], end=rng[1], symbol_id=target.symbol_id)
        )

    def _lookup_local(self, f: FileIndex, enclosing: Symbol, name: str) -> Symbol | None:
        # top-level of the same module, or a symbol in the same scope chain
        cand = self.by_full.get(f"{f.module}.{name}")
        if cand is not None:
            return cand
        if enclosing.qualname:
            prefix_parts = enclosing.qualname.split(".")
            for i in range(len(prefix_parts), 0, -1):
                q = ".".join(prefix_parts[:i]) + f".{name}"
                cand = self.by_full.get(f"{f.module}.{q}")
                if cand is not None:
                    return cand
        return None

    def _lookup_import(self, f: FileIndex, name: str) -> Symbol | None:
        if name in f.imports:
            return self.by_full.get(f.imports[name])
        return None

    def _lookup_in_class(self, enclosing: Symbol, name: str) -> Symbol | None:
        if not enclosing.qualname:
            return None
        parts = enclosing.qualname.split(".")
        for i in range(len(parts) - 1, 0, -1):
            q = ".".join(parts[:i]) + f".{name}"
            cand = self.by_full.get(f"{enclosing.module}.{q}")
            if cand is not None and cand.kind in ("method", "function"):
                return cand
        return None

    def _lookup_via_attr_type(
        self, enclosing: Symbol, attr: str, method: str
    ) -> Symbol | None:
        # enclosing method's parent class → that class's attr type → the type's method
        cls = self.by_id.get(enclosing.parent or "")
        while cls is not None and cls.kind != "class":
            cls = self.by_id.get(cls.parent or "")
        if cls is None:
            return None
        t = self.attr_types.get(cls.symbol_id, {}).get(attr)
        if t is None:
            return None
        return self.by_full.get(f"{t.module}.{t.qualname}.{method}")

    def _name_match(self, name: str, attribute_call: bool = False) -> tuple[Symbol | None, str]:
        # for attribute calls with no type info, block common builtin method names to avoid false positives
        if attribute_call and name in COMMON_BUILTIN_METHODS:
            return None, "exact"
        cands = [
            s for s in self.by_name.get(name, [])
            if s.kind in ("function", "method", "class")
        ]
        if len(cands) == 1:
            return cands[0], "name_match"
        return None, "exact"


# ---------------------------------------------------------------- slice

def compute_slice(
    files: list[FileIndex],
    edges: list[Edge],
    entries: list[str],
    hops: int,
) -> tuple[set[str], set[str], list[str]]:
    """Returns: (slice file path set, matched seed symbol ids, unmatched entries)"""
    all_symbols: dict[str, Symbol] = {}
    by_path: dict[str, list[Symbol]] = {}
    for f in files:
        by_path[f.path] = f.symbols
        for s in f.symbols:
            all_symbols[s.symbol_id] = s

    seeds: set[str] = set()
    unmatched: list[str] = []
    for entry in entries:
        matched = False
        norm = entry.replace("\\", "/").lstrip("./")
        for f in files:
            if f.path == norm or f.path.endswith("/" + norm):
                seeds.update(s.symbol_id for s in f.symbols)
                matched = True
        if not matched:
            for s in all_symbols.values():
                if (
                    s.full_name == entry
                    or s.symbol_id == entry
                    or (s.qualname and s.qualname == entry)
                    or s.full_name.endswith("." + entry)
                ):
                    seeds.add(s.symbol_id)
                    matched = True
        if not matched:
            unmatched.append(entry)

    adj: dict[str, set[str]] = {}
    for e in edges:
        adj.setdefault(e.src, set()).add(e.dst)
        adj.setdefault(e.dst, set()).add(e.src)
    # parent/child (contains) traversal is free
    for s in all_symbols.values():
        if s.parent:
            adj.setdefault(s.symbol_id, set()).add(s.parent)
            adj.setdefault(s.parent, set()).add(s.symbol_id)

    visited: dict[str, int] = {s: 0 for s in seeds}
    frontier = set(seeds)
    for hop in range(1, hops + 1):
        nxt: set[str] = set()
        for sid in frontier:
            for nb in adj.get(sid, ()):
                if nb not in visited:
                    visited[nb] = hop
                    nxt.add(nb)
        frontier = nxt
        if not frontier:
            break

    slice_paths = {
        all_symbols[sid].path for sid in visited if sid in all_symbols
    }
    return slice_paths, sorted(seeds), unmatched


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="Atlas index: symbol/edge/reference + slice")
    ap.add_argument("--repo", required=True, help="target repository root")
    ap.add_argument("--entry", action="append", default=[],
                    help="entry point: file path or symbol name (repeatable; "
                         "optional when --base is set)")
    ap.add_argument("--base",
                    help="git revision to diff against (review mode): changed files "
                         "become entry points and a changes summary is emitted")
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--max-source-bytes", type=int, default=DEFAULT_MAX_SOURCE_BYTES)
    ap.add_argument("--out", default="-", help="output file (default stdout)")
    args = ap.parse_args()

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"error: repo not found: {root}", file=sys.stderr)
        return 2
    excludes = DEFAULT_EXCLUDES + args.exclude

    base_sha = None
    changed_files: dict[str, str] = {}
    if args.base:
        try:
            base_sha, changed_files = git_changed_files(root, args.base)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            detail = e.stderr.strip() if getattr(e, "stderr", None) else str(e)
            print(f"error: cannot diff against {args.base}: {detail}", file=sys.stderr)
            return 2
    entries = args.entry + sorted(
        p for p, s in changed_files.items() if s != "removed"
    )
    if not entries:
        print(
            "error: no entry points (pass --entry, or --base with python changes)",
            file=sys.stderr,
        )
        return 2

    parser = Parser(PY_LANGUAGE)
    files: list[FileIndex] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if is_excluded(rel, excludes):
            continue
        try:
            data = path.read_bytes()
        except OSError as e:
            print(f"warn: skip {rel}: {e}", file=sys.stderr)
            continue
        tree = parser.parse(data)
        ex = Extractor(str(rel), module_name(rel), data)
        files.append(ex.run(tree.root_node))

    if not files:
        print("error: no python files found", file=sys.stderr)
        return 2

    resolver = Resolver(files)
    resolver.run()

    slice_paths, seed_ids, unmatched = compute_slice(
        files, resolver.edges, entries, args.hops
    )
    if not seed_ids:
        print(f"error: no entry matched: {args.entry}", file=sys.stderr)
        return 2
    for u in unmatched:
        print(f"warn: entry not matched: {u}", file=sys.stderr)

    truncations: list[str] = []
    slice_files = [f for f in files if f.path in slice_paths]
    total = sum(len(f.source.encode()) for f in slice_files)
    if total > args.max_source_bytes:
        slice_files.sort(key=lambda f: len(f.source))
        kept: list[FileIndex] = []
        acc = 0
        for f in slice_files:
            size = len(f.source.encode())
            if acc + size <= args.max_source_bytes:
                kept.append(f)
                acc += size
            else:
                truncations.append(
                    f"file dropped from slice (size budget): {f.path} ({size} bytes)"
                )
        slice_files = kept
        slice_paths = {f.path for f in slice_files}

    kept_symbols: dict[str, Symbol] = {}
    for f in slice_files:
        for s in f.symbols:
            s.in_slice = True
            kept_symbols[s.symbol_id] = s

    # boundary stub: symbol outside the slice but connected by an edge from it (PRD §9.3)
    all_syms = {s.symbol_id: s for f in files for s in f.symbols}
    kept_edges: list[Edge] = []
    for e in resolver.edges:
        src_in = e.src in kept_symbols
        dst_in = e.dst in kept_symbols
        if not (src_in or dst_in):
            continue
        for sid in (e.src, e.dst):
            if sid not in kept_symbols and sid in all_syms:
                stub = all_syms[sid]
                stub.in_slice = False
                kept_symbols[sid] = stub
        kept_edges.append(e)

    kept_refs = [
        r for r in resolver.references
        if r.path in slice_paths and r.symbol_id in kept_symbols
    ]

    # review mode: symbol-level diff between base and worktree (schema `changes`)
    changes = None
    if base_sha is not None:
        head_by_path: dict[str, dict[str, str]] = {}
        for f in files:
            if f.path in changed_files and changed_files[f.path] != "removed":
                head_by_path[f.path] = {
                    s.symbol_id: s.content_hash for s in f.symbols if s.kind != "module"
                }
        sym_changes: list[dict] = []
        base_sources: dict[str, str] = {}
        for path, status in sorted(changed_files.items()):
            if status == "added":
                base_map: dict[str, str] = {}
            else:
                base_map, base_src = base_file(root, base_sha, path)
                if base_src is not None:
                    base_sources[path] = base_src
            head_map = head_by_path.get(path, {})
            for sid, h in head_map.items():
                if sid not in base_map:
                    sym_changes.append({"symbol_id": sid, "change": "added"})
                elif base_map[sid] != h:
                    sym_changes.append({"symbol_id": sid, "change": "modified"})
            for sid in base_map:
                if sid not in head_map:
                    sym_changes.append({"symbol_id": sid, "change": "removed"})
        changes = {
            "previous_head_commit": base_sha,
            "files": [
                {
                    "path": p,
                    "change": s,
                    **(
                        {"base_source": base_sources[p]} if p in base_sources else {}
                    ),
                }
                for p, s in sorted(changed_files.items())
            ],
            "symbols": sorted(sym_changes, key=lambda x: x["symbol_id"]),
        }

    def range_json(start: tuple[int, int], end: tuple[int, int]) -> dict:
        return {
            "start_line": start[0], "start_character": start[1],
            "end_line": end[0], "end_character": end[1],
        }

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": {
            "id": root.name,
            "root": str(root),
            "head_commit": git_head(root),
            **({"base_commit": base_sha} if base_sha else {}),
        },
        **({"changes": changes} if changes else {}),
        "slice": {
            "entry_points": entries,
            "hop_limit": args.hops,
            "file_count": len(slice_files),
            "excludes": args.exclude,
            "truncations": truncations,
        },
        "index": {
            "files": [
                {"path": f.path, "language": "python", "sha256": f.sha256, "source": f.source}
                for f in slice_files
            ],
            "symbols": [
                {
                    "symbol_id": s.symbol_id,
                    "kind": s.kind,
                    "name": s.name,
                    "qualname": s.qualname,
                    "path": s.path,
                    "range": range_json(s.start, s.end),
                    "content_hash": s.content_hash,
                    "signature": s.signature,
                    **({"parent": s.parent} if s.parent else {}),
                    "in_slice": s.in_slice,
                }
                for s in kept_symbols.values()
            ],
            "edges": [
                {
                    "from": e.src,
                    "to": e.dst,
                    "kind": e.kind,
                    "resolution": e.resolution,
                    **(
                        {"site": {"path": e.site_path, "range": range_json(*e.site_range)}}
                        if e.site_path and e.site_range else {}
                    ),
                }
                for e in kept_edges
            ],
            "references": [
                {
                    "path": r.path,
                    "range": range_json(r.start, r.end),
                    "symbol_id": r.symbol_id,
                }
                for r in kept_refs
            ],
        },
        "stats": {
            "files_scanned": len(files),
            "slice_symbols": sum(1 for s in kept_symbols.values() if s.in_slice),
            "boundary_symbols": sum(1 for s in kept_symbols.values() if not s.in_slice),
            "edges": len(kept_edges),
            "unresolved_calls": resolver.unresolved_calls,
        },
    }

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(
            f"index written: {args.out} "
            f"(files={out['slice']['file_count']}, "
            f"symbols={out['stats']['slice_symbols']}+{out['stats']['boundary_symbols']} boundary, "
            f"edges={out['stats']['edges']}, unresolved={out['stats']['unresolved_calls']})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
