# Atlas

Agent skills for codebase onboarding and diff review. An agent investigates a
repository slice, produces a schema-validated JSON artifact, and renders it
into a self-contained HTML viewer: architecture and call graphs with data
flow, execution flows, code with go-to-definition and backlinks, lessons,
and review findings. No IDE, no server. Open the HTML in a browser.

## Skills

| Skill | What it does |
|---|---|
| `atlas-onboard` | Analyze a subsystem and build an onboarding artifact. Supports incremental updates when the code moves. |
| `atlas-review` | Review a diff against a base revision: evidence-backed findings, a 90-second Review Brief, and a Vim quickfix file. Requires `atlas-onboard`. |

## Install

```bash
# interactive: pick skills and agents from a prompt
npx skills add hongzio/atlas

# non-interactive: everything, no prompts
npx skills add hongzio/atlas --all
```

`atlas-review` uses the scripts, schema, and viewer template that ship with
`atlas-onboard`, so install both. Default scope is the current project
(`./.claude/skills/`); add `-g` for a global install.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) — the only real prerequisite. Scripts
  are PEP 723 standalone; uv fetches Python and dependencies on first run
  (network needed once).
- `git` — optional for onboarding (degrades to `unversioned`), required for
  review (`--base` diff).
- Language servers — optional. With one on PATH, call/reference resolution
  uses the server; without, it falls back to generic name matching:

  | Language | Servers probed |
  |---|---|
  | Python | `pyright-langserver`, `basedpyright-langserver`, `jedi-language-server`, `pylsp` |
  | TypeScript / JavaScript | `typescript-language-server` (+ `typescript`) |
  | Go | `gopls` |

## Repository layout

```
skills/
  atlas-onboard/   SKILL.md, scripts/, schemas/, templates/
  atlas-review/    SKILL.md, scripts/atlas_quickfix.py
tests/             pytest suite + sample_repo (py) / sample_ts_repo / sample_go_repo
fixtures/          sample learning and review artifacts (Phase 0 exit)
PRD.md             product requirements (Korean)
```

## Development

```bash
uv run pytest                     # LSP-tier tests skip unless a server is on PATH
uv run skills/atlas-onboard/scripts/atlas_render.py \
  fixtures/sample_onboard_artifact.json --out out/orders.html
```
