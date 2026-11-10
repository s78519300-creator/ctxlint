# Working on ctxlint

ctxlint is a zero-dependency Python 3.10+ CLI that lints the instruction files AI coding agents read.

## Layout

- `ctxlint/files.py` finds context files and gathers repo facts (scripts, lockfiles, Makefile targets).
- `ctxlint/checks.py` holds every rule. Each check returns `Finding` objects with a file and line.
- `ctxlint/report.py` renders text, JSON, SARIF, HTML and the badge.
- `examples/demo-repo/` is a deliberately broken fixture. Do not fix it.

## Commands

- Install for development with `pip install -e ".[dev]"`.
- Run the tests with `pytest`. Every new rule needs a test that shows it firing and a test that shows it staying quiet.

## Rules for changes

- Keep the package free of third-party dependencies.
- Prefer missing an edge case over a false positive. Check new rules against real repositories before adding them.
- Never print a secret value in a finding. Show at most the first few characters.
