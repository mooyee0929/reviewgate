"""Orchestrates the history agent: diff -> per-hunk history, co-change gaps,
precedents -> HistoryReport. No LLM call anywhere in this package."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..diff import ChangedFile, is_ignored, parse_diff
from ..git import DiffSpec, diff_text, resolve_old_rev, short_sha
from .cochange import build_or_update_table, co_change_gaps, gap_findings
from .lines import file_histories, regression_findings
from .precedent import identifier_needles, precedents
from .types import HistoryReport

DEFAULT_IGNORE: tuple[str, ...] = (
    "*.lock",
    "**/*.lock",
    "package-lock.json",
    "**/package-lock.json",
    "uv.lock",
    "**/uv.lock",
    "*.min.*",
    "**/*.min.*",
    "vendor/**",
    "**/vendor/**",
    "node_modules/**",
    "**/node_modules/**",
    "dist/**",
    "build/**",
    "*.png",
    "*.jpg",
    "*.gif",
    "*.pdf",
    "**/*.png",
    "**/*.jpg",
    "**/*.gif",
    "**/*.pdf",
)


@dataclass(frozen=True, slots=True)
class HistoryOptions:
    days: int = 90
    max_hunks: int = 40
    commits_per_hunk: int = 20
    cochange_window: int = 500
    max_files_per_commit: int = 30
    min_together: int = 3
    min_ratio: float = 0.5
    use_cache: bool = True
    cochange: bool = True
    precedent_needles: tuple[str, ...] = ()
    auto_precedents: int = 0
    """When > 0, also look up this many identifiers that vanished from the diff."""
    ignore: tuple[str, ...] = DEFAULT_IGNORE


def select_files(files: list[ChangedFile], ignore: tuple[str, ...]) -> list[ChangedFile]:
    return [f for f in files if not f.is_binary and not is_ignored(f.path, ignore)]


def analyze_files(
    root: Path,
    files: list[ChangedFile],
    *,
    old_rev: str,
    base_label: str,
    head_label: str,
    options: HistoryOptions | None = None,
) -> HistoryReport:
    """The core, independent of how the diff was obtained (callers that already
    hold parsed `ChangedFile`s, such as a pipeline stage, enter here)."""
    options = options or HistoryOptions()
    report = HistoryReport(base=base_label, head=head_label, days=options.days)
    kept = select_files(files, options.ignore)
    skipped = len(files) - len(kept)
    if skipped:
        report.notes.append(f"history: ignored {skipped} file(s) by pattern")

    hunks, notes = file_histories(
        root,
        old_rev,
        kept,
        days=options.days,
        limit=options.commits_per_hunk,
        max_hunks=options.max_hunks,
    )
    report.hunks.extend(hunks)
    report.notes.extend(notes)
    report.findings.extend(regression_findings(hunks))

    if options.cochange:
        table, note = build_or_update_table(
            root,
            window=options.cochange_window,
            max_files_per_commit=options.max_files_per_commit,
            use_cache=options.use_cache,
        )
        report.notes.append(note)
        changed_paths = [f.path for f in files] + [f.source_path for f in files]
        gaps = [
            gap
            for gap in co_change_gaps(
                root,
                table,
                changed_paths,
                min_together=options.min_together,
                min_ratio=options.min_ratio,
            )
            if not is_ignored(gap.file, options.ignore)
            and not is_ignored(gap.partner, options.ignore)
        ]
        report.gaps.extend(gaps)
        report.findings.extend(gap_findings(gaps))

    needles = list(options.precedent_needles)
    if options.auto_precedents > 0:
        for needle in identifier_needles(kept, max_needles=options.auto_precedents):
            if needle not in needles:
                needles.append(needle)
    for needle in needles:
        found = precedents(root, needle, rev=old_rev)
        if found.commits:
            report.precedents.append(found)

    report.findings.sort(key=lambda f: (-f.severity, f.file, f.line))
    return report


def analyze(root: Path, spec: DiffSpec, options: HistoryOptions | None = None) -> HistoryReport:
    """Diff `spec` in `root` and run every history query over it."""
    files = parse_diff(diff_text(root, spec))
    old_rev = resolve_old_rev(root, spec)
    head_label = (
        "working tree" if spec.base is None and not spec.staged else short_sha(root, "HEAD")
    )
    if spec.staged:
        head_label = "index"
    return analyze_files(
        root,
        files,
        old_rev=old_rev,
        base_label=f"{spec.label} ({old_rev[:7]})",
        head_label=head_label,
        options=options,
    )
