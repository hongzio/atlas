#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["jsonschema>=4.21"]
# ///
"""Atlas render script: artifact JSON → self-contained HTML.

Rendering gates (PRD §12.3):
  1. refuse to render when schema + integrity validation fails
  2. enforce secret redaction before rendering
  3. size budget check — fail/warn loudly, never silently (PRD §8.3)

    uv run scripts/atlas_render.py artifact.json --out artifact.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas_validate import validate  # noqa: E402

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "viewer.html"

TARGET_HTML_BYTES = 5 * 1024 * 1024
HARD_LIMIT_HTML_BYTES = 15 * 1024 * 1024

# patterns where the value itself looks like a secret
VALUE_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:pk|sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    ),
]

# key = "value" assignments (for source code text). Only the value part is
# replaced to preserve line structure — line-based ranges stay valid.
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b([A-Za-z_]*(?:password|passwd|secret|api_key|apikey|access_key|auth_token|token|credential)[A-Za-z_]*)"
    r"(\s*[:=]\s*)(['\"])([^'\"\n]{6,})(\3)"
)

# dict keys whose name implies a secret (values inside the artifact structure)
SECRET_KEY_PATTERN = re.compile(
    r"(?i)^(?:.*(password|passwd|secret|api_key|apikey|access_key|auth_token|credential).*)$"
)

REDACTED = "[REDACTED]"


def redact_text(text: str) -> tuple[str, int]:
    count = 0

    def sub_assignment(m: re.Match) -> str:
        nonlocal count
        count += 1
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}"

    text = ASSIGNMENT_PATTERN.sub(sub_assignment, text)
    for pat in VALUE_PATTERNS:
        text, n = pat.subn(REDACTED, text)
        count += n
    return text, count


def redact_artifact(obj, key_hint: str | None = None) -> tuple[object, int]:
    """Walk the whole artifact and redact every string value."""
    total = 0
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            red, n = redact_artifact(v, key_hint=k)
            out[k] = red
            total += n
        return out, total
    if isinstance(obj, list):
        out_list = []
        for v in obj:
            red, n = redact_artifact(v, key_hint=key_hint)
            out_list.append(red)
            total += n
        return out_list, total
    if isinstance(obj, str):
        if (
            key_hint
            and SECRET_KEY_PATTERN.match(key_hint)
            and key_hint not in ("content_hash", "sha256")
        ):
            return REDACTED, 1
        red, n = redact_text(obj)
        return red, n
    return obj, 0


def template_version(template: str) -> str:
    m = re.search(r"atlas-template-version:\s*([0-9.]+)", template)
    return m.group(1) if m else "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="Atlas render: artifact JSON → self-contained HTML")
    ap.add_argument("artifact", help="artifact JSON path")
    ap.add_argument("--out", required=True, help="output HTML path")
    ap.add_argument("--template", default=str(TEMPLATE_PATH))
    args = ap.parse_args()

    try:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read artifact: {e}", file=sys.stderr)
        return 1

    errors = validate(artifact)
    if errors:
        print(f"render refused: artifact invalid ({len(errors)} error(s))", file=sys.stderr)
        for e in errors[:30]:
            print(f"  - {e}", file=sys.stderr)
        if len(errors) > 30:
            print(f"  ... and {len(errors) - 30} more", file=sys.stderr)
        return 1

    template = Path(args.template).read_text(encoding="utf-8")

    artifact, redactions = redact_artifact(artifact)
    artifact["inputs"]["template_version"] = template_version(template)

    payload = json.dumps(artifact, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # prevent </script> breakout

    html = template.replace("__ATLAS_TITLE__", artifact.get("title", "Atlas"))
    if "__ATLAS_DATA__" not in html:
        print("error: template has no __ATLAS_DATA__ placeholder", file=sys.stderr)
        return 1
    html = html.replace("__ATLAS_DATA__", payload)

    size = len(html.encode("utf-8"))
    if size > HARD_LIMIT_HTML_BYTES:
        print(
            f"render refused: {size} bytes > hard limit {HARD_LIMIT_HTML_BYTES}. "
            "narrow the slice and regenerate (reduce --hops or adjust entries)",
            file=sys.stderr,
        )
        return 1
    if size > TARGET_HTML_BYTES:
        print(
            f"warn: {size} bytes > target {TARGET_HTML_BYTES}. consider narrowing the slice",
            file=sys.stderr,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(
        f"rendered: {out} ({size} bytes, redactions={redactions}, "
        f"template={artifact['inputs']['template_version']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
