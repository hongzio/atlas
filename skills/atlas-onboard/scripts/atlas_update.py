#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# ///
"""Atlas update script: compare the previous artifact with a fresh index and compute changes.

The deterministic part of incremental update (PRD §11): what changed is computed
by content hash comparison; only the meaning of changes is interpreted by the agent.

    uv run scripts/atlas_index.py --repo <path> --entry ... --out new_index.json
    uv run scripts/atlas_update.py --previous old_artifact.json --index new_index.json --out changes.json

Output:
  - changes: shaped to slot directly into the artifact.changes field
  - reinvestigate: symbols the agent must re-investigate
  - reusable: flow/concept/invariant ids whose evidence is still valid
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def symbol_map(index: dict) -> dict[str, dict]:
    return {s["symbol_id"]: s for s in index.get("symbols", []) if s.get("in_slice")}


def file_map(index: dict) -> dict[str, str]:
    return {f["path"]: f["sha256"] for f in index.get("files", [])}


def evidence_symbols(item: dict) -> set[str]:
    """Set of symbol_ids/paths referenced by a narrative item."""
    out: set[str] = set()

    def visit(ev_list):
        for ev in ev_list or []:
            if ev.get("symbol_id"):
                out.add(ev["symbol_id"])
            if ev.get("path"):
                out.add("path:" + ev["path"])

    visit(item.get("evidence"))
    for step in item.get("steps", []):
        visit(step.get("evidence"))
        for br in step.get("branches", []):
            visit(br.get("evidence"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Atlas update: compute changes vs the previous artifact")
    ap.add_argument("--previous", required=True, help="previous artifact JSON")
    ap.add_argument("--index", required=True, help="fresh index JSON (atlas_index output)")
    ap.add_argument("--out", default="-")
    args = ap.parse_args()

    try:
        prev = load(args.previous)
        new = load(args.index)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    old_syms = symbol_map(prev.get("index", {}))
    new_syms = symbol_map(new.get("index", {}))
    old_files = file_map(prev.get("index", {}))
    new_files = file_map(new.get("index", {}))

    sym_changes: list[dict] = []
    changed_ids: set[str] = set()
    for sid, s in new_syms.items():
        if sid not in old_syms:
            sym_changes.append({"symbol_id": sid, "change": "added"})
            changed_ids.add(sid)
        elif s["content_hash"] != old_syms[sid]["content_hash"]:
            sym_changes.append({"symbol_id": sid, "change": "modified"})
            changed_ids.add(sid)
    for sid in old_syms:
        if sid not in new_syms:
            sym_changes.append({"symbol_id": sid, "change": "removed"})
            changed_ids.add(sid)

    file_changes: list[dict] = []
    changed_paths: set[str] = set()
    for path, sha in new_files.items():
        if path not in old_files:
            file_changes.append({"path": path, "change": "added"})
            changed_paths.add(path)
        elif sha != old_files[path]:
            file_changes.append({"path": path, "change": "modified"})
            changed_paths.add(path)
    for path in old_files:
        if path not in new_files:
            file_changes.append({"path": path, "change": "removed"})
            changed_paths.add(path)

    # previous narrative touching changed evidence → stale candidate; the rest → reusable
    touched = changed_ids | {"path:" + p for p in changed_paths}
    stale: list[str] = []
    reusable: list[str] = []
    for kind in ("flows", "concepts", "invariants"):
        for item in prev.get(kind, []):
            refs = evidence_symbols(item)
            (stale if refs & touched else reusable).append(item["id"])

    out = {
        "changes": {
            "previous_head_commit": prev["repository"]["head_commit"],
            "files": sorted(file_changes, key=lambda x: x["path"]),
            "symbols": sorted(sym_changes, key=lambda x: x["symbol_id"]),
            "stale_items": stale,
        },
        "reinvestigate": sorted(changed_ids),
        "reusable": reusable,
        "previous_artifact_id": prev["artifact_id"],
    }

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(
            f"changes written: {args.out} "
            f"(symbols {len(sym_changes)} changed, files {len(file_changes)} changed, "
            f"stale {len(stale)}, reusable {len(reusable)})",
            file=sys.stderr,
        )
    if not sym_changes and not file_changes:
        print("no changes: artifact is up to date", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
