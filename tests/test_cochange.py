from __future__ import annotations

import json
from pathlib import Path

from conftest import commit_files, git

from reviewgate.history.cochange import (
    CACHE_RELPATH,
    CoChangeTable,
    build_or_update_table,
    build_table,
    co_change_gaps,
    count_commits,
    gap_findings,
    load_table,
    pair_key,
    save_table,
)
from reviewgate.model import Severity, fingerprint


def _abc_history(repo: Path) -> None:
    """3 commits a+b, 1 commit a alone, 1 commit a+c."""
    for i in range(3):
        commit_files(repo, {"a.py": f"a{i}\n", "b.py": f"b{i}\n"}, f"ab {i}")
    commit_files(repo, {"a.py": "a solo\n"}, "a alone")
    commit_files(repo, {"a.py": "a with c\n", "c.py": "c\n"}, "ac")


def test_pair_counting_and_partners(repo: Path) -> None:
    _abc_history(repo)
    table = build_table(repo)
    assert table.files["a.py"] == 5
    assert table.pairs[pair_key("b.py", "a.py")] == 3
    assert table.partners("a.py") == [("b.py", 3)]
    assert table.partners("a.py", min_together=1) == [("b.py", 3)]
    assert table.partners("a.py", min_together=1, min_ratio=0) == [("b.py", 3), ("c.py", 1)]
    assert table.partners("missing.py") == []


def test_max_files_per_commit_skips_mass_commits(repo: Path) -> None:
    commit_files(repo, {f"bulk/f{i}.py": "x\n" for i in range(40)}, "mass refactor")
    table = build_table(repo)
    assert table.commits == 1
    assert "bulk/f0.py" not in table.files
    counted = CoChangeTable(scanned_to=None, commits=0)
    count_commits(counted, [("sha", [f"f{i}" for i in range(40)])], max_files_per_commit=50)
    assert counted.commits == 1 and counted.files["f0"] == 1


def test_count_commits_ignores_empty_and_dedupes_paths() -> None:
    table = CoChangeTable(scanned_to=None, commits=0)
    count_commits(table, [("s1", []), ("s2", ["x", "x", "y"])], max_files_per_commit=30)
    assert table.commits == 1
    assert table.files == {"x": 1, "y": 1}
    assert table.pairs == {pair_key("x", "y"): 1}


def test_merge_commits_are_skipped(repo: Path) -> None:
    before = build_table(repo).commits
    git(repo, "checkout", "-q", "-b", "side")
    commit_files(repo, {"side.py": "s\n"}, "side change")
    git(repo, "checkout", "-q", "feature")
    commit_files(repo, {"main.py": "m\n"}, "feature change")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    assert build_table(repo).commits == before + 2


def test_cache_round_trip_and_invalid(repo: Path) -> None:
    table = CoChangeTable(
        scanned_to="a" * 40, commits=2, files={"x": 2}, pairs={pair_key("x", "y"): 1}, window=7
    )
    save_table(repo, table)
    assert (repo / CACHE_RELPATH).read_text().endswith("\n")
    assert load_table(repo) == table
    (repo / CACHE_RELPATH).write_text("{not json")
    assert load_table(repo) is None
    (repo / CACHE_RELPATH).write_text(json.dumps({**table.to_dict(), "version": 99}))
    assert load_table(repo) is None
    (repo / CACHE_RELPATH).write_text(json.dumps({**table.to_dict(), "files": {"x": "2"}}))
    assert load_table(repo) is None
    (repo / CACHE_RELPATH).unlink()
    assert load_table(repo) is None


def test_build_or_update_lifecycle(repo: Path) -> None:
    _abc_history(repo)
    cache = repo / CACHE_RELPATH
    table, note = build_or_update_table(repo)
    assert note.startswith("cochange: rebuilt from 6 commits")
    assert cache.exists() and table.scanned_to == git(repo, "rev-parse", "HEAD").strip()

    commit_files(repo, {"a.py": "a again\n", "b.py": "b again\n"}, "ab 3")
    table, note = build_or_update_table(repo)
    assert note.startswith("cochange: updated (+1 commits, 7 total)")
    assert table.pairs[pair_key("a.py", "b.py")] == 4
    assert load_table(repo) == table

    _, note = build_or_update_table(repo)
    assert note.startswith("cochange: cache current (7 commits)")

    _, note = build_or_update_table(repo, window=10)
    assert note.startswith("cochange: rebuilt")
    widened = load_table(repo)
    assert widened is not None and widened.window == 10

    stale = load_table(repo)
    assert stale is not None
    stale.scanned_to = "0" * 40
    save_table(repo, stale)
    _, note = build_or_update_table(repo, window=10)
    assert note.startswith("cochange: rebuilt")

    cache.unlink()
    table, note = build_or_update_table(repo, use_cache=False)
    assert note == "cochange: built from 7 commits (cache disabled)"
    assert not cache.exists()


def test_gaps_and_findings(repo: Path) -> None:
    _abc_history(repo)
    table = build_table(repo)
    gaps = co_change_gaps(repo, table, ["a.py"])
    assert [(g.file, g.partner, g.together, g.file_total) for g in gaps] == [("a.py", "b.py", 3, 5)]
    assert gaps[0].ratio == 0.6
    assert co_change_gaps(repo, table, ["a.py", "b.py"]) == []
    assert co_change_gaps(repo, table, ["b.py"]) == [
        type(gaps[0])(file="b.py", partner="a.py", together=3, file_total=3)
    ]

    findings = gap_findings(gaps)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity is Severity.LOW
    assert f.category == "co-change-missing" and f.dimension == "history"
    assert f.confidence == 0.6
    assert f.evidence[0].kind == "cochange" and f.evidence[0].detail == "3/5"
    assert f.fingerprint == fingerprint("a.py", "history", "co-change-missing", "b.py")

    git(repo, "rm", "-q", "b.py")
    git(repo, "commit", "-q", "-m", "drop b")
    assert co_change_gaps(repo, build_table(repo), ["a.py"]) == []
