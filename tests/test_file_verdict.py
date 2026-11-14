"""Contract tests for the context-aware long-file verdict (v2.17, benchmark phase 1).

The size budget alone cannot tell a bloated file from a big single
responsibility. Over the 400-line budget the length verdict consults
the LCOM4 cohesion oracle in slop_lcom4:

- a long file whose classes all hold one connected method group - and
at least one class actually measured - reports `long-but-cohesive`,
never `bloat-long-file`, with the line count and the proof as evidence,
- a long file carrying cohesion debt keeps the ordinary verdict,
- a long file the oracle cannot prove (no classes, stateless classes,
families without a sound graph) keeps the ordinary verdict,
- test paths stay exempt from the length budget entirely,
- the verdict is registered advisory code-health at info pressure and
is never part of the --enforce-bloat escalation.
"""

from __future__ import annotations

from pathlib import Path

from detector_module import detector as mod
from slop_bloat import check_bloat

LINE_BUDGET = 401  # the fixture-style non-blank count every source here clears


def _cohesive_source(methods: int = 80) -> str:
    """A long single-responsibility module: every method books through _book."""
    lines = [
        '"""One till ledger: every operation books through the same entry log."""',
        "",
        "",
        "class StoreLedger:",
        "    def __init__(self):",
        "        self.entries = []",
        "        self.total = 0",
        "",
        "    def _book(self, kind, amount):",
        "        self.entries.append((kind, amount))",
        "        self.total += amount",
        "        return self.total",
        "",
    ]
    for index in range(methods):
        lines += [
            f"    def op_{index}(self, amount):",
            f'        """Book operation {index} against the till."""',
            "        if amount <= 0:",
            f"            raise ValueError('amount {index} must be positive')",
            f"        return self._book('op-{index}', amount)",
            "",
        ]
    return "\n".join(lines)


def _debt_source(rows: int = 110) -> str:
    """A long file whose class splits into two disconnected method groups."""
    lines = [
        '"""A long file with cohesion debt: two disjoint state clusters."""',
        "",
        "",
        "class SplitHub:",
        "    def __init__(self):",
        "        self.balance = 0",
        "        self.outbox = []",
        "",
    ]
    for index in range(rows):
        lines += [
            f"    def debit_{index}(self, amount):",
            "        self.balance += amount",
            "        return self.balance",
            "",
            f"    def post_{index}(self, message):",
            "        self.outbox.append(message)",
            "        return len(self.outbox)",
            "",
        ]
    return "\n".join(lines)


def _stateless_source(rows: int = 140) -> str:
    """A long file whose only class is one stateless staticmethod table."""
    lines = ['"""A long file whose only class holds no instance state."""', "", "", "class Tables:"]
    for index in range(rows):
        lines += [
            "    @staticmethod",
            f"    def entry_{index}(value):",
            f"        return value * {index} + {index}",
            "",
        ]
    return "\n".join(lines)


def _scan(tmp_path: Path, name: str, text: str):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, mod.load_phrases())


def _non_blank(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


# --- the context-aware outcome ----------------------------------------------


def test_cohesive_long_file_gets_the_context_aware_verdict(tmp_path):
    """A proven-cohesive long file reports long-but-cohesive, never the bloat flag."""
    text = _cohesive_source()
    assert _non_blank(text) > LINE_BUDGET
    findings = _scan(tmp_path, "ledger.py", text)
    checks = {finding.check for finding in findings}
    assert "long-but-cohesive" in checks
    assert "bloat-long-file" not in checks
    finding = next(f for f in findings if f.check == "long-but-cohesive")
    assert str(_non_blank(text)) in finding.message  # evidence carries the real count
    assert "non-blank lines" in finding.message
    assert "one connected method group" in finding.message
    assert "StoreLedger" in finding.message


def test_check_bloat_emits_the_verdict_directly(tmp_path):
    """The producing module emits exactly the context-aware verdict for the corpus."""
    text = _cohesive_source()
    path = tmp_path / "ledger.py"
    path.write_text(text, encoding="utf-8")
    findings = check_bloat(path, text, "py", "ledger.py", [])
    assert [finding.check for finding in findings] == ["long-but-cohesive"]


def test_js_cohesive_long_file_gets_the_context_aware_verdict(tmp_path):
    """The scanner-backed JS class view proves cohesion the same way."""
    lines = [
        "class Stack {",
        "    constructor() {",
        "        this.items = []",
        "    }",
        "",
    ]
    for index in range(140):
        lines += [
            f"    push{index}(value) {{",
            "        this.items.push(value)",
            "        return this.items.length",
            "    }",
            "",
        ]
    text = "\n".join(lines) + "}\n"
    assert _non_blank(text) > LINE_BUDGET
    findings = _scan(tmp_path, "stack.js", text)
    checks = {finding.check for finding in findings}
    assert "long-but-cohesive" in checks
    assert "bloat-long-file" not in checks


# --- the ordinary verdict stands --------------------------------------------


def test_debted_long_file_keeps_the_ordinary_verdict(tmp_path):
    """A long file with cohesion debt reports bloat-long-file, not the advisory."""
    text = _debt_source()
    assert _non_blank(text) > LINE_BUDGET
    findings = _scan(tmp_path, "hub.py", text)
    checks = {finding.check for finding in findings}
    assert "bloat-long-file" in checks
    assert "long-but-cohesive" not in checks


def test_classless_long_file_keeps_the_ordinary_verdict(tmp_path):
    """No measurable class, no proof: the size verdict keeps its meaning."""
    text = "\n".join(f"value_{index} = {index}" for index in range(410)) + "\n"
    findings = _scan(tmp_path, "flat.py", text)
    checks = {finding.check for finding in findings}
    assert "bloat-long-file" in checks
    assert "long-but-cohesive" not in checks


def test_stateless_long_file_keeps_the_ordinary_verdict(tmp_path):
    """A stateless class is unmeasurable under LCOM4, so the verdict stands."""
    text = _stateless_source()
    assert _non_blank(text) > LINE_BUDGET
    findings = _scan(tmp_path, "tables.py", text)
    checks = {finding.check for finding in findings}
    assert "bloat-long-file" in checks
    assert "long-but-cohesive" not in checks


def test_long_test_file_stays_exempt_from_both_verdicts(tmp_path):
    """Test paths are exempt from the length budget before cohesion is consulted."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ledger.py").write_text(_cohesive_source(), encoding="utf-8")
    findings = mod.scan(tmp_path, mod.load_phrases())
    checks = {finding.check for finding in findings}
    assert "bloat-long-file" not in checks
    assert "long-but-cohesive" not in checks


# --- registration ------------------------------------------------------------


def test_verdict_registered_advisory_code_health():
    """The context-aware verdict is advisory code-health at info pressure."""
    assert mod.category_of("long-but-cohesive") == "code-health"
    assert "long-but-cohesive" in mod.REMEDIES
    assert mod.check_tier("long-but-cohesive") == 0.25
    registered = mod.Finding(path="x", line=1, check="long-but-cohesive", message="m", weight=1)
    assert registered.advisory
    assert "long-but-cohesive" not in mod.BLOCKING_CHECKS
    assert "long-but-cohesive" not in mod.STRUCTURAL_BUDGET_CHECKS
    # The enforcement mutates module-global state: snapshot and restore
    # so the escalation never leaks into tests that run after this one.
    enforced_snapshot = set(mod.ENFORCED_CHECKS)
    mod.enforce_structural_budget()
    escalated = mod.Finding(path="x", line=1, check="long-but-cohesive", message="m", weight=1)
    assert escalated.advisory, "the cohesion note must never escalate to blocking"
    mod.ENFORCED_CHECKS.clear()
    mod.ENFORCED_CHECKS.update(enforced_snapshot)
