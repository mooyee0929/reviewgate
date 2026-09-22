"""Data types produced by the history agent. Every module in this package
returns these; `agent.py` assembles them into a `HistoryReport`."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..git import CommitRef
from ..model import Finding


@dataclass(frozen=True, slots=True)
class LineOrigin:
    """A removed or rewritten old-side line and the commit that introduced it."""

    old_line: int
    text: str
    commit: CommitRef
    regression_keywords: tuple[str, ...]
    """Keywords (fix, race, leak, ...) matched in the origin commit's message."""
    tests_in_commit: tuple[str, ...]
    """Test files the origin commit touched; hand these to the execution agent."""

    @property
    def is_regression_risk(self) -> bool:
        return bool(self.regression_keywords)

    def to_dict(self) -> dict[str, object]:
        return {
            "old_line": self.old_line,
            "text": self.text,
            "commit": {
                "sha": self.commit.short,
                "date": self.commit.date,
                "subject": self.commit.subject,
            },
            "regression_keywords": list(self.regression_keywords),
            "tests_in_commit": list(self.tests_in_commit),
        }


@dataclass(frozen=True, slots=True)
class HunkHistory:
    """Per-hunk prior: how often these lines changed lately and where the
    removed lines came from."""

    file: str
    new_start: int
    new_end: int
    old_start: int
    old_end: int
    days: int
    commits: tuple[CommitRef, ...]
    """Commits touching the old-side range within `days`, newest first."""
    origins: tuple[LineOrigin, ...]

    @property
    def change_count(self) -> int:
        return len(self.commits)

    @property
    def is_hot(self) -> bool:
        return self.change_count >= 3

    def context_line(self) -> str:
        """The one-liner that goes into the reviewer's context bundle, e.g.
        `changed 4x in 90d; last: abc1234 "fix race in flush()"`."""
        if not self.commits:
            return f"no changes in {self.days}d"
        last = self.commits[0]
        return f'changed {self.change_count}x in {self.days}d; last: {last.short} "{last.subject}"'

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "new_start": self.new_start,
            "new_end": self.new_end,
            "old_start": self.old_start,
            "old_end": self.old_end,
            "days": self.days,
            "change_count": self.change_count,
            "is_hot": self.is_hot,
            "context": self.context_line(),
            "commits": [
                {"sha": c.short, "date": c.date, "subject": c.subject} for c in self.commits
            ],
            "origins": [o.to_dict() for o in self.origins],
        }


@dataclass(frozen=True, slots=True)
class CoChangeGap:
    """`file` changed in this diff; `partner` usually changes with it but did not."""

    file: str
    partner: str
    together: int
    """Commits in the table where both files appear."""
    file_total: int
    """Commits in the table where `file` appears at all."""

    @property
    def ratio(self) -> float:
        return self.together / self.file_total if self.file_total else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "partner": self.partner,
            "together": self.together,
            "file_total": self.file_total,
            "ratio": round(self.ratio, 2),
        }


@dataclass(frozen=True, slots=True)
class Precedent:
    """`git log -S<needle>` hit: an earlier commit that added or removed the needle."""

    needle: str
    path: str | None
    commits: tuple[CommitRef, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "needle": self.needle,
            "path": self.path,
            "commits": [
                {"sha": c.short, "date": c.date, "subject": c.subject} for c in self.commits
            ],
        }


@dataclass(slots=True)
class HistoryReport:
    base: str
    head: str
    days: int
    hunks: list[HunkHistory] = field(default_factory=list)
    gaps: list[CoChangeGap] = field(default_factory=list)
    precedents: list[Precedent] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def context_lines(self) -> dict[str, list[str]]:
        """file -> context one-liners, ready to paste into a reviewer bundle."""
        out: dict[str, list[str]] = {}
        for h in self.hunks:
            out.setdefault(h.file, []).append(f"L{h.new_start}-{h.new_end}: {h.context_line()}")
            for o in h.origins:
                if o.is_regression_risk:
                    out[h.file].append(
                        f'  removed L{o.old_line} came from {o.commit.short} "{o.commit.subject}"'
                    )
        for g in self.gaps:
            out.setdefault(g.file, []).append(
                f"usually changes with {g.partner} ({g.together}/{g.file_total}), not in this diff"
            )
        return out

    def to_dict(self) -> dict[str, object]:
        return {
            "base": self.base,
            "head": self.head,
            "days": self.days,
            "hunks": [h.to_dict() for h in self.hunks],
            "gaps": [g.to_dict() for g in self.gaps],
            "precedents": [p.to_dict() for p in self.precedents],
            "findings": [f.to_dict() for f in self.findings],
            "context": self.context_lines(),
            "notes": list(self.notes),
        }
