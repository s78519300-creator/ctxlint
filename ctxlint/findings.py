"""Rule catalogue and the Finding type."""

from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITIES = ("critical", "high", "medium", "low")

# rule id: (name, category, why it matters / how to fix)
RULES: dict[str, tuple[str, str, str]] = {
    "CL101": ("dead-path", "drift", "The file points the agent at a path that does not exist. Update or remove the reference."),
    "CL102": ("dead-import", "drift", "An @import points to a missing file, so that context is silently lost. Fix the path or remove the import."),
    "CL103": ("unknown-command", "drift", "The agent is told to run a script or target that does not exist, so it will fail and improvise. Match the real scripts."),
    "CL104": ("wrong-package-manager", "drift", "The instructions name a different package manager than the lockfile uses. The agent may create a second lockfile."),
    "CL201": ("conflicting-rules", "conflict", "Two instructions contradict each other. Agents resolve this unpredictably. Keep one and delete the other."),
    "CL202": ("duplicated-rule", "conflict", "The same instruction appears in several files, so you pay for it twice and must update it twice. Keep it in one shared file."),
    "CL203": ("never-applied", "conflict", "This rules file can never be loaded because of its frontmatter. Add a description, globs/applyTo, or alwaysApply."),
    "CL204": ("unmatched-scope", "conflict", "The file's globs or applyTo pattern matches no files in the repo, so it never applies."),
    "CL301": ("context-bloat", "cost", "Always-loaded context is sent in every session. Move rarely needed detail into scoped or on-demand files."),
    "CL302": ("vague-rule", "cost", "Generic advice the model already follows costs tokens and adds nothing. State a concrete, checkable rule or remove it."),
    "CL401": ("invisible-unicode", "security", "Invisible characters can hide instructions from reviewers (the 'Rules File Backdoor' technique). Remove them and review the decoded text."),
    "CL402": ("hidden-comment-instruction", "security", "HTML comments are invisible on GitHub but read by agents. Do not put instructions where reviewers cannot see them."),
    "CL403": ("dangerous-directive", "security", "This instruction weakens safety checks or runs remote code. Remove it, or scope it to a case a human approves."),
    "CL404": ("exfiltration-url", "security", "The file tells the agent to send data to an external URL. Verify it is intended."),
    "CL405": ("secret", "security", "A credential is written in a file every agent session reads and that is usually committed. Remove it and rotate it."),
    "CL406": ("encoded-payload", "security", "Encoded text in instructions is a common way to smuggle commands past review. Decode and review it."),
}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    line: int
    message: str
    evidence: str = ""

    @property
    def name(self) -> str:
        return RULES[self.rule][0]

    @property
    def category(self) -> str:
        return RULES[self.rule][1]

    @property
    def fix(self) -> str:
        return RULES[self.rule][2]

    def to_dict(self) -> dict:
        return {**asdict(self), "name": self.name, "category": self.category, "fix": self.fix}


def at_least(severity: str, threshold: str) -> bool:
    return SEVERITIES.index(severity) <= SEVERITIES.index(threshold)


def sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITIES.index(f.severity), f.path, f.line, f.rule))
