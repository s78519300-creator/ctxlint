"""Find agent context files in a repository and gather facts about the repo."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".turbo"}

# (tool, how to recognise the file, loaded in every session?)
ROOT_FILES = [
    ("Claude Code", "CLAUDE.md", True),
    ("Claude Code", ".claude/CLAUDE.md", True),
    ("Claude Code", "CLAUDE.local.md", True),
    ("AGENTS.md (Codex, Cursor, Copilot and others)", "AGENTS.md", True),
    ("Gemini CLI", "GEMINI.md", True),
    ("GitHub Copilot", ".github/copilot-instructions.md", True),
    ("Cursor (legacy)", ".cursorrules", True),
    ("Windsurf", ".windsurfrules", True),
    ("Cline", ".clinerules", True),
]
NESTED_NAMES = {"AGENTS.md": "AGENTS.md (Codex, Cursor, Copilot and others)", "CLAUDE.md": "Claude Code", "GEMINI.md": "Gemini CLI"}


@dataclass
class ContextFile:
    path: Path
    rel: str
    tool: str
    always_loaded: bool
    text: str
    frontmatter: dict[str, str] = field(default_factory=dict)
    body_start_line: int = 1
    alias_of: str | None = None  # set when this file is a symlink to another context file

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token for English and code)."""
    return max(1, round(len(text) / 4)) if text else 0


def parse_frontmatter(text: str) -> tuple[dict[str, str], int]:
    if not text.startswith("---"):
        return {}, 1
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            data = {}
            for line in lines[1:index]:
                if ":" in line and not line.startswith((" ", "\t", "-")):
                    key, value = line.split(":", 1)
                    data[key.strip()] = value.strip().strip("'\"")
            return data, index + 2
    return {}, 1


def _walk(root: Path):
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.is_symlink():
                    stack.append(entry)
            else:
                yield entry


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def ignore_patterns(root: Path) -> list[str]:
    """Patterns from .ctxlintignore: one glob or folder per line, # for comments."""
    ignore = root / ".ctxlintignore"
    if not ignore.is_file():
        return []
    return [line.strip() for line in _read(ignore).splitlines() if line.strip() and not line.strip().startswith("#")]


def is_ignored(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        folder = pattern.rstrip("/")
        if rel == folder or rel.startswith(folder + "/") or fnmatch.fnmatch(rel, pattern):
            return True
    return False


def discover(root: Path) -> list[ContextFile]:
    root = root.resolve()
    found: dict[Path, ContextFile] = {}
    patterns = ignore_patterns(root)

    def add(path: Path, tool: str, always: bool) -> None:
        if path in found or not path.is_file() or is_ignored(path.relative_to(root).as_posix(), patterns):
            return
        text = _read(path)
        front, start = parse_frontmatter(text)
        rel = path.relative_to(root).as_posix()
        alias = None
        if path.is_symlink():
            target = path.resolve()
            try:
                alias = target.relative_to(root).as_posix()
            except ValueError:
                alias = str(target)
        found[path] = ContextFile(path, rel, tool, always, text, front, start, alias)

    for tool, rel, always in ROOT_FILES:
        candidate = root / rel
        if candidate.is_dir() and rel == ".clinerules":
            for item in sorted(candidate.glob("*.md")):
                add(item, tool, True)
        else:
            add(candidate, tool, always)

    for path in _walk(root):
        rel = path.relative_to(root).as_posix()
        if path.parent != root and path.name in NESTED_NAMES:
            add(path, NESTED_NAMES[path.name], False)
        elif rel.startswith(".cursor/rules/") and path.suffix in {".mdc", ".md"}:
            front, _ = parse_frontmatter(_read(path))
            add(path, "Cursor", front.get("alwaysApply", "").lower() == "true")
        elif rel.startswith(".github/instructions/") and path.name.endswith(".md"):
            add(path, "GitHub Copilot", False)
        elif rel.startswith(".claude/rules/") and path.suffix == ".md":
            add(path, "Claude Code", True)
    return sorted(found.values(), key=lambda f: f.rel)


@dataclass
class RepoFacts:
    root: Path
    scripts: set[str]
    package_manager: str | None
    make_targets: set[str] | None
    just_recipes: set[str] | None
    dependencies: set[str]
    files: list[str]
    suffixes: set[str] = field(default_factory=set)

    def exists(self, rel: str) -> bool:
        rel = rel.strip("/")
        return (self.root / rel).exists()

    def known(self, token: str) -> bool:
        """True if some file or folder in the repo ends with this path, with or without an extension."""
        token = token.strip("/").removeprefix("./")
        return token in self.suffixes or token.rsplit(".", 1)[0] in self.suffixes


LOCKFILES = [("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun"), ("package-lock.json", "npm")]


def gather_facts(root: Path) -> RepoFacts:
    root = root.resolve()
    scripts: set[str] = set()
    dependencies: set[str] = set()
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(_read(package_json))
            scripts = set((data.get("scripts") or {}).keys())
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                dependencies |= set((data.get(key) or {}).keys())
            declared = str(data.get("packageManager") or "")
        except (json.JSONDecodeError, AttributeError):
            declared = ""
    else:
        declared = ""
    manager = declared.split("@")[0] if declared else None
    if not manager:
        manager = next((name for lock, name in LOCKFILES if (root / lock).exists()), None)

    for extra in _walk(root):
        if extra.name == "package.json" and extra != package_json:
            try:
                data = json.loads(_read(extra))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict):
                scripts |= set((data.get("scripts") or {}).keys())
                for key in ("dependencies", "devDependencies", "peerDependencies"):
                    dependencies |= set((data.get(key) or {}).keys())
                if data.get("name"):
                    dependencies.add(str(data["name"]))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = _read(pyproject)
        dependencies |= {match.lower() for match in re.findall(r"^\s*\"?([A-Za-z][\w.-]*)", text, re.M)}

    make_targets = None
    makefile = next((root / name for name in ("Makefile", "makefile", "GNUmakefile") if (root / name).is_file()), None)
    if makefile:
        make_targets = set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", _read(makefile), re.M))
    just_recipes = None
    justfile = next((root / name for name in ("justfile", "Justfile", ".justfile") if (root / name).is_file()), None)
    if justfile:
        just_recipes = set(re.findall(r"^@?([A-Za-z0-9_-]+)(?:\s+[^:=\n]*)?:(?!=)", _read(justfile), re.M))

    files = [path.relative_to(root).as_posix() for path in _walk(root)]
    suffixes: set[str] = set()
    for rel in files:
        parts = rel.split("/")
        for start in range(len(parts)):
            tail = "/".join(parts[start:])
            suffixes.add(tail)
            suffixes.add(tail.rsplit(".", 1)[0])
            for end in range(start + 1, len(parts)):
                suffixes.add("/".join(parts[start:end]))  # folders
    return RepoFacts(root, scripts, manager, make_targets, just_recipes, dependencies, files, suffixes)
