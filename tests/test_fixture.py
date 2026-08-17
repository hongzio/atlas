"""Phase 0 exit criteria: the fixture artifact validates and renders without an agent (PRD §24)."""
import json
from pathlib import Path

from conftest import ROOT, run_script

FIXTURE = ROOT / "fixtures" / "sample_artifact.json"
REVIEW_FIXTURE = ROOT / "fixtures" / "sample_review_artifact.json"


def test_fixture_is_valid():
    proc = run_script("atlas_validate.py", str(FIXTURE))
    assert proc.returncode == 0, proc.stderr


def test_review_fixture_is_valid_and_renders(tmp_path):
    proc = run_script("atlas_validate.py", str(REVIEW_FIXTURE))
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "review.html"
    proc = run_script("atlas_render.py", str(REVIEW_FIXTURE), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "Review" in out.read_text()


def test_fixture_renders(tmp_path):
    out = tmp_path / "fixture.html"
    proc = run_script("atlas_render.py", str(FIXTURE), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    html = out.read_text()
    art = json.loads(FIXTURE.read_text())
    assert art["artifact_id"] in html
    # the raw material for go-to-def: reference spans and symbol data are embedded
    assert "PaymentGateway.charge" in html
    assert '"references":' in html
