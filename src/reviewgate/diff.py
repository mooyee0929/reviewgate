"""Parse unified diff text into changed files with their added and removed lines."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Status = Literal["added", "modified", "deleted", "renamed"]

_DIFF_HEADER = re.compile(r"^diff --git a/(.*?) b/(.*)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True, slots=True)
class AddedLine:
    number: int
    text: str


@dataclass(frozen=True, slots=True)
class RemovedLine:
    """A `-` line; `number` is its line number on the OLD side."""

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added: tuple[AddedLine, ...]
    removed: tuple[RemovedLine, ...]
    raw: str

    @property
    def old_end(self) -> int:
        """Last old-side line covered by the hunk (== old_start - 1 for pure inserts)."""
        return self.old_start + self.old_count - 1

    @property
    def new_end(self) -> int:
        return self.new_start + self.new_count - 1


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    status: Status
    hunks: tuple[Hunk, ...]
    old_path: str | None = None
    is_binary: bool = False

    @property
    def added_lines(self) -> list[AddedLine]:
        return [line for hunk in self.hunks for line in hunk.added]

    @property
    def removed_lines(self) -> list[RemovedLine]:
        return [line for hunk in self.hunks for line in hunk.removed]

    @property
    def changed_line_numbers(self) -> frozenset[int]:
        return frozenset(line.number for line in self.added_lines)

    @property
    def added_line_count(self) -> int:
        return sum(len(h.added) for h in self.hunks)

    @property
    def source_path(self) -> str:
        """Path on the old side (rename-aware)."""
        return self.old_path or self.path


def parse_diff(text: str) -> list[ChangedFile]:
    files: list[ChangedFile] = []
    blocks = re.split(r"(?m)^(?=diff --git )", text)
    for block in blocks:
        if not block.startswith("diff --git "):
            continue
        parsed = _parse_block(block)
        if parsed is not None:
            files.append(parsed)
    return files


def _parse_block(block: str) -> ChangedFile | None:
    lines = block.splitlines()
    header = _DIFF_HEADER.match(lines[0])
    if header is None:
        return None
    old_path, new_path = header.group(1), header.group(2)
    status: Status = "modified"
    is_binary = False
    body_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "deleted"
        elif line.startswith("rename from "):
            status = "renamed"
        elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
            is_binary = True
        elif line.startswith("@@"):
            body_start = i
            break
    hunks = tuple(_parse_hunks(lines[body_start:]))
    if status == "deleted":
        return ChangedFile(path=old_path, status=status, hunks=hunks, is_binary=is_binary)
    return ChangedFile(
        path=new_path,
        status=status,
        hunks=hunks,
        old_path=old_path if old_path != new_path else None,
        is_binary=is_binary,
    )


def _parse_hunks(lines: list[str]) -> Iterable[Hunk]:
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if current:
                yield _build_hunk(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        yield _build_hunk(current)


def _build_hunk(lines: list[str]) -> Hunk:
    match = _HUNK.match(lines[0])
    if match is None:
        raise ValueError(f"malformed hunk header: {lines[0]!r}")
    old_start = int(match.group(1))
    old_count = int(match.group(2)) if match.group(2) is not None else 1
    new_start = int(match.group(3))
    new_count = int(match.group(4)) if match.group(4) is not None else 1
    added: list[AddedLine] = []
    removed: list[RemovedLine] = []
    new_number = new_start
    old_number = old_start
    for line in lines[1:]:
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            added.append(AddedLine(number=new_number, text=line[1:]))
            new_number += 1
        elif line.startswith("-"):
            removed.append(RemovedLine(number=old_number, text=line[1:]))
            old_number += 1
        else:
            new_number += 1
            old_number += 1
    return Hunk(
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        added=tuple(added),
        removed=tuple(removed),
        raw="\n".join(lines),
    )


def glob_match(path: str, pattern: str) -> bool:
    """fnmatch with `**/` (any leading directories) and `/**` (any suffix) support."""
    if pattern.startswith("**/"):
        tail = pattern[3:]
        parts = path.split("/")
        return any(fnmatch.fnmatchcase("/".join(parts[i:]), tail) for i in range(len(parts)))
    if pattern.endswith("/**"):
        head = pattern[:-3]
        return path == head or path.startswith(head + "/")
    return fnmatch.fnmatchcase(path, pattern)


def is_ignored(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)
