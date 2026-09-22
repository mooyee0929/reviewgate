"""Core data types shared by every stage."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

Source = Literal["check", "tool", "llm", "history"]


class Severity(IntEnum):
    """Ordered so that comparisons express "at least this bad"."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def parse(cls, text: str) -> Severity:
        try:
            return cls[text.strip().upper()]
        except KeyError as exc:
            raise ValueError(f"unknown severity {text!r}") from exc

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True, slots=True)
class Evidence:
    """A fact a judge can re-check: a commit, a file:line, or a command result."""

    kind: Literal["commit", "line", "cochange"]
    ref: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail}


_WS = re.compile(r"\s+")


def fingerprint(file: str, dimension: str, category: str, line_text: str) -> str:
    """Stable across line-number shifts: only the file, the classification and
    the normalized text of the anchoring line participate."""
    normalized = _WS.sub(" ", line_text.strip())
    digest = hashlib.sha256(f"{file}|{dimension}|{category}|{normalized}".encode())
    return digest.hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Finding:
    file: str
    line: int
    severity: Severity
    dimension: str
    category: str
    title: str
    scenario: str
    source: Source
    origin: str
    fingerprint: str
    end_line: int | None = None
    fix: str | None = None
    confidence: float = 1.0
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)
    line_text: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "severity": self.severity.label,
            "dimension": self.dimension,
            "category": self.category,
            "title": self.title,
            "scenario": self.scenario,
            "fix": self.fix,
            "confidence": self.confidence,
            "source": self.source,
            "origin": self.origin,
            "evidence": [e.to_dict() for e in self.evidence],
            "line_text": self.line_text,
        }
