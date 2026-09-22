"""Precedents: `git log -S<needle>` lookups and the heuristic that picks
which identifiers from a diff are worth looking up."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..diff import ChangedFile
from ..git import pickaxe
from .types import Precedent

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

STOPWORDS: frozenset[str] = frozenset(
    {
        "def",
        "class",
        "return",
        "import",
        "from",
        "self",
        "None",
        "True",
        "False",
        "else",
        "elif",
        "while",
        "with",
        "const",
        "function",
        "this",
        "null",
        "true",
        "false",
        "void",
        "static",
        "public",
        "private",
        "include",
        "namespace",
    }
)


def precedents(
    root: Path, needle: str, *, path: str | None = None, limit: int = 3, rev: str | None = None
) -> Precedent:
    """Earlier commits that added or removed `needle`, walking back from `rev`.
    Pass the diff's old side as `rev` so the diff's own commits are not
    reported as their own precedent."""
    commits = pickaxe(root, needle, limit=limit, path=path, rev=rev)
    return Precedent(needle=needle, path=path, commits=tuple(commits))


def _is_candidate(token: str) -> bool:
    if token in STOPWORDS:
        return False
    return not (token.islower() and len(token) < 5)


def identifier_needles(files: list[ChangedFile], *, max_needles: int = 5) -> list[str]:
    """Identifiers that occur in removed lines but in no added line of the same
    file: names that vanished from the code and may have a history worth
    reading. Ranked by how many removed lines mention them."""
    counts: Counter[str] = Counter()
    for f in files:
        surviving = {token for line in f.added_lines for token in _IDENTIFIER.findall(line.text)}
        for line in f.removed_lines:
            seen_on_line: set[str] = set()
            for token in _IDENTIFIER.findall(line.text):
                if token in surviving or token in seen_on_line or not _is_candidate(token):
                    continue
                seen_on_line.add(token)
                counts[token] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [token for token, _ in ranked[:max_needles]]
