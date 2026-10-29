# reviewgate

Code review gate. Two pieces ship today:

- the **history agent**, a standalone CLI that turns git history into review
  priors for a diff (no LLM), and
- a **multi-agent review** for Claude Code: five read-only reviewer agents run
  in parallel, a verifier tries to confirm or refute every candidate in a
  throwaway worktree, and the `/reviewgate` skill orchestrates the run and
  reports only what survived.

## History agent

Git-derived priors for a diff. No LLM, no network, no dependencies beyond
`git` and Python 3.11+. It answers four questions a human reviewer would ask
before reading a single line:

| Question | Query | Output |
|---|---|---|
| Is this a hot spot? | `git log -L <range>` over the last N days | `changed 4x in 90d; last: abc1234 "fix race in flush()"` per hunk |
| Are we undoing a fix? | `git blame` on removed lines, then the origin commit's message | `regression-risk` finding (MEDIUM) when the message says fix / race / leak / revert / ... |
| Did we forget a partner file? | co-change table over the last 500 commits, cached in `.review/cochange.json` | `co-change-missing` finding (LOW) when a file that usually changes with this one is absent |
| Has this identifier been fought over before? | `git log -S<ident>` from the diff base | up to 3 precedent commits |

Findings carry evidence a judge can re-check (commit sha, `file:line`, pair
counts) and a fingerprint that survives line-number shifts.

### Install

```
git clone https://github.com/mooyee0929/reviewgate ~/dev/reviewgate
uv tool install --editable ~/dev/reviewgate
```

or run inside the repo with `uv run reviewgate ...`. The `/reviewgate` skill
expects the clone at `~/dev/reviewgate`.

### Use

```
reviewgate history                         # origin's default branch ... HEAD
reviewgate history --base main
reviewgate history --staged
reviewgate history --working-tree
reviewgate history --json                  # machine readable
reviewgate history --markdown              # sticky PR comment body
reviewgate history --precedent flush_cache --auto-precedents 3
reviewgate history --days 30 --window 1000 --min-together 4 --min-ratio 0.6
reviewgate history --max-hunks 80              # hunks analyzed (default 40)
reviewgate history --no-cache --no-cochange
reviewgate history --json-out history.json     # also write JSON to a file
reviewgate -C ../other-repo history            # run as if started in another dir
```

Defaults: `--days 90`, `--window 500`, `--min-together 3`, `--min-ratio 0.5`.

Exit codes: 0 ran, 2 usage (not a repo, unknown ref), 3 git failed. The
history agent never blocks; its findings are context for a gate, not a gate.

### As a library

```python
from pathlib import Path
from reviewgate.git import DiffSpec
from reviewgate.history.agent import HistoryOptions, analyze

report = analyze(Path("."), DiffSpec(base="origin/main"), HistoryOptions(days=60))
report.context_lines()   # {file: [one-liners for a reviewer prompt]}
report.findings          # list[Finding]
report.to_dict()         # JSON-ready
```

`analyze_files()` takes already-parsed `ChangedFile`s, which is how a pipeline
stage will call it.

### As Claude Code agents

`.claude/agents/` holds the review roles, one agent per file, each read-only
and each owning one kind of evidence:

| agent | evidence | what it produces |
|---|---|---|
| `history-reviewer` | commits, blame, co-change | verified regression risk, partner files, hot-spot reading |
| `intent-reviewer` | commit messages vs diff | claims done / missing, scope creep, description drift |
| `correctness-reviewer` | inference | hypotheses with a concrete failure scenario |
| `security-reviewer` | inference | same, security checklist |
| `tests-reviewer` | inference | test gaps, weakened or ineffective tests |
| `verifier` | execution in a throwaway worktree | confirmed / disproved / unverified per candidate |

`.claude/skills/reviewgate/SKILL.md` is the orchestrator (`/reviewgate`):
it runs the history CLI, fans out to the reviewers in parallel, hands the
candidates to the verifier and reports only what survived, ending with
`BLOCK` / `WARN` / `OK`. It takes an optional base ref (default
`origin/HEAD`), `--staged`, `--working-tree`, or `--quick` (history and
correctness reviewers only, no verifier).

Install everything globally so any repo can use it:

```
uv tool install --editable ~/dev/reviewgate
cp ~/dev/reviewgate/.claude/agents/*.md ~/.claude/agents/
cp -r ~/dev/reviewgate/.claude/skills/reviewgate ~/.claude/skills/
```

New agent and skill files are picked up when a Claude Code session starts.

### Co-change cache

`.review/cochange.json` holds per-file and per-pair commit counts plus the sha
it was scanned to. Later runs add only `scanned_to..HEAD`; a rebase, squash or
changed `--window` triggers a rebuild. Commits touching more than 30 files are
ignored as mass edits. Add `.review/` to `.gitignore`.

## Layout

```
src/reviewgate/
  cli.py              `reviewgate` entry point, exit codes
  git.py              repo discovery, base resolution, diff text, raw history queries
  diff.py             unified diff -> changed files with added / removed lines
  model.py            Finding, Severity and fingerprints shared by every stage
  history/
    agent.py          diff -> hunks, co-change gaps, precedents -> HistoryReport
    lines.py          hot spots (git log -L) and regression risk (git blame)
    cochange.py       co-change table and its .review/ cache
    precedent.py      git log -S lookups and identifier selection
    report.py         terminal, markdown and JSON rendering
    types.py          history data types
.claude/agents/       reviewer and verifier agents
.claude/skills/reviewgate/SKILL.md   /reviewgate orchestrator
tests/                pytest suite on real temporary git repos
```

## Development

```
uv sync
uv run pytest
uv run mypy --strict src
uv run ruff check && uv run ruff format --check
```

Tests build real temporary git repositories; nothing is mocked except where a
call count is asserted.
