from conftest import SAMPLE_REPO, run_script


def edges_of(idx, kind):
    return [(e["from"], e["to"], e["resolution"]) for e in idx["index"]["edges"] if e["kind"] == kind]


def test_symbols_extracted(index_output):
    ids = {s["symbol_id"] for s in index_output["index"]["symbols"]}
    assert "python:orders.service:OrderService.create" in ids
    assert "python:orders.repository:OrderRepository.save" in ids
    assert "python:orders.models:Order" in ids


def test_annotation_based_resolution_is_exact(index_output):
    calls = edges_of(index_output, "calls")
    assert (
        "python:orders.service:OrderService.create",
        "python:orders.repository:OrderRepository.get",
        "exact",
    ) in calls
    assert (
        "python:orders.service:OrderService.create",
        "python:payments.gateway:PaymentGateway.charge",
        "exact",
    ) in calls


def test_no_builtin_method_false_positive(index_output):
    # payload.get(...) must not be falsely linked to OrderRepository.get
    calls = edges_of(index_output, "calls")
    assert not any(
        src.startswith("python:payments.gateway:PaymentGateway._post") and "OrderRepository.get" in dst
        for src, dst, _ in calls
    )


def test_references_are_clickable_spans(index_output):
    refs = index_output["index"]["references"]
    assert any(
        r["path"] == "orders/service.py"
        and r["symbol_id"] == "python:payments.gateway:PaymentGateway.charge"
        for r in refs
    )
    # each range is a single-line identifier
    for r in refs:
        assert r["range"]["start_line"] == r["range"]["end_line"]


def test_decorator_produces_edge_and_reference(index_output):
    calls = edges_of(index_output, "calls")
    assert (
        "python:orders.audit:record_audit",
        "python:orders.audit:logged",
        "exact",
    ) in calls
    refs = index_output["index"]["references"]
    assert any(
        r["path"] == "orders/audit.py"
        and r["symbol_id"] == "python:orders.audit:logged"
        for r in refs
    )


def test_non_call_usages_are_referenced(index_output):
    refs = [r for r in index_output["index"]["references"] if r["path"] == "orders/audit.py"]
    # annotation: repo: OrderRepository
    assert any(r["symbol_id"] == "python:orders.repository:OrderRepository" for r in refs)
    # default value and module-level registration: sink=null_sink, [null_sink]
    assert len([r for r in refs if r["symbol_id"] == "python:orders.audit:null_sink"]) >= 2


def test_import_names_are_referenced(index_output):
    refs = index_output["index"]["references"]
    assert any(
        r["path"] == "orders/service.py"
        and r["range"]["start_line"] == 1
        and r["symbol_id"] == "python:orders.models:Order"
        for r in refs
    )


def test_local_shadowing_suppresses_reference(index_output):
    # inside shadow_case, `logged` is a local string; it must not link to the decorator
    syms = {s["symbol_id"]: s for s in index_output["index"]["symbols"]}
    shadow = syms["python:orders.audit:shadow_case"]
    lo, hi = shadow["range"]["start_line"], shadow["range"]["end_line"]
    assert not any(
        r["path"] == "orders/audit.py"
        and lo <= r["range"]["start_line"] <= hi
        and r["symbol_id"] == "python:orders.audit:logged"
        for r in index_output["index"]["references"]
    )


def test_references_do_not_overlap_or_duplicate(index_output):
    by_line: dict = {}
    for r in index_output["index"]["references"]:
        by_line.setdefault((r["path"], r["range"]["start_line"]), []).append(r["range"])
    for spans in by_line.values():
        spans.sort(key=lambda s: s["start_character"])
        for a, b in zip(spans, spans[1:]):
            assert a["end_character"] <= b["start_character"]


def test_boundary_stub_at_hops_1(tmp_path):
    out = tmp_path / "idx.json"
    proc = run_script(
        "atlas_index.py",
        "--repo", str(SAMPLE_REPO),
        "--entry", "orders/service.py",
        "--hops", "1",
        "--out", str(out),
    )
    assert proc.returncode == 0, proc.stderr
    import json
    idx = json.loads(out.read_text())
    stubs = [s for s in idx["index"]["symbols"] if not s["in_slice"]]
    assert stubs, "with hops=1 the notifications side must be a boundary stub"
    stub_paths = {s["path"] for s in stubs}
    in_slice_paths = {f["path"] for f in idx["index"]["files"]}
    assert stub_paths.isdisjoint(in_slice_paths) is False or stub_paths - in_slice_paths
    # a stub's file source is not embedded
    assert any(s["path"] not in in_slice_paths for s in stubs)


def test_unmatched_entry_fails(tmp_path):
    proc = run_script(
        "atlas_index.py",
        "--repo", str(SAMPLE_REPO),
        "--entry", "no/such/file.py",
        "--out", str(tmp_path / "x.json"),
    )
    assert proc.returncode != 0
