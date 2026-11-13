#!/usr/bin/env python3
"""The quality ratchet: a committed baseline no check may regress from.

One JSON file (default ``.quality-baseline.json``) records, per check id,
how many findings the scan reports today. A pull request that pushes any
check above its recorded count fails (exit 1); fixing debt is celebrated
in the delta table; rewriting the floor is a conscious, reviewable commit
(``--write-quality-baseline``). The ratchet turns one-off thresholds into
a monotonic quality trajectory: legacy debt is frozen, regressions are
blocked, improvements compound.

Semantics:
- The ratchet counts every check id the scan reports, advisory or
    blocking, because a committed baseline is an explicit per-check
    contract the repo opted into (the same reasoning that lets
    ``--enforce-bloat`` escalate the structural family). The one exception
    is ``low-new-coverage``: it depends on coverage artifacts produced by
    other jobs, not on the tree alone, so freezing it would compare
    different environments.
- Fail-closed (exit 2, never a silent pass) when the baseline cannot be
    interpreted: missing file, invalid JSON, wrong schema version, a
    non-integer count, or check ids this engine's catalog does not know
    (the catalog changed - re-write the baseline). A floor that cannot be
    read is not a floor.
- Baseline check ids the current scan no longer reports count as
    improvements, not errors: debt paid down is debt gone.
- The ratchet is independent of the fingerprint baseline (``--baseline``):
    it counts what the scan reports, before fingerprint suppression, so
    accepted debt cannot hide a regression.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from slop_model import REMEDIES

BASELINE_VERSION = 1

# Findings that depend on the surrounding CI run (other jobs' artifacts)
# rather than on the tree alone: freezing them would compare different
# environments. Everything else in the catalog is a tree property.
RATCHET_EXCLUDED = frozenset({"low-new-coverage"})


class RatchetError(Exception):
    """The baseline cannot be interpreted; the caller must fail closed."""


def metrics(findings: list) -> dict[str, int]:
    """Per-check finding counts, sorted. This is what the ratchet freezes."""
    counts: Counter = Counter(f.check for f in findings if f.check not in RATCHET_EXCLUDED)
    return dict(sorted(counts.items()))


@dataclass(frozen=True)
class RatchetResult:
    """Outcome of comparing a scan against a baseline."""

    regressions: dict[str, int]
    improvements: dict[str, int]
    unchanged: int

    @property
    def ok(self) -> bool:
        return not self.regressions


def load_baseline(path: str | Path) -> dict[str, int]:
    """Read and validate a baseline; any surprise is a RatchetError."""
    location = Path(path)
    try:
        raw = location.read_text(encoding="utf-8")
    except OSError as exc:
        raise RatchetError(f"baseline {location} cannot be read: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RatchetError(f"baseline {location} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RatchetError(f"baseline {location} must be a JSON object")
    if data.get("version") != BASELINE_VERSION:
        raise RatchetError(
            f"baseline {location} has schema version {data.get('version')!r}, "
            f"this engine speaks {BASELINE_VERSION}; "
            "re-record the floor with --write-quality-baseline"
        )
    checks = data.get("checks")
    if not isinstance(checks, dict):
        raise RatchetError(f"baseline {location} needs a 'checks' object")
    for check, count in checks.items():
        if not isinstance(check, str) or not isinstance(count, int) or count < 0:
            raise RatchetError(
                f"baseline {location} 'checks' must map check ids to "
                f"non-negative integers (got {check!r}: {count!r})"
            )
    unknown = sorted(check for check in checks if check not in REMEDIES)
    if unknown:
        raise RatchetError(
            f"baseline {location} names checks this engine does not know: "
            f"{', '.join(unknown)}; the check catalog changed - "
            "re-record the floor with --write-quality-baseline"
        )
    return dict(checks)


def write_baseline(path: str | Path, findings: list) -> dict[str, int]:
    """Record the current per-check counts as the floor; deterministic bytes.

    Two-space indent: the template's own hygiene gate enforces the
    editorconfig (indent_size 2) on consumer trees, and a baseline that
    tripped it would block the very commit that records the floor.
    """
    counts = metrics(findings)
    payload = {"version": BASELINE_VERSION, "checks": counts}
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return counts


def compare(current: dict[str, int], baseline: dict[str, int]) -> RatchetResult:
    """Any per-check increase is a regression; decreases are improvements.

    A check id the baseline does not know is a regression the moment it
    has findings: adopting a catalog that knows more checks is a conscious
    re-baseline, not a silent widening of the gate.
    """
    regressions: dict[str, int] = {}
    improvements: dict[str, int] = {}
    unchanged = 0
    for check in sorted(set(current) | set(baseline)):
        now = current.get(check, 0)
        floor = baseline.get(check, 0)
        if now > floor:
            regressions[check] = now - floor
        elif now < floor:
            improvements[check] = floor - now
        else:
            unchanged += 1
    return RatchetResult(regressions, improvements, unchanged)


MAX_DELTA_ROWS = 20


def render(current: dict[str, int], baseline: dict[str, int], result: RatchetResult) -> list[str]:
    """A markdown delta table for the step summary, regressions first."""
    tracked = len(set(current) | set(baseline))
    verdict = "regressions blocked" if result.regressions else "no regressions"
    lines = [
        f"quality ratchet: {tracked} check(s) tracked, "
        f"{len(result.regressions)} regression(s), "
        f"{len(result.improvements)} improvement(s) - {verdict}",
        "",
        "| Check | Baseline | Now | Delta |",
        "|---|---|---|---|",
    ]
    regression_rows = [
        (f"`{check}`", baseline.get(check, 0), current.get(check, 0), f"+{delta}")
        for check, delta in sorted(result.regressions.items())
    ]
    improvement_rows = [
        (f"`{check}`", baseline.get(check, 0), current.get(check, 0), f"-{delta}")
        for check, delta in sorted(result.improvements.items())
    ]
    rows = regression_rows + improvement_rows
    for name, floor, now, delta in rows[:MAX_DELTA_ROWS]:
        lines.append(f"| {name} | {floor} | {now} | {delta} |")
    if len(rows) > MAX_DELTA_ROWS:
        lines.append(f"| ... | | | {len(rows) - MAX_DELTA_ROWS} more |")
    return lines
