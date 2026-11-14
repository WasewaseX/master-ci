"""The quality ratchet: a committed baseline no check may regress from.

One JSON file (default ``.quality-baseline.json``) records the floor no
pull request may push a check above. A regression fails (exit 1); paying
debt is celebrated in the delta table; rewriting the floor is a
conscious, reviewable commit (``--write-quality-baseline``). The ratchet
turns one-off thresholds into a monotonic quality trajectory: legacy
debt is frozen, regressions are blocked, improvements compound.

Schema version 2 closes the two holes a count-only floor has. It is
per-project - one count floor per monorepo project per check, derived
with slop_identities.project_of - so one project can no longer hide a
regression behind another project's improvement. And it carries the
identity layer: every tracked finding's stable identity string, so a
pull request that swaps one old finding for a new one under the same
rule reports exactly one resolved and one new identity even though the
per-check total never moved (the roadmap P2 acceptance case), plus a
``resolved_history`` ledger so debt that comes back is named
reintroduced instead of new.

Semantics:
- The ratchet counts every check id the scan reports, advisory or
    blocking, because a committed baseline is an explicit per-check
    contract the repo opted into. The one exception is
    ``low-new-coverage``: it depends on coverage artifacts produced by
    other jobs, not on the tree alone, so freezing it - as a count or
    as an identity - would compare different environments.
- Fail-closed (exit 2, never a silent pass) when the baseline cannot
    be interpreted: missing file, invalid JSON, wrong schema version
    (a version 1 floor predates the identity layer), a non-integer
    count, a malformed identity string, or unknown check ids (the
    catalog changed - re-write the baseline). A floor that cannot be
    read is not a floor.
- Baseline checks the current scan no longer reports count as
    improvements, not errors: debt paid down is debt gone.
- Identity churn (new / resolved / reintroduced) is evidence for a
    reviewer, never a second gate: ``RatchetResult.ok`` is the count
    verdict alone, so the exit-code contract is unchanged.
- The ratchet is independent of the fingerprint baseline
    (``--baseline``): it reads what the scan reports, before
    fingerprint suppression, so accepted debt cannot hide a regression.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from slop_identities import (
    BaselineIdentities,
    IdentityVerdict,
    check_of,
    classify,
    identity_string,
    is_well_formed,
    project_of,
    render as render_identities,
)
from slop_model import REMEDIES, Finding

BASELINE_VERSION = 2

# Findings that depend on the surrounding CI run (other jobs' artifacts)
# rather than on the tree alone: freezing them would compare different
# environments. Everything else in the catalog is a tree property. The
# exclusion covers both dimensions - counts and identities alike.
RATCHET_EXCLUDED = frozenset({"low-new-coverage"})


class RatchetError(Exception):
    """The baseline cannot be interpreted; the caller must fail closed."""


@dataclass(frozen=True)
class Baseline:
    """The loaded floor: count floors plus both identity ledgers, per project."""

    projects: dict[str, dict[str, int]] = field(default_factory=dict)
    identities: dict[str, tuple[str, ...]] = field(default_factory=dict)
    resolved_history: dict[str, tuple[str, ...]] = field(default_factory=dict)


def metrics(findings: list[Finding], root: Path | str = ".") -> dict[str, dict[str, int]]:
    """Per-project, per-check counts, both levels sorted - what the ratchet freezes.

    Project dimension from slop_identities.project_of (roadmap P2).
    """
    grouped: dict[str, Counter[str]] = {}
    for finding in findings:
        if finding.check in RATCHET_EXCLUDED:
            continue
        grouped.setdefault(project_of(finding.path, root), Counter())[finding.check] += 1
    return {project: dict(sorted(counts.items())) for project, counts in sorted(grouped.items())}


@dataclass(frozen=True)
class ScanState:
    """One scan, both ratchet dimensions: the count floor and the identity set."""

    counts: dict[str, dict[str, int]]
    identities: dict[str, tuple[str, ...]]


def scan_state(findings: list[Finding], root: Path | str = ".") -> ScanState:
    """metrics() plus the identity dimension: sorted, deduplicated identity strings."""
    grouped: dict[str, set[str]] = {}
    for finding in findings:
        if finding.check in RATCHET_EXCLUDED:
            continue
        project = project_of(finding.path, root)
        grouped.setdefault(project, set()).add(identity_string(finding, project))
    identities = {project: tuple(sorted(names)) for project, names in sorted(grouped.items())}
    return ScanState(metrics(findings, root), identities)


def _read_baseline_document(path: str | Path) -> dict:
    """Read and JSON-parse a baseline; unreadable or non-object is a RatchetError."""
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
    return data


def load_baseline(path: str | Path) -> Baseline:
    """Read and validate a v2 baseline; any surprise is a RatchetError."""
    location = Path(path)
    data = _read_baseline_document(path)
    found = data.get("version")
    if found != BASELINE_VERSION:
        raise RatchetError(
            f"baseline {location} has schema version {found!r}; this engine speaks "
            f"version {BASELINE_VERSION} (version 1 floors predate the identity "
            "layer) - re-record the floor with --write-quality-baseline"
        )
    projects = data.get("projects")
    if not isinstance(projects, dict):
        raise RatchetError(f"baseline {location} needs a 'projects' object")
    floors: dict[str, dict[str, int]] = {}
    identities: dict[str, tuple[str, ...]] = {}
    histories: dict[str, tuple[str, ...]] = {}
    unknown: set[str] = set()
    for project, block in projects.items():
        floors[project], identities[project], histories[project] = _load_project(
            location, project, block, unknown
        )
    if unknown:
        raise RatchetError(
            f"baseline {location} names checks this engine does not know: "
            f"{', '.join(sorted(unknown))}; the check catalog changed - "
            "re-record the floor with --write-quality-baseline"
        )
    return Baseline(floors, identities, histories)


def _load_project(
    location: Path, project: str, block: dict, unknown: set[str]
) -> tuple[dict[str, int], tuple[str, ...], tuple[str, ...]]:
    """One project's floor and both identity lists, shape-checked and catalog-checked."""
    if not isinstance(block, dict):
        raise RatchetError(
            f"baseline {location} 'projects' must map project names to objects "
            f"(got {project!r}: {block!r})"
        )
    checks = block.get("checks")
    if not isinstance(checks, dict):
        raise RatchetError(f"baseline {location} project {project!r} needs a 'checks' object")
    for check, count in checks.items():
        if not isinstance(check, str) or not isinstance(count, int) or count < 0:
            raise RatchetError(
                f"baseline {location} 'checks' must map check ids to non-negative "
                f"integers (got {check!r}: {count!r})"
            )
        if check not in REMEDIES:
            unknown.add(check)
    owned = _identity_list(location, project, block, "identities", unknown)
    ledger = _identity_list(location, project, block, "resolved_history", unknown)
    return dict(checks), owned, ledger


def _identity_list(
    location: Path, project: str, block: dict, key: str, unknown: set[str]
) -> tuple[str, ...]:
    """One project's identity list, validated like the counts: strings, four fields, known check."""
    entries = block.get(key, [])
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        raise RatchetError(
            f"baseline {location} project {project!r} {key!r} must be a list of strings"
        )
    for entry in entries:
        if not is_well_formed(entry):
            raise RatchetError(
                f"baseline {location} project {project!r} {key!r} holds an identity "
                f"that does not split into the four identity fields: {entry!r}"
            )
        check = check_of(entry)
        if check not in REMEDIES:
            unknown.add(check)
    return tuple(sorted(set(entries)))


def _read_previous_baseline(path: str | Path) -> Baseline | None:
    """The floor's previous incarnation at this path, or None when nothing is readable."""
    try:
        return load_baseline(path)
    except RatchetError:
        return None


def write_baseline(
    path: str | Path, findings: list[Finding], root: Path | str = "."
) -> dict[str, dict[str, int]]:
    """Record the current floors as the v2 baseline; deterministic bytes.

    Two-space indent: the template's own hygiene gate enforces the
    editorconfig (indent_size 2) on consumer trees, and a baseline that
    tripped it would block the very commit that records the floor.

    When the path holds a readable v2 baseline, the ledger carries
    forward: identities the previous floor tracked that this scan no
    longer reports join ``resolved_history`` (previous entries ride
    along), and identities reported again leave it - their return is
    acknowledged into the new floor, ready to be named reintroduced.
    """
    state = scan_state(findings, root)
    previous = _read_previous_baseline(path)
    history: dict[str, tuple[str, ...]] = {}
    known = set(state.identities)
    if previous is not None:
        known |= set(previous.identities) | set(previous.resolved_history)
    for project in sorted(known):
        current = set(state.identities.get(project, ()))
        tracked = set(previous.identities.get(project, ())) if previous else set()
        ledger = set(previous.resolved_history.get(project, ())) if previous else set()
        history[project] = tuple(sorted((ledger | (tracked - current)) - current))
    names = sorted(set(state.counts) | set(state.identities) | set(history))
    payload = {
        "version": BASELINE_VERSION,
        "projects": {
            project: {
                "checks": state.counts.get(project, {}),
                "identities": list(state.identities.get(project, ())),
                "resolved_history": list(history.get(project, ())),
            }
            for project in names
        },
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state.counts


@dataclass(frozen=True)
class RatchetResult:
    """Outcome of comparing a scan against a baseline, both dimensions.

    ``ok`` is the count verdict alone: identity churn never gates.
    """

    regressions: dict[tuple[str, str], int]
    improvements: dict[tuple[str, str], int]
    unchanged: int
    identities: dict[str, IdentityVerdict] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.regressions


def compare(
    current: ScanState | Mapping[str, Mapping[str, int]], baseline: Baseline
) -> RatchetResult:
    """Count verdict for every per-project check floor, identity verdict on top.

    Any per-project per-check increase is a regression; decreases are
    improvements. A check id the baseline does not know is a regression
    the moment it has findings: adopting a catalog that knows more
    checks is a conscious re-baseline, not a silent widening of the
    gate. A plain per-project count mapping gets the count verdict only
    - current identities are unknowable from counts alone, and passing
    a ScanState yields the full four-bucket verdict.
    """
    counts = _as_counts(current)
    regressions: dict[tuple[str, str], int] = {}
    improvements: dict[tuple[str, str], int] = {}
    unchanged = 0
    for project in sorted(set(counts) | set(baseline.projects)):
        now_counts = counts.get(project, {})
        floor_counts = baseline.projects.get(project, {})
        for check in sorted(set(now_counts) | set(floor_counts)):
            now = now_counts.get(check, 0)
            floor = floor_counts.get(check, 0)
            if now > floor:
                regressions[(project, check)] = now - floor
            elif now < floor:
                improvements[(project, check)] = floor - now
            else:
                unchanged += 1
    identities: dict[str, IdentityVerdict] = {}
    if isinstance(current, ScanState):
        named = set(current.identities) | set(baseline.identities)
        named |= set(baseline.resolved_history)
        for project in sorted(named):
            identities[project] = classify(
                current.identities.get(project, ()),
                BaselineIdentities(
                    tracked=frozenset(baseline.identities.get(project, ())),
                    resolved_history=frozenset(baseline.resolved_history.get(project, ())),
                ),
            )
    return RatchetResult(regressions, improvements, unchanged, identities)


MAX_DELTA_ROWS = 20


def render(
    current: ScanState | Mapping[str, Mapping[str, int]],
    baseline: Baseline,
    result: RatchetResult,
) -> list[str]:
    """Markdown delta tables for the step summary: count floors first, identities second.

    Regression rows lead the count table, improvements follow, and the
    table caps at MAX_DELTA_ROWS. The identity table prints whenever the
    comparison carried identities; a counts-only comparison against a
    baseline with identities says so instead of staying silent, so a
    wiring gap is visible in the summary rather than invisible.
    """
    counts = _as_counts(current)
    pairs = {
        (project, check)
        for source in (counts, baseline.projects)
        for project, checks in source.items()
        for check in checks
    }
    projects = {project for project, _ in pairs}
    verdict = "regressions blocked" if result.regressions else "no regressions"
    lines = [
        f"quality ratchet: {len(pairs)} per-project check(s) tracked across "
        f"{len(projects)} project(s), {len(result.regressions)} regression(s), "
        f"{len(result.improvements)} improvement(s) - {verdict}",
        "",
        "| Project | Check | Baseline | Now | Delta |",
        "|---|---|---|---|",
    ]
    rows = _delta_rows(result.regressions, "+", counts, baseline)
    rows += _delta_rows(result.improvements, "-", counts, baseline)
    for name, floor, now, delta in rows[:MAX_DELTA_ROWS]:
        lines.append(f"| {name} | {floor} | {now} | {delta} |")
    if len(rows) > MAX_DELTA_ROWS:
        lines.append(f"| ... | | | {len(rows) - MAX_DELTA_ROWS} more |")
    identity_lines = render_identities(result.identities)
    if identity_lines:
        lines += identity_lines
    elif not result.identities and any(baseline.identities.values()):
        lines += ["", "identity ledger: not evaluated - the comparison carried counts only"]
    return lines


def _as_counts(
    current: ScanState | Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    """The count dimension of either current-side shape, copied so locals can mutate."""
    if isinstance(current, ScanState):
        return {project: dict(checks) for project, checks in current.counts.items()}
    return {project: dict(checks) for project, checks in current.items()}


def _delta_rows(
    deltas: dict[tuple[str, str], int],
    sign: str,
    counts: dict[str, dict[str, int]],
    baseline: Baseline,
) -> list[tuple[str, int, int, str]]:
    """Count-table rows for one verdict bucket; regressions and improvements alike."""
    rows: list[tuple[str, int, int, str]] = []
    for (project, check), delta in sorted(deltas.items()):
        floor_counts = baseline.projects.get(project, {})
        now_counts = counts.get(project, {})
        rows.append(
            (
                f"{project} | `{check}`",
                floor_counts.get(check, 0),
                now_counts.get(check, 0),
                f"{sign}{delta}",
            )
        )
    return rows
