"""Pattern tables, extension maps and text helpers shared by every checker."""

from __future__ import annotations

import re
import sys
from pathlib import Path


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


TEST_DIRS = {"test", "tests", "spec", "specs", "__tests__"}


# ---------------------------------------------------------------------------
# Generic-AI design tells (tenex "8 signs", noqode "design IA generique")
# ---------------------------------------------------------------------------
UI_EYEBROW = re.compile(
    r"""(?:class(?:Name)?\s*=\s*["'{][^"'}]*\b(?:eyebrow|overline|kicker)\b"""
    r"""|\.(?:eyebrow|overline|kicker)\s*[{,])""",
    re.IGNORECASE,
)
UI_ITALIC_HEADLINE = re.compile(r"<h[1-3][^>]*>[^<]*<(?:em|i)\b", re.IGNORECASE)
UI_FAKE_STATS = re.compile(
    r"\b\d[\d,.]*\s*\+?\s*(?:happy\s+)?"
    r"(?:customers|users|teams|businesses|developers|companies|downloads|projects|integrations)\b"
    r"|\btrusted by\b[^.\n]{0,40}\d",
    re.IGNORECASE,
)
UI_GENERIC_TESTIMONIAL = re.compile(
    r"[A-Z][a-z]+,\s+(?:CEO|CTO|COO|CMO|CFO|founder|co-founder"
    r"|marketing director|head of [a-z]{3,25})\b"
)
UI_NOT_JUST = re.compile(r"\bnot (?:just|only)\b[^.!?\n]{1,40},\s*it'?s\b", re.IGNORECASE)
UI_TRIPLE_SLOGAN = re.compile(r"\b[A-Z][a-z]+\. [A-Z][a-z]+\. [A-Z][a-z]+\.")
UI_GLASS = re.compile(r"backdrop-filter:\s*blur|backdrop-blur\b")
UI_DECORATIVE_NUMBERS = re.compile(r"\b0[1-9]\b")
