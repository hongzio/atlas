import json
import shutil
import subprocess

from conftest import SAMPLE_REPO, make_review_artifact, run_script

CREATE = "python:orders.service:OrderService.create"
AUDIT = "python:orders.service:audit_order"

DUPLICATE_CHECK = (
    "        existing = self.repo.get(idempotency_key)\n"
    "        if existing is not None:\n"
    "            raise DuplicateOrderError(idempotency_key)\n"
)


def make_buggy_git_repo(tmp_path):
    """Copy sample_repo, commit it, then remove the duplicate check in the worktree."""
    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, repo)

    def git(*args):
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
            check=True, capture_output=True,
        )

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "init")

    svc = repo / "orders" / "service.py"
    src = svc.read_text()
    assert DUPLICATE_CHECK in src
    svc.write_text(
        src.replace(DUPLICATE_CHECK, "")
        + "\n\ndef audit_order(order):\n    return order.idempotency_key\n"
    )
    return repo


def diff_index(tmp_path, repo):
    out = tmp_path / "idx.json"
    proc = run_script(
        "atlas_index.py", "--repo", str(repo),
        "--base", "HEAD", "--hops", "2", "--out", str(out),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text())


def test_base_diff_detects_changed_symbols(tmp_path):
    idx = diff_index(tmp_path, make_buggy_git_repo(tmp_path))
    changes = {s["symbol_id"]: s["change"] for s in idx["changes"]["symbols"]}
    assert changes.get(CREATE) == "modified"
    assert changes.get(AUDIT) == "added"
    # changed files become entry points automatically
    assert idx["slice"]["entry_points"] == ["orders/service.py"]
    assert idx["repository"]["base_commit"] == idx["changes"]["previous_head_commit"]
    files = {f["path"]: f["change"] for f in idx["changes"]["files"]}
    assert files == {"orders/service.py": "modified"}


def test_review_artifact_validates_and_renders(tmp_path):
    idx = diff_index(tmp_path, make_buggy_git_repo(tmp_path))
    art = make_review_artifact(idx)
    src = tmp_path / "review.json"
    src.write_text(json.dumps(art, ensure_ascii=False))

    proc = run_script("atlas_validate.py", str(src))
    assert proc.returncode == 0, proc.stderr

    out = tmp_path / "review.html"
    proc = run_script("atlas_render.py", str(src), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    html = out.read_text()
    assert "duplicate order" in html
    assert '"severity":"blocking"' in html
    assert '"severity":"spotlight"' in html


def test_quickfix_format(tmp_path):
    idx = diff_index(tmp_path, make_buggy_git_repo(tmp_path))
    art = make_review_artifact(idx)
    src = tmp_path / "review.json"
    src.write_text(json.dumps(art, ensure_ascii=False))

    proc = run_script("atlas_quickfix.py", str(src), "--out", "-")
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("orders/service.py:")
    assert "[BLOCKING]" in lines[0]
    assert "[SPOTLIGHT]" in lines[1]
    # path:line:col: prefix is machine-parsable
    path, line, col = lines[0].split(":", 3)[:3]
    assert path == "orders/service.py" and line.isdigit() and col.isdigit()


def test_base_requires_git_repo(tmp_path):
    plain = tmp_path / "plain"
    shutil.copytree(SAMPLE_REPO, plain)
    proc = run_script(
        "atlas_index.py", "--repo", str(plain),
        "--base", "HEAD", "--out", str(tmp_path / "x.json"),
    )
    assert proc.returncode == 2
    assert "cannot diff" in proc.stderr
