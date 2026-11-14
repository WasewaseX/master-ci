"""Stable finding identities: what the quality ratchet remembers between scans.

A per-check count cannot tell a maintained floor from a churned one:
removing one old finding while introducing a new one under the same rule
leaves every count unchanged, and the count ratchet stays silent. This
module gives each finding a stable identity so the ratchet can report
new / resolved / unchanged / reintroduced (roadmap P2, "finding
fingerprints"). Three decisions live here, each with its reasoning:

- Identity = (project, check, path, fingerprint), lines deliberately
  EXCLUDED: a line drifts with unrelated edits, and a drifted line must
  never masquerade as a resolved-plus-new pair. The fingerprint half is
  slop_reporting's ``fingerprint`` - the one derivation the suppression
  baseline already uses (path, check, message with digits normalized to
  '#'), reused so the ratchet and the suppression baseline can never
  disagree about what "the same finding" means.

- The project dimension (``project_of``): the first path segment under
  the scan root when that segment directly holds a family manifest
  (``discover_projects.FAMILY_MANIFESTS``) or a dotnet project file,
  else the root project ``.``. Only the first segment is examined, on
  purpose: a project nested deeper (``services/api/package.json``)
  attributes to the root - over-grouping can only merge two projects'
  churn into one delta row, while deeper matching could split one
  project's findings across two identities and invent churn from a
  folder rename. Ignored directories (``discover_projects.SKIP_DIRS``,
  dot-prefixed segments, absolute paths) are root-attributed: discovery
  never enumerates them as projects, so no identity may claim them.

- Reintroduction, one of the roadmap's allowed shapes, chosen and kept
  testable: an identity present in the current scan AND recorded in the
  baseline's per-project ``resolved_history`` ledger - the baseline's
  witness that this identity existed under a previous floor and was
  resolved since (``slop_ratchet.write_baseline`` carries resolved
  identities forward when re-recording, and releases them once reported
  again). One committed baseline against one scan never sees the
  intermediate state, so the ledger stands in for the roadmap's "absent
  in the immediately following state" witness. Tracked membership wins
  over a stale ledger entry; reintroduction is reported, never gating -
  the count verdict stays the exit contract.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from discover_projects import DOTNET_GLOBS, FAMILY_MANIFESTS, SKIP_DIRS
from slop_model import Finding
from slop_reporting import fingerprint as reporting_fingerprint

# The monorepo root itself, when no project segment applies.
ROOT_PROJECT = "."

# project | check | path | fingerprint
IDENTITY_FIELDS = 4

# Every manifest that marks an independent project root, flattened from
# the per-family table: project_of is family-agnostic on purpose, so a
# polyglot monorepo needs no per-family routing here.
PROJECT_MANIFESTS = frozenset(name for manifests in FAMILY_MANIFESTS.values() for name in manifests)

Identity = tuple[str, str, str, str]


def stable_fingerprint(finding: Finding) -> str:
    """The suppression baseline's fingerprint, verbatim - one formula, zero drift."""
    return reporting_fingerprint(finding)


def identity(finding: Finding, project: str) -> Identity:
    """(project, check, path, fingerprint) - the line is excluded on purpose (see above)."""
    return (project, finding.check, finding.path, stable_fingerprint(finding))


def identity_string(finding: Finding, project: str) -> str:
    """The identity as one deterministic string - the unit the baseline stores."""
    return "|".join(identity(finding, project))


def is_well_formed(serialized: str) -> bool:
    """True when the string still splits into four fields; a floor, not an exact shape."""
    return len(serialized.split("|")) >= IDENTITY_FIELDS


def check_of(serialized: str) -> str:
    """The check id inside a serialized identity - the delta table's grouping key.

    Splitting from the left stays correct even for a pathological path
    containing the separator: project and check come first.
    """
    return serialized.split("|", 3)[1]


def project_of(path: str, root: Path | str = ".") -> str:
    """The monorepo project dimension; rule and false-positive direction: module docstring."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) < 2 or path.startswith("/"):
        return ROOT_PROJECT
    segment = parts[0]
    if segment in SKIP_DIRS or segment.startswith("."):
        return ROOT_PROJECT
    directory = Path(root) / segment
    try:
        entries = os.listdir(directory)
    except OSError:
        return ROOT_PROJECT
    if any((directory / name).is_file() for name in PROJECT_MANIFESTS.intersection(entries)):
        return segment
    if any(fnmatch.filter(entries, pattern) for pattern in DOTNET_GLOBS):
        return segment
    return ROOT_PROJECT


@dataclass(frozen=True)
class BaselineIdentities:
    """One project's identity record: ``tracked`` owns the floor, the ledger remembers."""

    tracked: frozenset[str] = frozenset()
    resolved_history: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IdentityVerdict:
    """One project's identity comparison: four disjoint buckets.

    The scan's identities partition into new, reintroduced and unchanged;
    tracked identities absent from the scan are resolved. A ledger entry
    still absent stays silent - it was resolved before this baseline.
    """

    new: frozenset[str] = frozenset()
    resolved: frozenset[str] = frozenset()
    unchanged: frozenset[str] = frozenset()
    reintroduced: frozenset[str] = frozenset()


def classify(
    current_identities: Iterable[str], baseline_identities: BaselineIdentities
) -> IdentityVerdict:
    """Sort one project's current identities into the four buckets.

    Precedence: tracked membership owns an identity first (unchanged,
    even against a stale ledger entry); a ledger identity the tracked
    set no longer holds is reintroduced when it shows up again; whatever
    is left over is new; tracked identities absent now are resolved.
    """
    now = frozenset(current_identities)
    tracked = baseline_identities.tracked
    ledger = baseline_identities.resolved_history
    reintroduced = (now & ledger) - tracked
    new = (now - tracked) - reintroduced
    resolved = tracked - now
    unchanged = now & tracked
    return IdentityVerdict(
        new=new, resolved=resolved, unchanged=unchanged, reintroduced=reintroduced
    )


# The same table discipline as the count delta table in slop_ratchet.
IDENTITY_TABLE_CAP = 20


def render(verdicts: dict[str, IdentityVerdict]) -> list[str]:
    """Markdown delta table: one row per (project, check) with identity counts.

    Columns are Project | Check | New | Resolved | Unchanged |
    Reintroduced. Rows sort by activity first (new + resolved +
    reintroduced), then by project and check: the churn a reviewer must
    read floats above the cap, a large stable floor sinks below it.
    """
    cells: dict[tuple[str, str], list[int]] = {}
    for project, verdict in verdicts.items():
        buckets = (verdict.new, verdict.resolved, verdict.unchanged, verdict.reintroduced)
        for column, bucket in enumerate(buckets):
            for serialized in bucket:
                row = cells.setdefault((project, check_of(serialized)), [0, 0, 0, 0])
                row[column] += 1
    if not cells:
        return []
    ordered = sorted(
        cells.items(), key=lambda entry: (-(entry[1][0] + entry[1][1] + entry[1][3]), entry[0])
    )
    lines = [
        "",
        "### Finding identities",
        "",
        "| Project | Check | New | Resolved | Unchanged | Reintroduced |",
        "|---|---|---|---|---|---|",
    ]
    for (project, check), row in ordered[:IDENTITY_TABLE_CAP]:
        lines.append(f"| {project} | `{check}` | {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    if len(ordered) > IDENTITY_TABLE_CAP:
        lines.append(f"| ... | | | | | {len(ordered) - IDENTITY_TABLE_CAP} more |")
    return lines
