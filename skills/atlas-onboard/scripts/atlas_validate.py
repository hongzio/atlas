#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["jsonschema>=4.21"]
# ///
"""Atlas validate script: validate artifact JSON against schema + existence.

Passing schema validation is not enough — evidence pointers that do not exist in
the index also fail (PRD §12.3: what natural language cannot guarantee, scripts enforce).

    uv run scripts/atlas_validate.py artifact.json

On failure, exits 1 with concrete errors — the agent reads them, fixes the JSON, retries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "artifact.schema.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def schema_errors(artifact: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema())
    errors = []
    for err in sorted(validator.iter_errors(artifact), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"schema: {loc}: {err.message}")
    return errors


def _iter_evidence(artifact: dict):
    """Iterate every evidence pointer in the artifact as (location description, pointer)."""

    def from_list(where: str, items):
        for i, ev in enumerate(items or []):
            yield f"{where}[{i}]", ev

    for fi, flow in enumerate(artifact.get("flows", [])):
        for si, step in enumerate(flow.get("steps", [])):
            yield from from_list(f"flows[{fi}].steps[{si}].evidence", step.get("evidence"))
            for bi, br in enumerate(step.get("branches", [])):
                yield from from_list(
                    f"flows[{fi}].steps[{si}].branches[{bi}].evidence", br.get("evidence")
                )
    for ci, c in enumerate(artifact.get("concepts", [])):
        yield from from_list(f"concepts[{ci}].evidence", c.get("evidence"))
    for ii, inv in enumerate(artifact.get("invariants", [])):
        yield from from_list(f"invariants[{ii}].evidence", inv.get("evidence"))
    for fi, f in enumerate(artifact.get("findings", [])):
        yield from from_list(f"findings[{fi}].evidence", f.get("evidence"))
    lessons = artifact.get("lessons", {})
    for pi, p in enumerate(lessons.get("predict", [])):
        yield from from_list(f"lessons.predict[{pi}].evidence", p.get("evidence"))
    for li, l in enumerate(lessons.get("localization", [])):
        yield from from_list(f"lessons.localization[{li}].evidence", l.get("evidence"))


def integrity_errors(artifact: dict) -> list[str]:
    errors: list[str] = []
    index = artifact.get("index", {})
    files = {f["path"]: f for f in index.get("files", [])}
    line_counts = {p: f["source"].count("\n") + 1 for p, f in files.items()}
    symbols = {s["symbol_id"]: s for s in index.get("symbols", [])}

    # index internal consistency
    for s in symbols.values():
        if s.get("in_slice") and s["path"] not in files:
            errors.append(f"index: in_slice symbol {s['symbol_id']} has no source file {s['path']}")
        parent = s.get("parent")
        if parent and parent not in symbols:
            errors.append(f"index: symbol {s['symbol_id']} parent {parent} not in symbols")
    for i, e in enumerate(index.get("edges", [])):
        for end in ("from", "to"):
            if e[end] not in symbols:
                errors.append(f"index: edges[{i}].{end} {e[end]} not in symbols")
    for i, r in enumerate(index.get("references", [])):
        if r["path"] not in files:
            errors.append(f"index: references[{i}].path {r['path']} not in files")
        if r["symbol_id"] not in symbols:
            errors.append(f"index: references[{i}].symbol_id {r['symbol_id']} not in symbols")

    # evidence pointer existence
    for where, ev in _iter_evidence(artifact):
        path = ev.get("path")
        rng = ev.get("range", {})
        sid = ev.get("symbol_id")
        if sid is not None and sid not in symbols:
            errors.append(f"{where}: symbol_id {sid} not in index.symbols")
        if path not in files:
            if sid is not None and sid in symbols and not symbols[sid].get("in_slice"):
                pass  # a boundary stub's source file is outside the slice — missing path allowed
            else:
                errors.append(f"{where}: path {path} not in index.files")
            continue
        maxline = line_counts[path]
        if rng.get("start_line", 1) > maxline or rng.get("end_line", 1) > maxline:
            errors.append(
                f"{where}: range L{rng.get('start_line')}-L{rng.get('end_line')} "
                f"out of bounds for {path} ({maxline} lines)"
            )

    # stale_items in changes must point at real ids
    changes = artifact.get("changes")
    if changes:
        known_ids = (
            {f["id"] for f in artifact.get("flows", [])}
            | {c["id"] for c in artifact.get("concepts", [])}
            | {i["id"] for i in artifact.get("invariants", [])}
        )
        for sid in changes.get("stale_items", []):
            if sid not in known_ids:
                errors.append(f"changes.stale_items: unknown id {sid}")

    return errors


def validate(artifact: dict) -> list[str]:
    errors = schema_errors(artifact)
    if errors:
        return errors  # integrity checks are meaningless if the schema is broken
    return integrity_errors(artifact)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: atlas_validate.py <artifact.json>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read artifact: {e}", file=sys.stderr)
        return 1
    errors = validate(artifact)
    if errors:
        print(f"INVALID: {len(errors)} error(s)", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"valid: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
