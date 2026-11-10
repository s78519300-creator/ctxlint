"""The checks. Each takes the discovered files and repo facts and returns findings.

Every check is tuned against real repositories to keep false positives low:
it is better to miss a vague case than to cry wolf on a good instruction file.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import re
from collections import defaultdict

from .files import ContextFile, RepoFacts, estimate_tokens
from .findings import Finding

INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?=[^\s.!?]))+[.!?]*")  # a period inside a URL or file name does not end a sentence
NEGATION = re.compile(r"\b(never|don't|do not|must not|mustn't|should not|shouldn't|avoid|no longer|not allowed|forbidden|prohibited)\b", re.I)

# ---------------------------------------------------------------- helpers ---


def active(files: list[ContextFile]) -> list[ContextFile]:
    """Skip symlinked duplicates so the same file is never reported twice."""
    return [cf for cf in files if not cf.alias_of]


def body_lines(cf: ContextFile):
    """Yield (line number, text, inside a code fence) for the body after any frontmatter."""
    in_fence = False
    for number, line in enumerate(cf.lines, start=1):
        if number < cf.body_start_line:
            continue
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        yield number, line, in_fence


def clip(text: str, start: int = 0, end: int | None = None, width: int = 40) -> str:
    end = len(text) if end is None else end
    return INVISIBLE.sub("�", text[max(0, start - width): end + width].strip())


# ------------------------------------------------------------ drift checks ---

PATH_EXTENSIONS = {"md", "mdc", "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "json", "yaml", "yml", "toml", "sh", "go", "rs", "java", "kt",
                   "rb", "php", "cs", "css", "scss", "html", "sql", "txt", "cfg", "ini", "prisma", "graphql", "proto", "swift", "vue", "svelte"}
MD_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
PATHLIKE = re.compile(r"^(?:\./)?[\w@-][\w.@-]*(?:/[\w.@\[\]-]+)*/?$")
GENERATED = ("node_modules/", "dist/", "build/", "out/", ".next/", "target/", "coverage/", ".venv/", "__pycache__/")
PLACEHOLDER = re.compile(r"(kebab|camel|snake|pascal)[-_]?case|\b(example|sample|foo|bar|baz|my|your|some|name|xxx|todo|path/to)\b", re.I)


def _path_candidate(token: str) -> bool:
    token = token.strip()
    if " " in token or "..." in token or any(ch in token for ch in "*?<>{}$|=,:;()'\"") or token.startswith(("/", "~", "http", "mailto", "@", "-", ".")):
        return False
    if not PATHLIKE.match(token) or token.startswith(GENERATED) or PLACEHOLDER.search(token):
        return False
    name = token.rstrip("/").rsplit("/", 1)[-1]
    if "." in name:
        return name.rsplit(".", 1)[-1].lower() in PATH_EXTENSIONS
    return token.endswith("/") and token.count("/") >= 1  # an explicit folder; 'a/b' without a slash is usually a module or method name


def _path_ok(cf: ContextFile, facts: RepoFacts, token: str) -> bool:
    token = token.split("#", 1)[0].removeprefix("./")
    if not token or (cf.path.parent / token).exists() or facts.exists(token) or facts.known(token):
        return True
    head = token.strip("/").split("/", 1)[0]
    # An extension-less 'a/b' whose first part is not a folder here is usually a package or rule name.
    if "." not in token.rsplit("/", 1)[-1] and not token.endswith("/") and head not in facts.suffixes:
        return True
    return False


def check_paths(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    """Paths the agent is told about that are nowhere in the repo.

    Paths with a folder ('src/api/client.ts') are reported as medium. Bare file
    names ('client.ts') are low, because they often name build outputs.
    """
    findings = []
    for cf in active(files):
        missing: dict[str, list[tuple[int, str]]] = {}
        for number, line, fenced in body_lines(cf):
            if fenced:
                continue
            candidates = [m.group(1) for m in CODE_SPAN.finditer(line)] + [m.group(1) for m in MD_LINK.finditer(line)]
            for token in dict.fromkeys(c.strip() for c in candidates):
                if _path_candidate(token) and not _path_ok(cf, facts, token):
                    missing.setdefault(token, []).append((number, line))
        for token, places in missing.items():
            number, line = places[0]
            severity = "medium" if "/" in token.strip("/") else "low"
            more = f" (also on {len(places) - 1} more line{'s' if len(places) > 2 else ''})" if len(places) > 1 else ""
            findings.append(Finding("CL101", severity, cf.rel, number, f"Refers to '{token}', which does not exist anywhere in the repo{more}", clip(line)))
    return findings


IMPORT = re.compile(r"(?<![\w`/])@((?:\.{1,2}/)?[\w.-]+(?:/[\w.-]+)*\.\w+)")


def claude_imports(cf: ContextFile):
    for number, line, fenced in body_lines(cf):
        if not fenced:
            for match in IMPORT.finditer(CODE_SPAN.sub("", line)):
                yield number, match.group(1)


def is_claude_file(cf: ContextFile) -> bool:
    return cf.path.name.upper().startswith("CLAUDE") or cf.rel.startswith(".claude/")


def check_imports(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    findings = []
    for cf in active(files):
        if is_claude_file(cf):
            for number, target in claude_imports(cf):
                if not (cf.path.parent / target).exists() and not facts.exists(target):
                    findings.append(Finding("CL102", "high", cf.rel, number, f"@{target} is imported but does not exist, so that context is never loaded"))
    return findings


RUNNERS = ("npm", "pnpm", "yarn", "bun")
NON_SCRIPT = {
    "npm": {"install", "i", "ci", "add", "uninstall", "update", "exec", "init", "publish", "link", "audit", "ls", "outdated", "pack", "version", "view", "config", "cache", "rebuild", "prune", "dedupe", "login", "whoami", "help", "pkg", "query", "explain", "fund", "doctor"},
    "pnpm": {"install", "i", "add", "remove", "rm", "uninstall", "update", "up", "exec", "dlx", "create", "init", "publish", "link", "audit", "ls", "list", "outdated", "why", "store", "import", "setup", "env", "patch", "patch-commit", "fetch", "rebuild", "prune", "dedupe", "config", "recursive", "-r", "--filter", "-F", "-C", "--dir", "-w", "--workspace-root", "help", "self-update", "licenses", "deploy", "pack"},
    "yarn": {"install", "add", "remove", "upgrade", "up", "dlx", "exec", "create", "init", "publish", "link", "audit", "info", "why", "workspace", "workspaces", "set", "config", "cache", "npm", "plugin", "dedupe", "constraints", "version", "pack", "help"},
    "bun": {"install", "i", "add", "remove", "rm", "update", "x", "create", "init", "publish", "link", "pm", "build", "upgrade", "help", "test", "outdated"},
}
NPM_LIFECYCLE = {"test", "start", "stop", "restart"}
COMMAND_START = re.compile(r"^\s*(?:\$\s*|>\s*)?((?:npm|pnpm|yarn|bun|make|just)\b.*)$")
SPLIT_COMMANDS = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
PROSE_RUN = re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+run\s+[A-Za-z0-9:_.-]+")


def command_candidates(line: str, fenced: bool) -> list[str]:
    """Strings that are actually commands: code that starts with a runner, or prose that says 'X run y'."""
    pieces = []
    if fenced:
        pieces.append(line)
    else:
        pieces += [m.group(1) for m in CODE_SPAN.finditer(line)]
        pieces += PROSE_RUN.findall(CODE_SPAN.sub("", line))
    commands = []
    for piece in pieces:
        for part in SPLIT_COMMANDS.split(piece):
            if match := COMMAND_START.match(part):
                commands.append(match.group(1).strip())
    return commands


def check_commands(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    findings = []
    has_package_json = (facts.root / "package.json").is_file()
    for cf in active(files):
        for number, raw, fenced in body_lines(cf):
            for command in command_candidates(raw, fenced):
                words = command.split()
                tool = words[0]
                rest, skip = [], False
                for word in words[1:]:
                    if skip:
                        skip = False
                    elif word in {"--filter", "-F", "-C", "--dir", "--prefix", "--cwd", "-w", "--workspace"}:
                        skip = word not in {"-w"}
                    elif not word.startswith("-"):
                        rest.append(word)
                if not rest:
                    continue
                if tool in RUNNERS:
                    explicit = rest[0] == "run"
                    name = rest[1] if explicit and len(rest) > 1 else rest[0]
                    if explicit and len(rest) < 2:
                        continue
                    if not explicit and (name in NON_SCRIPT[tool] or (tool == "npm" and name not in NPM_LIFECYCLE)):
                        continue
                    if not has_package_json or name in facts.scripts or (not explicit and name in facts.dependencies):
                        continue
                    available = ", ".join(sorted(facts.scripts)[:8]) or "none"
                    findings.append(Finding("CL103", "high", cf.rel, number,
                                            f"'{tool} {'run ' if explicit else ''}{name}' runs a script that is not in any package.json (scripts include: {available})", clip(raw)))
                elif tool in {"make", "just"}:
                    name = rest[0]
                    known = facts.make_targets if tool == "make" else facts.just_recipes
                    if "=" in name or "/" in name:
                        continue
                    if known is None:
                        findings.append(Finding("CL103", "high", cf.rel, number, f"'{tool} {name}' is used, but the repo has no {'Makefile' if tool == 'make' else 'justfile'}", clip(raw)))
                    elif name not in known:
                        findings.append(Finding("CL103", "high", cf.rel, number, f"'{tool} {name}' names a {'Makefile target' if tool == 'make' else 'just recipe'} that does not exist", clip(raw)))
    return findings


MANAGER_COMMAND = re.compile(r"^(npm|pnpm|yarn|bun)\s+(install|i|add|run|ci|remove|exec|dlx|x|test)\b(.*)$")


def check_package_manager(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    if not facts.package_manager:
        return []
    findings = []
    for cf in active(files):
        reported = set()
        for number, raw, fenced in body_lines(cf):
            for command in command_candidates(raw, fenced):
                match = MANAGER_COMMAND.match(command)
                if not match:
                    continue
                tool, rest = match.group(1), match.group(3)
                if re.search(r"(^|\s)(-g|--global)\b", rest):
                    continue  # e.g. `npm install -g pnpm` installs the right tool
                if tool != facts.package_manager and tool not in reported:
                    reported.add(tool)
                    findings.append(Finding("CL104", "high", cf.rel, number,
                                            f"Uses '{tool}', but this repo is managed with {facts.package_manager} (from its lockfile or packageManager field)", clip(raw)))
    return findings


# --------------------------------------------------------- conflict checks ---

CHOICE_GROUPS: dict[str, tuple[re.Pattern[str] | None, dict[str, re.Pattern[str]]]] = {
    "package manager": (None, {name: re.compile(rf"\b{name}\b(?![-.\w])", re.I) for name in RUNNERS}),
    "indentation": (re.compile(r"\bindent", re.I), {"tabs": re.compile(r"\btabs?\b", re.I), "spaces": re.compile(r"\bspaces\b", re.I)}),
    "string quote style": (re.compile(r"\bquot", re.I), {"single quotes": re.compile(r"\bsingle[- ]quot", re.I), "double quotes": re.compile(r"\bdouble[- ]quot", re.I)}),
    "test runner": (re.compile(r"\btest", re.I), {name: re.compile(rf"\b{name}\b(?![-'’\w])", re.I) for name in ("jest", "vitest", "mocha", "pytest", "unittest")}),
    "Python package tool": (None, {name: re.compile(rf"\b{name}\b(?![-.\w])", re.I) for name in ("pip", "poetry", "uv", "conda", "pipenv")}),
}
DIRECTIVE = re.compile(r"^\W*(always\s+)?(use|prefer|run with|we use|indent with)\b|\b(must|should|always)\s+(use|be|run)\b|\buses?\s+\w+\s+(for|to)\b|^\W*\w+\s+uses?\b", re.I)
QUALIFIED = re.compile(r"\b(otherwise|except|unless|when|only|if|for (strings|user|tests in|files in|the [\w-]+ package)|-like|instead of|rather than|not|over|vs\.?|versus|or)\b", re.I)


def positive_choices(sentence: str) -> dict[str, str]:
    """The single option a plain directive picks for each group, e.g. 'Use tabs for indentation.'"""
    if not DIRECTIVE.search(sentence) or NEGATION.search(sentence) or QUALIFIED.search(sentence) or len(sentence.split()) > 25:
        return {}
    chosen = {}
    for group, (topic, options) in CHOICE_GROUPS.items():
        if topic is not None and not topic.search(sentence):
            continue
        hits = [name for name, pattern in options.items() if pattern.search(sentence)]
        if len(hits) == 1:
            chosen[group] = hits[0]
    return chosen


def loaded_together(files: list[ContextFile]) -> list[set[str]]:
    """Groups of files that a single tool loads into the same session."""
    by_tool: dict[str, set[str]] = defaultdict(set)
    rels = {cf.rel for cf in files}
    for cf in files:
        if cf.always_loaded:
            by_tool[cf.tool].add(cf.rel)
            if is_claude_file(cf):
                for _, target in claude_imports(cf):
                    resolved = (cf.path.parent / target).resolve()
                    for other in files:
                        if other.path.resolve() == resolved and other.rel in rels:
                            by_tool[cf.tool].add(other.rel)
    return list(by_tool.values())


def check_conflicts(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    """Contradictions between global instructions. Nested folder files are meant to override, so they are skipped."""
    candidates = [cf for cf in active(files) if cf.always_loaded or cf.rel.startswith((".cursor/rules/", ".github/instructions/"))]
    together = loaded_together(active(files))
    seen: dict[str, list[tuple[str, str, int, str]]] = defaultdict(list)
    for cf in candidates:
        for number, line, fenced in body_lines(cf):
            if fenced:
                continue
            for sentence in SENTENCE.findall(CODE_SPAN.sub(lambda m: m.group(1), line)):
                for group, choice in positive_choices(sentence).items():
                    seen[group].append((choice, cf.rel, number, sentence.strip()))
    findings = []
    for group, mentions in seen.items():
        choices = {choice for choice, _, _, _ in mentions}
        if len(choices) < 2:
            continue
        summary = "; ".join(f"'{choice}' in {', '.join(sorted({f'{rel}:{n}' for c, rel, n, _ in mentions if c == choice}))}" for choice in sorted(choices))
        files_with = {choice: {rel for c, rel, _, _ in mentions if c == choice} for choice in choices}
        same_session = any(
            any(a in group_files for a in files_with[x]) and any(b in group_files for b in files_with[y])
            for group_files in together for x in choices for y in choices if x != y
        ) or any(len({c for c, rel, _, _ in mentions if rel == r}) > 1 for r in {rel for _, rel, _, _ in mentions})
        severity = "high" if same_session else "medium"
        where = "loaded together" if same_session else "across tools"
        reported = set()
        for choice, rel, number, sentence in mentions:
            if choice not in reported:
                reported.add(choice)
                findings.append(Finding("CL201", severity, rel, number, f"Conflicting {group} instructions ({where}): {summary}", sentence[:120]))
    return findings


def _normalise(sentence: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", sentence.lower())).strip()


def check_duplicates(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    where: dict[str, list[tuple[ContextFile, int, str]]] = defaultdict(list)
    for cf in active(files):
        seen_here = set()
        for number, line, fenced in body_lines(cf):
            if fenced:
                continue
            for sentence in SENTENCE.findall(line.lstrip("-*># ")):
                key = _normalise(sentence)
                if len(key.split()) >= 7 and key not in seen_here:
                    seen_here.add(key)
                    where[key].append((cf, number, sentence.strip()))
    findings = []
    for places in where.values():
        if len({cf.rel for cf, _, _ in places}) > 1:
            cf, number, sentence = places[0]
            others = ", ".join(f"{other.rel}:{n}" for other, n, _ in places[1:])
            findings.append(Finding("CL202", "low", cf.rel, number, f"Same instruction also appears in {others}", sentence[:120]))
    return findings


def expand_braces(pattern: str) -> list[str]:
    """Expand {a,b} alternatives, which fnmatch does not understand."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end():]
    return [expanded for option in match.group(1).split(",") for expanded in expand_braces(head + option + tail)]


def _glob_matches(pattern: str, files: list[str]) -> bool:
    pattern = pattern.strip().strip("'\"")
    if not pattern:
        return False
    for expanded in expand_braces(pattern):
        variants = {expanded, expanded.removeprefix("**/"), expanded.replace("**/", ""), expanded.replace("/**/", "/")}
        if any(fnmatch.fnmatch(path, variant) for path in files for variant in variants):
            return True
    return False


def _split_globs(value: str) -> list[str]:
    """Split a comma-separated or [list] value, keeping commas inside {braces}."""
    value = value.strip().strip("[]")
    parts, depth, current = [], 0, ""
    for ch in value:
        depth += ch == "{"
        depth -= ch == "}"
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip().strip("'\"") for p in parts if p.strip().strip("'\"")]


def check_scopes(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    findings = []
    for cf in active(files):
        front = cf.frontmatter
        if cf.rel.startswith(".cursor/rules/"):
            always = front.get("alwaysApply", "").lower() == "true"
            globs = _split_globs(front.get("globs", ""))
            if not always and not globs and not front.get("description"):
                findings.append(Finding("CL203", "medium", cf.rel, 1, "Cursor rule has no description, globs or alwaysApply, so it is never applied"))
            elif globs and not any(_glob_matches(g, facts.files) for g in globs):
                findings.append(Finding("CL204", "medium", cf.rel, 1, f"globs {globs} match no files in this repo"))
        elif cf.rel.startswith(".github/instructions/") and cf.rel.endswith(".instructions.md"):
            apply_to = _split_globs(front.get("applyTo", ""))
            if not apply_to:
                if not front.get("description"):
                    findings.append(Finding("CL203", "medium", cf.rel, 1, "Copilot instructions file has no applyTo (or description), so it is not applied automatically"))
            elif not any(_glob_matches(g, facts.files) for g in apply_to):
                findings.append(Finding("CL204", "medium", cf.rel, 1, f"applyTo {apply_to} matches no files in this repo"))
    return findings


# ------------------------------------------------------------- cost checks ---

def tool_budgets(files: list[ContextFile], facts: RepoFacts) -> dict[str, tuple[int, list[str]]]:
    """Always-loaded tokens per tool, following Claude Code @imports one level deep."""
    budgets: dict[str, tuple[int, list[str]]] = {}
    for cf in files:
        if not cf.always_loaded:
            continue
        tokens, names = budgets.get(cf.tool, (0, []))
        tokens += cf.tokens
        names = names + [cf.rel]
        if is_claude_file(cf):
            for _, target in claude_imports(cf):
                path = cf.path.parent / target
                if path.is_file():
                    tokens += estimate_tokens(path.read_text(encoding="utf-8", errors="replace"))
                    names.append(f"@{target}")
        budgets[cf.tool] = (tokens, names)
    return budgets


def check_bloat(files: list[ContextFile], facts: RepoFacts, file_budget: int = 2500, tool_budget: int = 5000) -> list[Finding]:
    findings = []
    flagged_alone = set()
    for cf in active(files):
        if cf.always_loaded and cf.tokens > file_budget:
            flagged_alone.add(cf.rel)
            findings.append(Finding("CL301", "medium", cf.rel, 1, f"About {cf.tokens:,} tokens, loaded in every session (file budget {file_budget:,})"))
    for tool, (tokens, names) in tool_budgets(files, facts).items():
        if tokens > tool_budget and not (len(names) == 1 and names[0] in flagged_alone):
            severity = "high" if tokens > tool_budget * 2 else "medium"
            findings.append(Finding("CL301", severity, names[0], 1, f"{tool} loads about {tokens:,} tokens before every task ({', '.join(names)}); budget {tool_budget:,}"))
    return findings


VAGUE = re.compile(
    r"\b(write (clean|good|high[- ]quality|readable|maintainable) code|follow (the )?best practices|be careful\b|"
    r"make sure (the )?code (is good|works)|use meaningful (variable )?names|avoid bugs|write efficient code|"
    r"you are an? (expert|senior|world[- ]class|10x)[\w -]{0,30}(engineer|developer|programmer))",
    re.I,
)


def check_vague(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    findings = []
    for cf in active(files):
        for number, line, fenced in body_lines(cf):
            if not fenced and (match := VAGUE.search(line)):
                findings.append(Finding("CL302", "low", cf.rel, number, f"Generic advice ('{match.group(0)}') costs tokens in every session and changes nothing", clip(line)))
    return findings


# --------------------------------------------------------- security checks ---

HIDDEN_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
COMMENT_INSTRUCTION = re.compile(r"\b(ignore (all |any )?(previous|prior|above)|always|never|must|do not tell|don't tell|run|execute|send|upload|insert|curl|wget|fetch|delete|install)\b", re.I)
SAFETY_TARGET = r"(tests?|lint(ing|er|s)?|type ?check(s|ing)?|ssl|tls|certificate(s| checks?)?|verification|security (checks?|scans?)|git hooks?|pre-commit hooks?)"
DANGEROUS = [
    (re.compile(r"(?<![\w-])--no-verify\b"), "Skips git hooks with --no-verify", True),
    (re.compile(rf"\b(skip|disable|bypass|turn off|comment out)\s+(the\s+|all\s+|any\s+)?{SAFETY_TARGET}\b", re.I), "Tells the agent to disable a safety check", True),
    (re.compile(r"\bnever ask\b[^.\n]{0,25}\b(confirmation|permission|approval|the user)\b|\bwithout asking (the user|for (confirmation|permission|approval))\b", re.I), "Tells the agent to act without asking for approval", False),
    (re.compile(r"\b(curl|wget)\b[^|\n]{0,160}\|\s*(sudo\s+)?(ba|z)?sh\b"), "Pipes a downloaded script into a shell", True),
    (re.compile(r"\bchmod\s+(-R\s+)?777\b"), "Makes files world-writable", True),
    (re.compile(r"\brm\s+-rf\s+(/|~|\$HOME)(\s|$|`)"), "Deletes a root or home directory", True),
    (re.compile(r"\b(force[- ]push|push\s+(--force|-f))\b[^.\n]{0,30}\b(main|master)\b|\bcommit directly to (main|master)\b", re.I), "Rewrites or bypasses review on the main branch", True),
]
HIDE_FROM_USER = re.compile(r"\b(do not|don't|never)\s+(tell|mention|inform|show|reveal)\b[^.\n]{0,40}\b(the user|users|the human|reviewers?)\b", re.I)
EXFIL = re.compile(r"\b(send|upload|post|forward|transmit|exfiltrate)\b[^.\n]{0,60}\bto\s+<?(https?://[^\s)\"'>]+)", re.I)
TOKEN_FORMATS = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|glpat-[A-Za-z0-9_-]{20})\b")
ASSIGNED_SECRET = re.compile(r"\b(password|passwd|api[_-]?key|secret|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"]?([A-Za-z0-9_\-+/]{12,})", re.I)
BASE64_BLOB = re.compile(r"(?<![\w/+])[A-Za-z0-9+/]{40,}={0,2}(?![\w/+])")


def _negated_before(sentence: str, start: int) -> bool:
    return bool(NEGATION.search(sentence[:start]))


def check_security(files: list[ContextFile], facts: RepoFacts) -> list[Finding]:
    findings = []
    for cf in active(files):
        for number, line in enumerate(cf.lines, start=1):
            hidden = INVISIBLE.findall(line)
            if hidden and not (hidden == ["﻿"] and number == 1):
                smuggled = "".join(chr(ord(ch) - 0xE0000) for ch in hidden if 0xE0020 <= ord(ch) <= 0xE007E)
                codes = sorted({f"U+{ord(ch):04X}" for ch in hidden})
                shown = ", ".join(codes[:4]) + (f" and {len(codes) - 4} more" if len(codes) > 4 else "")
                evidence = f"hidden text reads: {smuggled!r}" if smuggled else clip(line)
                findings.append(Finding("CL401", "critical", cf.rel, number, f"{len(hidden)} invisible characters ({shown})", evidence))
            visible = INVISIBLE.sub("", line)
            for sentence in SENTENCE.findall(visible):
                prose = CODE_SPAN.sub(" ", sentence)
                for pattern, message, code_ok in DANGEROUS:
                    text = sentence if code_ok else prose
                    if (match := pattern.search(text)) and not _negated_before(text, match.start()):
                        findings.append(Finding("CL403", "high", cf.rel, number, message, clip(sentence.strip(), match.start(), match.end())))
                if match := HIDE_FROM_USER.search(prose):
                    findings.append(Finding("CL403", "high", cf.rel, number, "Tells the agent to hide something from the user", clip(prose.strip(), match.start(), match.end())))
                for match in EXFIL.finditer(prose):
                    if not _negated_before(prose, match.start()) and not any(h in match.group(2) for h in ("localhost", "127.0.0.1")):
                        findings.append(Finding("CL404", "high", cf.rel, number, f"Tells the agent to send data to {match.group(2)}", clip(prose.strip(), match.start(), match.end())))
            for match in TOKEN_FORMATS.finditer(visible):
                findings.append(Finding("CL405", "critical", cf.rel, number, "Contains a token in a known provider format", match.group(0)[:6] + "…"))
            for match in ASSIGNED_SECRET.finditer(visible):
                if not re.search(r"(example|placeholder|your|xxxx|changeme|dummy|fake|test|<|\$\{|\*\*\*)", match.group(0), re.I):
                    findings.append(Finding("CL405", "critical", cf.rel, number, f"'{match.group(1)}' is set to a literal value", match.group(2)[:4] + "…"))
            for match in BASE64_BLOB.finditer(visible):
                try:
                    decoded = base64.b64decode(match.group(0), validate=True).decode("utf-8")
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    continue
                if sum(ch.isprintable() for ch in decoded) < len(decoded) * 0.95:
                    continue
                severity = "critical" if COMMENT_INSTRUCTION.search(decoded) else "medium"
                findings.append(Finding("CL406", severity, cf.rel, number, "Base64 text that decodes to readable content", decoded[:80]))
        for match in HIDDEN_COMMENT.finditer(cf.text):
            content = " ".join(match.group(1).split())
            if "ctxlint-disable" in content or len(content.split()) < 4:
                continue
            if COMMENT_INSTRUCTION.search(content):
                number = cf.text.count("\n", 0, match.start()) + 1
                findings.append(Finding("CL402", "high", cf.rel, number, "Instruction hidden in an HTML comment (invisible when rendered)", content[:110]))
    return findings


ALL_CHECKS = (check_paths, check_imports, check_commands, check_package_manager, check_conflicts,
              check_duplicates, check_scopes, check_bloat, check_vague, check_security)
