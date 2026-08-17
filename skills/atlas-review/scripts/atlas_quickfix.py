#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# ///
"""Atlas quickfix script: review artifact findings → Vim quickfix format.

A review run emits this file next to the HTML so the reviewer can jump
between findings in Vim without any installed command (PRD §17.3):

    uv run scripts/atlas_quickfix.py artifact.json --out review.qf
    vim -q review.qf

One line per finding, anchored at its first evidence pointer:

    orders/service.py:18:1: [BLOCKING] create no longer checks the idempotency key
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def quickfix_lines(artifact: dict) -> list[str]:
    lines: list[str] = []
    for f in artifact.get("findings", []):
        evs = f.get("evidence") or []
        if not evs:
            continue
        ev = evs[0]
        rng = ev.get("range", {})
        line = rng.get("start_line", 1)
        col = rng.get("start_character", 0) + 1  # quickfix columns are 1-based
        claim = " ".join(f.get("claim", "").split())
        lines.append(f"{ev['path']}:{line}:{col}: [{f['severity'].upper()}] {claim}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="Atlas quickfix: findings → Vim quickfix file")
    ap.add_argument("artifact", help="review artifact JSON path")
    ap.add_argument("--out", default="-", help="output path (default stdout)")
    args = ap.parse_args()

    try:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read artifact: {e}", file=sys.stderr)
        return 1

    lines = quickfix_lines(artifact)
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"quickfix written: {args.out} ({len(lines)} finding(s))", file=sys.stderr)
    if not lines:
        print("no findings with evidence: quickfix is empty", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
