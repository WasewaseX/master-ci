#!/usr/bin/env python3
"""Parse per-iteration test-runner output into one uniform outcome model.

The flake hunt runs a repository's own test suite several times and looks
for tests whose verdict changes between runs. Runners already emit result
files in four de-facto standard shapes, so the hunt consumes those instead
of asking any project to change how it tests:

- JUnit XML - pytest ``--junitxml``, phpunit ``--log-junit``, surefire,
    ctest ``--output-junit``, vitest ``--reporter=junit``, RSpec's JUnit
    formatter
- TRX - ``dotnet test --logger trx``
- Go test JSON - ``go test -json``
- libtest text - the Rust test harness stdout (``cargo test``)

Parsers are pure: text in, tuples out, no I/O beyond what the caller read,
and every parse failure raises :class:`FlakyParseError`. A result file the
analyzer cannot read is a fail-closed condition (the hunt exits 2), never
a silent zero-test pass.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"

SUFFIXES = (".xml", ".trx", ".gojson", ".libtest")

# Go emits one JSON object per line; only verdict actions on a named test
# are outcomes. Package-level pass/fail (no "Test" field) and the run,
# pause, cont and output stream actions are noise here.
_GO_VERDICTS = {"pass": PASSED, "fail": FAILED, "skip": SKIPPED}

# libtest prints one line per test: `test <name> ... ok|FAILED|ignored`
# (ok may carry a duration). The trailing `test result:` summary line is
# excluded by the shape, not by a denylist.
_LIBTEST_RE = re.compile(r"^test\s+(.+?)\s+\.\.\.\s+(ok|FAILED|ignored)(?:\s|$)")

# TRX fixes one namespace; UnitTestResult@outcome uses the .NET spelling.
_TRX_NS = "{http://microsoft.com/schemas/VisualStudio/TeamTest/2010}"
_TRX_OUTCOMES = {"Passed": PASSED, "Failed": FAILED, "NotExecuted": SKIPPED}


class FlakyParseError(Exception):
    """A result file cannot be interpreted; the caller must fail closed."""


@dataclass(frozen=True)
class Outcome:
    """One test's verdict in one iteration."""

    suite: str
    name: str
    outcome: str

    @property
    def key(self) -> str:
        """Stable identity across iterations and formats."""
        return f"{self.suite}::{self.name}" if self.suite else self.name


def parse_file(path: Path) -> list[Outcome]:
    """Dispatch on the file suffix; unknown shapes fail closed."""
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8", errors="strict")
    if suffix == ".xml":
        return parse_junit(text)
    if suffix == ".trx":
        return parse_trx(text)
    if suffix == ".gojson":
        return parse_go_json(text)
    if suffix == ".libtest":
        return parse_libtest(text)
    raise FlakyParseError(
        f"{path.name}: unknown result format '{suffix}' - expected one of {', '.join(SUFFIXES)}"
    )


def parse_junit(text: str) -> list[Outcome]:
    """JUnit XML: testcase@name, classname falling back to the suite name."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FlakyParseError(f"malformed JUnit XML: {exc}") from exc
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    if root.tag == "testsuite":
        suites = [root]
    outcomes: list[Outcome] = []
    for suite in suites:
        suite_name = suite.get("name") or ""
        for case in suite.iter("testcase"):
            name = case.get("name") or ""
            if not name:
                raise FlakyParseError("JUnit testcase without a name")
            suite_of_case = case.get("classname") or suite_name
            outcomes.append(Outcome(suite_of_case, name, _junit_verdict(case)))
    return outcomes


def _junit_verdict(case: ET.Element) -> str:
    if case.find("skipped") is not None:
        return SKIPPED
    if case.find("failure") is not None or case.find("error") is not None:
        return FAILED
    return PASSED


def parse_trx(text: str) -> list[Outcome]:
    """TRX: UnitTestResult@outcome in the fixed 2010 namespace."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FlakyParseError(f"malformed TRX: {exc}") from exc
    outcomes: list[Outcome] = []
    for result in root.iter(f"{_TRX_NS}UnitTestResult"):
        name = result.get("testName") or ""
        outcome = _TRX_OUTCOMES.get(result.get("outcome") or "")
        if not name or outcome is None:
            raise FlakyParseError(
                f"TRX UnitTestResult without testName or with unknown "
                f"outcome '{result.get('outcome')}'"
            )
        outcomes.append(Outcome("", name, outcome))
    return outcomes


def parse_go_json(text: str) -> list[Outcome]:
    """Go test JSON: verdict events carry a Test name and an Action."""
    outcomes: list[Outcome] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FlakyParseError(f"malformed go test JSON on line {lineno}: {exc}") from exc
        if not isinstance(event, dict):
            raise FlakyParseError(f"go test JSON line {lineno} is not an object")
        outcome = _GO_VERDICTS.get(event.get("Action") or "")
        test = event.get("Test") or ""
        if outcome is None or not test:
            continue
        outcomes.append(Outcome(event.get("Package") or "", test, outcome))
    return outcomes


def parse_libtest(text: str) -> list[Outcome]:
    """libtest stdout: one `test <name> ... <verdict>` line per test."""
    outcomes: list[Outcome] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _LIBTEST_RE.match(line)
        if not match:
            continue
        name, verdict = match.group(1), match.group(2)
        outcome = {"ok": PASSED, "FAILED": FAILED, "ignored": SKIPPED}[verdict]
        outcomes.append(Outcome("", name, outcome))
    if not outcomes and text.strip():
        raise FlakyParseError(
            f"libtest output parsed to zero tests - it does not look like "
            f"cargo test output (first line: {text.splitlines()[0][:80]!r})"
        )
    return outcomes
