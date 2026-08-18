#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "tree-sitter>=0.23,<0.26",
#   "tree-sitter-language-pack>=0.13",
# ]
# ///
"""Atlas index script: extract symbols/edges/references + compute the slice.

Invoked by the agent. Not a human-facing interface (PRD §12.1).

    uv run scripts/atlas_index.py --repo <path> --entry <file|symbol> [--hops 2] --out index.json

Structure (symbols, call sites, imports) comes from tree-sitter for every
supported language. Call/usage resolution is tiered per language:

  1. LSP: if a known language server is on PATH, definitions are resolved by
     the server (resolution "lsp").
  2. Generic: unique-name matching over project symbols (resolution
     "name_match"), with a per-language builtin-method blocklist.

Calls that neither tier can resolve produce no edge and are counted in
stats.unresolved_calls — no speculative edges (PRD §16).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import queue
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

DEFAULT_EXCLUDES = [
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".mypy_cache", ".ruff_cache", "build", "dist", ".eggs",
    "vendor", ".next", "coverage",
]

DEFAULT_MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_LSP_USAGE_REQUESTS = 500


# ---------------------------------------------------------------- languages

@dataclass(frozen=True)
class LangSpec:
    name: str                              # language id and symbol prefix
    exts: tuple[str, ...]
    grammars: dict[str, str]               # ext -> language-pack grammar
    lsp_language_id: str
    servers: tuple[tuple[str, ...], ...]   # candidate server argvs, in order
    defs: dict[str, str]                   # def node type -> kind
    call_nodes: dict[str, str]             # call node type -> callee field
    member_node: tuple[str, str, str]      # (node type, object field, name field)
    import_nodes: tuple[str, ...]
    builtin_block: frozenset[str]          # member-call names never name-matched
    dotted_modules: bool = False           # module path uses dots (python)


LANGUAGES: dict[str, LangSpec] = {
    "python": LangSpec(
        name="python",
        exts=(".py",),
        grammars={".py": "python"},
        lsp_language_id="python",
        servers=(
            ("pyright-langserver", "--stdio"),
            ("basedpyright-langserver", "--stdio"),
            ("jedi-language-server",),
            ("pylsp",),
        ),
        defs={"function_definition": "function", "class_definition": "class"},
        call_nodes={"call": "function"},
        member_node=("attribute", "object", "attribute"),
        import_nodes=("import_statement", "import_from_statement"),
        builtin_block=frozenset({
            "get", "set", "items", "keys", "values", "append", "extend", "add",
            "pop", "update", "join", "split", "strip", "lstrip", "rstrip",
            "format", "read", "write", "close", "copy", "encode", "decode",
            "count", "index", "sort", "replace", "startswith", "endswith",
            "lower", "upper", "setdefault", "clear", "remove", "insert",
        }),
        dotted_modules=True,
    ),
    "typescript": LangSpec(
        name="typescript",
        exts=(".ts", ".tsx"),
        grammars={".ts": "typescript", ".tsx": "tsx"},
        lsp_language_id="typescript",
        servers=(("typescript-language-server", "--stdio"),),
        defs={
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "class_declaration": "class",
            "abstract_class_declaration": "class",
            "method_definition": "method",
            "interface_declaration": "class",
            "enum_declaration": "class",
            "type_alias_declaration": "class",
        },
        call_nodes={"call_expression": "function", "new_expression": "constructor"},
        member_node=("member_expression", "object", "property"),
        import_nodes=("import_statement",),
        builtin_block=frozenset({
            "get", "set", "map", "filter", "reduce", "forEach", "push", "pop",
            "slice", "splice", "concat", "join", "split", "keys", "values",
            "entries", "includes", "indexOf", "find", "sort", "replace",
            "toString", "then", "catch", "finally", "add", "delete", "has",
            "trim", "startsWith", "endsWith", "charAt", "test", "exec",
        }),
    ),
    "javascript": LangSpec(
        name="javascript",
        exts=(".js", ".jsx", ".mjs", ".cjs"),
        grammars={".js": "javascript", ".jsx": "javascript",
                  ".mjs": "javascript", ".cjs": "javascript"},
        lsp_language_id="javascript",
        servers=(("typescript-language-server", "--stdio"),),
        defs={
            "function_declaration": "function",
            "generator_function_declaration": "function",
            "class_declaration": "class",
            "method_definition": "method",
        },
        call_nodes={"call_expression": "function", "new_expression": "constructor"},
        member_node=("member_expression", "object", "property"),
        import_nodes=("import_statement",),
        builtin_block=frozenset({
            "get", "set", "map", "filter", "reduce", "forEach", "push", "pop",
            "slice", "splice", "concat", "join", "split", "keys", "values",
            "entries", "includes", "indexOf", "find", "sort", "replace",
            "toString", "then", "catch", "finally", "add", "delete", "has",
            "trim", "startsWith", "endsWith", "charAt", "test", "exec",
        }),
    ),
    "go": LangSpec(
        name="go",
        exts=(".go",),
        grammars={".go": "go"},
        lsp_language_id="go",
        servers=(("gopls",),),
        defs={
            "function_declaration": "function",
            "method_declaration": "method",
        },
        call_nodes={"call_expression": "function"},
        member_node=("selector_expression", "operand", "field"),
        import_nodes=("import_declaration",),
        builtin_block=frozenset({
            "Error", "String", "Close", "Read", "Write", "Len", "Add",
            "Get", "Set", "Delete", "Lock", "Unlock", "Done", "Err",
        }),
    ),
}

EXT_TO_LANG: dict[str, LangSpec] = {
    ext: spec for spec in LANGUAGES.values() for ext in spec.exts
}


def lang_for(relpath: str) -> LangSpec | None:
    return EXT_TO_LANG.get(Path(relpath).suffix)


def module_name(spec: LangSpec, relpath: Path) -> str:
    parts = list(relpath.with_suffix("").parts)
    if spec.dotted_modules:
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) if parts else relpath.stem
    return "/".join(parts)


# ---------------------------------------------------------------- data model

@dataclass
class Symbol:
    symbol_id: str
    kind: str            # module | class | function | method
    name: str
    qualname: str        # path within the module ("" for module)
    module: str
    language: str
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
    resolution: str      # lsp | name_match | exact
    site_path: str | None = None
    site_range: tuple[tuple[int, int], tuple[int, int]] | None = None


@dataclass
class Reference:
    path: str
    start: tuple[int, int]
    end: tuple[int, int]
    symbol_id: str


@dataclass
class CallSite:
    enclosing: Symbol
    name: str                  # callee name used for generic matching
    is_member: bool            # x.y(...) — receiver type unknown to us
    click: tuple[tuple[int, int], tuple[int, int]]  # range of the name node
    pos: tuple[int, int]       # 0-based (row, byte col) of the name node start


@dataclass
class UsageSite:
    enclosing: Symbol
    name: str
    click: tuple[tuple[int, int], tuple[int, int]]
    pos: tuple[int, int]


@dataclass
class FileIndex:
    path: str
    module: str
    language: str
    source: str
    sha256: str
    symbols: list[Symbol] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    usages: list[UsageSite] = field(default_factory=list)
    # module targets of imports: list of (module string as written)
    import_targets: list[str] = field(default_factory=list)
    # (name span, imported symbol name, resolved-later) for import references
    import_names: list[tuple[tuple, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------- helpers

def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def node_range(node: Node) -> tuple[tuple[int, int], tuple[int, int]]:
    # tree-sitter rows are 0-based → convert to 1-based lines; characters stay 0-based.
    return (
        (node.start_point[0] + 1, node.start_point[1]),
        (node.end_point[0] + 1, node.end_point[1]),
    )


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
    """Resolve base and list supported-language files changed vs the worktree.

    Returns (base sha, {relpath: added|modified|removed}).
    """
    sha = subprocess.run(
        ["git", "rev-parse", base], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    pathspecs = [f"*{ext}" for ext in EXT_TO_LANG]
    out = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", sha, "--", *pathspecs],
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
    spec = lang_for(relpath)
    if spec is None:
        return {}, None
    proc = subprocess.run(
        ["git", "show", f"{sha}:{relpath}"], cwd=root, capture_output=True, check=False
    )
    if proc.returncode != 0:
        print(f"warn: cannot read {relpath} at base: skipped", file=sys.stderr)
        return {}, None
    fi = extract_file(spec, relpath, proc.stdout)
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
    """Generic tree-sitter walker: symbols, call sites, imports, usage sites."""

    def __init__(self, spec: LangSpec, relpath: str, source: bytes):
        self.spec = spec
        self.relpath = relpath
        self.module = module_name(spec, Path(relpath))
        self.source = source
        self.file = FileIndex(
            path=relpath,
            module=self.module,
            language=spec.name,
            source=source.decode("utf-8", errors="replace"),
            sha256=sha256_text(source.decode("utf-8", errors="replace")),
        )
        self.module_symbol = Symbol(
            symbol_id=f"{spec.name}:{self.module}",
            kind="module",
            name=self.module.split("." if spec.dotted_modules else "/")[-1],
            qualname="",
            module=self.module,
            language=spec.name,
            path=relpath,
            start=(1, 0),
            end=(max(1, self.file.source.count("\n") + 1), 0),
            content_hash=self.file.sha256,
            signature=f"module {self.module}",
            parent=None,
        )
        self.file.symbols.append(self.module_symbol)

    def text(self, node: Node) -> str:
        return self.source[node.start_byte : node.end_byte].decode(
            "utf-8", errors="replace"
        )

    def run(self, root: Node) -> FileIndex:
        self._walk(root, scope=[], enclosing=self.module_symbol)
        return self.file

    def _walk(self, node: Node, scope: list[tuple[str, str]], enclosing: Symbol) -> None:
        for child in node.children:
            self._visit(child, scope, enclosing)

    def _visit(self, node: Node, scope: list[tuple[str, str]], enclosing: Symbol) -> None:
        t = node.type
        spec = self.spec

        # wrappers that carry a definition inside
        if t == "decorated_definition":  # python
            inner = node.child_by_field_name("definition")
            sym = None
            if inner is not None and inner.type in spec.defs:
                sym = self._definition(inner, outer=node, scope=scope, enclosing=enclosing)
            owner = sym or enclosing
            for dec in node.children:
                if dec.type != "decorator":
                    continue
                expr = next((c for c in dec.children if c.is_named), None)
                if expr is None:
                    continue
                if expr.type in ("identifier", spec.member_node[0]):
                    self._call_site(expr, owner)
                elif expr.type in spec.call_nodes:
                    self._visit(expr, scope, owner)
                else:
                    self._walk(expr, scope, owner)
            return
        if t == "export_statement":  # ts/js: export wraps the declaration
            decl = node.child_by_field_name("declaration")
            if decl is not None:
                self._visit(decl, scope, enclosing)
            else:
                self._walk(node, scope, enclosing)
            return
        if t == "type_declaration" and spec.name == "go":
            for ts_spec in node.children:
                if ts_spec.type == "type_spec":
                    self._definition(ts_spec, outer=node, scope=scope,
                                     enclosing=enclosing, kind_override="class")
            return
        if t in ("lexical_declaration", "variable_declaration") and spec.name in (
            "typescript", "javascript"
        ):
            # const f = () => {} / const f = function () {}
            for decl in node.children:
                if decl.type != "variable_declarator":
                    continue
                value = decl.child_by_field_name("value")
                if value is not None and value.type in (
                    "arrow_function", "function_expression", "generator_function"
                ):
                    sym = self._definition(decl, outer=node, scope=scope,
                                           enclosing=enclosing, kind_override="function",
                                           body_node=value)
                    if sym is None:
                        self._walk(value, scope, enclosing)
                elif value is not None:
                    self._visit(value, scope, enclosing)
            return

        if t in spec.defs:
            self._definition(node, outer=node, scope=scope, enclosing=enclosing)
            return
        if t in spec.import_nodes:
            self._import(node)
            return
        if t in spec.call_nodes:
            fn = node.child_by_field_name(spec.call_nodes[t])
            if fn is not None:
                self._call_site(fn, enclosing)
            # keep walking for nested calls inside arguments/receivers
            self._walk(node, scope, enclosing)
            return
        member_type, obj_field, name_field = spec.member_node
        if t == member_type:
            name_node = node.child_by_field_name(name_field)
            if name_node is not None:
                self._usage(name_node, enclosing)
            obj = node.child_by_field_name(obj_field)
            if obj is not None:
                self._visit(obj, scope, enclosing)
            return
        if t in ("identifier", "type_identifier"):
            self._usage(node, enclosing)
            return
        self._walk(node, scope, enclosing)

    def _definition(self, node: Node, outer: Node, scope: list[tuple[str, str]],
                    enclosing: Symbol, kind_override: str | None = None,
                    body_node: Node | None = None) -> Symbol | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        name = self.text(name_node)
        spec = self.spec

        kind = kind_override or spec.defs.get(node.type, "function")
        parent_kinds = [k for k, _ in scope]
        if kind == "function" and parent_kinds and parent_kinds[-1] == "class":
            kind = "method"

        recv = None
        if spec.name == "go" and node.type == "method_declaration":
            recv = self._go_receiver(node)

        qual_parts = [n for _, n in scope]
        if recv:
            qual_parts.append(recv)
        qual_parts.append(name)
        qualname = ".".join(qual_parts)

        text = self.text(outer)
        first_line = text.split("\n", 1)[0].rstrip(":{ ").rstrip()
        sym = Symbol(
            symbol_id=f"{spec.name}:{self.module}:{qualname}",
            kind=kind,
            name=name,
            qualname=qualname,
            module=self.module,
            language=spec.name,
            path=self.relpath,
            start=node_range(outer)[0],
            end=node_range(outer)[1],
            content_hash=sha256_text(text),
            signature=first_line,
            parent=enclosing.symbol_id,
        )
        self.file.symbols.append(sym)

        child_scope = scope + [("class" if kind == "class" else "function", name)]
        walked = False
        for extra in ("parameters", "return_type", "superclasses", "type_parameters",
                      "result", "receiver"):
            part = node.child_by_field_name(extra)
            if part is not None:
                self._walk(part, child_scope, sym)
        body = body_node or node.child_by_field_name("body")
        if body is not None:
            self._walk(body, child_scope, sym)
            walked = True
        if not walked and body_node is None and node.type == "type_spec":
            tnode = node.child_by_field_name("type")
            if tnode is not None:
                self._walk(tnode, child_scope, sym)
        return sym

    def _go_receiver(self, node: Node) -> str | None:
        recv = node.child_by_field_name("receiver")
        if recv is None:
            return None
        for p in recv.children:
            if p.type == "parameter_declaration":
                tnode = p.child_by_field_name("type")
                if tnode is not None:
                    return self.text(tnode).lstrip("*").strip()
        return None

    def _call_site(self, fn: Node, enclosing: Symbol) -> None:
        member_type, _obj_field, name_field = self.spec.member_node
        if fn.type in ("identifier", "type_identifier"):
            click = fn
            name = self.text(fn)
            is_member = False
        elif fn.type == member_type:
            name_node = fn.child_by_field_name(name_field)
            if name_node is None:
                return
            click = name_node
            name = self.text(name_node)
            is_member = True
            # the receiver chain may contain nested calls/usages
            obj = fn.child_by_field_name(self.spec.member_node[1])
            if obj is not None:
                self._visit(obj, [], enclosing)
        else:
            return  # subscript/dynamic callee: not resolvable statically
        self.file.calls.append(CallSite(
            enclosing=enclosing,
            name=name,
            is_member=is_member,
            click=node_range(click),
            pos=(click.start_point[0], click.start_point[1]),
        ))

    def _usage(self, node: Node, enclosing: Symbol) -> None:
        self.file.usages.append(UsageSite(
            enclosing=enclosing,
            name=self.text(node),
            click=node_range(node),
            pos=(node.start_point[0], node.start_point[1]),
        ))

    # -------- imports (per language, static: feeds module edges + name refs)

    def _import(self, node: Node) -> None:
        spec = self.spec
        if spec.name == "python":
            self._import_python(node)
        elif spec.name in ("typescript", "javascript"):
            self._import_ts(node)
        elif spec.name == "go":
            self._import_go(node)

    def _import_python(self, node: Node) -> None:
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    self.file.import_targets.append(self.text(child))
                elif child.type == "aliased_import":
                    dn = child.child_by_field_name("name")
                    if dn is not None:
                        self.file.import_targets.append(self.text(dn))
            return
        mod_node = node.child_by_field_name("module_name")
        if mod_node is None:
            return
        base = self.text(mod_node)
        if base.startswith("."):
            level = len(base) - len(base.lstrip("."))
            remainder = base.lstrip(".")
            parts = self.module.split(".")
            anchor = parts[: len(parts) - level] if level <= len(parts) else []
            base = ".".join(anchor + ([remainder] if remainder else []))
        self.file.import_targets.append(base)
        seen_module_name = False
        for child in node.children:
            if child.type == "dotted_name":
                if not seen_module_name and child == mod_node:
                    seen_module_name = True
                    continue
                name = self.text(child)
                if "." not in name:
                    self.file.import_names.append(
                        (node_range(child), name, f"{base}.{name}" if base else name)
                    )
            elif child.type == "aliased_import":
                dn = child.child_by_field_name("name")
                if dn is not None:
                    name = self.text(dn)
                    self.file.import_names.append(
                        (node_range(dn), name, f"{base}.{name}" if base else name)
                    )

    def _import_ts(self, node: Node) -> None:
        src_node = node.child_by_field_name("source")
        if src_node is None:
            return
        target = self.text(src_node).strip("'\"`")
        if not target.startswith("."):
            return  # package imports are external
        # resolve './x' / '../x' against the importing module's directory
        parts: list[str] = []
        for p in (Path(self.module).parent / target).parts:
            if p == "..":
                if parts:
                    parts.pop()
            elif p != ".":
                parts.append(p)
        target_mod = "/".join(parts)
        if Path(target_mod).suffix in self.spec.exts:
            target_mod = str(Path(target_mod).with_suffix(""))
        self.file.import_targets.append(target_mod)
        for clause in node.children:
            if clause.type != "import_clause":
                continue
            for named in clause.children:
                if named.type == "named_imports":
                    for ispec in named.children:
                        if ispec.type == "import_specifier":
                            nm = ispec.child_by_field_name("name")
                            if nm is not None:
                                self.file.import_names.append(
                                    (node_range(nm), self.text(nm), f"{target_mod}.{self.text(nm)}")
                                )

    def _import_go(self, node: Node) -> None:
        def spec_target(s: Node) -> None:
            path_node = s.child_by_field_name("path")
            if path_node is not None:
                self.file.import_targets.append(self.text(path_node).strip('"'))

        for child in node.children:
            if child.type == "import_spec":
                spec_target(child)
            elif child.type == "import_spec_list":
                for s in child.children:
                    if s.type == "import_spec":
                        spec_target(s)


def extract_file(spec: LangSpec, relpath: str, source: bytes) -> FileIndex:
    parser: Parser = get_parser(spec.grammars[Path(relpath).suffix])
    tree = parser.parse(source)
    return Extractor(spec, relpath, source).run(tree.root_node)


# ---------------------------------------------------------------- LSP client

class LspError(Exception):
    pass


class LspClient:
    """Minimal LSP client over stdio. Definition requests only."""

    def __init__(self, argv: tuple[str, ...], root: Path):
        self.root = root
        try:
            self.proc = subprocess.Popen(
                list(argv), cwd=root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            raise LspError(f"cannot start {argv[0]}: {e}") from e
        self.server = argv[0]
        self._id = 0
        self._responses: queue.Queue[dict] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ---- transport

    def _read_loop(self) -> None:
        stdout = self.proc.stdout
        assert stdout is not None
        try:
            while True:
                length = None
                while True:
                    line = stdout.readline()
                    if not line:
                        return
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        length = int(line.split(b":", 1)[1])
                if length is None:
                    continue
                body = stdout.read(length)
                if not body:
                    return
                try:
                    msg = json.loads(body)
                except ValueError:
                    continue
                if "id" in msg and "method" in msg:
                    self._answer_server_request(msg)
                elif "id" in msg:
                    self._responses.put(msg)
                # notifications (diagnostics, logs) are dropped
        except (OSError, ValueError):
            return

    def _answer_server_request(self, msg: dict) -> None:
        method = msg.get("method")
        result: object = None
        if method == "workspace/configuration":
            items = msg.get("params", {}).get("items", [])
            result = [None] * len(items)
        elif method == "workspace/workspaceFolders":
            result = [{"uri": self.root.as_uri(), "name": self.root.name}]
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def _send(self, msg: dict) -> None:
        stdin = self.proc.stdin
        if stdin is None or self.proc.poll() is not None:
            raise LspError(f"{self.server} exited")
        body = json.dumps(msg).encode("utf-8")
        try:
            stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            stdin.flush()
        except OSError as e:
            raise LspError(f"{self.server} pipe closed: {e}") from e

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict, timeout: float) -> object:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline_left = timeout
        while True:
            try:
                msg = self._responses.get(timeout=deadline_left)
            except queue.Empty:
                raise LspError(f"{self.server}: timeout on {method}") from None
            if msg.get("id") == rid:
                if "error" in msg:
                    raise LspError(f"{self.server}: {method}: {msg['error'].get('message')}")
                return msg.get("result")
            # stale response for an earlier (timed-out) request: drop

    # ---- protocol

    def initialize(self, init_options: dict | None = None) -> None:
        uri = self.root.as_uri()
        self.request("initialize", {
            "processId": None,
            "rootUri": uri,
            "capabilities": {
                "textDocument": {"definition": {}},
                "workspace": {"configuration": True, "workspaceFolders": True},
            },
            "workspaceFolders": [{"uri": uri, "name": self.root.name}],
            **({"initializationOptions": init_options} if init_options else {}),
        }, timeout=30.0)
        self.notify("initialized", {})

    def did_open(self, relpath: str, language_id: str, text: str) -> None:
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": (self.root / relpath).as_uri(),
            "languageId": language_id,
            "version": 1,
            "text": text,
        }})

    def definition(self, relpath: str, line0: int, char_utf16: int) -> list[dict]:
        result = self.request("textDocument/definition", {
            "textDocument": {"uri": (self.root / relpath).as_uri()},
            "position": {"line": line0, "character": char_utf16},
        }, timeout=15.0)
        if result is None:
            return []
        if isinstance(result, dict):
            result = [result]
        locs = []
        for loc in result:
            if "targetUri" in loc:
                locs.append({"uri": loc["targetUri"],
                             "range": loc.get("targetSelectionRange") or loc["targetRange"]})
            elif "uri" in loc:
                locs.append({"uri": loc["uri"], "range": loc["range"]})
        return locs

    def shutdown(self) -> None:
        try:
            self.request("shutdown", {}, timeout=5.0)
            self.notify("exit", {})
        except LspError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3.0)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def utf16_col(line_text: str, byte_col: int) -> int:
    prefix = line_text.encode("utf-8")[:byte_col].decode("utf-8", errors="replace")
    return len(prefix.encode("utf-16-le")) // 2


def find_server(spec: LangSpec) -> tuple[str, ...] | None:
    for argv in spec.servers:
        if shutil.which(argv[0]):
            return argv
    return None


def init_options_for(spec: LangSpec, argv: tuple[str, ...], root: Path) -> dict | None:
    """Server-specific initialization options.

    typescript-language-server needs a typescript installation. When the
    workspace has none, point it at the typescript package that sits next to
    the server itself (the standard global-npm-install layout).
    """
    if argv[0] != "typescript-language-server":
        return None
    if (root / "node_modules" / "typescript" / "lib" / "tsserver.js").exists():
        return None
    server_bin = shutil.which(argv[0])
    if server_bin is None:
        return None
    resolved = Path(server_bin).resolve()
    for parent in resolved.parents:
        if parent.name == "node_modules":
            tsserver = parent / "typescript" / "lib" / "tsserver.js"
            if tsserver.exists():
                return {"tsserver": {"path": str(tsserver)}}
    return None


# ---------------------------------------------------------------- resolution

class SymbolTable:
    def __init__(self, files: list[FileIndex]):
        self.files = files
        self.by_id: dict[str, Symbol] = {}
        self.by_full: dict[str, dict[str, Symbol]] = {}   # language -> full_name -> sym
        self.by_name: dict[str, dict[str, list[Symbol]]] = {}
        self.by_path: dict[str, list[Symbol]] = {}
        self.modules: dict[str, dict[str, Symbol]] = {}
        for f in files:
            self.by_path[f.path] = f.symbols
            for s in f.symbols:
                self.by_id[s.symbol_id] = s
                self.by_full.setdefault(s.language, {})[s.full_name] = s
                self.by_name.setdefault(s.language, {}).setdefault(s.name, []).append(s)
                if s.kind == "module":
                    self.modules.setdefault(s.language, {})[s.module] = s

    def symbol_at(self, path: str, line1: int, char: int) -> Symbol | None:
        best: Symbol | None = None
        for s in self.by_path.get(path, []):
            if s.kind == "module":
                continue
            (sl, sc), (el, ec) = s.start, s.end
            if (line1, char) < (sl, sc) or (line1, char) > (el, ec):
                continue
            if best is None or (s.start, tuple(-x for x in s.end)) > (
                best.start, tuple(-x for x in best.end)
            ):
                best = s
        if best is not None:
            return best
        syms = self.by_path.get(path, [])
        return next((s for s in syms if s.kind == "module"), None)


def resolve_language(
    spec: LangSpec,
    files: list[FileIndex],
    table: SymbolTable,
    root: Path,
    no_lsp: bool = False,
) -> tuple[list[Edge], list[Reference], int, str, LspClient | None]:
    """Resolve one language's call sites. Returns (edges, refs, unresolved, tier, client).

    The client is returned still-initialized so usage resolution can reuse it;
    the caller shuts it down.
    """
    argv = None if no_lsp else find_server(spec)
    if argv is not None:
        client = None
        try:
            client = LspClient(argv, root)
            client.initialize(init_options_for(spec, argv, root))
            for f in files:
                client.did_open(f.path, spec.lsp_language_id, f.source)
            edges, refs, unresolved = _resolve_calls_lsp(spec, files, table, client)
            return edges, refs, unresolved, f"lsp:{argv[0]}", client
        except LspError as e:
            print(f"warn: {spec.name}: LSP failed ({e}); falling back to generic",
                  file=sys.stderr)
            if client is not None:
                client.shutdown()
    edges, refs, unresolved = _resolve_calls_generic(spec, files, table)
    return edges, refs, unresolved, "generic", None


def _resolve_calls_lsp(
    spec: LangSpec, files: list[FileIndex], table: SymbolTable, client: LspClient
) -> tuple[list[Edge], list[Reference], int]:
    edges: list[Edge] = []
    refs: list[Reference] = []
    unresolved = 0
    for f in files:
        lines = f.source.split("\n")
        for call in f.calls:
            row, bcol = call.pos
            col16 = utf16_col(lines[row] if row < len(lines) else "", bcol)
            target = _lsp_target(client, table, f.path, row, col16, call.name)
            if target is None:
                unresolved += 1
                continue
            edges.append(Edge(
                src=call.enclosing.symbol_id, dst=target.symbol_id, kind="calls",
                resolution="lsp", site_path=f.path, site_range=call.click,
            ))
            refs.append(Reference(
                path=f.path, start=call.click[0], end=call.click[1],
                symbol_id=target.symbol_id,
            ))
    return edges, refs, unresolved


def _lsp_target(
    client: LspClient, table: SymbolTable, path: str, row: int, col16: int,
    name: str,
) -> Symbol | None:
    """Map a definition location to a project symbol.

    The resolved symbol must carry the queried name. A definition that lands
    inside another symbol (a local variable or parameter) would otherwise be
    misattributed to its enclosing function (precision first).
    """
    locs = client.definition(path, row, col16)
    root = client.root.resolve()
    for loc in locs:
        uri = loc["uri"]
        if not uri.startswith("file://"):
            continue
        p = Path(unquote(urlparse(uri).path)).resolve()
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            continue  # definition outside the repo (stdlib/deps)
        rng = loc["range"]["start"]
        sym = table.symbol_at(rel, rng["line"] + 1, rng["character"])
        if sym is not None and sym.name == name:
            return sym
    return None


def _resolve_calls_generic(
    spec: LangSpec, files: list[FileIndex], table: SymbolTable
) -> tuple[list[Edge], list[Reference], int]:
    edges: list[Edge] = []
    refs: list[Reference] = []
    unresolved = 0
    by_name = table.by_name.get(spec.name, {})
    for f in files:
        for call in f.calls:
            if call.is_member and call.name in spec.builtin_block:
                unresolved += 1
                continue
            cands = [
                s for s in by_name.get(call.name, [])
                if s.kind in ("function", "method", "class")
            ]
            if len(cands) != 1:
                unresolved += 1
                continue
            target = cands[0]
            edges.append(Edge(
                src=call.enclosing.symbol_id, dst=target.symbol_id, kind="calls",
                resolution="name_match", site_path=f.path, site_range=call.click,
            ))
            refs.append(Reference(
                path=f.path, start=call.click[0], end=call.click[1],
                symbol_id=target.symbol_id,
            ))
    return edges, refs, unresolved


def import_edges_and_refs(
    spec: LangSpec, files: list[FileIndex], table: SymbolTable
) -> tuple[list[Edge], list[Reference]]:
    edges: list[Edge] = []
    refs: list[Reference] = []
    modules = table.modules.get(spec.name, {})
    by_full = table.by_full.get(spec.name, {})
    for f in files:
        seen_dst: set[str] = set()
        for target in f.import_targets:
            sym = modules.get(target)
            if sym is None and spec.dotted_modules:
                parts = target.split(".")
                for i in range(len(parts), 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in modules:
                        sym = modules[cand]
                        break
            if sym is None and spec.name == "go":
                # import path suffix match against module dirs
                for mod, msym in modules.items():
                    mdir = str(Path(mod).parent)
                    if target == mdir or target.endswith("/" + mdir):
                        sym = msym
                        break
            if sym is None:
                continue
            dst = f"{spec.name}:{sym.module}"
            if dst in seen_dst or dst == f"{spec.name}:{f.module}":
                continue
            seen_dst.add(dst)
            edges.append(Edge(
                src=f"{spec.name}:{f.module}", dst=dst, kind="imports",
                resolution="exact",
            ))
        for (click, _name, full) in f.import_names:
            sym = by_full.get(full)
            if sym is None:
                continue
            refs.append(Reference(
                path=f.path, start=click[0], end=click[1], symbol_id=sym.symbol_id,
            ))
    return edges, refs


def resolve_usages_lsp(
    spec: LangSpec,
    files: list[FileIndex],
    table: SymbolTable,
    client: LspClient,
) -> list[Reference]:
    """Resolve non-call usages via LSP, prefiltered to known symbol names."""
    refs: list[Reference] = []
    names = set(table.by_name.get(spec.name, {}))
    budget = MAX_LSP_USAGE_REQUESTS
    for f in files:
        lines = f.source.split("\n")
        own_names = {(s.start, s.name) for s in f.symbols}
        for u in f.usages:
            if u.name not in names:
                continue
            if (u.click[0], u.name) in own_names:
                continue  # the definition's own name is not a usage
            if budget <= 0:
                print(f"warn: {spec.name}: usage resolution capped at "
                      f"{MAX_LSP_USAGE_REQUESTS} requests", file=sys.stderr)
                return refs
            budget -= 1
            row, bcol = u.pos
            col16 = utf16_col(lines[row] if row < len(lines) else "", bcol)
            try:
                target = _lsp_target(client, table, f.path, row, col16, u.name)
            except LspError as e:
                print(f"warn: {spec.name}: usage resolution stopped ({e})",
                      file=sys.stderr)
                return refs
            if target is None or target.kind == "module":
                continue
            if target.path == f.path and target.start == u.click[0]:
                continue  # self-definition
            refs.append(Reference(
                path=f.path, start=u.click[0], end=u.click[1],
                symbol_id=target.symbol_id,
            ))
    return refs


# ---------------------------------------------------------------- slice

def compute_slice(
    files: list[FileIndex],
    edges: list[Edge],
    entries: list[str],
    hops: int,
) -> tuple[set[str], set[str], list[str]]:
    """Returns: (slice file path set, matched seed symbol ids, unmatched entries)"""
    all_symbols: dict[str, Symbol] = {}
    for f in files:
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
    ap.add_argument("--no-lsp", action="store_true",
                    help="skip LSP servers; use generic resolution only")
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
            "error: no entry points (pass --entry, or --base with supported-language changes)",
            file=sys.stderr,
        )
        return 2

    files: list[FileIndex] = []
    for spec in LANGUAGES.values():
        for ext in spec.exts:
            for path in sorted(root.rglob(f"*{ext}")):
                rel = path.relative_to(root)
                if is_excluded(rel, excludes):
                    continue
                try:
                    data = path.read_bytes()
                except OSError as e:
                    print(f"warn: skip {rel}: {e}", file=sys.stderr)
                    continue
                files.append(extract_file(spec, str(rel), data))

    if not files:
        print("error: no supported source files found "
              f"(looked for: {', '.join(sorted(EXT_TO_LANG))})", file=sys.stderr)
        return 2

    table = SymbolTable(files)
    by_lang: dict[str, list[FileIndex]] = {}
    for f in files:
        by_lang.setdefault(f.language, []).append(f)

    edges: list[Edge] = []
    references: list[Reference] = []
    unresolved_calls = 0
    tiers: dict[str, str] = {}
    clients: dict[str, LspClient] = {}
    try:
        for lang, lang_files in by_lang.items():
            spec = LANGUAGES[lang]
            e, r, u, tier, client = resolve_language(
                spec, lang_files, table, root, no_lsp=args.no_lsp
            )
            edges.extend(e)
            references.extend(r)
            unresolved_calls += u
            tiers[lang] = tier
            if client is not None:
                clients[lang] = client
            ie, ir = import_edges_and_refs(spec, lang_files, table)
            edges.extend(ie)
            references.extend(ir)

        slice_paths, seed_ids, unmatched = compute_slice(
            files, edges, entries, args.hops
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

        # LSP tier: resolve non-call usages, but only for files that made the slice
        for lang, client in clients.items():
            spec = LANGUAGES[lang]
            lang_slice = [f for f in by_lang[lang] if f.path in slice_paths]
            references.extend(resolve_usages_lsp(spec, lang_slice, table, client))
    finally:
        for client in clients.values():
            client.shutdown()

    kept_symbols: dict[str, Symbol] = {}
    for f in slice_files:
        for s in f.symbols:
            s.in_slice = True
            kept_symbols[s.symbol_id] = s

    # boundary stub: symbol outside the slice but connected by an edge from it (PRD §9.3)
    all_syms = {s.symbol_id: s for f in files for s in f.symbols}
    kept_edges: list[Edge] = []
    for e in edges:
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

    # references can also cross the boundary (imports, annotations, callbacks);
    # stub their targets too so the viewer can show the boundary popup
    for r in references:
        if r.path in slice_paths and r.symbol_id not in kept_symbols and r.symbol_id in all_syms:
            stub = all_syms[r.symbol_id]
            stub.in_slice = False
            kept_symbols[r.symbol_id] = stub

    seen_spans: set[tuple] = set()
    kept_refs: list[Reference] = []
    for r in references:
        if r.path not in slice_paths or r.symbol_id not in kept_symbols:
            continue
        span = (r.path, r.start, r.end)
        if span in seen_spans:
            continue  # call-site references come first and win over usage references
        seen_spans.add(span)
        kept_refs.append(r)

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
            "resolution": tiers,
            "files": [
                {"path": f.path, "language": f.language, "sha256": f.sha256,
                 "source": f.source}
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
            "unresolved_calls": unresolved_calls,
            "resolution": tiers,
        },
    }

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        tier_str = ",".join(f"{k}:{v}" for k, v in sorted(tiers.items()))
        print(
            f"index written: {args.out} "
            f"(files={out['slice']['file_count']}, "
            f"symbols={out['stats']['slice_symbols']}+{out['stats']['boundary_symbols']} boundary, "
            f"edges={out['stats']['edges']}, unresolved={out['stats']['unresolved_calls']}, "
            f"tiers={tier_str})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
