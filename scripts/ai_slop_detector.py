#!/usr/bin/env python3
"""Heuristic detector for AI slop, AI-generated prose and humanizer patterns.

This is a linter, not an oracle: it scores weighty signals that correlate
with machine-written filler and with prose that was deliberately "warmed up"
to look hand-written. Signals include telltale vocabulary in code comments,
emoji padding, TODO density, boilerplate duplication, em-dash overuse,
rule-of-three lists and interjection-heavy prose.

Exit code is 1 when the total weighted score exceeds --max-score.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

PHRASES_PATH = Path(__file__).resolve().parent / "ai_slop_phrases.json"

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


@dataclass
class Finding:
    path: str
    line: int
    check: str
    message: str
    weight: int


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


def phrase_findings(
    path: Path,
    text: str,
    phrases: list[str],
    check: str,
    weight: int,
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
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    findings.extend(phrase_findings(path, text, phrases["doc_phrases"], "ai-prose", 1))
    findings.extend(phrase_findings(path, text, phrases["humanizer_phrases"], "humanizer", 1))
    findings.extend(prose_rhythm_findings(path, text))
    return findings


def prose_rhythm_findings(path: Path, text: str) -> list[Finding]:
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


def scan(root: Path, phrases: dict) -> list[Finding]:
    files = iter_files(root)
    findings: list[Finding] = []
    for path in files:
        suffix = path.suffix.lower()
        if suffix in CODE_EXTS:
            findings.extend(scan_code_file(path, phrases))
        elif suffix in DOC_EXTS:
            findings.extend(scan_doc_file(path, phrases))
    findings.extend(scan_duplicates(files))
    return findings


def total_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings)


def write_report(path: str, root: Path, findings: list[Finding]) -> None:
    score = total_score(findings)
    lines = [
        "",
        f"Scanned `{root}`: {len(findings)} finding(s), weighted score {score}.",
        "",
    ]
    if findings:
        lines.append("| Check | Where | Detail | Weight |")
        lines.append("|---|---|---|---|")
        for item in findings:
            lines.append(
                f"| {item.check} | `{item.path}:{item.line}` | {item.message} | {item.weight} |"
            )
        lines.append("")
    else:
        lines.append("No AI-slop, AI-prose or humanizer signals above threshold.")
        lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def annotate(findings: list[Finding]) -> None:
    for item in findings[:MAX_ANNOTATIONS]:
        message = item.message.replace("\n", " ")
        print(f"::warning file={item.path},line={item.line}::[{item.check}] {message}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Repo path to scan")
    parser.add_argument(
        "--max-score",
        type=float,
        default=8,
        help="Fail when the weighted score exceeds this value",
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
    findings = scan(args.root, phrases)
    score = total_score(findings)
    if args.report_file:
        write_report(args.report_file, args.root, findings)
    if args.annotate:
        annotate(findings)
    print(f"ai-integrity score={score} max={args.max_score} findings={len(findings)}")
    return 1 if score > args.max_score else 0


if __name__ == "__main__":
    sys.exit(main())
