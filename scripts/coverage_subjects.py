"""Which changed files belong in a coverage denominator.

Test files and non-code files (configs, docs, manifests) are not
coverage subjects for any mainstream tool: jest/c8/vitest and go test
exclude tests by convention, and no tool instruments package.json.
They stay out of the new-code denominator; code files with no report
entry still count as uncovered, so untested new code cannot hide.
"""

from __future__ import annotations

from pathlib import Path

MEASURABLE_EXTS = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".java",
    ".kt",
    ".rs",
    ".cs",
    ".php",
    ".rb",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
}
TEST_PATH_MARKS = (
    "/test/",
    "/tests/",
    "/spec/",
    "/__tests__/",
    "/testdata/",
    "test_",
    "_test.go",
    ".test.",
    ".spec.",
    "_test.",
)


def is_coverage_subject(path: str) -> bool:
    """True when a coverage report could plausibly measure ``path``."""
    if Path(path).suffix.lower() not in MEASURABLE_EXTS:
        return False
    normalized = f"/{path.replace(chr(92), '/')}"
    return not any(mark in normalized for mark in TEST_PATH_MARKS)
