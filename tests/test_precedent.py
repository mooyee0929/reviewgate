from __future__ import annotations

from pathlib import Path

from conftest import commit_file

from reviewgate.diff import AddedLine, ChangedFile, Hunk, RemovedLine
from reviewgate.history.precedent import identifier_needles, precedents


def test_precedents_from_real_history(repo: Path) -> None:
    first = commit_file(repo, "cache.py", "def flush_cache():\n    pass\n", "add flush_cache")
    second = commit_file(repo, "cache.py", "def flush():\n    pass\n", "rename to flush")
    commit_file(repo, "other.py", "x = 1\n", "unrelated")

    hit = precedents(repo, "flush_cache")
    assert hit.needle == "flush_cache" and hit.path is None
    assert [c.sha for c in hit.commits] == [second, first]
    assert hit.commits[0].subject == "rename to flush"
    assert hit.commits[0].short == second[:7]

    assert [c.sha for c in precedents(repo, "flush_cache", limit=1).commits] == [second]
    assert precedents(repo, "flush_cache", path="other.py").commits == ()
    assert precedents(repo, "no_such_symbol_anywhere").commits == ()


def _file(removed: list[str], added: list[str]) -> ChangedFile:
    hunk = Hunk(
        old_start=1,
        old_count=len(removed),
        new_start=1,
        new_count=len(added),
        added=tuple(AddedLine(number=i + 1, text=t) for i, t in enumerate(added)),
        removed=tuple(RemovedLine(number=i + 1, text=t) for i, t in enumerate(removed)),
        raw="",
    )
    return ChangedFile(path="a.py", status="modified", hunks=(hunk,))


def test_identifier_needles_prefers_vanished_names() -> None:
    changed = _file(
        removed=["    def flush_cache(self, retries):"],
        added=["    def flush(self, retries):"],
    )
    assert identifier_needles([changed]) == ["flush_cache"]


def test_identifier_needles_frequency_then_alpha_and_limit() -> None:
    changed = _file(
        removed=[
            "zeta_thing = old_helper(zeta_thing)",
            "alpha_thing = old_helper()",
            "value = SomeClass(zeta_thing)",
        ],
        added=["value = 1"],
    )
    assert identifier_needles([changed]) == [
        "old_helper",
        "zeta_thing",
        "SomeClass",
        "alpha_thing",
    ]
    assert identifier_needles([changed], max_needles=2) == ["old_helper", "zeta_thing"]


def test_identifier_needles_skips_stopwords_and_short_words() -> None:
    changed = _file(removed=["return None if self else data"], added=[])
    assert identifier_needles([changed]) == []
