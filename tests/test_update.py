import json
import shutil

from conftest import SAMPLE_REPO, make_artifact, run_script


def test_update_detects_changes_and_stale(index_output, tmp_path):
    prev = make_artifact(index_output)
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev, ensure_ascii=False))

    # modify create and add a new function in a copy of the repo
    repo2 = tmp_path / "repo2"
    shutil.copytree(SAMPLE_REPO, repo2)
    svc = repo2 / "orders" / "service.py"
    svc.write_text(
        svc.read_text().replace(
            "raise DuplicateOrderError(idempotency_key)",
            "return existing  # policy change: return the existing order on duplicates",
        )
        + "\n\ndef audit_order(order):\n    return order.idempotency_key\n"
    )

    new_idx_path = tmp_path / "new_index.json"
    proc = run_script(
        "atlas_index.py", "--repo", str(repo2),
        "--entry", "orders/service.py", "--hops", "2",
        "--out", str(new_idx_path),
    )
    assert proc.returncode == 0, proc.stderr

    out = tmp_path / "changes.json"
    proc = run_script(
        "atlas_update.py", "--previous", str(prev_path),
        "--index", str(new_idx_path), "--out", str(out),
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out.read_text())

    sym_changes = {s["symbol_id"]: s["change"] for s in result["changes"]["symbols"]}
    assert sym_changes.get("python:orders.service:OrderService.create") == "modified"
    assert sym_changes.get("python:orders.service:audit_order") == "added"

    # narrative that uses create as evidence becomes a stale candidate
    assert "flow-create-order" in result["changes"]["stale_items"]
    assert "inv-idempotency" in result["changes"]["stale_items"]
    assert result["previous_artifact_id"] == prev["artifact_id"]


def test_update_no_changes(index_output, tmp_path):
    prev = make_artifact(index_output)
    prev_path = tmp_path / "prev.json"
    prev_path.write_text(json.dumps(prev, ensure_ascii=False))
    idx_path = tmp_path / "same_index.json"
    idx_path.write_text(json.dumps(
        {"repository": prev["repository"] | {"root": str(SAMPLE_REPO)},
         "slice": prev["slice"], "index": prev["index"]},
        ensure_ascii=False,
    ))
    proc = run_script(
        "atlas_update.py", "--previous", str(prev_path),
        "--index", str(idx_path), "--out", "-",
    )
    assert proc.returncode == 0, proc.stderr
    assert "no changes" in proc.stderr
    result = json.loads(proc.stdout)
    assert result["changes"]["symbols"] == []
    assert result["reusable"], "with no changes, every narrative item must be reusable"
