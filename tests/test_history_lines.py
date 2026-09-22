from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import commit_file, commit_files, git

from reviewgate.diff import ChangedFile, parse_diff
from reviewgate.git import CommitDetail, DiffSpec, diff_text, resolve_old_rev
from reviewgate.history import lines as lines_mod
from reviewgate.history.lines import (
    file_histories,
    hunk_history,
    is_test_path,
    match_keywords,
    regression_findings,
)
from reviewgate.model import Severity

INSIDE = "2026-09-10T00:00:00"
OUTSIDE = "2025-01-01T00:00:00"

BASE = "def add(a, b):\n    return a + b\n"


def _branch_diff(repo: Path, context: int = 3) -> tuple[list[ChangedFile], str]:
    spec = DiffSpec(base="origin/main")
    return parse_diff(diff_text(repo, spec, context=context)), resolve_old_rev(repo, spec)


def _reset_main(repo: Path) -> None:
    git(repo, "push", "-q", "origin", "feature:main")
    git(repo, "fetch", "-q", "origin")


def test_hot_spot_counts_only_recent_commits(repo: Path) -> None:
    for i, date in enumerate((OUTSIDE, INSIDE, INSIDE, INSIDE)):
        commit_file(
            repo, "app.py", f"def add(a, b):\n    return a + b + {i}\n", f"tweak {i}", date=date
        )
    _reset_main(repo)
    commit_file(repo, "app.py", "def add(a, b):\n    return a - b\n", "flip")
    files, old_rev = _branch_diff(repo)
    hunks, notes = file_histories(repo, old_rev, files)
    assert notes == []
    assert len(hunks) == 1
    h = hunks[0]
    assert h.change_count == 3
    assert h.is_hot
    assert h.context_line().startswith("changed 3x in 90d; last:")
    assert h.context_line().endswith('"tweak 3"')


def test_regression_risk_finding_and_fingerprint_stability(repo: Path) -> None:
    fixed = BASE + "    flush_lock.acquire()\n"
    commit_file(repo, "app.py", fixed, "fix race in flush", date=INSIDE)
    _reset_main(repo)
    commit_file(repo, "app.py", BASE, "drop lock")
    files, old_rev = _branch_diff(repo)
    hunks, _ = file_histories(repo, old_rev, files)
    assert len(hunks) == 1
    origins = hunks[0].origins
    assert len(origins) == 1
    assert origins[0].text == "    flush_lock.acquire()"
    assert origins[0].regression_keywords == ("fix", "race")
    assert origins[0].commit.subject == "fix race in flush"

    findings = regression_findings(hunks)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.MEDIUM
    assert f.category == "regression-risk"
    assert f.dimension == "history"
    assert f.source == "history"
    short = origins[0].commit.short
    assert any(e.kind == "commit" and e.ref == short for e in f.evidence)
    assert any(e.kind == "line" and e.ref == "app.py:3" for e in f.evidence)
    assert f.fix is not None and short in f.fix

    commit_file(repo, "app.py", "import os\n\n" + BASE, "insert above")
    files2, old_rev2 = _branch_diff(repo)
    hunks2, _ = file_histories(repo, old_rev2, files2)
    findings2 = regression_findings(hunks2)
    assert len(findings2) == 1
    assert findings2[0].fingerprint == f.fingerprint
    assert findings2[0].line != f.line or findings2[0].end_line != f.end_line


def test_tests_in_origin_commit_flow_into_fix(repo: Path) -> None:
    commit_files(
        repo,
        {"app.py": BASE + "    guard()\n", "tests/test_flush.py": "def test_flush():\n    pass\n"},
        "fix leak in flush",
        date=INSIDE,
    )
    _reset_main(repo)
    commit_file(repo, "app.py", BASE, "remove guard")
    files, old_rev = _branch_diff(repo)
    hunks, _ = file_histories(repo, old_rev, files)
    origin = hunks[0].origins[0]
    assert origin.tests_in_commit == ("tests/test_flush.py",)
    findings = regression_findings(hunks)
    assert findings[0].fix == "run tests/test_flush.py"


def test_pure_insert_and_added_file(repo: Path) -> None:
    commit_file(repo, "app.py", BASE, "touch", date=INSIDE)
    _reset_main(repo)
    commit_files(
        repo,
        {"app.py": BASE + "    # trailing\n", "new.py": "x = 1\n"},
        "append and add",
    )
    files, old_rev = _branch_diff(repo, context=0)
    assert {f.path for f in files} == {"app.py", "new.py"}
    hunks, notes = file_histories(repo, old_rev, files)
    assert notes == []
    assert [h.file for h in hunks] == ["app.py"]
    h = hunks[0]
    assert h.origins == ()
    assert h.old_start == 2 and h.old_end == 1
    assert [c.subject for c in h.commits] == ["init"]
    assert regression_findings(hunks) == []


def test_match_keywords_whole_words_in_canonical_order() -> None:
    assert match_keywords("Fix NPE in parser; revert later") == ("fix", "revert", "npe")
    assert match_keywords("add prefix handling") == ()
    assert match_keywords("") == ()


def test_is_test_path() -> None:
    assert is_test_path("tests/test_flush.py")
    assert is_test_path("pkg/sub/foo_test.go")
    assert is_test_path("src/App.test.tsx")
    assert not is_test_path("src/app.py")
    assert not is_test_path("contest.py")


def test_max_hunks_note(repo: Path) -> None:
    body = "".join(f"line{i}\n" for i in range(40))
    commit_file(repo, "big.txt", body, "big", date=INSIDE)
    _reset_main(repo)
    edited = "".join((f"LINE{i}\n" if i % 10 == 0 else f"line{i}\n") for i in range(40))
    commit_file(repo, "big.txt", edited, "edit every tenth")
    files, old_rev = _branch_diff(repo)
    assert sum(len(f.hunks) for f in files) == 4
    hunks, notes = file_histories(repo, old_rev, files, max_hunks=2)
    assert len(hunks) == 2
    assert notes == ["history: skipped 2 hunk(s) beyond max_hunks=2"]


def test_detail_cache_fetches_each_sha_once(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "".join(f"line{i}\n" for i in range(30))
    commit_file(repo, "big.txt", body, "fix crash in loader", date=INSIDE)
    _reset_main(repo)
    edited = "".join(f"line{i}\n" for i in range(30) if i not in (5, 25))
    commit_file(repo, "big.txt", edited, "remove two far-apart lines")
    files, old_rev = _branch_diff(repo)
    assert len(files[0].hunks) == 2

    calls: list[str] = []
    real: Callable[[Path, str], CommitDetail | None] = lines_mod.commit_detail

    def counting(root: Path, sha: str) -> CommitDetail | None:
        calls.append(sha)
        return real(root, sha)

    monkeypatch.setattr(lines_mod, "commit_detail", counting)
    hunks, _ = file_histories(repo, old_rev, files)
    assert len(hunks) == 2
    assert all(len(h.origins) == 1 for h in hunks)
    assert len(calls) == 1
    findings = regression_findings(hunks)
    assert len(findings) == 2
    assert {f.line_text for f in findings} == {"line5", "line25"}

    calls.clear()
    per_hunk = [hunk_history(repo, old_rev, files[0], h) for h in files[0].hunks]
    assert len(per_hunk) == 2
    assert len(calls) == 2
