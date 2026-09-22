from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=env
    )
    return proc.stdout


def commit_files(
    repo: Path, files: dict[str, str], message: str, *, date: str | None = None
) -> str:
    """Write every file, stage them, commit with `message` (optionally at ISO
    `date`, so tests can place commits inside or outside the lookback window).
    Returns the full sha."""
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git(repo, "add", rel)
    git(repo, "commit", "-q", "--allow-empty", "-m", message, date=date)
    return git(repo, "rev-parse", "HEAD").strip()


def commit_file(
    repo: Path, rel: str, content: str, message: str, *, date: str | None = None
) -> str:
    return commit_files(repo, {rel: content}, message, date=date)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo on branch `feature` with a bare `origin` whose HEAD is `main`.
    `main` holds one commit adding `app.py`."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "init", "-q", "--initial-branch=main", str(work))
    commit_file(work, "app.py", "def add(a, b):\n    return a + b\n", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "-u", "origin", "main")
    git(work, "remote", "set-head", "origin", "main")
    git(work, "checkout", "-q", "-b", "feature")
    return work
