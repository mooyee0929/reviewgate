"""File co-change table: which files tend to land in the same commit.

The table is built from the last `window` non-merge commits, cached in
`.review/cochange.json` and updated incrementally with the commits that
arrived since the cached `scanned_to` sha. The incremental path appends, so
after a while the table covers more than `window` commits; `window` is a
floor, not a ceiling.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from ..git import GitError, commit_file_sets, git, head_sha, is_ancestor, path_exists_at
from ..model import Evidence, Finding, Severity, fingerprint
from .types import CoChangeGap

CACHE_RELPATH = Path(".review") / "cochange.json"
TABLE_VERSION = 1


def pair_key(a: str, b: str) -> str:
    return "\t".join(sorted((a, b)))


@dataclass(slots=True)
class CoChangeTable:
    scanned_to: str | None
    commits: int
    files: dict[str, int] = field(default_factory=dict)
    pairs: dict[str, int] = field(default_factory=dict)
    window: int = 500

    def to_dict(self) -> dict[str, object]:
        return {
            "version": TABLE_VERSION,
            "scanned_to": self.scanned_to,
            "commits": self.commits,
            "window": self.window,
            "files": dict(self.files),
            "pairs": dict(self.pairs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CoChangeTable:
        if data.get("version") != TABLE_VERSION:
            raise ValueError(f"unsupported cochange table version {data.get('version')!r}")
        scanned_to = data.get("scanned_to")
        commits = data.get("commits")
        window = data.get("window")
        files = data.get("files")
        pairs = data.get("pairs")
        if scanned_to is not None and not isinstance(scanned_to, str):
            raise ValueError("scanned_to must be a string or null")
        if not isinstance(commits, int) or not isinstance(window, int):
            raise ValueError("commits and window must be integers")
        if not isinstance(files, dict) or not isinstance(pairs, dict):
            raise ValueError("files and pairs must be objects")
        return cls(
            scanned_to=scanned_to,
            commits=commits,
            files=_int_map(files),
            pairs=_int_map(pairs),
            window=window,
        )

    def partners(
        self, file: str, *, min_together: int = 3, min_ratio: float = 0.5
    ) -> list[tuple[str, int]]:
        total = self.files.get(file, 0)
        if total == 0:
            return []
        found: list[tuple[str, int]] = []
        for key, together in self.pairs.items():
            a, _, b = key.partition("\t")
            if file == a:
                partner = b
            elif file == b:
                partner = a
            else:
                continue
            if together < min_together or together / total < min_ratio:
                continue
            found.append((partner, together))
        found.sort(key=lambda item: (-item[1], item[0]))
        return found


def _int_map(raw: dict[object, object]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, int):
            raise ValueError("table maps must be str -> int")
        out[key] = value
    return out


def load_table(root: Path) -> CoChangeTable | None:
    path = root / CACHE_RELPATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return CoChangeTable.from_dict(data)
    except ValueError:
        return None


def save_table(root: Path, table: CoChangeTable) -> None:
    path = root / CACHE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table.to_dict(), sort_keys=True, indent=1) + "\n", encoding="utf-8")


def count_commits(
    table: CoChangeTable,
    commits: list[tuple[str, list[str]]],
    *,
    max_files_per_commit: int,
) -> None:
    for _sha, files in commits:
        paths = sorted(set(files))
        if not paths or len(paths) > max_files_per_commit:
            continue
        table.commits += 1
        for path in paths:
            table.files[path] = table.files.get(path, 0) + 1
        for a, b in combinations(paths, 2):
            key = pair_key(a, b)
            table.pairs[key] = table.pairs.get(key, 0) + 1


def _resolve(root: Path, rev: str) -> str:
    return head_sha(root) if rev == "HEAD" else git(root, "rev-parse", rev).strip()


def build_table(
    root: Path, *, head: str = "HEAD", window: int = 500, max_files_per_commit: int = 30
) -> CoChangeTable:
    table = CoChangeTable(scanned_to=None, commits=0, window=window)
    count_commits(
        table, commit_file_sets(root, head, limit=window), max_files_per_commit=max_files_per_commit
    )
    table.scanned_to = _resolve(root, head)
    return table


def update_table(
    root: Path, table: CoChangeTable, *, head: str = "HEAD", max_files_per_commit: int = 30
) -> CoChangeTable:
    if table.scanned_to is None:
        raise ValueError("cannot update a table that was never scanned")
    new = commit_file_sets(root, f"{table.scanned_to}..{head}", limit=None)
    count_commits(table, new, max_files_per_commit=max_files_per_commit)
    table.scanned_to = _resolve(root, head)
    return table


def build_or_update_table(
    root: Path,
    *,
    head: str = "HEAD",
    window: int = 500,
    max_files_per_commit: int = 30,
    use_cache: bool = True,
) -> tuple[CoChangeTable, str]:
    if not use_cache:
        table = build_table(
            root, head=head, window=window, max_files_per_commit=max_files_per_commit
        )
        return table, f"cochange: built from {table.commits} commits (cache disabled)"
    cached = load_table(root)
    stale = (
        cached is None
        or cached.window != window
        or cached.scanned_to is None
        or not is_ancestor(root, cached.scanned_to, head)
    )
    if cached is None or stale:
        table = build_table(
            root, head=head, window=window, max_files_per_commit=max_files_per_commit
        )
        save_table(root, table)
        return table, f"cochange: rebuilt from {table.commits} commits"
    before = cached.commits
    try:
        update_table(root, cached, head=head, max_files_per_commit=max_files_per_commit)
    except GitError:
        table = build_table(
            root, head=head, window=window, max_files_per_commit=max_files_per_commit
        )
        save_table(root, table)
        return table, f"cochange: rebuilt from {table.commits} commits"
    save_table(root, cached)
    added = cached.commits - before
    if added:
        return cached, f"cochange: updated (+{added} commits, {cached.commits} total)"
    return cached, f"cochange: cache current ({cached.commits} commits)"


def co_change_gaps(
    root: Path,
    table: CoChangeTable,
    changed_paths: Iterable[str],
    *,
    head: str = "HEAD",
    min_together: int = 3,
    min_ratio: float = 0.5,
) -> list[CoChangeGap]:
    changed = set(changed_paths)
    gaps: list[CoChangeGap] = []
    seen: set[tuple[str, str]] = set()
    for file in sorted(changed):
        for partner, together in table.partners(
            file, min_together=min_together, min_ratio=min_ratio
        ):
            if partner in changed or (file, partner) in seen:
                continue
            if not path_exists_at(root, head, partner):
                continue
            seen.add((file, partner))
            gaps.append(
                CoChangeGap(
                    file=file, partner=partner, together=together, file_total=table.files[file]
                )
            )
    gaps.sort(key=lambda g: (-g.together, g.file, g.partner))
    return gaps


def gap_findings(gaps: list[CoChangeGap]) -> list[Finding]:
    findings: list[Finding] = []
    for gap in gaps:
        findings.append(
            Finding(
                file=gap.file,
                line=1,
                severity=Severity.LOW,
                dimension="history",
                category="co-change-missing",
                title=f"{gap.partner} usually changes with {gap.file} but is not in this diff",
                scenario=(
                    f"{gap.partner} changed together with {gap.file} in {gap.together} of "
                    f"{gap.file_total} recent commits; a change here that does not touch it "
                    "may leave the pair inconsistent"
                ),
                fix=f"check whether {gap.partner} needs a matching change",
                source="history",
                origin="history:cochange",
                confidence=round(min(0.9, gap.ratio), 2),
                evidence=(Evidence("cochange", gap.partner, f"{gap.together}/{gap.file_total}"),),
                line_text=gap.partner,
                fingerprint=fingerprint(gap.file, "history", "co-change-missing", gap.partner),
            )
        )
    return findings
