"""Multi-language indexing: generic tier always, LSP tier when a server is on PATH."""
import json
import shutil

import pytest

from conftest import ROOT, run_script

TS_REPO = ROOT / "tests" / "sample_ts_repo"
GO_REPO = ROOT / "tests" / "sample_go_repo"

PY_SERVERS = (
    "pyright-langserver", "basedpyright-langserver", "jedi-language-server", "pylsp",
)

has_py_lsp = any(shutil.which(s) for s in PY_SERVERS)
has_ts_lsp = shutil.which("typescript-language-server") is not None
has_go_lsp = shutil.which("gopls") is not None


def index(tmp_path, repo, entry, *extra):
    out = tmp_path / "idx.json"
    proc = run_script(
        "atlas_index.py", "--repo", str(repo),
        "--entry", entry, "--hops", "2", "--out", str(out), *extra,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


def calls_of(idx):
    return {
        (e["from"], e["to"], e["resolution"])
        for e in idx["index"]["edges"] if e["kind"] == "calls"
    }


# ---------------------------------------------------------------- generic tier

def test_typescript_generic(tmp_path):
    idx = index(tmp_path, TS_REPO, "src/service.ts", "--no-lsp")
    assert idx["index"]["resolution"] == {"typescript": "generic"}
    ids = {s["symbol_id"] for s in idx["index"]["symbols"]}
    assert "typescript:src/service:OrderService.create" in ids
    assert "typescript:src/models:Order.markPaid" in ids
    calls = calls_of(idx)
    assert (
        "typescript:src/service:OrderService.create",
        "typescript:src/repository:OrderRepository.findByKey",
        "name_match",
    ) in calls
    # relative imports become module edges
    imports = {(e["from"], e["to"]) for e in idx["index"]["edges"] if e["kind"] == "imports"}
    assert ("typescript:src/service", "typescript:src/repository") in imports
    langs = {f["language"] for f in idx["index"]["files"]}
    assert langs == {"typescript"}


def test_go_generic(tmp_path):
    idx = index(tmp_path, GO_REPO, "orders/service.go", "--no-lsp")
    assert idx["index"]["resolution"] == {"go": "generic"}
    ids = {s["symbol_id"] for s in idx["index"]["symbols"]}
    # receiver methods are qualified by their type
    assert "go:orders/service:OrderService.Create" in ids
    assert "go:orders/repository:OrderRepository.FindByKey" in ids
    calls = calls_of(idx)
    assert (
        "go:orders/service:OrderService.Create",
        "go:orders/repository:OrderRepository.Save",
        "name_match",
    ) in calls
    # fmt.Errorf is external: no speculative edge
    assert not any("Errorf" in dst for _, dst, _ in calls)


# ---------------------------------------------------------------- LSP tier

@pytest.mark.skipif(not has_py_lsp, reason="no python language server on PATH")
def test_python_lsp(tmp_path):
    from conftest import SAMPLE_REPO
    idx = index(tmp_path, SAMPLE_REPO, "orders/service.py")
    (tier,) = idx["index"]["resolution"].values()
    assert tier.startswith("lsp:")
    calls = calls_of(idx)
    # the blocklisted name resolves through the server's type analysis
    assert any(
        src == "python:orders.service:OrderService.create"
        and dst == "python:orders.repository:OrderRepository.get"
        and res == "lsp"
        for src, dst, res in calls
    )
    # a call through a parameter must not produce a self/enclosing edge
    assert not any(src == dst for src, dst, _ in calls)
    # non-call usages (annotations) become references in the LSP tier
    refs = [r for r in idx["index"]["references"] if r["path"] == "orders/audit.py"]
    assert any(r["symbol_id"] == "python:orders.repository:OrderRepository" for r in refs)
    # locals shadowing a symbol name must not be linked
    syms = {s["symbol_id"]: s for s in idx["index"]["symbols"]}
    shadow = syms["python:orders.audit:shadow_case"]
    lo, hi = shadow["range"]["start_line"], shadow["range"]["end_line"]
    assert not any(
        r["path"] == "orders/audit.py"
        and lo < r["range"]["start_line"] <= hi
        and r["symbol_id"] == "python:orders.audit:logged"
        for r in idx["index"]["references"]
    )


@pytest.mark.skipif(not has_ts_lsp, reason="typescript-language-server not on PATH")
def test_typescript_lsp(tmp_path):
    idx = index(tmp_path, TS_REPO, "src/service.ts")
    assert idx["index"]["resolution"]["typescript"].startswith("lsp:")
    calls = calls_of(idx)
    assert (
        "typescript:src/service:OrderService.create",
        "typescript:src/repository:OrderRepository.findByKey",
        "lsp",
    ) in calls
    # constructor annotation resolves as a usage reference
    refs = [r for r in idx["index"]["references"] if r["path"] == "src/service.ts"]
    assert any(
        r["symbol_id"] == "typescript:src/repository:OrderRepository" for r in refs
    )


@pytest.mark.skipif(not has_go_lsp, reason="gopls not on PATH")
def test_go_lsp(tmp_path):
    idx = index(tmp_path, GO_REPO, "orders/service.go")
    assert idx["index"]["resolution"]["go"].startswith("lsp:")
    calls = calls_of(idx)
    assert (
        "go:orders/service:OrderService.Create",
        "go:orders/repository:OrderRepository.FindByKey",
        "lsp",
    ) in calls
