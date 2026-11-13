"""Hotspot risk: churn x complexity x defect history x dependents.

The roadmap's P1 metric layer slice. SonarQube-style hotspot
prioritization: the file where change frequency, complexity, past
defects and the number of dependents multiply is where the next defect
pays the most interest. A complexity number alone says nothing about
what to fix first; this ranks the corpus.

    risk = (churn + 3 * defects) * ccn * (1 + fan_in / 10)

- churn and fix history price the change pressure: every commit is
    evidence the file keeps needing work, and a fix-shaped subject
    (fix, bug, regress, crash, leak, hotfix, patch) is evidence it broke
    before - weighted three times, because a file that already broke is
    where the next break lands.
- complexity multiplies the pressure: a file with zero complexity
    contributes zero risk by construction - churn alone is history, not
    risk, and a flat file cannot hide a defect behind decision points.
- dependents amplify the blast radius: every ten fan-in doubles it - a
    file everything imports turns one defect into everyone's defect.

The change pressure is git history read in the scan root. ANY failure
- git missing, not a repository, a non-zero exit, a timeout - means no
history: churn_map returns two empty maps and the metric stays silent,
never a guessed number. git prints repo-root-relative paths while the
scan root may be a subdirectory, so the log is filtered to the scan
root's --show-prefix and stripped: the keys line up with the rel paths
the densities already scored.

The ranked set is the densities' worst_files, exactly the measured
files with ccn > 0. A file the lexer could not read has no complexity
and therefore no risk - the same degrade-quietly contract as the
ratios.

Advisory, maintainability dimension, one finding per scan, emitted
only when the max risk exceeds the budget - a CLEAN scan stays CLEAN.
The finding anchors at the hottest file and the message carries the
formula terms plus the bounded ranked list as evidence, sorted before
rendering so the report is stable run to run.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from slop_coupling import CouplingFacts
from slop_density import MIN_CORPUS_LINES, DensityFacts
from slop_model import Finding

# A fix-shaped commit prices three plain commits: a file that already
# broke once is where the next break lands.
DEFECT_WEIGHT = 3
# Blast-radius amplifier: every ten dependents double the risk term.
FAN_IN_AMPLIFIER = 10.0
# Calibrated 2026-11-14 against this codebase's own self-scan: the
# hottest measured file scored 352.0 risk (scripts/ai_slop_detector.py,
# 8 commits x ccn 44). Churn drifts with every commit, so the budget
# keeps 3x headroom above the current self max. Advisory, never a gate.
HOTSPOT_RISK_BUDGET = 1200
GIT_TIMEOUT = 30  # seconds; a cold git log must never hang the scan
MAX_HOTSPOTS_RENDER = 4  # hottest files shown before the "+N more" tail

# git's record separator: %x1e in the format string, one block per commit.
SEP = "\x1e"
FIX_SUBJECT = re.compile(r"\b(fix|bug|regress|crash|leak|hotfix|patch)\b", re.IGNORECASE)

# One ranked row: (rel, risk, churn, fixes, ccn, fan_in).
Row = tuple[str, float, int, int, int, int]


@dataclass(frozen=True)
class HotspotFacts:
    """One scan's ranked hotspots, hottest first, ties by path."""

    scored: tuple[Row, ...]  # risk desc, path asc
    files: int  # scored count: measured files with ccn > 0
    max_risk: float  # the hottest file's risk, 0.0 for an empty ranking


def _git_output(root: Path, args: list[str]) -> str | None:
    """One git query's stdout, or None on any failure.

    Swallowing is the contract, not an oversight: git missing, a
    non-repository root, a non-zero exit, a timeout or undecodable
    bytes all mean no history, and a silent metric beats a guessed
    number. They are caught explicitly because every one of them is an
    expected state of an arbitrary scan root, not a bug.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def _parse_blocks(log: str) -> list[tuple[str, tuple[str, ...]]]:
    """Split the log into (subject, repo-relative paths) commit blocks.

    Every block opens with the record separator and the subject on the
    same line; the remaining lines are the touched paths. The split's
    first segment is the empty run-up before the first marker: dropped.
    """
    blocks: list[tuple[str, tuple[str, ...]]] = []
    for segment in log.split(SEP)[1:]:
        lines = segment.splitlines()
        if not lines:
            continue
        paths = tuple(dict.fromkeys(line for line in lines[1:] if line))
        blocks.append((lines[0], paths))
    return blocks


def churn_map(root: Path, days: int = 90) -> tuple[dict[str, int], dict[str, int]]:
    """Commit and fix-commit counts per file under the scan root.

    `churn[rel]` counts the commits touching the file inside the
    window; `defects[rel]` counts the commits whose subject matches a
    fix-shaped word. Paths are kept only under the scan root's prefix
    (empty at a repo root) and stripped of it, so the keys are
    root-relative like the scan's rel paths. ANY git failure yields
    ({}, {}): no history, no churn signal, never a guessed number.
    """
    raw_prefix = _git_output(root, ["rev-parse", "--show-prefix"])
    log = _git_output(
        root,
        ["log", f"--since={days} days", "--name-only", "--pretty=format:%x1e%s"],
    )
    if log is None or raw_prefix is None:
        return {}, {}
    prefix = raw_prefix.strip()  # --show-prefix ends with a newline
    churn: dict[str, int] = {}
    defects: dict[str, int] = {}
    for subject, paths in _parse_blocks(log):
        rels = [path[len(prefix) :] for path in paths if path.startswith(prefix)]
        if not rels:
            continue
        is_fix = bool(FIX_SUBJECT.search(subject))
        for rel in rels:
            churn[rel] = churn.get(rel, 0) + 1
            if is_fix:
                defects[rel] = defects.get(rel, 0) + 1
    return churn, defects


def measure_hotspot(
    facts: DensityFacts,
    coupling: CouplingFacts | None,
    churn: dict[str, int],
    defects: dict[str, int],
) -> HotspotFacts:
    """Risk-rank the measured corpus: pressure x complexity x radius.

    `facts.worst_files` is exactly the measured files with ccn > 0, so
    a file with zero complexity never enters the ranking - churn alone
    is history, not risk. Missing history and missing coupling both
    count as zero. Ties on risk resolve by path, so the order is
    stable run to run.
    """
    scored: list[Row] = []
    for rel, ccn in facts.worst_files:
        fan_in = coupling.fan_in.get(rel, 0) if coupling is not None else 0
        touches = churn.get(rel, 0)
        fixes = defects.get(rel, 0)
        risk = (touches + DEFECT_WEIGHT * fixes) * ccn * (1 + fan_in / FAN_IN_AMPLIFIER)
        scored.append((rel, risk, touches, fixes, ccn, fan_in))
    scored.sort(key=lambda row: (-row[1], row[0]))
    max_risk = scored[0][1] if scored else 0.0
    return HotspotFacts(scored=tuple(scored), files=len(scored), max_risk=max_risk)


def _render_hotspots(scored: tuple[Row, ...]) -> str:
    """The ranked list as 'rel risk', bounded with the +N more tail."""
    ranked = [f"{row[0]} {row[1]:g}" for row in scored]
    head = ", ".join(ranked[:MAX_HOTSPOTS_RENDER])
    if len(ranked) > MAX_HOTSPOTS_RENDER:
        head += f", +{len(ranked) - MAX_HOTSPOTS_RENDER} more"
    return head


def _hotspot_finding(hotspot: HotspotFacts) -> Finding:
    """One finding for the whole scan: hottest file, ranked proof."""
    rel, risk, touches, fixes, ccn, fan_in = hotspot.scored[0]
    message = (
        f"hotspot risk {risk:g} (budget {HOTSPOT_RISK_BUDGET}): "
        f"{rel} - {touches} commits ({fixes} fix commits) "
        f"x complexity {ccn} x {fan_in} dependents; "
        f"hottest: {_render_hotspots(hotspot.scored)}"
    )
    return Finding(
        path=rel,
        line=1,
        check="hotspot-risk",
        message=message,
        weight=2,
        advisory=True,
    )


def check_hotspot_metrics(
    facts: DensityFacts,
    coupling: CouplingFacts | None,
    churn: dict[str, int],
    defects: dict[str, int],
) -> list[Finding]:
    """The hotspot verdict: advisory, one finding, over budget only.

    `facts` and `coupling` come from the pipeline's one measured corpus
    and import graph; `churn` and `defects` come from churn_map over
    the same root, so every term prices the same files.
    """
    if facts.exec_lines < MIN_CORPUS_LINES:
        return []
    hotspot = measure_hotspot(facts, coupling, churn, defects)
    if not hotspot.scored or hotspot.max_risk <= HOTSPOT_RISK_BUDGET:
        return []
    return [_hotspot_finding(hotspot)]
