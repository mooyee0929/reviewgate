---
name: reviewgate
description: Multi-agent code review of the current branch or a diff. Runs the reviewgate history CLI, fans out to the history / intent / correctness / security / tests reviewer agents in parallel, then hands every candidate to the verifier agent, and reports only what survived. Use when the user says "review this branch", "review before push", "跑 reviewgate", "幫我 review 這個 diff", or invokes /reviewgate. Accepts an optional base ref (default origin/HEAD), `--staged`, `--working-tree`, or `--quick` (history + correctness only, no verifier).
---

# /reviewgate

You are the orchestrator. Agents cannot spawn agents, so the fan-out and the
merge happen here. Never edit code during a review.

## 1. Scope

- Base: the argument if given, else `git rev-parse --abbrev-ref origin/HEAD`
  (strip `origin/` only for display). `--staged` / `--working-tree` switch the
  scope; tell every agent which one applies.
- If `git diff <base>...HEAD --stat` is empty and the working tree is clean,
  stop: "nothing to review".
- Skip files matching lockfiles, vendor, generated, binaries; say so in the
  coverage line at the end.

## 2. Deterministic pass (seconds, no LLM)

Run once and keep the output:

```
reviewgate history --base <base> --json --auto-precedents 3   # or --staged / --working-tree
```

If `reviewgate` is not on PATH: `uv tool install --editable ~/dev/reviewgate`,
then retry once. From the JSON keep `context` (per-file one-liners) and
`findings` (regression-risk, co-change-missing).

Also run whatever linters the repo already configures (`uv run ruff check`,
`uv run mypy`, `npm run lint`) on the changed files only; their output is a
finding list too (source `tool`) and tells the reviewers what not to repeat.

## 3. Fan out (one message, parallel Agent calls)

Spawn these with `subagent_type` set to the agent name. Give each the same
preamble: repo path, scope, base, the `context` lines for the files it will
see, and "do not repeat these tool findings: <list>".

| agent | extra input | when |
|---|---|---|
| history-reviewer | none (it reruns the CLI itself) | always |
| intent-reviewer | PR title/body if the user gave one | always |
| correctness-reviewer | context lines | always |
| security-reviewer | context lines | skip only if `--quick` or the diff touches no code |
| tests-reviewer | context lines | skip if `--quick` |

`--quick` = history-reviewer + correctness-reviewer, no step 4.

## 4. Verify

Collect every ```json block from the reviewers. Build the candidate list:
- reviewer findings (file, line, severity, dimension, category, title,
  scenario, fix, confidence, origin=<agent name>)
- history findings the history-reviewer marked confirmed or unverified
- intent `claims` with `checkable: true`, plus risky `scope_creep`

Dedupe by (file, line ±2, category); keep the higher severity, merge origins.
Then spawn **verifier** once with the whole list and the intent JSON.
If there are more than 12 candidates, spawn one verifier per 6 candidates in
parallel, each with the full intent JSON.

## 5. Merge and rank

- Drop `disproved`; count them.
- `confirmed` first, by severity then file.
- `unverified` next, only if confidence ≥ 0.7 or severity ≥ high; the rest
  go to a one-line "also noted" count.
- Apply `[claim not met]` notes from the verifier; a `missing` claim is
  itself a finding (dimension intent, severity medium).
- At most 5 low findings; say how many were cut.

## 6. Report

```
## reviewgate: <base>..<head>   <N> file(s), <M> candidate(s) → <C> confirmed, <D> disproved, <U> unverified

### Blocking (high+)
- <severity> <file:line> — <title>. <scenario>. Evidence: <cmd → exit / file:line>. Fix: <fix>. (<origins>)

### Should fix
...

### Intent
- <claim> — done | missing (<evidence>)
- scope creep: <file:line> <why>

### Context
- hot spots and co-change notes worth knowing, max 5 lines

Coverage: <files reviewed>/<files in diff>; skipped: <patterns>; agents: <list>; sandbox: <yes/no>.
```

Verdict line at the very end: `BLOCK` if any confirmed high+, `WARN` if any
confirmed medium or unverified high+, else `OK`. The user decides what to do
with it; this skill never pushes, commits, or edits.
