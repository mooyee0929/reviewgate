"""Hot spots and regression risk for each hunk, from `git log -L` and `git blame`."""

from __future__ import annotations

import re
from pathlib import Path

from ..diff import ChangedFile, Hunk, glob_match
from ..git import CommitDetail, CommitRef, blame_lines, commit_detail, line_count_at, line_history
from ..model import Evidence, Finding, Severity, fingerprint
from .types import HunkHistory, LineOrigin

REGRESSION_KEYWORDS: tuple[str, ...] = (
    "fix",
    "bug",
    "race",
    "crash",
    "leak",
    "security",
    "regression",
    "revert",
    "hotfix",
    "cve",
    "deadlock",
    "overflow",
    "nullptr",
    "npe",
)

TEST_PATH_PATTERNS: tuple[str, ...] = (
    "test_*.py",
    "**/test_*.py",
    "*_test.py",
    "**/*_test.py",
    "tests/**",
    "test/**",
    "**/tests/**",
    "**/test/**",
    "*.test.*",
    "**/*.test.*",
    "*.spec.*",
    "**/*.spec.*",
    "*_test.go",
    "**/*_test.go",
    "*Test.java",
    "**/*Test.java",
    "*Tests.swift",
    "**/*Tests.swift",
)

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in REGRESSION_KEYWORDS) + r")\b", re.IGNORECASE
)

_MAX_LINE_EVIDENCE = 3


def match_keywords(text: str) -> tuple[str, ...]:
    """Regression keywords present in `text` as whole words, in
    `REGRESSION_KEYWORDS` order, each at most once."""
    hits = {m.group(1).lower() for m in _KEYWORD_RE.finditer(text)}
    return tuple(k for k in REGRESSION_KEYWORDS if k in hits)


def is_test_path(path: str) -> bool:
    return any(glob_match(path, pattern) for pattern in TEST_PATH_PATTERNS)


def _old_range(root: Path, old_rev: str, path: str, hunk: Hunk) -> tuple[int, int] | None:
    if hunk.old_count > 0:
        return hunk.old_start, hunk.old_end
    line_count = line_count_at(root, old_rev, path)
    if line_count is None or line_count == 0:
        return None
    start = max(1, hunk.old_start)
    end = min(hunk.old_start + 1, line_count)
    if end < start:
        return None
    return start, end


def _origins(
    root: Path,
    old_rev: str,
    path: str,
    hunk: Hunk,
    detail_cache: dict[str, CommitDetail | None],
) -> tuple[LineOrigin, ...]:
    if not hunk.removed:
        return ()
    first, last = hunk.removed[0].number, hunk.removed[-1].number
    blame = blame_lines(root, old_rev, path, first, last)
    origins: list[LineOrigin] = []
    for removed in hunk.removed:
        sha = blame.get(removed.number)
        if sha is None:
            continue
        if sha not in detail_cache:
            detail_cache[sha] = commit_detail(root, sha)
        detail = detail_cache[sha]
        if detail is None:
            continue
        origins.append(
            LineOrigin(
                old_line=removed.number,
                text=removed.text,
                commit=CommitRef(sha=sha, date=detail.date, subject=detail.subject),
                regression_keywords=match_keywords(f"{detail.subject}\n{detail.body}"),
                tests_in_commit=tuple(f for f in detail.files if is_test_path(f)),
            )
        )
    return tuple(origins)


def hunk_history(
    root: Path,
    old_rev: str,
    file: ChangedFile,
    hunk: Hunk,
    *,
    days: int = 90,
    limit: int = 20,
    detail_cache: dict[str, CommitDetail | None] | None = None,
) -> HunkHistory:
    cache: dict[str, CommitDetail | None] = {} if detail_cache is None else detail_cache
    path = file.source_path
    old_range = _old_range(root, old_rev, path, hunk)
    commits: list[CommitRef] = []
    if old_range is not None:
        commits = line_history(root, old_rev, path, *old_range, days=days, limit=limit)
    deleted = file.status == "deleted"
    return HunkHistory(
        file=file.path,
        new_start=0 if deleted else hunk.new_start,
        new_end=0 if deleted else hunk.new_end,
        old_start=hunk.old_start,
        old_end=hunk.old_end,
        days=days,
        commits=tuple(commits),
        origins=_origins(root, old_rev, path, hunk, cache),
    )


def file_histories(
    root: Path,
    old_rev: str,
    files: list[ChangedFile],
    *,
    days: int = 90,
    limit: int = 20,
    max_hunks: int = 40,
) -> tuple[list[HunkHistory], list[str]]:
    """Histories for every hunk that has an old side, in diff order, capped at
    `max_hunks`. Returns (histories, notes)."""
    cache: dict[str, CommitDetail | None] = {}
    histories: list[HunkHistory] = []
    skipped = 0
    for file in files:
        if file.status == "added" or file.is_binary:
            continue
        for hunk in file.hunks:
            if len(histories) >= max_hunks:
                skipped += 1
                continue
            histories.append(
                hunk_history(root, old_rev, file, hunk, days=days, limit=limit, detail_cache=cache)
            )
    notes: list[str] = []
    if skipped:
        notes.append(f"history: skipped {skipped} hunk(s) beyond max_hunks={max_hunks}")
    return histories, notes


def _grouped_by_commit(hunk: HunkHistory) -> list[tuple[CommitRef, list[LineOrigin]]]:
    groups: dict[str, tuple[CommitRef, list[LineOrigin]]] = {}
    for origin in hunk.origins:
        if not origin.is_regression_risk:
            continue
        entry = groups.get(origin.commit.sha)
        if entry is None:
            groups[origin.commit.sha] = (origin.commit, [origin])
        else:
            entry[1].append(origin)
    return list(groups.values())


def regression_findings(hunks: list[HunkHistory]) -> list[Finding]:
    """One MEDIUM finding per (hunk, origin commit) whose message reads like a fix."""
    findings: list[Finding] = []
    for hunk in hunks:
        for commit, origins in _grouped_by_commit(hunk):
            keywords = origins[0].regression_keywords
            tests = tuple(dict.fromkeys(t for o in origins for t in o.tests_in_commit))
            line = max(hunk.new_start, 1)
            evidence: list[Evidence] = [Evidence("commit", commit.short, commit.subject)]
            evidence.extend(
                Evidence("line", f"{hunk.file}:{o.old_line}", o.text)
                for o in origins[:_MAX_LINE_EVIDENCE]
            )
            line_text = origins[0].text
            findings.append(
                Finding(
                    file=hunk.file,
                    line=line,
                    end_line=hunk.new_end if hunk.new_end >= line else None,
                    severity=Severity.MEDIUM,
                    dimension="history",
                    category="regression-risk",
                    title=f'removes line(s) introduced by fix {commit.short} "{commit.subject}"',
                    scenario=(
                        f"{len(origins)} removed line(s) were added by {commit.short}, whose "
                        f"message matches {', '.join(keywords)}; removing them may bring the "
                        "original bug back."
                    ),
                    fix=(
                        f"run {', '.join(tests)}"
                        if tests
                        else f"confirm the original issue in {commit.short} no longer applies"
                    ),
                    source="history",
                    origin="history:regression",
                    confidence=0.6,
                    evidence=tuple(evidence),
                    line_text=line_text,
                    fingerprint=fingerprint(hunk.file, "history", "regression-risk", line_text),
                )
            )
    return findings
