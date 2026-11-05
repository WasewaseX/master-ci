#!/usr/bin/env python3
"""Heuristic detector for AI slop, AI-generated prose and humanizer patterns.

This is a linter, not an oracle. It reports signals that correlate with
machine-written filler: telltale vocabulary in code comments, emoji padding,
TODO density, boilerplate duplication, and - advisory only - prose rhythm
patterns such as em-dash overuse and rule-of-three lists.

Contract:
- Exit code 1 only when the *blocking* score exceeds --max-score.
- Blocking signals are deterministic code-content checks (comments,
duplication). Everything derived from documents - prose vocabulary,
humanizer phrases, prose rhythm - is always advisory: reported and
annotated, never failing a build.
- Every finding carries a check id and a remediation hint.
- Repos can opt out per path or per check via a `.ai-integrity-ignore`
file: glob patterns, or `check:<id>` lines to disable one rule.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PHRASES_PATH = Path(__file__).resolve().parent / "ai_slop_phrases.json"
IGNORE_FILE = ".ai-integrity-ignore"

CODE_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".rs",
    ".go",
    ".java",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".sh",
    ".swift",
    ".kt",
}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt"}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    "venv",
    ".venv",
    "__pycache__",
    ".next",
    ".turbo",
    "coverage",
    "_ci",
    "min",
}
SKIP_PATTERN = re.compile(
    r"(\.min\.[cm]?js$|\.lock$|package-lock\.json$|pnpm-lock\.yaml$|"
    r"yarn\.lock$|Cargo\.lock$|poetry\.lock$|\.snap$|\.d\.ts$|\.map$)"
)
MAX_FILE_BYTES = 1_000_000
MAX_ANNOTATIONS = 10

COMMENT_PREFIXES = ("#", "//")
TRIVIAL_COMMENT = re.compile(
    r"^(#|//)\s*(this (function|method|class|module)|returns? |increment|"
    r"loop (over|through|until)|define[sd]? |initialize[sd]? |declare[sd]? |"
    r"set the value|get the value|do the thing|perform the operation)",
    re.IGNORECASE,
)
TODO_PATTERN = re.compile(r"\b(TODO|FIXME|HACK)\b")
EMOJI_PATTERN = re.compile("[\U0001f300-\U0001faff\u2600-\u26ff\u2700-\u27bf\ufe0f\u2b00-\u2bff]")
DUP_LINE_MIN_LEN = 40
DUP_LINE_THRESHOLD = 10
WORD_PATTERN = re.compile(r"\w+")

REMEDIES = {
    "ai-vocab": "Rewrite the comment to describe intent in plain words",
    "emoji-comments": "Remove decorative emoji from code comments",
    "todo-density": "Resolve, ticket or delete the TODO/FIXME markers",
    "trivial-comments": "Delete comments that restate the code; explain why instead",
    "boilerplate-duplication": "Extract the repeated lines into a shared helper",
    "ai-prose": "Rewrite the passage in your own words",
    "humanizer": "Remove the filler phrase",
    "em-dash-density": "Use commas, colons or parentheses instead of em-dashes",
    "rule-of-three": "Vary list lengths; uniform triples are a machine-prose tell",
    "rhetorical-questions": "State the answer instead of asking the reader",
    "exclamations": "Remove exclamation marks from documentation",
}


@dataclass
class Finding:
    path: str
    line: int
    check: str
    message: str
    weight: int
    advisory: bool = False

    @property
    def remedy(self) -> str:
        return REMEDIES.get(self.check, "Review and fix manually")


def load_phrases() -> dict:
    with open(PHRASES_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if root.is_file():
        return [root]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if path.name in SKIP_DIRS or path.name.startswith("."):
            continue
        if SKIP_PATTERN.search(path.name):
            continue
        if path.suffix.lower() not in CODE_EXTS and path.suffix.lower() not in DOC_EXTS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        files.append(path)
    return files


def load_ignore_rules(root: Path) -> tuple[list[str], set[str]]:
    """Read .ai-integrity-ignore: glob lines and `check:<id>` disable lines."""
    globs: list[str] = []
    disabled: set[str] = set()
    ignore_path = root if root.is_dir() else root.parent
    ignore_path = ignore_path / IGNORE_FILE
    if ignore_path.is_file():
        try:
            lines = ignore_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("check:"):
                disabled.add(line.split(":", 1)[1].strip().lower())
            else:
                globs.append(line.rstrip("/"))
    return globs, disabled


def is_ignored(rel_path: str, globs: list[str]) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(
        fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(norm, g.rstrip("/*") + "/*") for g in globs
    )


def phrase_findings(
    path: Path,
    text: str,
    phrases: list[str],
    check: str,
    weight: int,
    advisory: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    lowered = text.lower()
    for phrase in phrases:
        first = lowered.find(phrase.lower())
        if first == -1:
            continue
        line = text.count("\n", 0, first) + 1
        count = lowered.count(phrase.lower())
        findings.append(
            Finding(
                path=str(path),
                line=line,
                check=check,
                message=f"{phrase!r} found ({count}x)",
                weight=weight,
                advisory=advisory,
            )
        )
    return findings


def scan_code_file(path: Path, phrases: dict) -> list[Finding]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    findings.extend(phrase_findings(path, text, phrases["code_phrases"], "ai-vocab", 2))
    lines = text.splitlines()
    comments = [line for line in lines if line.lstrip().startswith(COMMENT_PREFIXES)]
    emoji_hits = sum(bool(EMOJI_PATTERN.search(c)) for c in comments)
    if emoji_hits:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="emoji-comments",
                message=f"emoji in code comments ({emoji_hits}x)",
                weight=min(emoji_hits, 5),
            )
        )
    code_lines = max(len([line for line in lines if line.strip()]), 1)
    todos = len(TODO_PATTERN.findall(text))
    if code_lines >= 20 and todos / code_lines > 0.05:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="todo-density",
                message=f"TODO/FIXME density {todos / code_lines:.1%} exceeds 5%",
                weight=3,
            )
        )
    if len(comments) >= 5:
        trivial = sum(bool(TRIVIAL_COMMENT.match(c.strip())) for c in comments)
        if trivial / len(comments) > 0.4:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="trivial-comments",
                    message=(f"{trivial}/{len(comments)} comments merely restate the code"),
                    weight=3,
                )
            )
    return findings


def scan_doc_file(path: Path, phrases: dict) -> list[Finding]:
    """Document findings are advisory: style is never a build breaker."""
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    findings.extend(
        phrase_findings(path, text, phrases["doc_phrases"], "ai-prose", 1, advisory=True)
    )
    findings.extend(
        phrase_findings(path, text, phrases["humanizer_phrases"], "humanizer", 1, advisory=True)
    )
    findings.extend(prose_rhythm_findings(path, text))
    return findings


def prose_rhythm_findings(path: Path, text: str) -> list[Finding]:
    """Stylistic prose signals. Always advisory: reported, never blocking.

    Em-dashes, uniform list lengths and rhetorical questions appear in
    plenty of hand-written documents, so they carry no authorship verdict
    and must never fail a build.
    """
    findings: list[Finding] = []
    prose_lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            prose_lines.append(line)
    prose = "\n".join(prose_lines)
    words = max(len(WORD_PATTERN.findall(prose)), 1)
    emdashes = prose.count("\u2014")
    if emdashes / words * 1000 > 4:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="em-dash-density",
                message=f"em-dash density {emdashes / words * 1000:.1f}/1k words",
                weight=3,
                advisory=True,
            )
        )
    groups = collect_bullet_groups(prose_lines)
    if len(groups) >= 3:
        threes = sum(1 for group in groups if len(group) == 3)
        if threes / len(groups) > 0.4:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="rule-of-three",
                    message=(f"{threes}/{len(groups)} bullet lists have exactly 3 items"),
                    weight=2,
                    advisory=True,
                )
            )
    questions = prose.count("?")
    if questions / words * 500 > 3:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="rhetorical-questions",
                message=f"{questions} question marks in prose",
                weight=2,
                advisory=True,
            )
        )
    bangs = prose.count("!")
    if bangs > 5:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="exclamations",
                message=f"{bangs} exclamation marks in prose",
                weight=1,
                advisory=True,
            )
        )
    return findings


def collect_bullet_groups(lines: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if re.match(r"^\s*[-*]\s+", line):
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def scan_duplicates(files: list[Path]) -> list[Finding]:
    counter: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    for path in files:
        if path.suffix.lower() not in CODE_EXTS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if len(line) >= DUP_LINE_MIN_LEN:
                counter[line] += 1
                first_seen.setdefault(line, (str(path), lineno))
    findings: list[Finding] = []
    for line, count in counter.most_common():
        if count < DUP_LINE_THRESHOLD:
            break
        where = first_seen[line]
        findings.append(
            Finding(
                path=where[0],
                line=where[1],
                check="boilerplate-duplication",
                message=f"identical line repeated {count}x: {line[:60]}...",
                weight=4,
            )
        )
    return findings[:3]


def scan(
    root: Path,
    phrases: dict,
    extra_globs: list[str] | None = None,
) -> list[Finding]:
    files = iter_files(root)
    globs, disabled = load_ignore_rules(root)
    globs = globs + list(extra_globs or [])
    findings: list[Finding] = []
    for path in files:
        rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        if is_ignored(rel, globs):
            continue
        suffix = path.suffix.lower()
        if suffix in CODE_EXTS:
            findings.extend(scan_code_file(path, phrases))
        elif suffix in DOC_EXTS:
            findings.extend(scan_doc_file(path, phrases))
    findings.extend(scan_duplicates(files))
    if disabled:
        findings = [f for f in findings if f.check.lower() not in disabled]
    return findings


def blocking_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings if not f.advisory)


def advisory_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings if f.advisory)


def total_score(findings: list[Finding]) -> int:
    """Total weight of all findings, including advisory ones."""
    return sum(f.weight for f in findings)


def write_report(path: str, root: Path, findings: list[Finding]) -> None:
    blocking = [f for f in findings if not f.advisory]
    advisory = [f for f in findings if f.advisory]
    lines = [
        "",
        f"Scanned `{root}`: {len(blocking)} blocking / {len(advisory)} advisory finding(s).",
        f"Blocking score **{blocking_score(findings)}**, advisory score "
        f"{advisory_score(findings)} (advisory findings never fail a build).",
        "",
    ]
    if blocking:
        lines.append("### Blocking findings")
        lines.append("")
        lines.append("| Check | Where | Detail | Weight | Fix |")
        lines.append("|---|---|---|---|---|")
        for item in blocking:
            lines.append(
                f"| {item.check} | `{item.path}:{item.line}` | {item.message} "
                f"| {item.weight} | {item.remedy} |"
            )
        lines.append("")
    else:
        lines.append("No blocking AI-slop signals above threshold.")
        lines.append("")
    if advisory:
        lines.append("### Advisory signals (never fail a build)")
        lines.append("")
        lines.append("| Check | Where | Detail | Weight |")
        lines.append("|---|---|---|---|---|")
        for item in advisory:
            lines.append(
                f"| {item.check} | `{item.path}:{item.line}` | {item.message} | {item.weight} |"
            )
        lines.append("")
    lines.append(
        f"Opt out per path or check via `{IGNORE_FILE}` "
        "(glob lines, or `check:<id>` to disable one rule)."
    )
    lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def annotate(findings: list[Finding]) -> None:
    for item in findings[:MAX_ANNOTATIONS]:
        message = item.message.replace("\n", " ")
        prefix = "[advisory] " if item.advisory else ""
        print(f"::warning file={item.path},line={item.line}::{prefix}[{item.check}] {message}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Repo path to scan")
    parser.add_argument(
        "--max-score",
        type=float,
        default=8,
        help="Fail when the blocking score exceeds this value",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="Skip files matching this glob (repeatable, also via .ai-integrity-ignore)",
    )
    parser.add_argument(
        "--report-file",
        default="",
        help="Write a markdown report to this path",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Emit GitHub ::warning annotations on stdout",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    phrases = load_phrases()
    findings = scan(args.root, phrases, extra_globs=args.ignore)
    score = blocking_score(findings)
    if args.report_file:
        write_report(args.report_file, args.root, findings)
    if args.annotate:
        annotate(findings)
    print(
        f"ai-integrity blocking={score} max={args.max_score} "
        f"advisory={advisory_score(findings)} findings={len(findings)}"
    )
    return 1 if score > args.max_score else 0


if __name__ == "__main__":
    sys.exit(main())
