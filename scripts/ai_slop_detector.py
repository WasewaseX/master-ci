#!/usr/bin/env python3
"""Master CI integrity engine: AI slop, structural slop and provenance.

A linter, not an oracle. It reports deterministic signals that correlate
with machine-written filler and with agent-introduced defects: telltale
vocabulary in comments, emoji padding, elided code, stub functions, empty
tests, swallowed exceptions, phantom and undeclared imports, debug residue,
placeholder credentials, plus - advisory only - prose rhythm patterns in
documents and UI-gradient slop in frontend files.

Design borrowed in spirit from three public slop detectors: flamehaven01's
structural deficit model (purity = exp(-0.5 * blocking weight), per-file
deficit, 0.6*worst + 0.4*mean aggregation, CLEAN/SUSPICIOUS/INFLATED/
CRITICAL verdicts), FixMySlop's rule-with-remedy + baseline + SARIF flow,
and slopdetector.org's explainable, evidence-first prose checks.

Contract:
- Exit code 1 only when the *blocking* score exceeds --max-score.
- Blocking-grade signals are deterministic content defects (elided code,
    stubs, empty tests, swallowed exceptions, chatbot residue in comments).
    Everything stylistic or speculative is advisory: reported and annotated,
    never failing a build.
- Provenance signals (`ai-provenance`) and the test inventory
    (`missing-tests`) are traceability signals, never authorship verdicts;
    advisory. Disable with `--no-provenance`.
- Every finding carries a check id and a remediation hint.
- Repos opt out per path or per check via `.ai-integrity-ignore` (glob
    lines, or `check:<id>` lines), and can accept existing findings via
    `--baseline` / record them with `--write-baseline`.
- `--sarif out.sarif` emits SARIF 2.1.0 for the GitHub Security tab.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Sequence


def _line_of(text: str, pos: int) -> int:
    """1-based line number for a character offset."""
    return text.count("\n", 0, pos) + 1


def _strip_strings(text: str) -> str:
    """Remove quoted string literals so regex literals do not self-match."""
    text = re.sub(r'"[^"\n]*"', " ", text)
    text = re.sub(r"'[^'\n]*'", " ", text)
    return re.sub(r"`[^`\n]*`", " ", text)


PHRASES_PATH = Path(__file__).resolve().parent / "ai_slop_phrases.json"
IGNORE_FILE = ".ai-integrity-ignore"
TOOL_NAME = "master-ci-ai-integrity"

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

CODE_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".groovy",
    ".rb",
    ".php",
    ".cs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".hh",
    ".sh",
    ".swift",
    ".kt",
    ".dart",
    ".vue",
    ".svelte",
    ".astro",
}
UI_EXTS = {".css", ".scss", ".less", ".html", ".htm"}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt"}

FAMILY_OF_EXT = {
    ".py": "py",
    ".ts": "js",
    ".tsx": "js",
    ".mts": "js",
    ".cts": "js",
    ".js": "js",
    ".jsx": "js",
    ".mjs": "js",
    ".cjs": "js",
    ".vue": "js",
    ".svelte": "js",
    ".astro": "js",
    ".go": "go",
    ".java": "jvm",
    ".kt": "jvm",
    ".kts": "jvm",
    ".scala": "jvm",
    ".groovy": "jvm",
    ".cs": "csharp",
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".sh": "sh",
    ".swift": "swift",
    ".dart": "dart",
}

BLOCK_OPEN = "/*"
BLOCK_CLOSE = "*/"

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

CHATBOT_COMMENT_PHRASES = (
    "certainly!",
    "here's the revised",
    "here's the updated",
    "here's the complete",
    "here is the revised",
    "hope this helps",
    "let me know if you",
    "as an ai",
    "i cannot fulfill",
    "i've fixed the issue",
    "i've updated the code",
)
CREDIT_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|passwd|token|access[_-]?key)\b[\"']?"
    r"\s*[:=]\s*[\"']([^\"']{6,})[\"']"
)
CREDIT_PLACEHOLDER_VALUE = re.compile(
    r"(?i)your[-_ ]|xxx|changeme|change[-_ ]me|placeholder|dummy|example|"
    r"lorem|insert |todo|test[-_ ]?key|123456|<[^>]*>"
)
PLACEHOLDER_NAME_PATTERN = re.compile(
    r"(?<![\w$])("
    + "|".join(("f" + "oo", "b" + "ar", "b" + "az", "q" + "ux", "foobar"))
    + r")(?![\w$])"
)
STEP_COMMENT_PATTERN = re.compile(
    r"(?i)^//\s*(?:step\s*\d+[:.)]|first,|second,|third,|next,|then,|finally,)\s"
)
ELIDED_PATTERNS = (
    re.compile(r"\.\.\.\s*existing\s+code", re.IGNORECASE),
    re.compile(r"\.\.\.\s*rest\s+of\s+(?:the\s+)?(?:file|code)", re.IGNORECASE),
    re.compile(
        r"\b(?:rest|remainder)\s+of\s+(?:the\s+)?(?:file|code|implementation)", re.IGNORECASE
    ),
    re.compile(r"omitted\s+for\s+(?:brevity|clarity|length)", re.IGNORECASE),
    re.compile(
        r"insert\s+(?:the\s+)?(?:previous|original|remaining)\s+(?:code|implementation)",
        re.IGNORECASE,
    ),
)
PLACEHOLDER_IMPL_PATTERNS = (
    re.compile(r"in (?:a |the )?real (?:app|application|world|production|project)", re.IGNORECASE),
    re.compile(r"left as an? exercise", re.IGNORECASE),
    re.compile(r"for demonstration " r"purposes", re.IGNORECASE),
    re.compile(r"to be " r"implemented", re.IGNORECASE),
    re.compile(r"not yet implemented;? (?:this|the) (?:is|are)", re.IGNORECASE),
)
PLACEHOLDER_NOTE_PATTERNS = (
    re.compile(r"replace (?:this|it) with", re.IGNORECASE),
    re.compile(r"fill (?:this|in) (?:in )?with", re.IGNORECASE),
    re.compile(r"swap (?:this|out) for", re.IGNORECASE),
)
GO_IGNORED_ERROR = re.compile(r"if\s+(?:[^;{;]*;\s*)?err\s*!=\s*nil\s*\{\s*\n?\s*return\s+nil\b")
GO_BLANK_ERR = re.compile(r"^\s*_\s*(?:,\s*_\s*)?=\s*err\b", re.MULTILINE)
GO_DEBUG_PRINT = re.compile(r"\bfmt\.Print(?:ln|f)?\s*\(")
EMPTY_CATCH = re.compile(r"catch\s*(?:\([^)]*\))?\s*\{\s*\}")
PRINT_STACKTRACE_ONLY = re.compile(
    r"catch\s*\([^)]*\)\s*\{\s*(?:[\w$.]+\.printStackTrace\(\);?)\s*\}"
)
EMPTY_RESCUE = re.compile(r"^\s*rescue(?:\s+[A-Z]\w+)?(?:\s*=>\s*\w+)?\s*(?:#.*)?$")
RUST_STUB = re.compile(r"\b(?:todo|unimplemented)!\s*\(")
RUST_UNWRAP = re.compile(r"\.unwrap\(\)")
DEBUGGER_LEFT = re.compile(r"^\s*(?:debugger;\s*$|binding\.pry\b|byebug\b)", re.MULTILINE)
JS_CONSOLE_LOG = re.compile(r"\bconsole\.log\s*\(")
TS_SUPPRESSION = re.compile(r"@(?:ts-ignore|ts-nocheck|ts-expect-error)|:\s*any\b")
BLANKET_SUPPRESS = re.compile(
    r"(?://|/\*)\s*eslint-disable(?:\s|$)|#\s*noqa(?!\s*:)|#\s*type:\s*ignore\b"
    r"|#\s*type: ignore\s*#|nolint\b",
    re.IGNORECASE,
)
UI_GRADIENT = re.compile(
    r"from-(?:purple|pink|blue|indigo|violet|fuchsia)-\d00\s+to-(?:pink|purple|rose|fuchsia|violet)-\d00"
    r"|linear-gradient\([^)]*(?:667eea|764ba2|a855f7|ec4899|8b5cf6)",
    re.IGNORECASE,
)
UI_GRADIENT_TEXT = re.compile(r"bg-clip-text|text-transparent\s+bg-gradient")
UI_MARKETING = re.compile(
    r"revolutioniz\w*|game-?chang\w*|next-gen(?:eration)?\b|seamless(?:ly)?\b|"
    r"cutting-edge|10,000\+|99\.9% uptime|happy users|trusted by thousands",
    re.IGNORECASE,
)
UI_PLACEHOLDER = re.compile(
    r"lorem ipsum|your (?:company|name|logo) here|yourusername|yourbrand|example\.com",
    re.IGNORECASE,
)
MD_EMOJI_HEADING = re.compile(r"^#{1,6}\s+[^\n]*" + EMOJI_PATTERN.pattern, re.MULTILINE)
MD_RESIDUE = re.compile(
    r"certainly!|here's (?:a|the) (?:comprehensive|revised|detailed|complete)|"
    r"\[insert [^\]]+ here\]|tbd:?\s|to be written|chatgpt|as an ai",
    re.IGNORECASE,
)
NODE_BUILTINS = {
    "assert",
    "buffer",
    "child_process",
    "cluster",
    "console",
    "constants",
    "crypto",
    "dgram",
    "dns",
    "domain",
    "events",
    "fs",
    "http",
    "http2",
    "https",
    "inspector",
    "module",
    "net",
    "os",
    "path",
    "perf_hooks",
    "process",
    "punycode",
    "querystring",
    "readline",
    "repl",
    "stream",
    "string_decoder",
    "timers",
    "tls",
    "tty",
    "url",
    "util",
    "v8",
    "vm",
    "worker_threads",
    "zlib",
}
PY_IMPORT_ALIASES = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "attr": "attrs",
    "git": "gitpython",
    "Crypto": "pycryptodome",
    "fitz": "pymupdf",
    "psycopg2": "psycopg2",
    "google": "protobuf",
}
STD_LIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ()))

REMEDIES = {
    "ai-vocab": "Rewrite the comment to describe intent in plain words",
    "emoji-comments": "Remove decorative emoji from code comments",
    "todo-density": "Resolve, ticket or delete the TODO/FIXME markers",
    "trivial-comments": "Delete comments that restate the code; explain why instead",
    "boilerplate-duplication": "Extract the repeated lines into a shared helper",
    "bloat-long-file": (
        "Split the file by responsibility; ask for the version that does "
        "the same job in fewer lines"
    ),
    "bloat-param-count": (
        "Group the parameters into an options object or struct; more than "
        "seven parameters is unreadable at the call site"
    ),
    "bloat-comment-ratio": (
        "Delete comments that narrate the code; keep only the ones that "
        "explain intent or constraints"
    ),
    "bloat-banner-comments": (
        "Remove decorative banner comments; section names belong to the structure, not to ASCII art"
    ),
    "elided-code": "Restore the omitted code; elided edits silently delete working code",
    "placeholder-impl": "Implement the body or delete the function; stubs look done but are not",
    "placeholder-note": "Replace the template note with the real value or implementation",
    "stub-function": "Implement the stub or remove it; list it in a TODO ticket instead",
    "empty-test": "Add assertions; a test that cannot fail proves nothing",
    "bare-except": "Catch the specific exception and handle or re-raise it",
    "swallowed-exception": "Log, wrap or re-raise; a pass-only handler hides failures",
    "empty-catch": "Handle the error or propagate it; an empty catch hides bugs",
    "empty-rescue": "Handle the specific error class or re-raise",
    "go-ignored-error": "Return or wrap the error; returning nil hides the failure",
    "go-error-assigned-blank": "Handle or comment why the error is deliberately discarded",
    "print-stacktrace-only": "Log with context via your logger instead of printStackTrace",
    "debugger-left": "Remove the debugger statement or breakpoint before committing",
    "debug-print": "Remove the debug print or use structured logging",
    "phantom-import": "Delete the import or use it; unused imports are dead weight",
    "undeclared-import": (
        "Verify the package exists and declare it in the manifest; "
        "hallucinated names get squatted by attackers (slopsquatting)"
    ),
    "lint-escape": "Fix the underlying issue or scope the suppression to one rule and line",
    "ts-suppression": "Fix the type instead of suppressing; avoid any",
    "blanket-suppression": "Scope the suppression to a single rule with a reason",
    "step-comments": "Let the code read like code; move narration to the PR description",
    "placeholder-naming": "Rename placeholder identifiers to names that describe the data",
    "placeholder-credential": "Load credentials from the environment or a secret store",
    "god-function": "Split the function along its responsibilities",
    "deep-nesting": "Guard-clause and extract helpers to flatten the nesting",
    "unwrap-density": "Handle the Result/Option; unwrap panics in production",
    "rust-stub": "Implement the function; todo!() panics at runtime",
    "mutable-default": "Default to None and build the container inside the function",
    "star-import": "Import the names you use; star imports hide origins",
    "ui-ai-gradient": "Use the project's design tokens; the purple-pink gradient is the AI default",
    "ui-gradient-text": "Gradient headlines are the template aesthetic; use solid brand color",
    "ui-marketing-copy": "Replace buzzwords with what the product actually does",
    "ui-placeholder-copy": "Replace lorem/company placeholders with real copy",
    "ui-emoji-decor": "Use the icon set, not emoji, for feature decoration",
    "md-emoji-headings": "Keep headings text-only; emoji headings are a generated-doc tell",
    "md-residue": "Delete chatbot residue and placeholders from the document",
    "chatbot-residue": "Remove assistant-voice phrases from comments",
    "ai-prose": "Rewrite the passage in your own words",
    "humanizer": "Remove the filler phrase",
    "em-dash-density": "Use commas, colons or parentheses instead of em-dashes",
    "rule-of-three": "Vary list lengths; uniform triples are a machine-prose tell",
    "rhetorical-questions": "State the answer instead of asking the reader",
    "exclamations": "Remove exclamation marks from documentation",
    "ai-provenance": (
        "Declare the generating tool in the commit message or PR description, "
        "or remove the stale marker"
    ),
    "missing-tests": (
        "Add tests for the source files, or move generated vendored code "
        "behind an interface and ignore the directory"
    ),
}
BLOCKING_CHECKS = frozenset(
    {
        "ai-vocab",
        "emoji-comments",
        "todo-density",
        "trivial-comments",
        "boilerplate-duplication",
        "elided-code",
        "placeholder-impl",
        "stub-function",
        "empty-test",
        "bare-except",
        "swallowed-exception",
        "empty-catch",
        "empty-rescue",
        "go-ignored-error",
        "print-stacktrace-only",
        "debugger-left",
        "rust-stub",
        "chatbot-residue",
    }
)

# Structural-budget checks are judgment calls: a finding here can never
# fail a build, no matter how a call site constructs the Finding.
ADVISORY_ONLY_CHECKS = frozenset(
    {
        "god-function",
        "deep-nesting",
        "mutable-default",
        "star-import",
        "unwrap-density",
        "bloat-long-file",
        "bloat-param-count",
        "bloat-comment-ratio",
        "bloat-banner-comments",
    }
)


@dataclass
class Finding:
    path: str
    line: int
    check: str
    message: str
    weight: int
    advisory: bool = field(default=False)

    def __post_init__(self) -> None:
        if self.check in BLOCKING_CHECKS:
            self.advisory = False
        elif self.check in ADVISORY_ONLY_CHECKS:
            self.advisory = True

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
        suffix = path.suffix.lower()
        if suffix not in CODE_EXTS and suffix not in DOC_EXTS and suffix not in UI_EXTS:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        files.append(path)
    return files


def load_ignore_rules(root: Path) -> tuple[list[str], set[str], dict[str, list[str]]]:
    """Read .ai-integrity-ignore.

    Grammar:
    - `glob/line`            - skip the file entirely
    - `check:<id>`           - disable the check everywhere
    - `check:<id>:<glob>`    - disable the check only on matching paths
    """
    globs: list[str] = []
    disabled: set[str] = set()
    scoped: dict[str, list[str]] = {}
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
                parts = line.split(":", 2)
                check_id = parts[1].strip().lower()
                if len(parts) == 3 and parts[2].strip():
                    scoped.setdefault(check_id, []).append(parts[2].strip())
                else:
                    disabled.add(check_id)
            else:
                globs.append(line.rstrip("/"))
    return globs, disabled, scoped


def _scoped_out(finding: Finding, scoped: dict[str, list[str]]) -> bool:
    """True when a scoped `check:<id>:<glob>` rule covers the finding."""
    rules = scoped.get(finding.check.lower())
    if not rules:
        return False
    name = PurePosixPath(finding.path.replace("\\", "/")).name
    return any(
        fnmatch.fnmatch(finding.path, g)
        or fnmatch.fnmatch(name, g)
        or fnmatch.fnmatch(finding.path, g.rstrip("/*") + "/*")
        for g in rules
    )


def is_ignored(rel_path: str, globs: list[str]) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(
        fnmatch.fnmatch(norm, g) or fnmatch.fnmatch(norm, g.rstrip("/*") + "/*") for g in globs
    )


# ---------------------------------------------------------------------------
# Language-aware comment extraction
# ---------------------------------------------------------------------------


@dataclass
class Comment:
    line: int
    text: str
    kind: str  # 'line' | 'block' | 'docstring'


def _py_docstring_step(
    idx: int,
    stripped: str,
    in_doc: bool,
    doc_delim: str,
) -> tuple[Comment, bool, str] | None:
    """Handle one line while in/entering Python docstring state.

    Returns (comment, still_in_doc, delim), or None when the line is not
    docstring state and the caller should fall through to other rules.
    """
    if in_doc:
        closing = doc_delim in stripped
        return Comment(idx, stripped, "docstring"), not closing, doc_delim
    if not stripped.startswith(('"""', "'''")):
        return None
    delim = stripped[:3]
    if stripped.count(delim) >= 2 and len(stripped) > 3:
        return Comment(idx, stripped, "docstring"), False, doc_delim
    return Comment(idx, stripped, "docstring"), True, delim


def extract_comments(text: str, family: str) -> list[Comment]:
    """Extract comments with a small state machine per language family."""
    line_prefixes = {
        "py": ("#",),
        "sh": ("#",),
        "ruby": ("#",),
        "php": ("//", "#"),
    }.get(family, ("//",))
    comments: list[Comment] = []
    in_block = False
    in_doc = False
    doc_delim = ""
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if in_block:
            comments.append(Comment(idx, stripped, "block"))
            if BLOCK_CLOSE in stripped:
                in_block = False
            continue
        if family == "py":
            step = _py_docstring_step(idx, stripped, in_doc, doc_delim)
            if step is not None:
                comments.append(step[0])
                in_doc, doc_delim = step[1], step[2]
                continue
        if family == "ruby" and stripped.startswith("=begin"):
            in_block = True
            comments.append(Comment(idx, stripped, "block"))
            continue
        if any(stripped.startswith(prefix) for prefix in line_prefixes):
            comments.append(Comment(idx, stripped, "line"))
            continue
        if stripped.startswith(BLOCK_OPEN):
            comments.append(Comment(idx, stripped, "block"))
            if BLOCK_CLOSE not in stripped[len(BLOCK_OPEN) :]:
                in_block = True
    return comments


# ---------------------------------------------------------------------------
# Phrase-based checks
# ---------------------------------------------------------------------------


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
        line = _line_of(text, first)
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


def _phrase_in_any(comment_texts: list[str], phrase: str) -> int:
    return sum(1 for text in comment_texts if phrase.lower() in text.lower())


def _phrase_rule_findings(
    path: Path,
    comments: list[Comment],
    texts: list[str],
    phrases: Sequence[str],
    check: str,
    message_fmt: str,
    weight: int,
) -> list[Finding]:
    """One finding per phrase family that appears in any comment."""
    findings: list[Finding] = []
    for phrase in phrases:
        count = _phrase_in_any(texts, phrase)
        if not count:
            continue
        line = next(c.line for c in comments if phrase.lower() in c.text.lower())
        findings.append(
            Finding(
                path=str(path),
                line=line,
                check=check,
                message=message_fmt.format(phrase=phrase, count=count),
                weight=weight,
            )
        )
    return findings


def _comment_style_findings(path: Path, comments: list[Comment]) -> list[Finding]:
    """Emoji padding, trivial restatements and step-by-step narration."""
    findings: list[Finding] = []
    emoji_hits = sum(bool(EMOJI_PATTERN.search(c.text)) for c in comments)
    if emoji_hits:
        findings.append(
            Finding(
                path=str(path),
                line=comments[0].line,
                check="emoji-comments",
                message=f"emoji in code comments ({emoji_hits}x)",
                weight=min(emoji_hits, 5),
            )
        )
    line_comments = [c.text for c in comments if c.kind == "line"]
    if len(line_comments) >= 5:
        trivial = sum(bool(TRIVIAL_COMMENT.match(c.strip())) for c in line_comments)
        if trivial / len(line_comments) > 0.4:
            findings.append(
                Finding(
                    path=str(path),
                    line=comments[0].line,
                    check="trivial-comments",
                    message=(f"{trivial}/{len(line_comments)} comments merely restate the code"),
                    weight=3,
                )
            )
    step_lines = [c for c in comments if STEP_COMMENT_PATTERN.match(c.text)]
    if len(step_lines) >= 3:
        findings.append(
            Finding(
                path=str(path),
                line=step_lines[0].line,
                check="step-comments",
                message=f"{len(step_lines)} step-by-step narration comments",
                weight=1,
                advisory=True,
            )
        )
    return findings


def scan_comment_vocabulary(path: Path, comments: list[Comment], phrases: dict) -> list[Finding]:
    texts = [c.text for c in comments]
    findings = _phrase_rule_findings(
        path,
        comments,
        texts,
        phrases["code_phrases"],
        "ai-vocab",
        "{phrase!r} in comment ({count}x)",
        2,
    )
    findings += _phrase_rule_findings(
        path,
        comments,
        texts,
        CHATBOT_COMMENT_PHRASES,
        "chatbot-residue",
        "assistant-voice {phrase!r} in comment ({count}x)",
        2,
    )
    findings += _comment_style_findings(path, comments)
    return findings


# ---------------------------------------------------------------------------
# Structural checks (per language family)
# ---------------------------------------------------------------------------


def check_elided_code(path: Path, text: str) -> list[Finding]:
    for pattern in ELIDED_PATTERNS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "elided-code",
                    f"elided code marker {pattern.pattern!r}",
                    3,
                )
            ]
    return []


def check_placeholder_impl(path: Path, text: str) -> list[Finding]:
    for pattern in PLACEHOLDER_IMPL_PATTERNS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "placeholder-impl",
                    f"placeholder implementation: {pattern.pattern!r}",
                    2,
                )
            ]
    for pattern in PLACEHOLDER_NOTE_PATTERNS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "placeholder-note",
                    "template note left in code",
                    1,
                    advisory=True,
                )
            ]
    return []


GOD_FUNCTION_LINES = 100
MAX_NESTING = 5

# ---------------------------------------------------------------------------
# Bloat budget: the "AI writes 300 lines for 100 lines of work" guards.
# All advisory - bloat is judgment, not a content defect. Budgets are
# deliberately generous so normal, well-factored code never trips them.
# ---------------------------------------------------------------------------
BLOAT_FILE_LINES = 400  # non-blank lines before a source file is flagged
BLOAT_MAX_PARAMS = 7  # parameters before a signature is unreadable
BLOAT_COMMENT_RATIO = 0.5  # comment share of non-blank lines
BLOAT_COMMENT_MIN_LINES = 60  # only judge the ratio in substantial files
BLOAT_BANNER_MIN = 4  # decorative banner fences before judging their density
BLOAT_BANNER_GAP = 20  # median gap under which banners are padding, not navigation
MAX_PARAMS = BLOAT_MAX_PARAMS

# Lines that may open a function block in a brace family. Conservative:
# anything starting with a control, expression or declaration keyword is
# out; what remains must carry a parameter list.
BLOAT_HEADER_EXCLUDE = re.compile(
    r"^\s*(?:if|else|for|while|switch|catch|do|try|with|match|case|default|"
    r"namespace|class|struct|enum|union|interface|impl|trait|mod|extension|"
    r"protocol|object|return|throw|new|import|package|using|require|include|"
    r"typedef|operator|await|yield|print|echo|puts|elsif|elif|when|foreach|"
    r"synchronized|loop)\b"
)
BLOAT_KW_FUNCS = re.compile(r"\b(?:function|fn|func|def|sub)\b")
BLOAT_METHOD_STYLE = re.compile(
    r"^\s*(?:@[\w.]+\s+)*(?:(?:public|private|protected|internal|static|"
    r"final|abstract|override|virtual|sealed|async|unsafe|extern|inline|"
    r"suspend|explicit|implicit|const|pub)\s+)*[\w<>\[\],:. ]*?\w+\s*\("
)
BLOAT_ARROW_STYLE = re.compile(r"=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{?\s*$")
BANNER_DECO = re.compile(r"^[=\-_~+ ]{6,}$")
RUBY_OPENER = re.compile(
    r"^\s*(?:def|if|unless|while|until|case|begin|class|module)\b|do\s*(?:\|[^|]*\|)?\s*$"
)
RUBY_CLOSER = re.compile(r"^\s*end\b")


def _finding(
    path: Path,
    text: str,
    pos: int,
    check: str,
    message: str,
    weight: int,
    advisory: bool = False,
) -> Finding:
    return Finding(
        path=str(path),
        line=_line_of(text, pos),
        check=check,
        message=message,
        weight=weight,
        advisory=advisory,
    )


def _nesting_depth(node: ast.AST, depth: int = 0) -> int:
    best = depth
    for child in ast.iter_child_nodes(node):
        step = (
            depth + 1
            if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try))
            else depth
        )
        best = max(best, _nesting_depth(child, step))
    return best


def _py_func_findings(path: Path, node) -> list[Finding]:
    """Size, nesting and mutable-default checks for one Python function."""
    findings: list[Finding] = []
    length = (node.end_lineno or node.lineno) - node.lineno + 1
    if length > GOD_FUNCTION_LINES:
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="god-function",
                message=f"function {node.name!r} spans {length} lines",
                weight=1,
                advisory=True,
            )
        )
    if _nesting_depth(node) > MAX_NESTING:
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="deep-nesting",
                message=f"function {node.name!r} nests more than {MAX_NESTING} levels",
                weight=1,
                advisory=True,
            )
        )
    defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
    if any(
        isinstance(d, (ast.List, ast.Dict, ast.Set))
        or (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Name)
            and d.func.id in ("list", "dict", "set")
        )
        for d in defaults
    ):
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="mutable-default",
                message=f"function {node.name!r} has a mutable default argument",
                weight=1,
                advisory=True,
            )
        )
    params = (
        len(node.args.posonlyargs)
        + len(node.args.args)
        + len(node.args.kwonlyargs)
        + (1 if node.args.vararg else 0)
        + (1 if node.args.kwarg else 0)
    )
    if params > BLOAT_MAX_PARAMS:
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="bloat-param-count",
                message=(
                    f"function {node.name!r} takes {params} parameters (budget {BLOAT_MAX_PARAMS})"
                ),
                weight=1,
                advisory=True,
            )
        )
    return findings


def _py_stub_body(body: list[ast.stmt]) -> bool:
    stmts = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if len(stmts) != 1:
        return False
    only = stmts[0]
    if isinstance(only, ast.Pass):
        return True
    if isinstance(only, ast.Raise):
        exc = only.exc
        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name):
            return exc.func.id in ("NotImplementedError", "NotImplemented")
        if isinstance(exc, ast.Name):
            return exc.id == "NotImplementedError"
    return False


def _py_has_assert(node: ast.AST) -> bool:
    return any(isinstance(child, (ast.Assert, ast.Raise)) for child in ast.walk(node))


def _py_def_findings(path: Path, node) -> list[Finding]:
    """FunctionDef branch: stub bodies, assertion-less tests, function smells."""
    findings: list[Finding] = []
    if _py_stub_body(node.body):
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="stub-function",
                message=f"function {node.name!r} has a stub-only body",
                weight=2,
            )
        )
    if node.name.startswith("test_") and not _py_has_assert(node):
        findings.append(
            Finding(
                path=str(path),
                line=node.lineno,
                check="empty-test",
                message=f"test {node.name!r} contains no assert or raise",
                weight=2,
            )
        )
    findings.extend(_py_func_findings(path, node))
    return findings


def _py_handler_findings(path: Path, node: ast.ExceptHandler) -> list[Finding]:
    """ExceptHandler branch: bare excepts and swallowed exceptions."""
    if node.type is not None and not all(isinstance(s, ast.Pass) for s in node.body):
        return []
    if node.type is None:
        return [
            Finding(
                path=str(path),
                line=node.lineno,
                check="bare-except",
                message="bare `except:` catches everything including SystemExit",
                weight=2,
            )
        ]
    return [
        Finding(
            path=str(path),
            line=node.lineno,
            check="swallowed-exception",
            message="exception swallowed (pass-only handler)",
            weight=2,
        )
    ]


def check_py_ast(path: Path, text: str, family: str) -> list[Finding]:
    """AST-backed Python checks: stubs, empty tests, exceptions, smells."""
    if family != "py":
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            findings.extend(_py_def_findings(path, node))
        elif isinstance(node, ast.ExceptHandler):
            findings.extend(_py_handler_findings(path, node))
        elif isinstance(node, ast.ImportFrom) and node.module and node.names:
            if any(a.name == "*" for a in node.names):
                findings.append(
                    Finding(
                        path=str(path),
                        line=node.lineno,
                        check="star-import",
                        message=f"star import from {node.module!r}",
                        weight=1,
                        advisory=True,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Bloat budget: function, file and comment-pressure checks.
# Python is measured through its AST; brace families through a conservative
# line scanner; Ruby through def/end pairing. Every check is advisory and
# reports at most one finding per file per rule (the worst offender wins).
# ---------------------------------------------------------------------------


def _code_lines(text: str, family: str) -> list[str]:
    """Lines with strings and trailing comments blanked for brace counting."""
    prefixes: tuple[str, ...] = ("//", "#") if family == "php" else ("//",)
    if family == "ruby":
        prefixes = ("#",)
    out: list[str] = []
    for raw in text.splitlines():
        line = _strip_strings(raw)
        for prefix in prefixes:
            pos = line.find(prefix)
            if pos != -1:
                line = line[:pos]
        out.append(line)
    return out


def _brace_depths(code_lines: list[str]) -> tuple[list[int], list[int], list[int]]:
    """Per-line brace bookkeeping.

    Returns (depth_end, max_depth, max_control_depth) where max_depth is the
    highest brace depth reached on the line and max_control_depth counts
    only braces that open control blocks or closures (`) {`, `else {`,
    `do {`, `try {`) - the nesting a reader actually has to track.
    """
    n = len(code_lines)
    depth_end = [0] * n
    max_depth = [0] * n
    max_control = [0] * n
    depth = 0
    for idx, code in enumerate(code_lines):
        line_max = depth
        line_ctl = depth
        for ch in code:
            if ch == "{":
                depth += 1
                line_max = max(line_max, depth)
            elif ch == "}":
                depth = max(0, depth - 1)
        tail = code.rstrip()
        if tail.endswith("{"):
            head = tail[:-1].rstrip()
            if head.endswith(")") or re.search(r"\b(?:else|do|try)\s*$", head):
                line_ctl = max(line_ctl, depth)
        depth_end[idx] = depth
        max_depth[idx] = line_max
        max_control[idx] = line_ctl
    return depth_end, max_depth, max_control


def _last_param_group(header: str) -> str | None:
    """The last balanced (...) group of a signature line."""
    best: str | None = None
    start = -1
    nested = 0
    for pos, ch in enumerate(header):
        if ch == "(":
            if nested == 0:
                start = pos
            nested += 1
        elif ch == ")" and nested > 0:
            nested -= 1
            if nested == 0 and start != -1:
                best = header[start + 1 : pos]
                start = -1
    return best


def _param_count(header: str) -> int:
    group = _last_param_group(header)
    if group is None or not group.strip():
        return 0
    depth = 0
    params = 1
    for ch in group:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            params += 1
    return params


def _header_openers(code_lines: list[str]) -> list[tuple[int, str]]:
    """Lines that may open a function block: (index, header_text).

    A header is a signature line that carries its own `{` or is followed by
    a line whose first token is `{` (Allman style). Control, expression and
    declaration keywords never open blocks.
    """
    n = len(code_lines)
    headers: list[tuple[int, str]] = []
    for idx, code in enumerate(code_lines):
        stripped = code.strip()
        if not stripped or BLOAT_HEADER_EXCLUDE.match(stripped):
            continue
        has_parens = "(" in code and ")" in code
        is_kw = bool(BLOAT_KW_FUNCS.search(code))
        is_method = bool(BLOAT_METHOD_STYLE.match(code))
        is_arrow = bool(BLOAT_ARROW_STYLE.search(code))
        if not (has_parens and (is_kw or is_method or is_arrow)):
            continue
        if "{" in code:
            headers.append((idx, code))
            continue
        for nxt in range(idx + 1, min(idx + 3, n)):
            nxt_stripped = code_lines[nxt].strip()
            if not nxt_stripped:
                continue
            if nxt_stripped.startswith("{"):
                headers.append((idx, code))
            break
    return headers


def _brace_blocks(code_lines: list[str]) -> list[dict]:
    """Function-ish brace blocks with length and control-nesting depth."""
    depth_end, max_depth, max_control = _brace_depths(code_lines)
    blocks: list[dict] = []
    for idx, header in _header_openers(code_lines):
        open_idx: int
        if "{" in code_lines[idx]:
            open_idx = idx
        else:
            found: int | None = None
            for nxt in range(idx + 1, min(idx + 3, len(code_lines))):
                if code_lines[nxt].strip().startswith("{"):
                    found = nxt
                    break
            if found is None:
                continue
            open_idx = found
        open_depth = depth_end[open_idx] - 1
        if open_depth < 0:
            open_depth = 0
        close_idx = None
        for j in range(open_idx, len(code_lines)):
            if depth_end[j] <= open_depth and j >= open_idx:
                close_idx = j
                break
        if close_idx is None:
            continue
        inner_ctl = max(max_control[open_idx : close_idx + 1]) - (open_depth + 1)
        blocks.append(
            {
                "header": idx,
                "open": open_idx,
                "close": close_idx,
                "length": close_idx - idx + 1,
                "depth": max(0, inner_ctl),
                "header_text": header,
            }
        )
    return blocks


def _ruby_def_blocks(code_lines: list[str]) -> list[dict]:
    """def/end blocks with length and nesting via keyword pairing."""
    blocks: list[dict] = []
    n = len(code_lines)
    idx = 0
    while idx < n:
        if re.match(r"^\s*def\s", code_lines[idx]):
            depth = 0
            max_depth = 0
            close = None
            for j in range(idx, n):
                if j > idx and RUBY_OPENER.match(code_lines[j]):
                    depth += 1
                    max_depth = max(max_depth, depth)
                if RUBY_CLOSER.match(code_lines[j]):
                    if depth == 0:
                        close = j
                        break
                    depth -= 1
            if close is not None:
                blocks.append(
                    {
                        "header": idx,
                        "close": close,
                        "length": close - idx + 1,
                        "depth": max_depth,
                        "header_text": code_lines[idx],
                    }
                )
                idx = close + 1
                continue
        idx += 1
    return blocks


def _is_banner(comment: Comment) -> bool:
    core = re.sub(r"^(?:#|//|/\*|\*)+\s*", "", comment.text.strip())
    core = core.rstrip("*/").strip()
    if not core:
        return False
    return bool(BANNER_DECO.match(core))


def _comment_pressure_finding(
    path: Path,
    comments: list[Comment],
    non_blank: int,
) -> Finding | None:
    """Flag files where comments outweigh code (AI padding tell)."""
    if non_blank < BLOAT_COMMENT_MIN_LINES:
        return None
    comment_lines = {c.line for c in comments}
    ratio = len(comment_lines) / non_blank
    if ratio <= BLOAT_COMMENT_RATIO:
        return None
    return Finding(
        path=str(path),
        line=1,
        check="bloat-comment-ratio",
        message=(
            f"{len(comment_lines)}/{non_blank} lines are comments "
            f"({ratio:.0%} - budget {BLOAT_COMMENT_RATIO:.0%})"
        ),
        weight=1,
        advisory=True,
    )


def _banner_cluster_starts(comments: list[Comment]) -> list[int]:
    """First line of each banner cluster (a titled block is one unit)."""
    starts: list[int] = []
    prev = 0
    for c in comments:
        if not _is_banner(c):
            continue
        if not starts or c.line - prev > 2:
            starts.append(c.line)
        prev = c.line
    return starts


def _banner_padding_finding(
    path: Path,
    comments: list[Comment],
    non_blank: int,
) -> Finding | None:
    """Flag bunched banner blocks; spread-out dividers are navigation."""
    clusters = _banner_cluster_starts(comments)
    if len(clusters) < BLOAT_BANNER_MIN:
        return None
    gaps = sorted(b - a for a, b in zip(clusters, clusters[1:]))
    median_gap = gaps[len(gaps) // 2] if gaps else non_blank
    if median_gap >= BLOAT_BANNER_GAP:
        return None
    return Finding(
        path=str(path),
        line=clusters[0],
        check="bloat-banner-comments",
        message=(
            f"{len(clusters)} decorative banner blocks pad the file; "
            "structure should carry the sections"
        ),
        weight=1,
        advisory=True,
    )


def _file_bloat_findings(
    path: Path,
    text: str,
    comments: list[Comment],
    rel: str,
) -> list[Finding]:
    """File-level budgets: length, comment pressure, banner padding."""
    findings: list[Finding] = []
    non_blank = sum(1 for line in text.splitlines() if line.strip())
    if non_blank > BLOAT_FILE_LINES and not is_test_path(rel):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="bloat-long-file",
                message=f"file spans {non_blank} non-blank lines (budget {BLOAT_FILE_LINES})",
                weight=1,
                advisory=True,
            )
        )
    pressure = _comment_pressure_finding(path, comments, non_blank)
    if pressure:
        findings.append(pressure)
    padding = _banner_padding_finding(path, comments, non_blank)
    if padding:
        findings.append(padding)
    return findings


def _worst_block_findings(path: Path, blocks: list[dict]) -> list[Finding]:
    """One finding per rule for the worst function block in the file."""
    findings: list[Finding] = []
    worst_len = max(blocks, key=lambda b: b["length"])
    worst_depth = max(blocks, key=lambda b: b["depth"])
    worst_params = max(blocks, key=lambda b: _param_count(b["header_text"]))
    if worst_len["length"] > GOD_FUNCTION_LINES:
        findings.append(
            Finding(
                path=str(path),
                line=worst_len["header"] + 1,
                check="god-function",
                message=f"function block spans {worst_len['length']} lines",
                weight=1,
                advisory=True,
            )
        )
    if worst_depth["depth"] > MAX_NESTING:
        findings.append(
            Finding(
                path=str(path),
                line=worst_depth["header"] + 1,
                check="deep-nesting",
                message=f"nests more than {MAX_NESTING} levels",
                weight=1,
                advisory=True,
            )
        )
    params = _param_count(worst_params["header_text"])
    if params > BLOAT_MAX_PARAMS:
        findings.append(
            Finding(
                path=str(path),
                line=worst_params["header"] + 1,
                check="bloat-param-count",
                message=f"{params} parameters (budget {BLOAT_MAX_PARAMS})",
                weight=1,
                advisory=True,
            )
        )
    return findings


def check_bloat(
    path: Path,
    text: str,
    family: str,
    rel: str,
    comments: list[Comment],
) -> list[Finding]:
    """File-level and function-level bloat budget checks (all advisory)."""
    findings = _file_bloat_findings(path, text, comments, rel)
    if family == "py":
        return findings
    blocks = (
        _ruby_def_blocks(_code_lines(text, family))
        if family == "ruby"
        else _brace_blocks(_code_lines(text, family))
    )
    if blocks:
        findings.extend(_worst_block_findings(path, blocks))
    return findings


def check_exceptions_js_family(path: Path, text: str, family: str) -> list[Finding]:
    findings: list[Finding] = []
    if family in ("js", "jvm", "csharp", "cpp", "php", "swift", "dart"):
        for match in EMPTY_CATCH.finditer(text):
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "empty-catch",
                    "empty catch block hides every error",
                    2,
                )
            )
    if family == "jvm":
        for match in PRINT_STACKTRACE_ONLY.finditer(text):
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "print-stacktrace-only",
                    "catch block only prints a stack trace",
                    1,
                )
            )
    if family == "ruby":
        lines = text.splitlines()
        for idx, raw in enumerate(lines):
            if EMPTY_RESCUE.match(raw):
                nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
                if nxt == "end":
                    findings.append(
                        Finding(
                            path=str(path),
                            line=idx + 1,
                            check="empty-rescue",
                            message="rescue with no handling before end",
                            weight=2,
                        )
                    )
    return findings


def check_go(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    if family != "go":
        return []
    findings: list[Finding] = []
    for match in GO_IGNORED_ERROR.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "go-ignored-error",
                "err checked but nil returned - failure hidden",
                2,
            )
        )
    for match in GO_BLANK_ERR.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "go-error-assigned-blank",
                "error assigned to blank identifier",
                1,
                advisory=True,
            )
        )
    is_test = rel.endswith("_test.go")
    if not is_test:
        debugs = len(GO_DEBUG_PRINT.findall(text))
        if debugs:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="debug-print",
                    message=f"fmt.Print* debug output ({debugs}x)",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def check_rust(path: Path, text: str, family: str) -> list[Finding]:
    if family != "rust":
        return []
    findings: list[Finding] = []
    for match in RUST_STUB.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "rust-stub",
                f"{match.group(0)}!() panics at runtime",
                1,
            )
        )
    unwraps = len(RUST_UNWRAP.findall(text))
    if unwraps >= 10:
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="unwrap-density",
                message=f"{unwraps} unwrap() calls; panics on None/Err",
                weight=1,
                advisory=True,
            )
        )
    return findings


def check_debug_left(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    lowered_rel = rel.lower()
    if ".test." in lowered_rel or ".spec." in lowered_rel or "/test" in lowered_rel:
        return []
    findings: list[Finding] = []
    if family == "js" and "debugger;" in text:
        findings.append(
            _finding(
                path,
                text,
                text.find("debugger;"),
                "debugger-left",
                "debugger; statement committed",
                2,
            )
        )
    if family in ("ruby",) and re.search(r"binding\.pry|byebug\b", text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="debugger-left",
                message="pry/byebug breakpoint committed",
                weight=2,
            )
        )
    return findings


def check_js_slop(path: Path, text: str, family: str, rel: str) -> list[Finding]:
    if family != "js":
        return []
    findings: list[Finding] = []
    is_test = bool(re.search(r"(?:\.test\.|\.spec\.|/tests?/|/__tests__/)", rel.lower()))
    if not is_test:
        logs = len(JS_CONSOLE_LOG.findall(text))
        if logs:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="debug-print",
                    message=f"console.log debug output ({logs}x)",
                    weight=1,
                    advisory=True,
                )
            )
    for match in TS_SUPPRESSION.finditer(text):
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "ts-suppression",
                f"type-system bypass {match.group(0)!r}",
                1,
                advisory=True,
            )
        )
    return findings


def check_blanket_suppression(path: Path, text: str) -> list[Finding]:
    count = len(BLANKET_SUPPRESS.findall(text))
    if count >= 2:
        return [
            Finding(
                path=str(path),
                line=1,
                check="blanket-suppression",
                message=f"{count} blanket lint/type suppressions",
                weight=1,
                advisory=True,
            )
        ]
    return []


def check_placeholder_names(path: Path, text: str) -> list[Finding]:
    hits = len(PLACEHOLDER_NAME_PATTERN.findall(_strip_strings(text)))
    if hits >= 5:
        return [
            Finding(
                path=str(path),
                line=1,
                check="placeholder-naming",
                message=f"{hits} foo/bar/baz-style identifiers",
                weight=1,
                advisory=True,
            )
        ]
    return []


def check_placeholder_credential(path: Path, text: str) -> list[Finding]:
    for match in CREDIT_VALUE_PATTERN.finditer(text):
        value = match.group(2)
        if CREDIT_PLACEHOLDER_VALUE.search(value):
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "placeholder-credential",
                    f"placeholder credential assigned to {match.group(1)!r}",
                    2,
                    advisory=True,
                )
            ]
    return []


def check_phantom_imports(path: Path, text: str, family: str) -> list[Finding]:
    """Imports that are never used in the file (advisory)."""
    if family == "py":
        return _py_phantom_imports(path, text)
    if family == "js":
        return _js_phantom_imports(path, text)
    return []


def _py_imported_names(tree: ast.AST) -> list[tuple[str, int]]:
    """Collect (bound name, line) for every import in the module."""
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                imported.append((name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    name = alias.asname or alias.name
                    imported.append((name, node.lineno))
    return imported


def _py_unused_import_findings(
    path: Path,
    body: str,
    imported: list[tuple[str, int]],
) -> list[Finding]:
    findings: list[Finding] = []
    for name, lineno in imported[:40]:
        if name in ("__future__", "annotations"):
            continue
        if not re.search(rf"\b{re.escape(name)}\b", body):
            findings.append(
                Finding(
                    path=str(path),
                    line=lineno,
                    check="phantom-import",
                    message=f"import {name!r} is never used",
                    weight=1,
                    advisory=True,
                )
            )
    return findings[:5]


def _py_phantom_imports(path: Path, text: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    imported = _py_imported_names(tree)
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("import ", "from "))
    )
    return _py_unused_import_findings(path, body, imported)


def _js_phantom_imports(path: Path, text: str) -> list[Finding]:
    import_pattern = r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']([^\"']+)[\"']"
    findings: list[Finding] = []
    seen_specs: set[str] = set()
    for match in re.finditer(import_pattern, text):
        spec = match.group(1)
        if spec.startswith((".", "/", "node:")) or spec in seen_specs:
            continue
        seen_specs.add(spec)
        cleaned = re.sub(
            r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']"
            + re.escape(spec)
            + r"[\"']",
            "",
            text,
        )
        if not re.search(rf"[\"']{re.escape(spec)}[\"']", cleaned) and spec not in cleaned:
            findings.append(
                _finding(
                    path,
                    text,
                    match.start(),
                    "phantom-import",
                    f"import {spec!r} is never referenced",
                    1,
                    advisory=True,
                )
            )
    return findings[:5]


def _parse_py_manifest(manifest: Path, declared: set[str]) -> None:
    try:
        content = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if manifest.suffix == ".txt":
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith(("-", "#")):
                continue
            name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
            if name:
                declared.add(name.lower().replace("_", "-"))
        return
    block = re.search(r"dependencies\s*=\s*\[(.*?)\]", content, re.DOTALL)
    if block:
        for match in re.finditer(r"[\"']([A-Za-z0-9_.\-]+)", block.group(1)):
            declared.add(match.group(1).lower().replace("_", "-"))


def _local_modules(root: Path) -> set[str]:
    """Top-level importable module names in the project tree."""
    local: set[str] = set()
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.startswith("."):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            local.add(entry.name)
        elif entry.is_dir() and (entry / "src").is_dir():
            for sub in sorted((entry / "src").iterdir()):
                if sub.is_dir() and (sub / "__init__.py").exists():
                    local.add(sub.name)
        elif entry.suffix == ".py":
            local.add(entry.stem)
    return local


def collect_py_dependencies(root: Path) -> tuple[set[str], set[str]]:
    """Declared PyPI distributions and local importable module names."""
    declared: set[str] = set()
    local: set[str] = set()
    if root.is_dir():
        for pattern in ("requirements*.txt", "pyproject.toml"):
            for manifest in root.glob(pattern):
                _parse_py_manifest(manifest, declared)
        local = _local_modules(root)
    return declared, local


def collect_js_dependencies(root: Path) -> set[str]:
    manifest = root / "package.json" if root.is_dir() else None
    if not manifest or not manifest.is_file():
        return set()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            deps.update(value.keys())
    return deps


def _py_undeclared_findings(
    path: Path,
    text: str,
    py_manifests: tuple[set[str], set[str]],
) -> list[Finding]:
    """Python imports that no manifest declares (advisory)."""
    declared, local = py_manifests
    if not declared and not local:
        return []
    findings: list[Finding] = []
    seen: set[str] = set()
    for match in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", text, re.MULTILINE):
        top = match.group(1).split(".")[0]
        if top in seen:
            continue
        seen.add(top)
        norm = top.lower().replace("_", "-")
        if top in STD_LIB_NAMES or top in local:
            continue
        aliased = PY_IMPORT_ALIASES.get(top, "").lower()
        if norm in declared or (aliased and aliased in declared):
            continue
        if norm.replace("-", "") in {d.replace("-", "") for d in declared}:
            continue
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "undeclared-import",
                (
                    f"import {top!r} is neither stdlib, local nor declared in a "
                    "manifest - verify it exists (slopsquatting)"
                ),
                2,
                advisory=True,
            )
        )
    return findings[:5]


def _js_undeclared_findings(path: Path, text: str, js_deps: set[str]) -> list[Finding]:
    """npm packages that package.json does not declare (advisory)."""
    findings: list[Finding] = []
    seen_pkgs: set[str] = set()
    for match in re.finditer(
        r"(?:import\s+[^'\"]*?from\s+|import\s+|require\s*\(\s*)[\"']([^\"']+)[\"']", text
    ):
        spec = match.group(1)
        if spec.startswith((".", "/", "node:")):
            continue
        pkg = spec if spec.startswith("@") else spec.split("/")[0]
        if pkg in seen_pkgs or pkg in NODE_BUILTINS or pkg in js_deps:
            continue
        seen_pkgs.add(pkg)
        findings.append(
            _finding(
                path,
                text,
                match.start(),
                "undeclared-import",
                (
                    f"package {pkg!r} is not declared in package.json - verify it "
                    "exists (slopsquatting)"
                ),
                2,
                advisory=True,
            )
        )
    return findings[:5]


def check_undeclared_imports(
    path: Path,
    text: str,
    family: str,
    rel: str,
    root: Path,
    py_manifests: tuple[set[str], set[str]],
    js_deps: set[str],
) -> list[Finding]:
    """Imports that no manifest declares (slopsquatting guard, advisory)."""
    if family == "py":
        return _py_undeclared_findings(path, text, py_manifests)
    if family == "js" and js_deps:
        return _js_undeclared_findings(path, text, js_deps)
    return []


def check_ui_slop(path: Path, text: str, ext: str) -> list[Finding]:
    if ext not in UI_EXTS and ext not in {
        ".jsx",
        ".tsx",
        ".vue",
        ".svelte",
        ".astro",
        ".html",
        ".htm",
    }:
        return []
    findings: list[Finding] = []
    if UI_GRADIENT.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-ai-gradient",
                message="AI-default purple/pink gradient",
                weight=2,
                advisory=True,
            )
        )
    if UI_GRADIENT_TEXT.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-gradient-text",
                message="gradient clip-text headline",
                weight=1,
                advisory=True,
            )
        )
    if UI_MARKETING.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-marketing-copy",
                message="marketing buzzwords in UI copy",
                weight=1,
                advisory=True,
            )
        )
    if UI_PLACEHOLDER.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="ui-placeholder-copy",
                message="placeholder copy (lorem / your-name-here)",
                weight=1,
                advisory=True,
            )
        )
    if ext in UI_EXTS:
        emojis = len(EMOJI_PATTERN.findall(text))
        if emojis >= 3:
            findings.append(
                Finding(
                    path=str(path),
                    line=1,
                    check="ui-emoji-decor",
                    message=f"{emojis} emoji used as decoration",
                    weight=1,
                    advisory=True,
                )
            )
    return findings


def check_doc_extras(path: Path, text: str, ext: str) -> list[Finding]:
    if ext not in DOC_EXTS:
        return []
    findings: list[Finding] = []
    if ext in (".md", ".mdx") and MD_EMOJI_HEADING.search(text):
        findings.append(
            Finding(
                path=str(path),
                line=1,
                check="md-emoji-headings",
                message="emoji in markdown headings",
                weight=1,
                advisory=True,
            )
        )
    residue = MD_RESIDUE.search(text)
    if residue:
        findings.append(
            _finding(
                path,
                text,
                residue.start(),
                "md-residue",
                "chatbot residue or placeholder in document",
                2,
                advisory=True,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Prose rhythm (docs) - always advisory
# ---------------------------------------------------------------------------


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
    bangs = len(re.findall(r"!(?![\[(=])", prose))  # skip ![img], != operators
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


def scan_doc_file(path: Path, ext: str, phrases: dict) -> list[Finding]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    findings.extend(
        phrase_findings(path, text, phrases["doc_phrases"], "ai-prose", 1, advisory=True)
    )
    findings.extend(
        phrase_findings(path, text, phrases["humanizer_phrases"], "humanizer", 1, advisory=True)
    )
    findings.extend(prose_rhythm_findings(path, text))
    findings.extend(check_doc_extras(path, text, ext))
    return findings


# ---------------------------------------------------------------------------
# Code file scan
# ---------------------------------------------------------------------------


def scan_code_file(
    path: Path,
    ext: str,
    rel: str,
    phrases: dict,
    py_manifests: tuple[set[str], set[str]],
    js_deps: set[str],
) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="replace")
    family = FAMILY_OF_EXT.get(ext, "")
    findings: list[Finding] = []
    comments = extract_comments(text, family)
    findings.extend(scan_comment_vocabulary(path, comments, phrases))
    findings.extend(check_elided_code(path, text))
    findings.extend(check_placeholder_impl(path, text))
    findings.extend(check_py_ast(path, text, family))
    findings.extend(check_bloat(path, text, family, rel, comments))
    findings.extend(check_exceptions_js_family(path, text, family))
    findings.extend(check_go(path, text, family, rel))
    findings.extend(check_rust(path, text, family))
    findings.extend(check_debug_left(path, text, family, rel))
    findings.extend(check_js_slop(path, text, family, rel))
    findings.extend(check_ui_slop(path, text, ext))
    findings.extend(check_blanket_suppression(path, text))
    findings.extend(check_placeholder_names(path, text))
    findings.extend(check_placeholder_credential(path, text))
    findings.extend(check_phantom_imports(path, text, family))
    findings.extend(
        check_undeclared_imports(path, text, family, rel, path.parent, py_manifests, js_deps)
    )
    todos = len(TODO_PATTERN.findall(text))
    code_lines = max(len([line for line in text.splitlines() if line.strip()]), 1)
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
    return findings


# ---------------------------------------------------------------------------
# Duplication, provenance, test inventory
# ---------------------------------------------------------------------------


def scan_duplicates(files: list[Path]) -> list[Finding]:
    counter: Counter[str] = Counter()
    first_seen: dict[str, tuple[str, int]] = {}
    for path in files:
        if path.suffix.lower() not in CODE_EXTS:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith(("#", "//", "*", "<!--")):
                continue
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


# Provenance markers: traceability hints, not authorship verdicts.
PROVENANCE_MARKERS = (
    (
        re.compile(r"generated\s+(?:by|with|using)\s+(?:github\s+)?copilot", re.IGNORECASE),
        "Copilot generation banner",
    ),
    (
        re.compile(
            r"generated\s+(?:by|with|using)\s+"
            r"(?:claude|anthropic|gpt|openai|chatgpt|cursor|aider|gemini|deepseek)",
            re.IGNORECASE,
        ),
        "LLM generation banner",
    ),
    (
        re.compile(
            r"co-?authored-?by:.*(?:copilot|claude|gpt|openai|anthropic|"
            r"cursor|aider|gemini|deepseek)",
            re.IGNORECASE,
        ),
        "AI co-author trailer",
    ),
    (re.compile(r"\bai[- ]generated\b", re.IGNORECASE), "AI label"),
    (re.compile(r"@\s?generated\b", re.IGNORECASE), "@" + "generated tag"),
    (
        re.compile(
            r"this (?:file|code|module) (?:was |is )?(?:auto[- ])?generated (?:by|with)",
            re.IGNORECASE,
        ),
        "generated banner",
    ),
)
TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}
MIN_SOURCES_FOR_INVENTORY = 8


def provenance_findings(path: Path, text: str) -> list[Finding]:
    for pattern, label in PROVENANCE_MARKERS:
        match = pattern.search(text)
        if match:
            return [
                _finding(
                    path,
                    text,
                    match.start(),
                    "ai-provenance",
                    f"machine-generation marker: {label}",
                    1,
                    advisory=True,
                )
            ]
    return []


def is_test_path(rel_path: str) -> bool:
    norm = PurePosixPath(rel_path.replace("\\", "/"))
    parts = norm.parts
    if any(part.lower() in TEST_DIRS for part in parts[:-1]):
        return True
    name = norm.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.go")
        or name.endswith("test.js")
        or name.endswith("test.ts")
    )


def inventory_findings(
    root: Path,
    files: list[Path],
    globs: list[str],
) -> list[Finding]:
    sources: list[str] = []
    tests = 0
    base = root if root.is_dir() else root.parent
    for path in files:
        if path.suffix.lower() not in CODE_EXTS:
            continue
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs):
            continue
        parts = PurePosixPath(rel).parts
        if any("fixture" in part.lower() for part in parts):
            continue
        if is_test_path(rel):
            tests += 1
        else:
            sources.append(rel)
    if len(sources) >= MIN_SOURCES_FOR_INVENTORY and tests == 0:
        return [
            Finding(
                path=sources[0],
                line=1,
                check="missing-tests",
                message=(
                    f"{len(sources)} source files and no test files in the "
                    "repository (test inventory, advisory)"
                ),
                weight=2,
                advisory=True,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Scoring: purity -> per-file deficit -> conservative project score
# ---------------------------------------------------------------------------


def file_deficit(findings: list[Finding]) -> int:
    blocking_weight = sum(f.weight for f in findings if not f.advisory)
    advisory_weight = sum(f.weight for f in findings if f.advisory)
    purity = math.exp(-0.5 * blocking_weight)
    deficit = 100 * (1 - purity) + 8 * math.log1p(advisory_weight)
    return min(100, round(deficit))


def project_score(deficits: list[int]) -> tuple[int, str, str]:
    """0.6*worst + 0.4*mean aggregation with grade and verdict labels."""
    if not deficits:
        return 0, "A", "CLEAN"
    worst = max(deficits)
    mean = sum(deficits) / len(deficits)
    score = round(min(100.0, 0.6 * worst + 0.4 * mean))
    if score < 10:
        grade = "A"
    elif score < 25:
        grade = "B"
    elif score < 45:
        grade = "C"
    elif score < 65:
        grade = "D"
    else:
        grade = "F"
    if score >= 70:
        verdict = "CRITICAL"
    elif score >= 50:
        verdict = "INFLATED"
    elif score >= 30:
        verdict = "SUSPICIOUS"
    else:
        verdict = "CLEAN"
    return score, grade, verdict


def fingerprint(item: Finding) -> str:
    normalized = re.sub(r"\d+", "#", item.message)
    raw = f"{item.path}|{item.check}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _scan_one_file(
    path: Path,
    suffix: str,
    rel: str,
    phrases: dict,
    py_manifests: tuple[set[str], set[str]],
    js_deps: set[str],
    provenance: bool,
) -> list[Finding]:
    findings: list[Finding] = []
    if suffix in DOC_EXTS:
        findings.extend(scan_doc_file(path, suffix, phrases))
    elif suffix in CODE_EXTS or suffix in UI_EXTS:
        findings.extend(scan_code_file(path, suffix, rel, phrases, py_manifests, js_deps))
    else:
        return findings
    if provenance:
        findings.extend(
            provenance_findings(path, path.read_text(encoding="utf-8", errors="replace"))
        )
    return findings


def scan(
    root: Path,
    phrases: dict,
    extra_globs: list[str] | None = None,
    provenance: bool = True,
) -> list[Finding]:
    files = iter_files(root)
    globs, disabled, scoped = load_ignore_rules(root)
    globs = globs + list(extra_globs or [])
    py_manifests = collect_py_dependencies(root if root.is_dir() else root.parent)
    js_deps = collect_js_dependencies(root if root.is_dir() else root.parent)
    findings: list[Finding] = []
    for path in files:
        try:
            rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
        except ValueError:
            rel = path.as_posix()
        if is_ignored(rel, globs):
            continue
        findings.extend(
            _scan_one_file(
                path,
                path.suffix.lower(),
                rel,
                phrases,
                py_manifests,
                js_deps,
                provenance,
            )
        )
    findings.extend(scan_duplicates(files))
    if provenance:
        findings.extend(inventory_findings(root, files, globs))
    if disabled:
        findings = [f for f in findings if f.check.lower() not in disabled]
    if scoped:
        findings = [f for f in findings if not _scoped_out(f, scoped)]
    return findings


def blocking_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings if not f.advisory)


def advisory_score(findings: list[Finding]) -> int:
    return sum(f.weight for f in findings if f.advisory)


# ---------------------------------------------------------------------------
# Reporting: summary, per-rule table, fix-first brief, SARIF
# ---------------------------------------------------------------------------


def _report_rule_table(rule_counts: Counter) -> list[str]:
    lines = ["| Rule | Findings | Severity |", "|---|---|---|"]
    for check, count in rule_counts.most_common():
        severity = "blocking" if check in BLOCKING_CHECKS else "advisory"
        lines.append(f"| {check} | {count} | {severity} |")
    return lines


def _report_fix_first(blocking: list[Finding]) -> list[str]:
    lines = ["", "### Fix these first", ""]
    priority = sorted(blocking, key=lambda f: -f.weight)[:5]
    if priority:
        for idx, item in enumerate(priority, start=1):
            lines.append(
                f"{idx}. `{item.path}:{item.line}` [{item.check}] {item.message} -> {item.remedy}"
            )
    else:
        lines.append("No blocking findings - nothing to fix first.")
    lines += [""]
    return lines


def _report_blocking_table(blocking: list[Finding]) -> list[str]:
    if not blocking:
        return ["No blocking AI-slop signals above threshold.", ""]
    lines = [
        "### Blocking findings",
        "",
        "| Check | Where | Detail | Weight | Fix |",
        "|---|---|---|---|---|",
    ]
    for item in blocking:
        lines.append(
            f"| {item.check} | `{item.path}:{item.line}` | {item.message} "
            f"| {item.weight} | {item.remedy} |"
        )
    lines.append("")
    return lines


def _report_advisory_table(advisory: list[Finding]) -> list[str]:
    if not advisory:
        return []
    lines = [
        "### Advisory signals (never fail a build)",
        "",
        "| Check | Where | Detail | Weight |",
        "|---|---|---|---|---|",
    ]
    for item in advisory:
        lines.append(
            f"| {item.check} | `{item.path}:{item.line}` | {item.message} | {item.weight} |"
        )
    lines.append("")
    return lines


def _report_top_files(deficits: dict[str, int]) -> list[str]:
    top_files = sorted(deficits.items(), key=lambda kv: -kv[1])[:5]
    if not top_files or top_files[0][1] <= 0:
        return []
    lines = ["### Highest-deficit files", "", "| File | Deficit |", "|---|---|"]
    for name, value in top_files:
        lines.append(f"| `{name}` | {value} |")
    lines.append("")
    return lines


def write_report(
    path: str,
    root: Path,
    findings: list[Finding],
    suppressed: int = 0,
) -> None:
    blocking = [f for f in findings if not f.advisory]
    advisory = [f for f in findings if f.advisory]
    per_file: dict[str, list[Finding]] = {}
    for item in findings:
        per_file.setdefault(item.path, []).append(item)
    deficits = {name: file_deficit(items) for name, items in per_file.items()}
    score, grade, verdict = project_score(list(deficits.values()))
    lines = [
        "",
        f"Scanned `{root}`: {len(blocking)} blocking / {len(advisory)} advisory finding(s).",
        f"Blocking score **{blocking_score(findings)}**, advisory score "
        f"{advisory_score(findings)} (advisory findings never fail a build).",
    ]
    if suppressed:
        lines.append(f"Baseline applied: {suppressed} known finding(s) suppressed.")
    lines += [
        "",
        f"**Slop score {score}/100 - grade {grade} - {verdict}** "
        "(0.6x worst file + 0.4x mean file deficit).",
        "",
    ]
    lines += _report_rule_table(Counter(f.check for f in findings))
    lines += _report_fix_first(blocking)
    lines += _report_blocking_table(blocking)
    lines += _report_advisory_table(advisory)
    lines += _report_top_files(deficits)
    lines.append(
        f"Opt out per path or check via `{IGNORE_FILE}` "
        "(glob lines, or `check:<id>` to disable one rule); "
        "accept existing findings with `--write-baseline` + `--baseline`."
    )
    lines.append("")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


SARIF_SCHEMA = (
    "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"
)


def write_sarif(path: str, findings: list[Finding]) -> None:
    rules = sorted({f.check for f in findings})
    rule_meta = [
        {
            "id": check,
            "shortDescription": {"text": REMEDIES.get(check, check)},
            "defaultConfiguration": {"level": "error" if check in BLOCKING_CHECKS else "warning"},
            "properties": {"security-severity": "8.0" if check in BLOCKING_CHECKS else "3.0"},
        }
        for check in rules
    ]
    results = [
        {
            "ruleId": item.check,
            "level": "error" if not item.advisory else "warning",
            "message": {"text": f"{item.message}. Fix: {item.remedy}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": item.path.replace("\\", "/"),
                        },
                        "region": {"startLine": max(1, item.line)},
                    }
                }
            ],
            "partialFingerprints": {"masterCIContextHash/v1": fingerprint(item)},
        }
        for item in findings
    ]
    sarif = {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "informationUri": "https://github.com/WasewaseX/master-ci",
                        "rules": rule_meta,
                    }
                },
                "originalUriBaseIds": {},
                "results": results,
            }
        ],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(sarif, handle, indent=1)


def annotate(findings: list[Finding]) -> None:
    for item in findings[:MAX_ANNOTATIONS]:
        message = item.message.replace("\n", " ")
        prefix = "[advisory] " if item.advisory else ""
        print(f"::warning file={item.path},line={item.line}::{prefix}[{item.check}] {message}")


def apply_baseline(findings: list[Finding], baseline_path: str) -> tuple[list[Finding], int]:
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return findings, 0
    known = set(data.get("fingerprints", []))
    if not known:
        return findings, 0
    kept = [f for f in findings if fingerprint(f) not in known]
    return kept, len(findings) - len(kept)


def write_baseline(path: str, findings: list[Finding]) -> None:
    data = {"version": 1, "fingerprints": sorted(fingerprint(f) for f in findings)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)


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
        "--sarif",
        default="",
        help="Write SARIF 2.1.0 results to this path (for the Security tab)",
    )
    parser.add_argument(
        "--baseline",
        default="",
        help="Suppress findings recorded in this baseline file",
    )
    parser.add_argument(
        "--write-baseline",
        default="",
        help="Record current findings as the new baseline and exit 0",
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Emit GitHub ::warning annotations on stdout",
    )
    parser.add_argument(
        "--provenance",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Report advisory provenance markers (ai-provenance) and the "
            "repository test inventory (missing-tests); default on"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    phrases = load_phrases()
    findings = scan(args.root, phrases, extra_globs=args.ignore, provenance=args.provenance)
    suppressed = 0
    if args.baseline:
        findings, suppressed = apply_baseline(findings, args.baseline)
    if args.write_baseline:
        write_baseline(args.write_baseline, findings)
        print(
            f"ai-integrity baseline written: {len(findings)} fingerprint(s) "
            f"-> {args.write_baseline}"
        )
        return 0
    score, grade, verdict = project_score(
        [file_deficit([f for f in findings if f.path == p]) for p in {f.path for f in findings}]
    )
    if args.report_file:
        write_report(args.report_file, args.root, findings, suppressed=suppressed)
    if args.sarif:
        write_sarif(args.sarif, findings)
    if args.annotate:
        annotate(findings)
    print(
        f"ai-integrity blocking={blocking_score(findings)} max={args.max_score} "
        f"advisory={advisory_score(findings)} findings={len(findings)} "
        f"score={score}/100 grade={grade} verdict={verdict}"
    )
    return 1 if blocking_score(findings) > args.max_score else 0


if __name__ == "__main__":
    sys.exit(main())
