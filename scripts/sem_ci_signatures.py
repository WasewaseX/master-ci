"""The CI diagnosis signature table: what this repository's gates emit.

The matching half of ``sem_ci_diagnose`` (B34), split so the diagnoser
stays under the hygiene ceiling - the detector's structural law applies
to its own source first. This module owns the data and the pure matchers:
the signature table over the repository's own gates (ruff check, ruff
format, mypy, pytest, yamllint, markdown-lint, the file-too-large
hygiene ceiling, the quality ratchet and the pin-lag watchdog), the
step-name matchers (the step name is authoritative), the generic failure
lines that read as failures without naming a tool, and the Actions log
wrapper stripper. Every remediation maps to the exact text documented in
docs/checks.md; the table grows with the gates - an unknown tool is not
a wrong answer, it is an honest one.
"""

from __future__ import annotations

import re

# Lines that read as a failure even though no signature names them:
# preserved as unclassified evidence, while progress lines stay silence.
GENERIC_FAILURES: tuple[re.Pattern[str], ...] = (
    re.compile(r"##\[error\]"),
    re.compile(r"\bfatal:\s"),
    re.compile(r"^Error:\s"),
    re.compile(r"Process completed with exit code (?!0\b)\d+"),
)

# The runner timestamp prefix on downloaded Actions log lines.
RUNNER_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+")

# Linter kinds whose tool-name lines are not evidence by themselves.
TOOL_NAME_KINDS = frozenset({"markdown-lint", "yamllint"})
FAIL_WORD = re.compile(r"error|failed|warn", re.IGNORECASE)

SIGNATURES: tuple[tuple[str, tuple[re.Pattern[str], ...], str], ...] = (
    (
        "ruff-format",
        (re.compile(r"would reformat", re.IGNORECASE),),
        "run `ruff format .` locally and commit the reformatted files",
    ),
    (
        "ruff",
        (
            re.compile(r"(?:^|: ?)[EFW]\d{3} "),
            re.compile(r"ruff check failed", re.IGNORECASE),
        ),
        "run `ruff check .` locally, fix the named violations",
    ),
    (
        "mypy",
        (
            re.compile(
                r"error: (?:Cannot find|Incompatible|Missing|Function|Argument)", re.IGNORECASE
            ),
            re.compile(r"Found \d+ error", re.IGNORECASE),
        ),
        "run `mypy --ignore-missing-imports .` locally and fix the named errors",
    ),
    (
        "pytest",
        (re.compile(r"^(?:FAILED|ERROR) tests/"),),
        "run the failing test locally; a green suite never excuses a red one",
    ),
    (
        "file-too-large",
        (re.compile(r"file-too-large|file too large", re.IGNORECASE),),
        "split the file below the 400-line ceiling or document the suppression in docs/checks.md",
    ),
    (
        "quality-ratchet",
        (re.compile(r"quality ratchet|baseline (?:drift|regression)", re.IGNORECASE),),
        "fix the new findings or re-record the baseline deliberately; never lower the floor silently",
    ),
    (
        "pin-lag",
        (re.compile(r"pin lag|pin-lag", re.IGNORECASE),),
        "see the pin-lag watchdog section in docs/checks.md",
    ),
    (
        "yamllint",
        (re.compile(r"yaml\s*lint|lint\s*yaml", re.IGNORECASE),),
        "run `yamllint .` locally and fix the named lines",
    ),
    (
        "markdown-lint",
        (re.compile(r"markdown-lint|markdownlint", re.IGNORECASE),),
        "run the markdown linter locally and fix the named files",
    ),
)

STEP_NAME_MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ruff-format", re.compile(r"ruff\s*format", re.IGNORECASE)),
    ("ruff", re.compile(r"ruff\s*(?:check|lint)", re.IGNORECASE)),
    ("mypy", re.compile(r"mypy", re.IGNORECASE)),
    ("pytest", re.compile(r"pytest|python tests|unit tests", re.IGNORECASE)),
    ("file-too-large", re.compile(r"file[- ]too[- ]large|hygiene", re.IGNORECASE)),
    ("yamllint", re.compile(r"yaml\s*lint|lint\s*yaml", re.IGNORECASE)),
    ("markdown-lint", re.compile(r"markdown", re.IGNORECASE)),
    ("quality-ratchet", re.compile(r"ratchet", re.IGNORECASE)),
    ("pin-lag", re.compile(r"pin\s*lag", re.IGNORECASE)),
)


def classify_text(text: str) -> tuple[str, re.Pattern[str]] | None:
    """The first signature whose pattern matches the text."""
    for kind, patterns, _ in SIGNATURES:
        for pattern in patterns:
            if pattern.search(text):
                return kind, pattern
    return None


def classify_step(step_name: str) -> str | None:
    """The step name is authoritative: exact confidence on a match."""
    for kind, matcher in STEP_NAME_MATCHERS:
        if matcher.search(step_name):
            return kind
    return None


def remediation(kind: str) -> str:
    for entry_kind, _, remediation_text in SIGNATURES:
        if entry_kind == kind:
            return remediation_text
    return ""


def strip_actions_prefix(line: str) -> str:
    """Strip the Actions log wrapper: gh's job/step tabs, then the timestamp.

    Downloaded Actions logs read ``2026-11-20T10:00:00Z FAILED tests/...``
    and ``gh run view --log`` prepends ``job<TAB>step<TAB>`` before the
    timestamp; signatures match the message, not the wrapper. The tab
    segment is taken only when its tail carries a runner timestamp, so a
    tab inside real output cannot swallow the message.
    """
    core = line
    if "\t" in core:
        tail = core.rsplit("\t", 1)[1]
        if RUNNER_TIMESTAMP.match(tail):
            core = tail
    return RUNNER_TIMESTAMP.sub("", core, count=1).strip()


def looks_like_failure(core: str) -> bool:
    """True for lines that clearly failed without naming a known tool."""
    return any(pattern.search(core) for pattern in GENERIC_FAILURES)
