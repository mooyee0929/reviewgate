from __future__ import annotations

import json

from reviewgate.git import CommitRef
from reviewgate.history.report import render_json, render_markdown, render_terminal
from reviewgate.history.types import (
    CoChangeGap,
    HistoryReport,
    HunkHistory,
    LineOrigin,
    Precedent,
)
from reviewgate.model import Evidence, Finding, Severity, fingerprint

FIX = CommitRef(
    sha="abc1234" + "0" * 33, date="2026-09-10T12:00:00+00:00", subject="fix race in flush()"
)
OLD = CommitRef(sha="def5678" + "0" * 33, date="2026-09-01T12:00:00+00:00", subject="tidy flush")


def _report() -> HistoryReport:
    origin = LineOrigin(
        old_line=14,
        text="    lock.acquire()",
        commit=FIX,
        regression_keywords=("fix", "race"),
        tests_in_commit=("tests/test_flush.py",),
    )
    hot = HunkHistory(
        file="app.py",
        new_start=12,
        new_end=20,
        old_start=12,
        old_end=21,
        days=90,
        commits=(FIX, OLD, OLD, OLD),
        origins=(origin,),
    )
    cold = HunkHistory(
        file="util.py",
        new_start=1,
        new_end=3,
        old_start=1,
        old_end=3,
        days=90,
        commits=(),
        origins=(),
    )
    gap = CoChangeGap(file="app.py", partner="foo.py", together=3, file_total=5)
    finding = Finding(
        file="app.py",
        line=12,
        end_line=20,
        severity=Severity.MEDIUM,
        dimension="history",
        category="regression-risk",
        title='removes line(s) introduced by fix abc1234 "fix race in flush()"',
        scenario="the removed lines were added by a fix commit; the bug may come back",
        fix="run tests/test_flush.py",
        source="history",
        origin="history:regression",
        confidence=0.6,
        evidence=(Evidence("commit", "abc1234", "fix race in flush()"),),
        line_text="    lock.acquire()",
        fingerprint=fingerprint("app.py", "history", "regression-risk", "    lock.acquire()"),
    )
    low = Finding(
        file="app.py",
        line=1,
        severity=Severity.LOW,
        dimension="history",
        category="co-change-missing",
        title="foo.py usually changes with app.py but is not in this diff",
        scenario="changed together in 3 of 5 recent commits",
        source="history",
        origin="history:cochange",
        confidence=0.6,
        fingerprint=fingerprint("app.py", "history", "co-change-missing", "foo.py"),
    )
    return HistoryReport(
        base="origin/main",
        head="feature",
        days=90,
        hunks=[hot, cold],
        gaps=[gap],
        precedents=[Precedent(needle="flush_cache", path=None, commits=(FIX,))],
        findings=[low, finding],
        notes=["cochange: rebuilt from 5 commits"],
    )


def test_terminal_sections() -> None:
    text = render_terminal(_report())
    lines = text.splitlines()
    assert lines[0] == (
        "reviewgate history: origin/main -> feature, 2 hunk(s), 1 hot, "
        "1 co-change gap(s), 2 finding(s)"
    )
    assert lines[1] == "  note: cochange: rebuilt from 5 commits"
    assert '  L12-20  changed 4x in 90d; last: abc1234 "fix race in flush()"   [HOT]' in lines
    assert (
        '          removed L14 came from abc1234 "fix race in flush()"  (fix, race)'
        "  tests: tests/test_flush.py"
    ) in lines
    assert "  usually changes with foo.py (3/5), not in this diff" in lines
    assert "  L1-3  no changes in 90d" in lines
    assert "\x1b" not in text
    body = text[text.index("findings") :]
    assert body.index("MEDIUM") < body.index("LOW")
    assert "  MEDIUM   app.py:12  [regression-risk] removes line(s)" in text
    assert "           fix: run tests/test_flush.py" in lines
    assert "           (history:regression conf=0.60)" in lines
    assert 'precedents\n  flush_cache  abc1234 2026-09-10 "fix race in flush()"' in text
    assert "[HOT]" in text
    assert "util.py" in text


def test_terminal_color_and_empty() -> None:
    assert "\x1b[33m[HOT]\x1b[0m" in render_terminal(_report(), color=True)
    empty = HistoryReport(base="a", head="b", days=30)
    assert render_terminal(empty) == (
        "reviewgate history: a -> b, 0 hunk(s), 0 hot, 0 co-change gap(s), 0 finding(s)\n"
    )


def test_json_round_trip() -> None:
    data = json.loads(render_json(_report()))
    assert set(data) >= {
        "base",
        "head",
        "days",
        "hunks",
        "gaps",
        "precedents",
        "findings",
        "context",
        "notes",
    }
    assert data["hunks"][0]["change_count"] == 4 and data["hunks"][0]["is_hot"] is True
    assert data["findings"][0]["severity"] == "low"
    assert data["context"]["app.py"][0].startswith("L12-20: changed 4x")


def test_markdown_tables_and_omitted_sections() -> None:
    text = render_markdown(_report())
    assert text.startswith("### History\n")
    assert "| file | lines | changes (90d) | last commit | regression origins |" in text
    assert "| `app.py` | 12-20 | 4 (hot) | `abc1234` fix race in flush() |" in text
    assert "L14 from `abc1234` fix race in flush()" in text
    assert "- `foo.py` usually changes with `app.py` (3/5) but is not in this diff" in text
    assert "| severity | location | category | title | fix |" in text
    assert "| medium | `app.py:12` | regression-risk |" in text
    assert "- `flush_cache`: `abc1234` fix race in flush()" in text

    empty = render_markdown(HistoryReport(base="a", head="b", days=30))
    assert "| file |" not in empty and "| severity |" not in empty and "- `" not in empty
