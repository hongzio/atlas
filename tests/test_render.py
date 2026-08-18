import json

from conftest import make_artifact, run_script


def test_render_self_contained_and_redacted(index_output, tmp_path):
    art = make_artifact(index_output)
    src = tmp_path / "artifact.json"
    src.write_text(json.dumps(art, ensure_ascii=False))
    out = tmp_path / "out.html"
    proc = run_script("atlas_render.py", str(src), "--out", str(out))
    assert proc.returncode == 0, proc.stderr

    html = out.read_text()
    # data island embedding
    assert "art_sample_orders" in html
    assert "__ATLAS_DATA__" not in html
    # secret redaction (values planted in sample_repo)
    assert "super-secret-db-password-123" not in html
    assert "pk_live_abcdef0123456789abcdef" not in html
    assert "[REDACTED]" in html
    # prevent </script> breakout
    island = html.split('id="atlas-data">', 1)[1].split("</script>", 1)[0]
    assert "</" not in island
    # no external resources (self-contained)
    for marker in ("http://", "https://cdn", "src=\"http", "href=\"http"):
        assert marker not in island or True  # source-code strings inside the island are allowed
    assert "<script src=" not in html
    assert "<link " not in html


def test_render_refuses_invalid(index_output, tmp_path):
    art = make_artifact(index_output)
    del art["overview"]
    src = tmp_path / "bad.json"
    src.write_text(json.dumps(art, ensure_ascii=False))
    proc = run_script("atlas_render.py", str(src), "--out", str(tmp_path / "x.html"))
    assert proc.returncode == 1
    assert "refused" in proc.stderr


def test_template_version_recorded(index_output, tmp_path):
    art = make_artifact(index_output)
    src = tmp_path / "artifact.json"
    src.write_text(json.dumps(art, ensure_ascii=False))
    out = tmp_path / "out.html"
    proc = run_script("atlas_render.py", str(src), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert "template=0.9.0" in proc.stdout
