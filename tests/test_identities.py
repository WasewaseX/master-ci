"""Contract tests for the identity layer: fingerprints, projects, classification.

The ratchet's v2 verdicts are only as honest as the identity derivation,
so these tests pin every decision the engine made: the fingerprint is
slop_reporting's formula verbatim (one derivation, never a second), the
identity excludes the line number, the project dimension follows the
first manifest-bearing segment with its documented over-grouping
direction, the ledger defines reintroduction, and the delta table sorts
activity first so the churn a reviewer must read floats above the cap.
"""

from __future__ import annotations

from pathlib import Path

import slop_identities as ident  # noqa: E402
import slop_reporting as reporting  # noqa: E402
from slop_model import Finding  # noqa: E402


def _finding(path: str, message: str, line: int = 1, check: str = "bloat-long-file") -> Finding:
    return Finding(path=path, line=line, check=check, message=message, weight=1)


def _monorepo(tmp_path: Path) -> Path:
    """A scan root whose services/ segment holds a node manifest."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "package.json").write_text("{}", encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# stable_fingerprint: the reused derivation
# ---------------------------------------------------------------------------


def test_stable_fingerprint_matches_the_reporting_derivation():
    """The ratchet reuses the suppression baseline's fingerprint - one formula, zero drift."""
    finding = _finding("services/api/handler.py", "file spans 900 lines", line=7)
    assert ident.stable_fingerprint(finding) == reporting.fingerprint(finding)


def test_fingerprint_normalizes_digits_like_the_reporting_baseline():
    """Messages differing only in numbers are the same finding, per the reporting rule."""
    first = _finding("services/api/handler.py", "file spans 900 lines")
    second = _finding("services/api/handler.py", "file spans 912 lines")
    assert ident.stable_fingerprint(first) == ident.stable_fingerprint(second)


# ---------------------------------------------------------------------------
# identity: the stable tuple and its serialization
# ---------------------------------------------------------------------------


def test_identity_excludes_the_line_number():
    """A line drifts with unrelated edits; a drifted line must never read as churn."""
    before = _finding("services/api/handler.py", "file spans 900 lines", line=10)
    after = _finding("services/api/handler.py", "file spans 900 lines", line=44)
    assert ident.identity(before, "services") == ident.identity(after, "services")


def test_identity_shape_is_project_check_path_fingerprint():
    """The tuple carries exactly the four stable fields, in documented order."""
    finding = _finding("services/api/handler.py", "file spans 900 lines")
    project, check, path, fingerprint = ident.identity(finding, "services")
    assert (project, check, path) == ("services", "bloat-long-file", "services/api/handler.py")
    assert fingerprint == ident.stable_fingerprint(finding)


def test_identity_string_joins_the_four_fields():
    """The baseline stores serialized identities; the separator keeps fields orderable."""
    finding = _finding("services/api/handler.py", "file spans 900 lines")
    serialized = ident.identity_string(finding, "services")
    expected = "|".join(
        (
            "services",
            "bloat-long-file",
            "services/api/handler.py",
            ident.stable_fingerprint(finding),
        )
    )
    assert serialized == expected
    assert ident.is_well_formed(serialized)
    assert ident.check_of(serialized) == "bloat-long-file"


# ---------------------------------------------------------------------------
# project_of: the monorepo dimension
# ---------------------------------------------------------------------------


def test_project_of_nested_project(tmp_path):
    """A first segment holding a family manifest is the project dimension."""
    _monorepo(tmp_path)
    assert ident.project_of("services/api/handler.py", tmp_path) == "services"


def test_project_of_root_file(tmp_path):
    """A file at the scan root has no project segment and lands in the root project."""
    assert ident.project_of("sample.py", tmp_path) == ident.ROOT_PROJECT


def test_project_of_unmanifested_segment(tmp_path):
    """A first segment without any manifest is not a project - over-group to the root."""
    (tmp_path / "plain").mkdir()
    assert ident.project_of("plain/lib/core.py", tmp_path) == ident.ROOT_PROJECT


def test_project_of_ignores_discovery_skip_dirs(tmp_path):
    """Ignored directories are root-attributed even when they hold a manifest.

    Discovery never enumerates node_modules as a project, so an identity
    must not claim it either - the same reasoning covers vendor trees
    and virtualenvs.
    """
    vendor = tmp_path / "node_modules"
    vendor.mkdir()
    (vendor / "package.json").write_text("{}", encoding="utf-8")
    assert ident.project_of("node_modules/pkg/index.js", tmp_path) == ident.ROOT_PROJECT


def test_project_of_ignores_dot_directories(tmp_path):
    """Dot-prefixed segments are never projects, manifest or not."""
    hidden = tmp_path / ".venv"
    hidden.mkdir()
    (hidden / "pyproject.toml").write_text("", encoding="utf-8")
    assert ident.project_of(".venv/lib/site.py", tmp_path) == ident.ROOT_PROJECT


def test_project_of_dotnet_globs(tmp_path):
    """The dotnet family's glob markers count as manifests, per discovery."""
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "App.csproj").write_text("<Project></Project>", encoding="utf-8")
    assert ident.project_of("native/reporting.cs", tmp_path) == "native"


def test_project_of_deep_nesting_belongs_to_the_root(tmp_path):
    """A manifest one level deeper is not the first segment's manifest.

    This is the documented over-grouping direction: a project nested
    deeper than one level is attributed to the root project rather than
    risk splitting one project's findings across two identities.
    """
    deep = tmp_path / "services" / "api"
    deep.mkdir(parents=True)
    (deep / "package.json").write_text("{}", encoding="utf-8")
    assert ident.project_of("services/api/handler.py", tmp_path) == ident.ROOT_PROJECT


# ---------------------------------------------------------------------------
# classify: new / resolved / unchanged / reintroduced
# ---------------------------------------------------------------------------


def test_classify_new_and_resolved():
    """Flat counts hide a swap; classify sees one new and one resolved."""
    kept = ident.identity_string(_finding("services/stays.py", "file spans 900 lines"), "services")
    gone = ident.identity_string(_finding("services/gone.py", "file spans 800 lines"), "services")
    fresh = ident.identity_string(_finding("services/fresh.py", "file spans 700 lines"), "services")
    verdict = ident.classify(
        [kept, fresh], ident.BaselineIdentities(tracked=frozenset({kept, gone}))
    )
    assert verdict.new == {fresh}
    assert verdict.resolved == {gone}
    assert verdict.unchanged == {kept}
    assert not verdict.reintroduced


def test_classify_reintroduced_from_the_ledger():
    """Ledgered debt that shows up again is reintroduced - returned, not new."""
    kept = ident.identity_string(_finding("services/stays.py", "file spans 900 lines"), "services")
    returned = ident.identity_string(
        _finding("services/returned.py", "file spans 800 lines"), "services"
    )
    verdict = ident.classify(
        [kept, returned],
        ident.BaselineIdentities(tracked=frozenset({kept}), resolved_history=frozenset({returned})),
    )
    assert verdict.reintroduced == {returned}
    assert not verdict.new
    assert verdict.unchanged == {kept}
    assert not verdict.resolved


def test_classify_tracked_membership_beats_a_stale_ledger():
    """An identity the floor still tracks is unchanged, even if the ledger remembers it."""
    kept = ident.identity_string(_finding("services/stays.py", "file spans 900 lines"), "services")
    verdict = ident.classify(
        [kept],
        ident.BaselineIdentities(tracked=frozenset({kept}), resolved_history=frozenset({kept})),
    )
    assert verdict.unchanged == {kept}
    assert not verdict.reintroduced
    assert not verdict.new
    assert not verdict.resolved


def test_classify_still_gone_ledger_entries_stay_silent():
    """A ledger entry absent from the scan was resolved before this baseline.

    It must not be celebrated twice: resolved counts tracked identities
    only, and a ledger-only history contributes no bucket at all.
    """
    gone = ident.identity_string(_finding("services/gone.py", "file spans 800 lines"), "services")
    verdict = ident.classify([], ident.BaselineIdentities(resolved_history=frozenset({gone})))
    assert verdict == ident.IdentityVerdict()


def test_classify_with_nothing_on_either_side():
    """An empty comparison is an empty verdict, not an error."""
    assert ident.classify([], ident.BaselineIdentities()) == ident.IdentityVerdict()


# ---------------------------------------------------------------------------
# render: the identity delta table
# ---------------------------------------------------------------------------


def test_identity_render_leads_with_activity_and_names_the_columns():
    """The columns are fixed and churn-heavy rows float above stable ones."""
    new_one = ident.identity_string(
        _finding("services/fresh.py", "file spans 700 lines"), "services"
    )
    kept_one = ident.identity_string(
        _finding("services/stays.py", "file spans 900 lines"), "services"
    )
    gone_one = ident.identity_string(
        _finding("services/gone.py", "file spans 800 lines"), "services"
    )
    root_one = ident.identity_string(
        _finding("notes.md", "prose paragraph", check="prose-block"), ident.ROOT_PROJECT
    )
    verdicts = {
        "services": ident.IdentityVerdict(
            new=frozenset({new_one}),
            resolved=frozenset({gone_one}),
            unchanged=frozenset({kept_one}),
        ),
        ident.ROOT_PROJECT: ident.IdentityVerdict(new=frozenset({root_one})),
    }
    lines = ident.render(verdicts)
    assert "| Project | Check | New | Resolved | Unchanged | Reintroduced |" in lines
    table = [line for line in lines if line.startswith("|") and "`" in line]
    assert table[0] == "| services | `bloat-long-file` | 1 | 1 | 1 | 0 |"  # activity 2 leads
    assert table[1] == f"| {ident.ROOT_PROJECT} | `prose-block` | 1 | 0 | 0 | 0 |"


def test_identity_render_with_no_identities_is_silent():
    """No identity data, no table - the count table carries the summary alone."""
    assert ident.render({}) == []
    assert ident.render({"services": ident.IdentityVerdict()}) == []
