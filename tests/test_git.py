from __future__ import annotations

from pathlib import Path

import pytest
from conftest import commit_file, commit_files, git

from reviewgate.diff import parse_diff
from reviewgate.git import (
    DiffSpec,
    GitError,
    blame_lines,
    commit_detail,
    commit_file_sets,
    default_branch,
    diff_text,
    head_sha,
    is_ancestor,
    line_count_at,
    line_history,
    merge_base,
    path_exists_at,
    pickaxe,
    ref_exists,
    repo_root,
    resolve_old_rev,
    short_sha,
)

OLD_DATE = "2025-01-01T00:00:00"
NEW_DATE = "2026-09-10T00:00:00"


def test_repo_root(repo: Path, tmp_path: Path) -> None:
    assert repo_root(repo / "nested" / "deeper") is None
    (repo / "nested").mkdir()
    assert repo_root(repo / "nested") == repo.resolve()
    assert repo_root(tmp_path / "nowhere") is None


def test_default_branch_and_merge_base(repo: Path) -> None:
    assert default_branch(repo, "origin") == "origin/main"
    assert default_branch(repo, "nope") is None
    base_before = head_sha(repo)
    commit_file(repo, "app.py", "def add(a, b):\n    return a + b + 0\n", "tweak")
    assert merge_base(repo, "HEAD", "origin/main") == base_before
    assert len(base_before) == 40
    assert short_sha(repo, base_before) == base_before[: len(short_sha(repo, base_before))]
    assert is_ancestor(repo, base_before, "HEAD")
    assert not is_ancestor(repo, "HEAD", base_before)
    assert not is_ancestor(repo, "0" * 40, "HEAD")


def test_default_branch_without_remote_head(repo: Path) -> None:
    git(repo, "remote", "set-head", "origin", "--delete")
    assert default_branch(repo, "origin") == "origin/main"


def test_diff_specs(repo: Path) -> None:
    commit_file(repo, "app.py", "def add(a, b):\n    return a + b + 0\n", "tweak")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b + 1\n")
    branch = parse_diff(diff_text(repo, DiffSpec(base="origin/main")))
    assert [f.path for f in branch] == ["app.py"]
    assert branch[0].added_lines[0].text == "    return a + b + 0"
    assert branch[0].removed_lines[0].text == "    return a + b"
    worktree = parse_diff(diff_text(repo, DiffSpec(base=None)))
    assert worktree[0].added_lines[0].text == "    return a + b + 1"
    assert parse_diff(diff_text(repo, DiffSpec(base=None, staged=True))) == []
    git(repo, "add", "app.py")
    assert len(parse_diff(diff_text(repo, DiffSpec(base=None, staged=True)))) == 1
    assert DiffSpec(base=None, staged=True).label == "index"
    assert DiffSpec(base=None).old_rev == "HEAD"
    assert DiffSpec(base="origin/main").old_rev == "origin/main"


def test_resolve_old_rev(repo: Path) -> None:
    base = head_sha(repo)
    commit_file(repo, "app.py", "def add(a, b):\n    return a + b + 0\n", "tweak")
    assert resolve_old_rev(repo, DiffSpec(base="origin/main")) == base
    assert resolve_old_rev(repo, DiffSpec(base=None)) == head_sha(repo)
    assert resolve_old_rev(repo, DiffSpec(base="HEAD")) == head_sha(repo)


def test_ref_exists_and_errors(repo: Path) -> None:
    assert ref_exists(repo, "origin/main")
    assert not ref_exists(repo, "no-such-branch")
    with pytest.raises(GitError):
        diff_text(repo, DiffSpec(base="no-such-branch"))


def test_line_history_respects_window_and_order(repo: Path) -> None:
    commit_file(repo, "f.py", "a\nb\nc\n", "old edit", date=OLD_DATE)
    mid = commit_file(repo, "f.py", "a\nB\nc\n", "mid edit", date="2026-09-01T00:00:00")
    new = commit_file(repo, "f.py", "a\nBB\nc\n", "new edit", date=NEW_DATE)
    commits = line_history(repo, "HEAD", "f.py", 2, 2, days=90)
    assert [c.sha for c in commits] == [new, mid]
    assert all(len(c.sha) == 40 for c in commits)
    assert commits[0].short == new[:7]
    assert commits[0].subject == "new edit"
    assert commits[0].date.startswith("2026-09-10")
    assert len(line_history(repo, "HEAD", "f.py", 2, 2, days=90, limit=1)) == 1
    assert [c.sha for c in line_history(repo, mid, "f.py", 2, 2, days=90)] == [mid]
    assert line_history(repo, "HEAD", "missing.py", 1, 1, days=90) == []


def test_blame_lines(repo: Path) -> None:
    first = commit_file(repo, "f.py", "a\nb\nc\n", "first")
    second = commit_file(repo, "f.py", "a\nB\nc\n", "second")
    assert blame_lines(repo, "HEAD", "f.py", 1, 3) == {1: first, 2: second, 3: first}
    assert blame_lines(repo, "HEAD", "f.py", 2, 2) == {2: second}
    assert blame_lines(repo, "HEAD", "missing.py", 1, 1) == {}


def test_commit_detail(repo: Path) -> None:
    sha = commit_files(
        repo,
        {"src/flush.py": "x = 1\n", "tests/test_flush.py": "def test_x(): pass\n"},
        "fix race in flush\n\nThe lock was released early.\nSecond body line.",
        date=NEW_DATE,
    )
    detail = commit_detail(repo, sha)
    assert detail is not None
    assert detail.sha == sha
    assert detail.date.startswith("2026-09-10")
    assert detail.subject == "fix race in flush"
    assert detail.body == "The lock was released early.\nSecond body line."
    assert detail.files == ("src/flush.py", "tests/test_flush.py")
    assert commit_detail(repo, "0" * 40) is None


def test_commit_file_sets_order_limit_and_merges(repo: Path) -> None:
    a = commit_files(repo, {"a.py": "1\n", "b.py": "1\n"}, "ab")
    git(repo, "checkout", "-q", "-b", "side")
    side = commit_file(repo, "c.py", "1\n", "side")
    git(repo, "checkout", "-q", "feature")
    main_side = commit_file(repo, "d.py", "1\n", "feature side")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    sets = commit_file_sets(repo, "HEAD", limit=None)
    shas = [sha for sha, _ in sets]
    assert head_sha(repo) not in shas
    assert shas[:3] == [main_side, side, a] or shas[:3] == [side, main_side, a]
    assert dict(sets)[a] == ["a.py", "b.py"]
    assert dict(sets)[side] == ["c.py"]
    assert len(commit_file_sets(repo, "HEAD", limit=2)) == 2
    assert [sha for sha, _ in commit_file_sets(repo, f"{a}..HEAD", limit=None)] == shas[:2]


def test_pickaxe(repo: Path) -> None:
    first = commit_file(repo, "cache.py", "def flush_cache():\n    pass\n", "add")
    second = commit_file(repo, "cache.py", "def flush():\n    pass\n", "rename")
    commit_file(repo, "other.py", "flush_cache = 1\n", "other")
    assert [c.sha for c in pickaxe(repo, "flush_cache", limit=5)][1:] == [second, first]
    assert [c.sha for c in pickaxe(repo, "flush_cache", limit=5, path="cache.py")] == [
        second,
        first,
    ]
    assert [c.sha for c in pickaxe(repo, "flush_cache", limit=1)] == [head_sha(repo)]
    assert pickaxe(repo, "nothing_here", limit=3) == []


def test_path_exists_and_line_count(repo: Path) -> None:
    commit_files(repo, {"nl.txt": "a\nb\n", "nonl.txt": "a\nb", "empty.txt": ""}, "files")
    assert path_exists_at(repo, "HEAD", "nl.txt")
    assert not path_exists_at(repo, "HEAD", "missing.txt")
    assert not path_exists_at(repo, "origin/main", "nl.txt")
    assert line_count_at(repo, "HEAD", "nl.txt") == 2
    assert line_count_at(repo, "HEAD", "nonl.txt") == 2
    assert line_count_at(repo, "HEAD", "empty.txt") == 0
    assert line_count_at(repo, "HEAD", "missing.txt") is None
