"""History agent: git-derived priors for a diff. No LLM involved.

- hot spots: how often each hunk's lines changed recently (`lines.py`)
- regression risk: removed lines whose origin commit was a fix (`lines.py`)
- co-change gaps: partner files that usually change together but did not (`cochange.py`)
- precedents: `git log -S` for an identifier (`precedent.py`)
"""

from .types import CoChangeGap, HistoryReport, HunkHistory, LineOrigin, Precedent

__all__ = ["CoChangeGap", "HistoryReport", "HunkHistory", "LineOrigin", "Precedent"]
