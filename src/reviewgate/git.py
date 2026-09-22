"""Thin git wrapper: repo discovery, base resolution, diff text and the raw
history queries the history agent is built on."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    """`git` could not be run or returned a failure."""


def git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise GitError("git executable not found on PATH") from exc
    if completed.returncode != 0:
        raise GitError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def repo_root(start: Path) -> Path | None:
    try:
        out = git(start, "rev-parse", "--show-toplevel")
    except GitError:
        return None
    return Path(out.strip())


def head_sha(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def short_sha(root: Path, rev: str) -> str:
    return git(root, "rev-parse", "--short", rev).strip()


def ref_exists(root: Path, ref: str) -> bool:
    try:
        git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except GitError:
        return False
    return True


def default_branch(root: Path, remote: str) -> str | None:
    """`origin/main`-style ref for the remote's HEAD, falling back to main/master."""
    try:
        out = git(root, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
        name = out.strip().removeprefix("refs/remotes/")
        if ref_exists(root, name):
            return name
    except GitError:
        pass
    for candidate in ("main", "master"):
        if ref_exists(root, f"{remote}/{candidate}"):
            return f"{remote}/{candidate}"
    return None


def merge_base(root: Path, a: str, b: str) -> str:
    return git(root, "merge-base", a, b).strip()


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    try:
        git(root, "merge-base", "--is-ancestor", ancestor, descendant)
    except GitError:
        return False
    return True


def path_exists_at(root: Path, rev: str, path: str) -> bool:
    try:
        git(root, "cat-file", "-e", f"{rev}:{path}")
    except GitError:
        return False
    return True


def line_count_at(root: Path, rev: str, path: str) -> int | None:
    try:
        text = git(root, "show", f"{rev}:{path}")
    except GitError:
        return None
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


@dataclass(frozen=True, slots=True)
class DiffSpec:
    """What to diff. `base=None` with `staged=False` means working tree vs HEAD."""

    base: str | None
    staged: bool = False

    @property
    def label(self) -> str:
        if self.staged:
            return "index"
        return self.base or "HEAD (working tree)"

    @property
    def old_rev(self) -> str:
        """The revision the `-` side of the diff was taken from. Working-tree
        and staged diffs compare against HEAD; a base ref compares against the
        merge base, which `git diff base...HEAD` resolves for us and which
        `merge_base()` resolves for callers that need the sha."""
        return "HEAD" if self.base is None else self.base


def diff_text(root: Path, spec: DiffSpec, context: int = 3) -> str:
    args = ["diff", f"--unified={context}", "--no-color", "--no-ext-diff", "--find-renames"]
    if spec.staged:
        args.append("--cached")
    elif spec.base is not None:
        args.append(f"{spec.base}...HEAD" if spec.base != "HEAD" else "HEAD")
    else:
        args.append("HEAD")
    return git(root, *args)


def resolve_old_rev(root: Path, spec: DiffSpec) -> str:
    """Full sha of the revision the diff's old side came from."""
    if spec.base is None or spec.base == "HEAD":
        return head_sha(root)
    return merge_base(root, spec.base, "HEAD")


@dataclass(frozen=True, slots=True)
class CommitRef:
    sha: str
    date: str
    subject: str

    @property
    def short(self) -> str:
        return self.sha[:7]


_LOG_MARK = "@@RG@@"


def _parse_marked_log(text: str) -> list[CommitRef]:
    refs: list[CommitRef] = []
    for line in text.splitlines():
        if not line.startswith(_LOG_MARK):
            continue
        parts = line[len(_LOG_MARK) :].split("|", 2)
        if len(parts) != 3:
            continue
        refs.append(CommitRef(sha=parts[0], date=parts[1], subject=parts[2]))
    return refs


def line_history(
    root: Path,
    rev: str,
    path: str,
    start: int,
    end: int,
    *,
    days: int,
    limit: int = 20,
) -> list[CommitRef]:
    """Commits that touched `path:start-end` (as numbered at `rev`) within the
    last `days` days, newest first. Empty when git cannot follow the range."""
    try:
        out = git(
            root,
            "log",
            f"-L{start},{end}:{path}",
            "--no-patch",
            f"--since={days} days ago",
            f"-n{limit}",
            f"--format={_LOG_MARK}%H|%aI|%s",
            rev,
        )
    except GitError:
        return []
    return _parse_marked_log(out)


def blame_lines(root: Path, rev: str, path: str, start: int, end: int) -> dict[int, str]:
    """Map each line number in `start..end` (as numbered at `rev`) to the full
    sha of the commit that introduced it."""
    try:
        out = git(root, "blame", f"-L{start},{end}", "--porcelain", rev, "--", path)
    except GitError:
        return {}
    origins: dict[int, str] = {}
    for line in out.splitlines():
        if line.startswith("\t"):
            continue
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 40 and parts[2].isdigit():
            origins[int(parts[2])] = parts[0]
    return origins


@dataclass(frozen=True, slots=True)
class CommitDetail:
    sha: str
    date: str
    subject: str
    body: str
    files: tuple[str, ...]


_FILES_MARK = "@@RGFILES@@"


def commit_detail(root: Path, sha: str) -> CommitDetail | None:
    try:
        out = git(
            root,
            "show",
            "--no-color",
            "--name-only",
            f"--format=%aI%n%s%n%b%n{_FILES_MARK}",
            sha,
        )
    except GitError:
        return None
    head, sep, tail = out.partition(_FILES_MARK)
    if not sep:
        return None
    lines = head.split("\n")
    date, subject = lines[0], lines[1] if len(lines) > 1 else ""
    body = "\n".join(lines[2:]).strip()
    files = tuple(f for f in tail.splitlines() if f.strip())
    return CommitDetail(sha=sha, date=date, subject=subject, body=body, files=files)


_COMMIT_MARK = "@@RGC@@"


def commit_file_sets(
    root: Path, rev_range: str, *, limit: int | None
) -> list[tuple[str, list[str]]]:
    """(sha, files) for each commit in `rev_range`, newest first, merges skipped."""
    args = ["log", "--no-merges", "--name-only", f"--format={_COMMIT_MARK}%H"]
    if limit is not None:
        args.append(f"-n{limit}")
    args.append(rev_range)
    out = git(root, *args)
    result: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in out.splitlines():
        if line.startswith(_COMMIT_MARK):
            current = []
            result.append((line[len(_COMMIT_MARK) :], current))
        elif line.strip() and current is not None:
            current.append(line.strip())
    return result


def pickaxe(
    root: Path, needle: str, *, limit: int, path: str | None = None, rev: str | None = None
) -> list[CommitRef]:
    """`git log -S<needle>`: commits that added or removed an occurrence,
    walking back from `rev` (HEAD when None)."""
    args = ["log", f"-S{needle}", f"-n{limit}", f"--format={_LOG_MARK}%H|%aI|%s"]
    if rev is not None:
        args.append(rev)
    if path is not None:
        args.extend(["--", path])
    try:
        out = git(root, *args)
    except GitError:
        return []
    return _parse_marked_log(out)
