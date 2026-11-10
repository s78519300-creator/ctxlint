"""Command-line interface: ``ctxlint [PATH]``."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from . import __version__
from .checks import INVISIBLE
from .files import discover
from .findings import SEVERITIES, at_least
from .lint import lint
from .report import badge_svg, to_html, to_json, to_sarif, to_text


def cmd_check(args: argparse.Namespace) -> int:
    disabled = {rule.strip().upper() for rule in (args.disable or "").split(",") if rule.strip()}
    result = lint(args.path, args.file_budget, args.budget, disabled)
    renderers = {
        "text": lambda: to_text(result, color=sys.stdout.isatty() and not args.output),
        "json": lambda: to_json(result),
        "sarif": lambda: to_sarif(result),
        "html": lambda: to_html(result, budget=args.budget),
    }
    output = renderers[args.format]()
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        letter, score = result.grade
        print(f"Wrote {args.format} report to {args.output}: grade {letter} ({score}/100), {len(result.findings)} findings")
    else:
        print(output)
    if args.badge:
        Path(args.badge).write_text(badge_svg(result.grade[0]), encoding="utf-8")
    return 1 if any(at_least(f.severity, args.fail_on) for f in result.findings) else 0


def cmd_fix(args: argparse.Namespace) -> int:
    """Remove invisible characters. Shows a diff; changes files only with --write."""
    changed = 0
    for cf in discover(Path(args.path)):
        if not INVISIBLE.search(cf.text):
            continue
        cleaned = INVISIBLE.sub("", cf.text)
        changed += 1
        diff = difflib.unified_diff(cf.text.splitlines(), cleaned.splitlines(), f"a/{cf.rel}", f"b/{cf.rel}", lineterm="")
        print("\n".join(INVISIBLE.sub("�", line) for line in diff))
        if args.write:
            cf.path.write_text(cleaned, encoding="utf-8")
    if not changed:
        print("Nothing to fix: no invisible characters found.")
    elif args.write:
        print(f"\nRemoved invisible characters from {changed} file(s). Review the decoded text in the report before trusting these files.")
    else:
        print(f"\n{changed} file(s) would change (� marks removed characters). Re-run with --write to apply.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctxlint", description="Lint the instruction files AI coding agents read.")
    parser.add_argument("--version", action="version", version=f"ctxlint {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    check = sub.add_parser("check", help="lint context files (default)")
    for p in (check,):
        p.add_argument("path", nargs="?", default=".", help="repository root (default: current folder)")
        p.add_argument("--format", choices=["text", "json", "sarif", "html"], default="text")
        p.add_argument("--output", "-o")
        p.add_argument("--badge", help="also write an SVG grade badge to this path")
        p.add_argument("--fail-on", choices=SEVERITIES, default="high")
        p.add_argument("--budget", type=int, default=5000, help="token budget for each tool's always-loaded context")
        p.add_argument("--file-budget", type=int, default=2500, help="token budget for a single always-loaded file")
        p.add_argument("--disable", help="comma-separated rule ids to skip, e.g. CL202,CL302")
    check.set_defaults(func=cmd_check)

    fix = sub.add_parser("fix", help="remove invisible characters (shows a diff; --write to apply)")
    fix.add_argument("path", nargs="?", default=".")
    fix.add_argument("--write", action="store_true")
    fix.set_defaults(func=cmd_fix)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to a legacy code page
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in {"check", "fix"} and argv[0] not in {"-h", "--help", "--version"}):
        argv.insert(0, "check")  # `ctxlint`, `ctxlint path` and `ctxlint --format html` all mean check
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
