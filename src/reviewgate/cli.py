"""`reviewgate` command line. Only the `history` subcommand exists so far."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .git import DiffSpec, GitError, default_branch, ref_exists, repo_root
from .history.agent import HistoryOptions, analyze
from .history.report import render_json, render_markdown, render_terminal

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INFRA = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewgate")
    parser.add_argument("-C", dest="directory", default=".", help="run as if started in DIR")
    sub = parser.add_subparsers(dest="command", required=True)

    hist = sub.add_parser(
        "history",
        help="git-derived priors for a diff: hot spots, regression risk, co-change gaps",
    )
    scope = hist.add_mutually_exclusive_group()
    scope.add_argument(
        "--base",
        help="review base...HEAD (default: origin's default branch when on a branch that has "
        "one, else the working tree)",
    )
    scope.add_argument("--staged", action="store_true", help="review the index against HEAD")
    scope.add_argument(
        "--working-tree", action="store_true", help="review uncommitted changes against HEAD"
    )
    hist.add_argument("--days", type=int, default=90, help="lookback window for hot spots")
    hist.add_argument("--max-hunks", type=int, default=40)
    hist.add_argument("--window", type=int, default=500, help="commits in the co-change table")
    hist.add_argument("--min-together", type=int, default=3)
    hist.add_argument("--min-ratio", type=float, default=0.5)
    hist.add_argument("--no-cache", action="store_true", help="do not read or write .review/")
    hist.add_argument("--no-cochange", action="store_true", help="skip the co-change table")
    hist.add_argument(
        "--precedent",
        action="append",
        default=[],
        metavar="IDENT",
        help="git log -S lookup for IDENT (repeatable)",
    )
    hist.add_argument(
        "--auto-precedents",
        type=int,
        default=0,
        metavar="N",
        help="also look up N identifiers that disappeared in the diff",
    )
    fmt = hist.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--markdown", action="store_true")
    hist.add_argument("--json-out", type=Path, metavar="FILE", help="also write JSON to FILE")
    return parser


def _resolve_spec(root: Path, args: argparse.Namespace) -> DiffSpec | str:
    if args.staged:
        return DiffSpec(base=None, staged=True)
    if args.working_tree:
        return DiffSpec(base=None)
    if args.base:
        if not ref_exists(root, args.base):
            return f"unknown base ref {args.base!r}"
        return DiffSpec(base=args.base)
    base = default_branch(root, "origin")
    if base is None:
        return DiffSpec(base=None)
    return DiffSpec(base=base)


def _history(args: argparse.Namespace) -> int:
    root = repo_root(Path(args.directory))
    if root is None:
        print(f"reviewgate: {args.directory} is not inside a git repository", file=sys.stderr)
        return EXIT_USAGE
    spec = _resolve_spec(root, args)
    if isinstance(spec, str):
        print(f"reviewgate: {spec}", file=sys.stderr)
        return EXIT_USAGE
    options = HistoryOptions(
        days=args.days,
        max_hunks=args.max_hunks,
        cochange_window=args.window,
        min_together=args.min_together,
        min_ratio=args.min_ratio,
        use_cache=not args.no_cache,
        cochange=not args.no_cochange,
        precedent_needles=tuple(args.precedent),
        auto_precedents=args.auto_precedents,
    )
    try:
        report = analyze(root, spec, options)
    except GitError as exc:
        print(f"reviewgate: git failed: {exc}", file=sys.stderr)
        return EXIT_INFRA
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(render_json(report), encoding="utf-8")
    if args.json:
        sys.stdout.write(render_json(report))
    elif args.markdown:
        sys.stdout.write(render_markdown(report))
    else:
        sys.stdout.write(render_terminal(report, color=sys.stdout.isatty()) + "\n")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "history":
        return _history(args)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
