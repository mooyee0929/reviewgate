"""Terminal, markdown and JSON rendering of a HistoryReport."""

from __future__ import annotations

import json

from ..model import Finding, Severity
from .types import CoChangeGap, HistoryReport, HunkHistory

_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_SEVERITY_COLORS = {
    Severity.CRITICAL: "\033[1;31m",
    Severity.HIGH: "\033[31m",
    Severity.MEDIUM: _YELLOW,
    Severity.LOW: _CYAN,
}


def _paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


def _sorted_findings(report: HistoryReport) -> list[Finding]:
    return sorted(report.findings, key=lambda f: (-f.severity, f.file, f.line))


def _files_in_order(report: HistoryReport) -> list[str]:
    order: list[str] = []
    for h in report.hunks:
        if h.file not in order:
            order.append(h.file)
    for g in report.gaps:
        if g.file not in order:
            order.append(g.file)
    return order


def _hunk_lines(h: HunkHistory, color: bool) -> list[str]:
    tag = f"   {_paint('[HOT]', _YELLOW, color)}" if h.is_hot else ""
    lines = [f"  L{h.new_start}-{h.new_end}  {h.context_line()}{tag}"]
    for o in h.origins:
        if not o.is_regression_risk:
            continue
        keywords = ", ".join(o.regression_keywords)
        tests = f"  tests: {', '.join(o.tests_in_commit)}" if o.tests_in_commit else ""
        lines.append(
            f'          removed L{o.old_line} came from {o.commit.short} "{o.commit.subject}"'
            f"  ({keywords}){tests}"
        )
    return lines


def _gap_line(g: CoChangeGap) -> str:
    return f"  usually changes with {g.partner} ({g.together}/{g.file_total}), not in this diff"


def _finding_lines(f: Finding, color: bool) -> list[str]:
    tag = _paint(f.severity.label.upper(), _SEVERITY_COLORS[f.severity], color)
    pad = " " * max(0, 9 - len(f.severity.label))
    lines = [
        f"  {tag}{pad}{f.file}:{f.line}  [{f.category}] {f.title}",
        f"           {f.scenario}",
    ]
    if f.fix:
        lines.append(f"           fix: {f.fix}")
    lines.append(f"           {_paint(f'({f.origin} conf={f.confidence:.2f})', _DIM, color)}")
    return lines


def render_terminal(report: HistoryReport, *, color: bool = False) -> str:
    hot = sum(1 for h in report.hunks if h.is_hot)
    out = [
        f"reviewgate history: {report.base} -> {report.head}, "
        f"{len(report.hunks)} hunk(s), {hot} hot, "
        f"{len(report.gaps)} co-change gap(s), {len(report.findings)} finding(s)"
    ]
    out.extend(_paint(f"  note: {note}", _DIM, color) for note in report.notes)
    for path in _files_in_order(report):
        out.extend(["", path])
        for h in report.hunks:
            if h.file == path:
                out.extend(_hunk_lines(h, color))
        out.extend(_gap_line(g) for g in report.gaps if g.file == path)
    findings = _sorted_findings(report)
    if findings:
        out.extend(["", "findings"])
        for f in findings:
            out.extend(_finding_lines(f, color))
    if report.precedents:
        out.extend(["", "precedents"])
        for p in report.precedents:
            if not p.commits:
                out.append(f"  {p.needle}  none")
                continue
            for c in p.commits:
                out.append(f'  {p.needle}  {c.short} {c.date[:10]} "{c.subject}"')
    return "\n".join(out) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: HistoryReport) -> str:
    hot = sum(1 for h in report.hunks if h.is_hot)
    lines = [
        "### History",
        "",
        f"base `{report.base}`, head `{report.head}`: {len(report.hunks)} hunk(s), "
        f"{hot} hot, {len(report.gaps)} co-change gap(s), {len(report.findings)} finding(s).",
        "",
    ]
    for note in report.notes:
        lines.append(f"- _{_cell(note)}_")
    if report.notes:
        lines.append("")
    if report.hunks:
        lines += [
            f"| file | lines | changes ({report.days}d) | last commit | regression origins |",
            "|---|---|---|---|---|",
        ]
        for h in report.hunks:
            last = f"`{h.commits[0].short}` {_cell(h.commits[0].subject)}" if h.commits else ""
            origins = "<br>".join(
                f"L{o.old_line} from `{o.commit.short}` {_cell(o.commit.subject)}"
                for o in h.origins
                if o.is_regression_risk
            )
            changes = f"{h.change_count}" + (" (hot)" if h.is_hot else "")
            lines.append(
                f"| `{h.file}` | {h.new_start}-{h.new_end} | {changes} | {last} | {origins} |"
            )
        lines.append("")
    if report.gaps:
        for g in report.gaps:
            lines.append(
                f"- `{g.partner}` usually changes with `{g.file}` "
                f"({g.together}/{g.file_total}) but is not in this diff"
            )
        lines.append("")
    findings = _sorted_findings(report)
    if findings:
        lines += ["| severity | location | category | title | fix |", "|---|---|---|---|---|"]
        for f in findings:
            lines.append(
                f"| {f.severity.label} | `{f.file}:{f.line}` | {f.category} | "
                f"{_cell(f.title)} | {_cell(f.fix or '')} |"
            )
        lines.append("")
    if report.precedents:
        for p in report.precedents:
            hits = ", ".join(f"`{c.short}` {_cell(c.subject)}" for c in p.commits) or "none"
            lines.append(f"- `{p.needle}`: {hits}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_json(report: HistoryReport) -> str:
    return json.dumps(report.to_dict(), indent=2, sort_keys=False) + "\n"
