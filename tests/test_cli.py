from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import commit_file, git

from reviewgate.cli import EXIT_INFRA, EXIT_OK, EXIT_USAGE, main


def _feature_change(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")
    commit_file(
        repo,
        "app.py",
        "def add(a, b):\n    if a is None:\n        return b\n    return a + b\n",
        "fix None crash",
    )
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "feature")
    git(repo, "reset", "-q", "--hard", "main")
    commit_file(repo, "app.py", "def add(a, b):\n    return a + b\n", "drop guard")


def test_history_defaults_to_origin_default_branch(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _feature_change(repo)
    assert main(["-C", str(repo), "history", "--no-cache"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("reviewgate history: origin/main (")
    assert "regression-risk" in out
    assert "\x1b" not in out


def test_history_json_and_json_out(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _feature_change(repo)
    out_file = tmp_path / "out" / "history.json"
    code = main(
        [
            "-C",
            str(repo),
            "history",
            "--base",
            "origin/main",
            "--json",
            "--json-out",
            str(out_file),
            "--no-cochange",
        ]
    )
    assert code == EXIT_OK
    stdout = json.loads(capsys.readouterr().out)
    assert stdout["findings"][0]["category"] == "regression-risk"
    assert json.loads(out_file.read_text()) == stdout


def test_history_markdown(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _feature_change(repo)
    assert main(["-C", str(repo), "history", "--markdown", "--no-cache"]) == EXIT_OK
    assert "History" in capsys.readouterr().out


def test_history_precedent_flag(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _feature_change(repo)
    assert (
        main(["-C", str(repo), "history", "--json", "--no-cache", "--precedent", "None"]) == EXIT_OK
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["precedents"][0]["needle"] == "None"


def test_history_usage_errors(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    assert main(["-C", str(outside), "history"]) == EXIT_USAGE
    assert "not inside a git repository" in capsys.readouterr().err
    assert main(["-C", str(repo), "history", "--base", "no-such-ref"]) == EXIT_USAGE
    assert "unknown base ref" in capsys.readouterr().err


def test_history_infra_error(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from reviewgate import cli
    from reviewgate.git import GitError

    def boom(*_: object, **__: object) -> object:
        raise GitError("simulated")

    monkeypatch.setattr(cli, "analyze", boom)
    assert main(["-C", str(repo), "history", "--working-tree"]) == EXIT_INFRA
    assert "git failed: simulated" in capsys.readouterr().err


def test_no_remote_falls_back_to_working_tree(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "remote", "remove", "origin")
    (repo / "app.py").write_text("def add(a, b):\n    return a + b + 1\n")
    assert main(["-C", str(repo), "history", "--no-cache", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["head"] == "working tree"
    assert len(payload["hunks"]) == 1
