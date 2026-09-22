from __future__ import annotations

import json
from pathlib import Path

from conftest import commit_file, commit_files, git

from reviewgate.diff import parse_diff
from reviewgate.git import DiffSpec, diff_text
from reviewgate.history.agent import (
    DEFAULT_IGNORE,
    HistoryOptions,
    analyze,
    analyze_files,
    select_files,
)
from reviewgate.model import Severity


def _seed_history(repo: Path) -> None:
    """On main: `core.py` and `core_test.py` change together three times, and a
    fix commit adds a guard line that `feature` will later delete."""
    git(repo, "checkout", "-q", "main")
    for i in range(3):
        commit_files(
            repo,
            {"core.py": f"def run():\n    return {i}\n", "core_test.py": f"# v{i}\n"},
            f"iterate {i}",
            date=f"2026-09-0{i + 1}T00:00:00",
        )
    commit_file(
        repo,
        "core.py",
        "def run():\n    if not ready():\n        return None\n    return 2\n",
        "fix crash when not ready",
        date="2026-09-10T00:00:00",
    )
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "feature")
    git(repo, "reset", "-q", "--hard", "main")


def test_analyze_end_to_end(repo: Path) -> None:
    _seed_history(repo)
    commit_file(repo, "core.py", "def run():\n    return 2\n", "simplify run")
    report = analyze(repo, DiffSpec(base="origin/main"))

    assert report.base.startswith("origin/main (")
    assert len(report.hunks) == 1
    hunk = report.hunks[0]
    assert hunk.file == "core.py"
    assert hunk.is_hot
    assert hunk.context_line().startswith(f"changed {hunk.change_count}x in 90d; last:")
    assert 'fix crash when not ready"' in hunk.context_line()

    categories = sorted(f.category for f in report.findings)
    assert categories == ["co-change-missing", "regression-risk"]
    regression = next(f for f in report.findings if f.category == "regression-risk")
    assert regression.severity is Severity.MEDIUM
    assert regression.file == "core.py"
    gap = next(f for f in report.findings if f.category == "co-change-missing")
    assert gap.severity is Severity.LOW
    assert gap.line_text == "core_test.py"
    assert report.gaps[0].partner == "core_test.py"
    assert report.findings[0] is regression, "sorted by severity desc"

    context = report.context_lines()
    assert "core.py" in context
    assert any("usually changes with core_test.py" in line for line in context["core.py"])
    assert any("removed L" in line for line in context["core.py"])
    assert any(note.startswith("cochange: rebuilt") for note in report.notes)
    assert (repo / ".review" / "cochange.json").is_file()

    payload = json.loads(json.dumps(report.to_dict()))
    assert payload["hunks"][0]["is_hot"] is True
    assert len(payload["findings"]) == 2


def test_analyze_options_disable_cochange_and_cache(repo: Path) -> None:
    _seed_history(repo)
    commit_file(repo, "core.py", "def run():\n    return 2\n", "simplify run")
    options = HistoryOptions(cochange=False, use_cache=False)
    report = analyze(repo, DiffSpec(base="origin/main"), options)
    assert report.gaps == []
    assert [f.category for f in report.findings] == ["regression-risk"]
    assert not (repo / ".review").exists()


def test_analyze_working_tree_and_staged(repo: Path) -> None:
    _seed_history(repo)
    (repo / "core.py").write_text("def run():\n    return 2\n")
    report = analyze(repo, DiffSpec(base=None), HistoryOptions(cochange=False))
    assert report.head == "working tree"
    assert [f.category for f in report.findings] == ["regression-risk"]
    git(repo, "add", "core.py")
    staged = analyze(repo, DiffSpec(base=None, staged=True), HistoryOptions(cochange=False))
    assert staged.head == "index"
    assert len(staged.hunks) == 1


def test_precedents_explicit_and_auto(repo: Path) -> None:
    _seed_history(repo)
    commit_file(repo, "core.py", "def run():\n    return 2\n", "simplify run")
    options = HistoryOptions(
        cochange=False, precedent_needles=("ready", "nonexistent_zzz"), auto_precedents=3
    )
    report = analyze(repo, DiffSpec(base="origin/main"), options)
    needles = [p.needle for p in report.precedents]
    assert "ready" in needles
    assert "nonexistent_zzz" not in needles
    assert needles.count("ready") == 1, "explicit needle is not duplicated by auto lookup"
    hit = next(p for p in report.precedents if p.needle == "ready")
    assert hit.commits[0].subject == "fix crash when not ready"


def test_select_files_and_ignore_note(repo: Path) -> None:
    _seed_history(repo)
    commit_files(
        repo,
        {"core.py": "def run():\n    return 2\n", "uv.lock": "x\n", "img.png": "\x89PNG\n"},
        "mixed",
    )
    files = parse_diff(diff_text(repo, DiffSpec(base="origin/main")))
    assert [f.path for f in select_files(files, DEFAULT_IGNORE)] == ["core.py"]
    report = analyze_files(
        repo,
        files,
        old_rev="origin/main",
        base_label="b",
        head_label="h",
        options=HistoryOptions(cochange=False),
    )
    assert "history: ignored 2 file(s) by pattern" in report.notes
    assert [h.file for h in report.hunks] == ["core.py"]


def test_ignored_files_never_appear_as_gaps(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")
    for i in range(3):
        commit_files(
            repo,
            {"core.py": f"x = {i}\n", "uv.lock": f"lock {i}\n", "core_test.py": f"# {i}\n"},
            f"bump {i}",
        )
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "feature")
    git(repo, "reset", "-q", "--hard", "main")
    commit_files(repo, {"core.py": "x = 9\n", "uv.lock": "lock 9\n"}, "bump lock too")
    report = analyze(repo, DiffSpec(base="origin/main"), HistoryOptions(use_cache=False))
    partners = {(g.file, g.partner) for g in report.gaps}
    assert partners == {("core.py", "core_test.py")}
    assert all(f.file != "uv.lock" for f in report.findings)
