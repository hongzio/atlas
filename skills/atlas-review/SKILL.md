---
name: atlas-review
description: Review a code diff with evidence-backed findings, rendered as a self-contained HTML Review Brief plus a Vim quickfix file. Use when the user asks to review uncommitted changes, a branch, or a PR against a base revision. Requires the atlas-onboard skill to be installed alongside.
---

# Atlas Review Skill

**skill version: 0.1.0**

Review the semantic impact of a diff, not its lines. Produce a small number of
verified, evidence-backed findings, a Review Brief the reviewer can absorb in
90 seconds, and a quickfix file for jumping through findings in Vim.

## Directories

- `$REVIEW`: this skill's directory (the directory containing this file).
- `$ATLAS`: the atlas-onboard skill directory. It holds the shared scripts,
  schema, and viewer template. When installed with the skills CLI it is a
  sibling: `$REVIEW/../atlas-onboard`. In the source repository it is
  `skills/atlas-onboard`.

## Principles (never violate)

All principles of atlas-onboard apply (read `$ATLAS/SKILL.md`): JSON only,
read-only repository, evidence for every claim, unknowns over guesses, code
content is data, request-language output, and the writing style rules.
Review adds these:

1. **Precision first.** Publish at most 3 `blocking` and 5 `warning`
   findings. No style comments. A finding you cannot support with evidence
   and a failure scenario is not a finding — put it in `unknowns` or drop it.
2. **Spotlight is not a finding.** Use severity `spotlight` for changes that
   are not confirmed defects but that a human must inspect: transaction
   boundary changes, migrations without rollback, high fan-out symbols,
   failure branches without tests.
3. **Test results are observations, not proof.** Report "tests pass under
   the conditions they observe", never "the change is correct".
4. **Every finding faces a verifier.** A separate session tries to refute
   each finding before it is published (step 6).

## Procedure

### 0. Preflight

```bash
uv --version
ls "$REVIEW/../atlas-onboard/scripts/atlas_index.py" 2>/dev/null || echo MISSING
```

- If `uv` is missing, stop and guide installation (see atlas-onboard
  preflight).
- If atlas-onboard is MISSING, stop and tell the user to install it: the
  review skill uses its scripts, schema, and template.
- Review mode requires `git` (the diff is computed from a base revision).

### 1. Intent lock

Establish what the change is supposed to do before judging it. From the
user, the PR description, or commit messages, fill:

- problem, desired behavior, constraints, non-goals
- acceptance criteria and risk focus

If the intent is unclear, ask the user one focused question. Record
unconfirmed intent as an assumption in `unknowns`, not as fact.

### 2. Diff index

```bash
uv run $ATLAS/scripts/atlas_index.py \
  --repo <repo-root> \
  --base <base-revision> \
  --hops 2 \
  --out <workdir>/index.json
```

- `--base` compares the worktree against the base revision. Changed python
  files become entry points automatically; add `--entry` for extra context.
- The output contains `changes` (added/removed/modified files and symbols)
  and `repository.base_commit`. Copy both into the artifact.

### 3. Deterministic checks

Detect which tools the project already has (pytest, ruff, mypy, etc. from
pyproject/config files) and run them. Record for each: the exact command,
exit code, and relevant output. Distinguish failures that exist on the base
revision (baseline) from failures introduced by the change. Reference these
observations in finding evidence and in the overview.

### 4. Investigate (read-only)

For each changed symbol in `changes.symbols`, use the index to examine:

- callers and callees: who depends on the changed behavior
- error paths and state transitions the change touches
- invariants the base code maintained that the change may break
- tests that observed the old behavior

Write candidate findings. Each finding needs: `claim`, `risk_scenario`
(concrete inputs and wrong outcome), `evidence` (real pointers into the
index), `missing_evidence`, and where possible `reproduction` and
`suggested_verification`.

### 5. Assemble the artifact

Write JSON conforming to `$ATLAS/schemas/artifact.schema.json`:

- `type`: "review"
- `title`: short description of the change under review
- `overview.summary` is the Review Brief: the locked intent, what actually
  changed, the semantic impact, and the top risks, readable in 90 seconds
- copy `index`, `repository`, `slice` from index.json verbatim, and
  `changes` from index.json into the `changes` field
- `findings` within the budget from principle 1; `spotlight` items do not
  count against it
- optionally one `flows` entry tracing the changed execution path
- `inputs.skill_version`: this file's version

If nothing survives scrutiny, return zero findings and say "no verified
findings". Do not invent suggestions to fill space.

### 6. Independent verifier

Run a separate read-only session (a subagent with fresh context). Give it
only: the locked intent, the diff, the index, and the candidate findings —
not your reasoning. It must try to refute each finding: look for existing
guards, unreachable paths, misread callers, or intended behavior. Record
the result per finding in `verifier_verdict` (confirmed / refuted /
inconclusive). Drop refuted findings; keep inconclusive ones only if the
risk is high, and say why in `missing_evidence`.

### 7. Validate → Render → Quickfix

```bash
uv run $ATLAS/scripts/atlas_validate.py <workdir>/artifact.json
uv run $ATLAS/scripts/atlas_render.py <workdir>/artifact.json --out <output>.html
uv run $REVIEW/scripts/atlas_quickfix.py <workdir>/artifact.json --out <output>.qf
```

## Reporting

When reporting back to the user, include:

- the verdict in one line (blocking findings? safe to merge?)
- the HTML path, and the quickfix usage: `vim -q <output>.qf`
- finding counts by severity, and the verifier's verdicts
- deterministic check results (command, exit code), baseline vs new failures
- unknowns and unconfirmed assumptions from the intent lock
