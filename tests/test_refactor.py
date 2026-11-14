"""Contract tests for the refactoring proposals emitter (v2.17, P2).

Covers the documented contract in scripts/slop_refactor.py:
- every written draft round-trips a real finding's identity through
    bundle.json, engine message and registry remedy verbatim,
- minutes equal slop_debt's pricing function output for the finding's
    class, per registry class (design 120, structural 30, blocking 20,
    default 10),
- a healthy scan (the full detector, zero findings) emits zero
    proposals and an empty index under the success contract,
- two consecutive runs are byte-identical, including regeneration in
    place - no timestamps anywhere in the artifacts,
- the marker guard refuses a foreign non-empty directory and a
    foreign-schema marker, leaving every foreign byte untouched,
- regeneration over an existing bundle removes exactly the stale files
    the previous bundle recorded and never a foreign file,
- the index row count equals the proposal count, ordered by
    (check, path),
- every proposal's check id has a slop_remedies entry, rendered
    verbatim in the draft,
- a check id outside the shared registry fails closed before any write,
- the verify-after-fix section carries the ceiling flags of the
    finding's class.

Findings are real Finding instances (the registry resolves their
advisory flags); the healthy-scan case runs the actual detector over a
clean corpus, the same construction the debt and density tests use.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_debt import (  # noqa: E402
    BLOCKING_MINUTES,
    DEFAULT_MINUTES,
    DESIGN_MINUTES,
    STRUCTURAL_MINUTES,
    remediation_minutes,
)
from slop_model import Finding  # noqa: E402
from slop_refactor import (  # noqa: E402
    BUNDLE_NAME,
    DRAFT_HEADER,
    INDEX_NAME,
    MARKER_NAME,
    SCHEMA_VERSION,
    ProposalError,
    emit_proposals,
)
from slop_remedies import REMEDIES  # noqa: E402


def _finding(check: str, path: str, line: int, message: str, weight: int = 1) -> Finding:
    return Finding(path=path, line=line, check=check, message=message, weight=weight)


def _sample() -> list[Finding]:
    """One finding per pricing class, plus a same-file collision pair."""
    return [
        _finding("bare-except", "pkg/handler.py", 30, "bare except swallows the failure", 1),
        _finding("bare-except", "pkg/handler.py", 58, "bare except hides the parse error", 1),
        _finding("god-function", "pkg/handler.py", 12, "spans 4 responsibilities over 90 lines", 2),
        _finding("generic-name", "pkg/util.py", 3, "identifier 'data' names nothing", 1),
        _finding("import-cycle", "pkg/cycle.py", 5, "3 modules in a cycle: c -> a -> b -> c", 3),
    ]


def _emission_order(findings: list[Finding]) -> list[Finding]:
    """The documented emission order: (check, path, line, message)."""
    return sorted(findings, key=lambda item: (item.check, item.path, item.line, item.message))


# --- a corpus the full detector scans to zero findings ---------------------


_FLAT_MODULE = """
OFFSET{i} = {off}

def add{i}(a, b):
    shifted = a + OFFSET{i}
    return shifted + b

def sub{i}(a, b):
    shifted = a - OFFSET{i}
    return shifted - b
"""

_FLAT_COUNT = 6


def _clean_corpus(root: Path) -> None:
    """Six flat helpers plus the test file that references every name.

    The deadness oracle harvests candidates from non-test sources and
    counts every file as its usage oracle, so a helper exercised by the
    test file survives and the inventory has its test file - the scan's
    honest empty result, the same builder shape the debt tests use.
    """
    imports = "\n".join(f"from flat{i} import add{i}, sub{i}" for i in range(_FLAT_COUNT))
    asserts = "\n".join(
        f"    assert add{i}(1, 2) == {6 + i} and sub{i}(1, 2) == {-4 - i}"
        for i in range(_FLAT_COUNT)
    )
    for i in range(_FLAT_COUNT):
        module = root / f"flat{i}.py"
        module.write_text(_FLAT_MODULE.format(i=i, off=3 + i), encoding="utf-8")
    (root / "test_flat.py").write_text(
        f"{imports}\n\n\ndef test_every_shift_helper():\n{asserts}\n", encoding="utf-8"
    )


# --- the emission contract --------------------------------------------------


def test_every_written_draft_round_trips_the_finding_identity(tmp_path: Path) -> None:
    """Why: a draft that cannot be traced back to its finding is noise,
    so bundle.json must carry the identity fields verbatim and the
    markdown must name the same finding the engine emitted."""
    findings = _sample()
    out = tmp_path / "proposals"
    emit_proposals(findings, out, Path("."))
    records = json.loads((out / BUNDLE_NAME).read_text(encoding="utf-8"))
    assert records["schema"] == SCHEMA_VERSION
    written = records["proposals"]
    assert len(written) == len(findings)
    drafts = [p for p in out.rglob("*.md") if p.name != INDEX_NAME]
    assert len(drafts) == len(findings)
    assert len({str(record["file"]) for record in written}) == len(written)
    for finding, record in zip(_emission_order(findings), written):
        assert record["check"] == finding.check
        assert record["path"] == finding.path
        assert record["line"] == finding.line
        assert record["message"] == finding.message
        assert record["weight"] == finding.weight
        assert record["advisory"] == finding.advisory
        draft = (out / str(record["file"])).read_text(encoding="utf-8")
        assert draft.startswith(DRAFT_HEADER)
        assert f"`{finding.check}`" in draft
        assert f"{finding.path}:{finding.line}" in draft
        assert finding.message in draft
        assert finding.remedy in draft
    same_file = [record["file"] for record in written if record["check"] == "bare-except"]
    assert len(same_file) == 2 and "bare-except/pkg-handler-py-L30.md" in same_file


def test_minutes_equal_the_debt_engines_pricing_for_the_same_class(tmp_path: Path) -> None:
    """Why: the drafts must carry slop_debt's price, never a second cost
    model - each class prices exactly as remediation_minutes prices a
    freshly constructed finding of the same shape."""
    out = tmp_path / "proposals"
    bundle = emit_proposals(_sample(), out, Path("."))
    priced = {proposal.check: proposal.minutes for proposal in bundle.proposals}
    assert priced["import-cycle"] == DESIGN_MINUTES == 120
    assert priced["god-function"] == STRUCTURAL_MINUTES == 30
    assert priced["bare-except"] == BLOCKING_MINUTES == 20
    assert priced["generic-name"] == DEFAULT_MINUTES == 10
    for proposal in bundle.proposals:
        again = _finding(proposal.check, proposal.path, proposal.line, proposal.message)
        assert proposal.minutes == remediation_minutes(again)


def test_healthy_scan_emits_zero_proposals_and_an_empty_index(tmp_path: Path) -> None:
    """Why: a clean scan proposes nothing - the exit-success contract is
    the normal case, so the detector's own zero-finding result must
    yield an empty index and an empty bundle, not an error."""
    root = tmp_path / "repo"
    root.mkdir()
    _clean_corpus(root)
    findings = mod.scan(root, mod.load_phrases())
    assert findings == []
    out = tmp_path / "proposals"
    bundle = emit_proposals(findings, out, root)
    assert bundle.proposals == ()
    records = json.loads((out / BUNDLE_NAME).read_text(encoding="utf-8"))
    assert records["proposals"] == []
    index = (out / INDEX_NAME).read_text(encoding="utf-8")
    assert "No findings" in index
    assert not [line for line in index.splitlines() if line.startswith("| `")]


def test_two_consecutive_runs_are_byte_identical(tmp_path: Path) -> None:
    """Why: the drafts are review artifacts a workflow may commit or
    diff - timestamps or set-ordered names would make every run look
    like a change, so the same findings must write the same bytes,
    including when regenerating an existing bundle in place."""
    findings = _sample()
    one, two = tmp_path / "one", tmp_path / "two"
    emit_proposals(findings, one, Path("."))
    emit_proposals(findings, two, Path("."))
    names = sorted(str(path.relative_to(one)) for path in one.rglob("*") if path.is_file())
    assert MARKER_NAME in names and BUNDLE_NAME in names and INDEX_NAME in names
    for name in names:
        assert (one / name).read_bytes() == (two / name).read_bytes()
    emit_proposals(findings, two, Path("."))
    for name in names:
        assert (one / name).read_bytes() == (two / name).read_bytes()


def test_foreign_non_empty_directory_is_refused_untouched(tmp_path: Path) -> None:
    """Why: the guard exists so a misdirected emission cannot trample a
    directory the bundle did not generate - a foreign file, or a marker
    naming another schema, refuses the run and keeps every byte."""
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("not ours", encoding="utf-8")
    with pytest.raises(ProposalError, match="marker"):
        emit_proposals(_sample(), foreign, Path("."))
    assert (foreign / "keep.txt").read_text(encoding="utf-8") == "not ours"
    assert not (foreign / BUNDLE_NAME).exists()
    (foreign / MARKER_NAME).write_text('{"schema": 99}', encoding="utf-8")
    with pytest.raises(ProposalError, match="schema-1"):
        emit_proposals(_sample(), foreign, Path("."))
    assert (foreign / "keep.txt").exists()


def test_regeneration_replaces_the_bundle_without_stale_or_foreign_losses(tmp_path: Path) -> None:
    """Why: debt gets paid between runs - yesterday's draft must not
    linger after its finding is gone, while anything the bundle never
    generated survives untouched."""
    out = tmp_path / "proposals"
    first = emit_proposals(
        [_finding("bare-except", "pkg/a.py", 7, "bare except one", 1)], out, Path(".")
    )
    stale = out / first.proposals[0].file
    assert stale.exists()
    (out / "notes.txt").write_text("foreign", encoding="utf-8")
    second = emit_proposals(
        [_finding("god-function", "pkg/b.py", 3, "spans 3 responsibilities", 2)],
        out,
        Path("."),
    )
    assert not stale.exists()
    assert not stale.parent.exists()
    assert (out / "notes.txt").read_text(encoding="utf-8") == "foreign"
    on_disk = {str(path.relative_to(out)) for path in out.rglob("*.md") if path.name != INDEX_NAME}
    assert on_disk == {proposal.file for proposal in second.proposals}
    records = json.loads((out / BUNDLE_NAME).read_text(encoding="utf-8"))
    assert [record["check"] for record in records["proposals"]] == ["god-function"]


def test_index_rows_equal_proposals_ordered_by_check_and_path(tmp_path: Path) -> None:
    """Why: the index is the review surface - one row per draft in the
    documented order, each row linking the written file and carrying the
    price, so a reviewer reads the debt without opening every draft."""
    out = tmp_path / "proposals"
    bundle = emit_proposals(_sample(), out, Path("."))
    index = (out / INDEX_NAME).read_text(encoding="utf-8")
    rows = [line for line in index.splitlines() if line.startswith("| `")]
    assert len(rows) == len(bundle.proposals) == 5
    for row, proposal in zip(rows, bundle.proposals):
        assert row.startswith(f"| `{proposal.check}` |")
        assert f"]({proposal.file})" in row
        assert f"| {proposal.minutes} |" in row
        assert proposal.remedy in row


def test_every_proposal_check_id_has_a_registry_remedy(tmp_path: Path) -> None:
    """Why: the remedy text is taxonomy, not invention - slop_remedies
    is the one registry, so each draft must render the exact entry its
    check id maps to, and an unregistered id never reaches a draft."""
    out = tmp_path / "proposals"
    bundle = emit_proposals(_sample(), out, Path("."))
    assert bundle.proposals
    for proposal in bundle.proposals:
        assert proposal.check in REMEDIES
        assert proposal.remedy == REMEDIES[proposal.check]
        draft = (out / proposal.file).read_text(encoding="utf-8")
        assert proposal.remedy in draft


def test_unregistered_check_id_fails_closed_before_any_write(tmp_path: Path) -> None:
    """Why: an id the shared registry never classified has no remedy to
    render and no price behind it - inventing either would poison the
    review, so the run refuses before a single byte lands."""
    out = tmp_path / "proposals"
    mystery = [_finding("mystery-check", "pkg/x.py", 1, "unregistered verdict", 1)]
    with pytest.raises(ProposalError, match="mystery-check"):
        emit_proposals(mystery, out, Path("."))
    assert not out.exists()


def test_verify_section_carries_the_ceiling_flags_of_the_finding_class(tmp_path: Path) -> None:
    """Why: the proof step must be runnable as written - structural
    findings verify under the escalation, blocking findings under the
    score ceiling, advisory findings under the plain scan whose rule
    table the reviewer greps."""
    out = tmp_path / "proposals"
    bundle = emit_proposals(_sample(), out, Path("."))
    texts = {
        proposal.check: (out / proposal.file).read_text(encoding="utf-8")
        for proposal in bundle.proposals
    }
    assert "--enforce-bloat --max-score 0" in texts["god-function"]
    assert "--max-score 0" in texts["bare-except"]
    assert "--enforce-bloat" not in texts["bare-except"]
    assert "python3 scripts/ai_slop_detector.py .\n```" in texts["generic-name"]
    assert "--max-score" not in texts["generic-name"]
