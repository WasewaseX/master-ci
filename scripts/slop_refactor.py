#!/usr/bin/env python3
"""Refactoring proposals: debt-priced, reviewable drafts from scan findings.

The quarantine-PR pattern generalized to quality debt. The flake hunt
proposes a quarantine PR and the fix engine opens one PR per fix sweep:
both automation loops land as reviewable artifacts a human merges or
closes. Quality debt had no such loop - the scan prices findings, the
report renders them, and the remediation stays a conversation with no
artifact to review. This module closes that gap: one markdown draft per
finding, priced by the debt engine, remedied by the shared registry.

The v2.16.0 lesson, kept: the snapshots job demanded ``contents:write``
its callers did not grant, and failed in CI. Emission here writes local
files only, inside the one directory the caller names - no push, no PR,
no permissions beyond what writing the caller's own artifacts needs.
A workflow that wants a reviewable PR wraps these drafts the way the
autofix job wraps its manifest; the drafts never touch git themselves.

Contract:
- one markdown file per finding at ``<out>/<check>/<slug-of-rel-path>.md``
    (two findings sharing a check and file disambiguate by line), plus
    ``<out>/index.md`` - one row per draft, ordered by (check, path) -
    and ``<out>/bundle.json``, schema-stamped, one record per finding:
    identity fields, minutes, the remedy id (the check id is the remedy
    registry key) and the written file path, relative to ``out``.
- sourced verbatim, never recomputed: identity and message from the
    Finding, remedy text from the slop_remedies registry, minutes from
    slop_debt's pricing. A check id outside the shared registry has no
    remedy to render and no registry class behind a price - hard error,
    fail closed, no invented minutes.
- deterministic: sorted emission order, slugged file names, no
    timestamps anywhere - two runs over the same findings write
    byte-identical files.
- idempotent regeneration: an existing ``out`` directory must be empty
    or carry the bundle's own ``.slop-proposals`` marker (a schema
    version); anything else is refused with a clear error. Regeneration
    removes exactly the files the previous bundle recorded, never a
    file the bundle did not generate.
- zero findings: an empty index, a bundle with zero proposals, success
    - a healthy scan proposes nothing, which is the normal case here.

Nothing is auto-applied. Every draft opens with that header, and its
"verify after fix" section carries the engine command whose ceiling
flags gate the finding's class - the proof step for the human review.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from slop_categories import category_of
from slop_debt import DESIGN_CHECKS, _render_minutes, remediation_minutes
from slop_model import STRUCTURAL_BUDGET_CHECKS, Finding, is_blocking
from slop_remedies import REMEDIES

SCHEMA_VERSION = 1
MARKER_NAME = ".slop-proposals"
INDEX_NAME = "index.md"
BUNDLE_NAME = "bundle.json"
DRAFT_HEADER = "Draft proposal - for human review. Nothing is auto-applied."


class ProposalError(Exception):
    """Emission refused: a guard or the pricing gate tripped - maps to exit 2."""


@dataclass(frozen=True)
class Proposal:
    """One finding's draft: identity verbatim, the registry price and remedy."""

    check: str
    dimension: str
    path: str
    line: int
    message: str
    weight: int
    advisory: bool
    minutes: int
    remedy: str
    file: str

    def identity(self) -> dict[str, object]:
        """The bundle.json record; the check id doubles as the remedy registry id."""
        return {
            "advisory": self.advisory,
            "check": self.check,
            "dimension": self.dimension,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "minutes": self.minutes,
            "path": self.path,
            "remedy": self.remedy,
            "weight": self.weight,
        }


@dataclass(frozen=True)
class ProposalBundle:
    """What one emission produced: the drafts and where they landed."""

    proposals: tuple[Proposal, ...]
    out_dir: Path
    index: Path
    bundle: Path


def _slug(text: str) -> str:
    """A deterministic file-name slug: lowercase, non-alphanumerics collapsed."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "file"


def _file_names(ordered: list[Finding]) -> list[str]:
    """One markdown name per finding, inside its check directory.

    The base name is the slug of the finding's path. Findings that share
    a check and a file (two bare excepts in one module) append the line;
    a remaining tie - same check, path and line - appends a digest of the
    message. Names stay deterministic for a given finding set.
    """
    by_base: Counter[tuple[str, str]] = Counter(
        (finding.check, _slug(finding.path)) for finding in ordered
    )
    by_line: Counter[tuple[str, str, int]] = Counter(
        (finding.check, _slug(finding.path), finding.line)
        for finding in ordered
        if by_base[(finding.check, _slug(finding.path))] > 1
    )
    names: list[str] = []
    for finding in ordered:
        slug = _slug(finding.path)
        if by_base[(finding.check, slug)] > 1:
            slug = f"{slug}-L{finding.line}"
            if by_line[(finding.check, _slug(finding.path), finding.line)] > 1:
                digest = hashlib.sha256(finding.message.encode("utf-8")).hexdigest()[:8]
                slug = f"{slug}-{digest}"
        names.append(f"{slug}.md")
    return names


def _price(finding: Finding) -> int:
    """slop_debt's price for this finding's class; an unknown id fails closed."""
    if finding.check not in REMEDIES:
        raise ProposalError(
            f"check id {finding.check!r} is not in the shared remedy registry - "
            "no remedy to render and no registry class to price; refusing to "
            "invent either"
        )
    return remediation_minutes(finding)


def _price_class(check: str) -> str:
    """The pricing class a reader sees; mirrors slop_debt's branch order."""
    if check in DESIGN_CHECKS:
        return "design"
    if check in STRUCTURAL_BUDGET_CHECKS:
        return "structural"
    if is_blocking(check):
        return "blocking"
    return "advisory"


def _verify_command(check: str, root: Path) -> str:
    """The re-run that proves the fix: the ceiling flags this check's class feeds."""
    base = f"python3 scripts/ai_slop_detector.py {shlex.quote(str(root))}"
    if check in STRUCTURAL_BUDGET_CHECKS:
        return f"{base} --enforce-bloat --max-score 0"
    if is_blocking(check):
        return f"{base} --max-score 0"
    return base


def _verify_hint(check: str) -> str:
    """What a clean re-run proves, per finding class."""
    if check in STRUCTURAL_BUDGET_CHECKS:
        return "The escalated scan fails while any structural-budget finding remains."
    if is_blocking(check):
        return "The scan fails while any blocking-weight finding remains."
    return "The scan re-runs advisory; the check must leave the report's rule table."


def _proposal_text(proposal: Proposal, root: Path) -> str:
    """One draft: identity, the engine's own evidence, remedy, price, verify."""
    severity = "advisory" if proposal.advisory else "blocking"
    quoted = "\n".join(f"> {line}" for line in proposal.message.splitlines())
    return "\n".join(
        [
            DRAFT_HEADER,
            "",
            f"# `{proposal.check}` at `{proposal.path}:{proposal.line}`",
            "",
            f"- Dimension: {proposal.dimension}",
            f"- Severity: {severity} (weight {proposal.weight})",
            f"- Remediation effort: {_render_minutes(proposal.minutes)} "
            f"({_price_class(proposal.check)} class, slop_debt pricing)",
            "",
            "## Finding",
            "",
            quoted,
            "",
            "The message above is the engine's own evidence for the verdict -",
            "cohesion groups, ratios and counts arrive exactly as the scan",
            "produced them; nothing here is recomputed.",
            "",
            "## Remedy",
            "",
            proposal.remedy,
            "",
            "## Verify after fix",
            "",
            "```",
            _verify_command(proposal.check, root),
            "```",
            "",
            _verify_hint(proposal.check),
            "",
        ]
    )


def _index_text(proposals: tuple[Proposal, ...]) -> str:
    """The review surface: one row per draft, ordered by (check, path, line)."""
    lines = [DRAFT_HEADER, ""]
    if not proposals:
        return "\n".join(lines + ["No findings - nothing to propose.", ""])
    lines += [
        "One draft per finding the scan produced; nothing is auto-applied.",
        "",
        "| Check | Path | Minutes | Remedy |",
        "|---|---|---|---|",
    ]
    for proposal in proposals:
        where = f"[`{proposal.path}:{proposal.line}`]({proposal.file})"
        lines.append(f"| `{proposal.check}` | {where} | {proposal.minutes} | {proposal.remedy} |")
    return "\n".join(lines + ["", ""])


def _bundle_text(proposals: tuple[Proposal, ...]) -> str:
    """The machine-readable bundle: schema-stamped, sorted keys, no timestamps."""
    payload = {
        "schema": SCHEMA_VERSION,
        "proposals": [proposal.identity() for proposal in proposals],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _validate_marker(out_dir: Path) -> None:
    """The marker must name our schema; a foreign or newer marker refuses."""
    marker = out_dir / MARKER_NAME
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProposalError(
            f"{out_dir} is not empty and carries no {MARKER_NAME} marker - this "
            "bundle did not generate that directory; refusing to write over it"
        ) from None
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposalError(f"{marker} is not a readable schema marker: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise ProposalError(
            f"{marker} is not a schema-{SCHEMA_VERSION} proposals marker - "
            "refusing to touch a directory this bundle did not generate"
        )


def _validate_out_dir(out_dir: Path) -> None:
    """Empty or own-marker, nothing else: a foreign directory is refused."""
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise ProposalError(f"{out_dir} exists and is not a directory")
    if any(out_dir.iterdir()):
        _validate_marker(out_dir)


def _confined(out_dir: Path, recorded: object) -> Path:
    """A bundle-recorded path as a target inside out_dir, or refuse."""
    if not isinstance(recorded, str) or not recorded:
        raise ProposalError(f"bundle records a non-path {recorded!r} - refusing")
    parts = PurePosixPath(recorded).parts
    if PurePosixPath(recorded).is_absolute() or any(part in (".", "..") for part in parts):
        raise ProposalError(f"recorded path {recorded!r} is not confined to the bundle")
    return out_dir / recorded


def _drop_stale(out_dir: Path) -> None:
    """Remove exactly what the previous bundle recorded, never more."""
    previous = out_dir / BUNDLE_NAME
    if not previous.exists():
        return
    try:
        payload = json.loads(previous.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposalError(f"{previous} is unreadable - refusing to regenerate: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise ProposalError(f"{previous} is not a schema-{SCHEMA_VERSION} bundle - refusing")
    recorded = payload.get("proposals")
    if not isinstance(recorded, list):
        raise ProposalError(f"{previous} carries no proposals list - refusing")
    for entry in recorded:
        target = _confined(out_dir, entry.get("file") if isinstance(entry, dict) else None)
        target.unlink(missing_ok=True)
        parent = target.parent
        if parent != out_dir and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    (out_dir / INDEX_NAME).unlink(missing_ok=True)
    previous.unlink()


def emit_proposals(findings: list[Finding], out_dir: Path, root: Path) -> ProposalBundle:
    """Turn scan findings into reviewable local markdown drafts.

    Every draft carries the finding identity and message verbatim, the
    registry remedy for its check id, the debt engine's minutes for its
    class and the engine command that proves the fix. Nothing here
    recomputes engine facts and nothing is auto-applied; the only writes
    land inside ``out_dir``. Raises ProposalError before any write when
    a check id is unpriced or the output directory is not ours to use.
    """
    out_dir = Path(out_dir)
    _validate_out_dir(out_dir)
    ordered = sorted(findings, key=lambda item: (item.check, item.path, item.line, item.message))
    proposals = tuple(
        Proposal(
            check=finding.check,
            dimension=category_of(finding.check),
            path=finding.path,
            line=finding.line,
            message=finding.message,
            weight=finding.weight,
            advisory=finding.advisory,
            minutes=_price(finding),
            remedy=REMEDIES[finding.check],
            file=f"{finding.check}/{name}",
        )
        for finding, name in zip(ordered, _file_names(ordered))
    )
    bundle = ProposalBundle(
        proposals=proposals,
        out_dir=out_dir,
        index=out_dir / INDEX_NAME,
        bundle=out_dir / BUNDLE_NAME,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    _drop_stale(out_dir)
    for proposal in proposals:
        target = out_dir / proposal.file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_proposal_text(proposal, root), encoding="utf-8")
    (out_dir / INDEX_NAME).write_text(_index_text(proposals), encoding="utf-8")
    (out_dir / BUNDLE_NAME).write_text(_bundle_text(proposals), encoding="utf-8")
    (out_dir / MARKER_NAME).write_text(
        json.dumps({"schema": SCHEMA_VERSION}, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bundle
