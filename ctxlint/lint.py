"""Run every check over a repository and score the result."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checks import ALL_CHECKS, check_bloat, tool_budgets
from .files import ContextFile, RepoFacts, discover, gather_facts
from .findings import Finding, sort_findings

PENALTY = {"critical": 25, "high": 8, "medium": 3, "low": 1}


@dataclass
class Result:
    root: Path
    files: list[ContextFile]
    facts: RepoFacts
    findings: list[Finding]
    budgets: dict[str, tuple[int, list[str]]] = field(default_factory=dict)

    @property
    def grade(self) -> tuple[str, int]:
        return grade(self.findings)


def grade(findings: list[Finding]) -> tuple[str, int]:
    """Letter grade and score. Any critical finding means F."""
    score = max(0, 100 - sum(PENALTY[f.severity] for f in findings))
    if any(f.severity == "critical" for f in findings):
        score = min(score, 39)
    letter = next(g for g, floor in (("A", 90), ("B", 75), ("C", 60), ("D", 40), ("F", 0)) if score >= floor)
    return letter, score


def lint(root: str | Path, file_budget: int = 2500, tool_budget: int = 5000, disabled: set[str] = frozenset()) -> Result:
    root = Path(root).resolve()
    files = discover(root)
    facts = gather_facts(root)
    findings: list[Finding] = []
    for check in ALL_CHECKS:
        if check is check_bloat:
            findings += check(files, facts, file_budget, tool_budget)
        else:
            findings += check(files, facts)
    by_path = {cf.rel: cf.lines for cf in files}

    def suppressed(f: Finding) -> bool:
        lines = by_path.get(f.path, [])
        previous = lines[f.line - 2] if 1 < f.line <= len(lines) + 1 else ""
        if "ctxlint-disable-next-line" not in previous:
            return False
        named = previous.split("ctxlint-disable-next-line", 1)[1].replace("-->", "").split()
        return not named or f.rule in named

    findings = [f for f in findings if f.rule not in disabled and not suppressed(f)]
    unique = list(dict.fromkeys(findings))
    return Result(root, files, facts, sort_findings(unique), tool_budgets(files, facts))
