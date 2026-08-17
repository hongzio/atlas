import copy
import json

from conftest import make_artifact, run_script


def write(tmp_path, artifact, name="artifact.json"):
    p = tmp_path / name
    p.write_text(json.dumps(artifact, ensure_ascii=False))
    return p


def test_valid_artifact_passes(index_output, tmp_path):
    p = write(tmp_path, make_artifact(index_output))
    proc = run_script("atlas_validate.py", str(p))
    assert proc.returncode == 0, proc.stderr


def test_missing_evidence_rejected(index_output, tmp_path):
    art = make_artifact(index_output)
    del art["flows"][0]["steps"][0]["evidence"]
    proc = run_script("atlas_validate.py", str(write(tmp_path, art)))
    assert proc.returncode == 1
    assert "evidence" in proc.stderr


def test_shallow_detail_rejected(index_output, tmp_path):
    # one-line summaries are forbidden (PRD §14.2), enforced via minLength
    art = make_artifact(index_output)
    art["flows"][0]["steps"][0]["detail"] = "Creates an order"
    proc = run_script("atlas_validate.py", str(write(tmp_path, art)))
    assert proc.returncode == 1


def test_nonexistent_evidence_path_rejected(index_output, tmp_path):
    art = make_artifact(index_output)
    ev = copy.deepcopy(art["flows"][0]["steps"][0]["evidence"][0])
    ev.pop("symbol_id")
    ev["path"] = "orders/ghost.py"
    art["flows"][0]["steps"][0]["evidence"].append(ev)
    proc = run_script("atlas_validate.py", str(write(tmp_path, art)))
    assert proc.returncode == 1
    assert "ghost" in proc.stderr


def test_out_of_range_evidence_rejected(index_output, tmp_path):
    art = make_artifact(index_output)
    art["flows"][0]["steps"][0]["evidence"][0]["range"]["end_line"] = 99999
    proc = run_script("atlas_validate.py", str(write(tmp_path, art)))
    assert proc.returncode == 1
    assert "out of bounds" in proc.stderr
