# reviewgate

Code review gate. The full pipeline (deterministic checks, parallel LLM
reviewers, verify, adversarial) is planned; what ships today is the first
standalone stage, the **history agent**.

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
uv tool install --editable ~/dev/reviewgate
```

or run inside the repo with `uv run reviewgate ...`.

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
reviewgate history --no-cache --no-cochange
```

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

### Co-change cache

`.review/cochange.json` holds per-file and per-pair commit counts plus the sha
it was scanned to. Later runs add only `scanned_to..HEAD`; a rebase, squash or
changed `--window` triggers a rebuild. Commits touching more than 30 files are
ignored as mass edits. Add `.review/` to `.gitignore`.

## Development

```
uv sync
uv run pytest
uv run mypy --strict src
uv run ruff check && uv run ruff format --check
```

Tests build real temporary git repositories; nothing is mocked except where a
call count is asserted.
