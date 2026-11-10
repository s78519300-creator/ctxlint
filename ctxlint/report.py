"""Render results as text, JSON, SARIF 2.1.0, HTML, or an SVG badge."""

from __future__ import annotations

import json
from collections import Counter
from html import escape

from . import __version__
from .findings import RULES, SEVERITIES, Finding, sort_findings
from .lint import Result

COLORS = {"critical": "\033[91m", "high": "\033[93m", "medium": "\033[96m", "low": "\033[37m"}
GRADE_COLORS = {"A": "#16a34a", "B": "#65a30d", "C": "#d97706", "D": "#ea580c", "F": "#dc2626"}


def to_text(result: Result, color: bool = False) -> str:
    letter, score = result.grade
    lines = []
    if not result.files:
        return "No agent context files found (AGENTS.md, CLAUDE.md, .cursor/rules, .github/copilot-instructions.md, GEMINI.md ...)."
    lines.append(f"Checked {len(result.files)} context files: " + ", ".join(cf.rel for cf in result.files))
    for tool, (tokens, names) in sorted(result.budgets.items(), key=lambda kv: -kv[1][0]):
        lines.append(f"  {tool}: ~{tokens:,} tokens loaded before every task")
    lines.append("")
    for f in sort_findings(result.findings):
        tag = f"{COLORS[f.severity]}{f.severity.upper():<8}\033[0m" if color else f"{f.severity.upper():<8}"
        lines.append(f"{tag} {f.rule} {f.path}:{f.line}  {f.message}")
        if f.evidence:
            lines.append(f"         {f.evidence}")
    counts = Counter(f.severity for f in result.findings)
    lines.append("")
    lines.append("Summary: " + ", ".join(f"{counts.get(s, 0)} {s}" for s in SEVERITIES) + f"  ·  grade {letter} ({score}/100)")
    return "\n".join(lines)


def to_json(result: Result) -> str:
    letter, score = result.grade
    return json.dumps({
        "grade": letter, "score": score,
        "files": [{"path": cf.rel, "tool": cf.tool, "always_loaded": cf.always_loaded, "tokens": cf.tokens} for cf in result.files],
        "budgets": {tool: {"tokens": tokens, "files": names} for tool, (tokens, names) in result.budgets.items()},
        "findings": [f.to_dict() for f in sort_findings(result.findings)],
    }, indent=2)


SARIF_LEVEL = {"critical": "error", "high": "error", "medium": "warning", "low": "note"}
SECURITY_SEVERITY = {"critical": "9.0", "high": "7.0", "medium": "5.0", "low": "2.0"}


def to_sarif(result: Result) -> str:
    used = sorted({f.rule for f in result.findings})
    rules = [{
        "id": rule, "name": RULES[rule][0],
        "shortDescription": {"text": RULES[rule][0].replace("-", " ")},
        "help": {"text": RULES[rule][2]},
        "properties": {"tags": ["ai-agents", RULES[rule][1]] + (["security"] if RULES[rule][1] == "security" else [])},
    } for rule in used]
    results = []
    for f in sort_findings(result.findings):
        entry = {
            "ruleId": f.rule, "level": SARIF_LEVEL[f.severity], "message": {"text": f.message},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": f.path}, "region": {"startLine": max(1, f.line)}}}],
        }
        if RULES[f.rule][1] == "security":
            entry["properties"] = {"security-severity": SECURITY_SEVERITY[f.severity]}
        results.append(entry)
    driver = {"name": "ctxlint", "version": __version__, "informationUri": "https://github.com/s78519300-creator/ctxlint", "rules": rules}
    return json.dumps({"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": [{"tool": {"driver": driver}, "results": results}]}, indent=2)


def badge_svg(letter: str, label: str = "agent context") -> str:
    value = f"ctxlint {letter}"
    width = lambda text: int(sum(7.2 if ch.isupper() else 6.3 for ch in text)) + 12  # noqa: E731
    left, right = width(label), width(value)
    total, color = left + right, GRADE_COLORS[letter]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{label}: {value}"><title>{label}: {value}</title>'
        f'<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath><g clip-path="url(#r)">'
        f'<rect width="{left}" height="20" fill="#555"/><rect x="{left}" width="{right}" height="20" fill="{color}"/><rect width="{total}" height="20" fill="url(#s)"/></g>'
        f'<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{left / 2:.1f}" y="14">{label}</text><text x="{left + right / 2:.1f}" y="14">{value}</text></g></svg>'
    )


STYLE = """
:root{--bg:#0a0c10;--panel:#11151c;--line:#232a36;--text:#e9eef6;--muted:#8b96a8;--accent:#7c9cff;--critical:#ff5c7a;--high:#ffa94d;--medium:#5cc8ff;--low:#9aa4b2;--ok:#34d399}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1000px 500px at 90% -20%,#1d2b5c 0,transparent 60%),radial-gradient(800px 400px at -10% 0,#132a26 0,transparent 55%),var(--bg);color:var(--text);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:auto;padding:40px 20px 64px}.eyebrow{color:var(--accent);font-weight:800;font-size:13px;letter-spacing:.16em;text-transform:uppercase}h1{font-size:42px;line-height:1.1;margin:6px 0 8px}h2{font-size:20px;margin:30px 0 12px}.sub{color:var(--muted);margin:0 0 26px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.card,.box{background:var(--panel);border:1px solid var(--line);border-radius:14px}.card{padding:18px;border-top:3px solid var(--c)}.card b{display:block;font-size:36px;line-height:1.1}.card span{color:var(--muted);font-size:13px}
.box{padding:16px;overflow-x:auto}.bar{display:grid;grid-template-columns:minmax(180px,260px) 1fr 90px;gap:12px;align-items:center;margin:8px 0;font-size:14px;min-width:520px}.track{height:12px;background:#0a0c10;border-radius:12px;position:relative}.fill{position:absolute;inset:0 auto 0 0;border-radius:12px;background:linear-gradient(90deg,var(--accent),var(--c))}.limit{position:absolute;top:-4px;bottom:-4px;width:2px;background:#ffffff55}.num{font:700 13px ui-monospace,monospace;text-align:right}
.file{margin-top:14px}.file h3{font:700 14px ui-monospace,monospace;margin:0 0 6px;color:#c9d6ff}.f{display:grid;grid-template-columns:86px 64px 1fr;gap:10px;padding:9px 0;border-top:1px solid var(--line);font-size:14px}.sev{font:800 11px ui-monospace,monospace;text-transform:uppercase;padding:3px 7px;border-radius:6px;height:fit-content;text-align:center;color:#071018;background:var(--c)}.ln{font:12px ui-monospace,monospace;color:var(--muted);padding-top:3px}
.ev{font:12px ui-monospace,monospace;color:var(--muted);background:#0a0c10;border-radius:8px;padding:5px 8px;margin-top:5px;overflow-wrap:anywhere}.fix{font-size:13px;color:var(--ok);margin-top:4px}.meta{font:12px ui-monospace,monospace;color:var(--muted)}
@media(max-width:640px){h1{font-size:32px}.f{grid-template-columns:1fr}}
"""


def to_html(result: Result, title: str = "ctxlint report", budget: int = 5000) -> str:
    letter, score = result.grade
    counts = Counter(f.severity for f in result.findings)
    cards = f'<div class="card" style="--c:{GRADE_COLORS[letter]}"><b style="color:{GRADE_COLORS[letter]}">{letter}</b><span>grade · {score}/100</span></div>'
    cards += "".join(f'<div class="card" style="--c:var(--{s})"><b>{counts.get(s, 0)}</b><span>{s}</span></div>' for s in SEVERITIES)
    widest = max([budget * 1.25] + [tokens for tokens, _ in result.budgets.values()])
    bars = "".join(
        f'<div class="bar"><span>{escape(tool)}</span><span class="track"><span class="fill" style="width:{tokens / widest * 100:.1f}%;--c:{"var(--critical)" if tokens > budget else "var(--ok)"}"></span>'
        f'<span class="limit" style="left:{budget / widest * 100:.1f}%"></span></span><span class="num">{tokens:,}</span></div>'
        for tool, (tokens, _) in sorted(result.budgets.items(), key=lambda kv: -kv[1][0])
    ) or '<div class="meta">No always-loaded context files.</div>'
    groups = {}
    for f in sort_findings(result.findings):
        groups.setdefault(f.path, []).append(f)
    sections = "".join(
        f'<div class="file"><h3>{escape(path)}</h3>' + "".join(
            f'<div class="f"><span class="sev" style="--c:var(--{f.severity})">{f.severity}</span><span class="ln">line {f.line}</span><div>{escape(f.message)}'
            + (f'<div class="ev">{escape(f.evidence)}</div>' if f.evidence else "")
            + f'<div class="fix">{escape(f.fix)}</div><div class="meta">{f.rule} · {escape(f.name)}</div></div></div>' for f in items) + "</div>"
        for path, items in groups.items()
    ) or '<div class="meta">No findings.</div>'
    return (
        f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title><style>{STYLE}</style><main>'
        f'<div class="eyebrow">Agent context lint</div><h1>{escape(title)}</h1><p class="sub">{len(result.files)} context files checked: '
        f'{escape(", ".join(cf.rel for cf in result.files))}</p><section class="cards">{cards}</section>'
        f'<h2>Tokens loaded before every task</h2><div class="box">{bars}<div class="meta">White line = budget ({budget:,} tokens). Estimates use ~4 characters per token.</div></div>'
        f'<h2>Findings</h2><section class="box">{sections}</section></main></html>'
    )
