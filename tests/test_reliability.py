"""Contract tests for the reliability dimension and its rating (P1).

Covers the two AST-backed defect checks that fill the roadmap's named
gaps and the rating built over them: unclosed-resource (held and
abandoned flags; a with-block anywhere or a .close() call anywhere
clears it; inline consumption is a call chain, not a held handle),
shared-mutable-state (class-level containers flag, ClassVar clears,
instance attributes and immutable defaults stay silent), and the
rating (severity-based as of v2.18: blocking x 100 + advisory x 2,
Master CI's own bands, one advisory finding off the A band only, the
corpus floor; priced minutes stay in the message as informational
share, not the grade).

Registration is wired by the orchestrator and is not asserted here -
behavior tests only.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_density import DensityFacts  # noqa: E402
from slop_model import Finding  # noqa: E402
from slop_reliability import (  # noqa: E402
    RELIABILITY_CHECKS,
    check_reliability_rating,
    check_shared_mutable_state,
    check_unclosed_resource,
    reliability_grade,
    reliability_minutes,
)


def plain_facts(exec_lines: int) -> DensityFacts:
    """A minimal measured corpus for direct check_reliability_rating calls."""
    return DensityFacts(
        files=1,
        exec_lines=exec_lines,
        complexity=0,
        duplicated=0,
        per_lang={},
        worst_files=(),
        worst_line=None,
    )


# --- unclosed-resource: the resource-leak gap -------------------------------


def test_unclosed_resource_flags_held_and_abandoned_handle():
    source = """
def load(path):
    stream = open(path)
    rows = stream.read()
    return rows
"""
    hits = check_unclosed_resource(Path("mod.py"), source)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.check == "unclosed-resource"
    assert hit.weight == 1
    assert hit.line == 3
    assert "stream" in hit.message
    assert "with-block" in hit.message
    assert "stream.close()" in hit.message


def test_unclosed_resource_cleared_by_with_block():
    source = """
def load(path):
    stream = open(path)
    with stream:
        return stream.read()
"""
    assert check_unclosed_resource(Path("mod.py"), source) == []


def test_unclosed_resource_cleared_by_close_call():
    source = """
def load(path):
    stream = open(path)
    rows = stream.read()
    stream.close()
    return rows
"""
    assert check_unclosed_resource(Path("mod.py"), source) == []


def test_unclosed_resource_inline_consumption_is_silent():
    """open(p).read() assigns the read() call, not the open() Call.

    The chain is consumed in the same expression: nothing is held, and
    the contract flags only a bare assignment of open() itself.
    """
    source = """
def load(path):
    text = open(path).read()
    return text
"""
    assert check_unclosed_resource(Path("mod.py"), source) == []


# --- shared-mutable-state: the shared-state gap ------------------------------


def test_shared_mutable_state_flags_class_level_containers():
    source = """
class Registry:
    cache = {}
    names = []
    seen = set()
    fallback = dict()
    defaults = list()

    def remember(self, name):
        return name in self.cache
"""
    hits = check_shared_mutable_state(Path("mod.py"), source)
    assert len(hits) == 5
    assert sorted(hit.line for hit in hits) == [3, 4, 5, 6, 7]
    for hit in hits:
        assert hit.check == "shared-mutable-state"
        assert hit.advisory and hit.weight == 2
        assert "Registry" in hit.message
    assert any("cache" in hit.message for hit in hits)


def test_shared_mutable_state_skips_classvar_annotation():
    source = """
from typing import ClassVar

class Registry:
    cache: ClassVar[dict] = {}
    names: list = []
"""
    hits = check_shared_mutable_state(Path("mod.py"), source)
    assert len(hits) == 1  # ClassVar declares the sharing intentional
    assert "names" in hits[0].message


def test_shared_mutable_state_skips_instance_attributes():
    source = """
class Registry:
    def __init__(self):
        self.cache = {}
        self.names = []
"""
    assert check_shared_mutable_state(Path("mod.py"), source) == []


def test_shared_mutable_state_immutable_defaults_stay_silent():
    source = """
class Config:
    retries = 3
    label = "primary"
    missing = None
    ratio: float = 0.5
"""
    assert check_shared_mutable_state(Path("mod.py"), source) == []


# --- the rating: pricing, bands, one advisory finding ------------------------


def test_reliability_grade_bands():
    # v2.18 severity bands: blocking x 100 + advisory x 2. The edges
    # encode the canonical shapes - A clean, B up to 5 advisory flags,
    # C up to 2 blocking, D up to 4, E five or more.
    cases = [
        (0, 0, "A"),  # clean
        (0, 5, "B"),  # 5 advisory flags = 10, the B edge
        (0, 6, "C"),  # past the B edge, still no blocking defect
        (1, 0, "C"),  # one blocking defect = 100
        (2, 5, "C"),  # 210, the C edge exactly
        (2, 6, "D"),  # 212, past the C edge
        (4, 5, "D"),  # 410, inside D
        (4, 11, "E"),  # 422, past the D edge
        (5, 0, "E"),  # five blocking defects = 500
    ]
    for blocking, advisory, grade in cases:
        assert reliability_grade(blocking, advisory) == grade, (blocking, advisory)


def test_reliability_minutes_prices_only_the_dimension():
    assert {"unclosed-resource", "shared-mutable-state"} <= RELIABILITY_CHECKS
    assert "import-cycle" not in RELIABILITY_CHECKS
    defect = Finding(path="a.py", line=2, check="bare-except", message="m", weight=1)
    judgment = Finding(path="b.py", line=3, check="mutable-default", message="m", weight=1)
    stray = Finding(path="c.py", line=4, check="import-cycle", message="m", weight=3)
    own = Finding(path="d.py", line=5, check="reliability-rating", message="m", weight=2)
    priced = reliability_minutes([defect, judgment, stray, own])
    assert priced == mod.BLOCKING_MINUTES + mod.DEFAULT_MINUTES


def test_rating_silent_under_corpus_floor():
    findings = [
        Finding(path=f"a{i}.py", line=1, check="bare-except", message="m", weight=1)
        for i in range(10)
    ]
    assert check_reliability_rating(plain_facts(249), findings) == []


def test_rating_silent_at_grade_a():
    # No dimension finding at all: grade A, silent. The design finding
    # prices minutes but is not the dimension's to grade.
    findings = [Finding(path="z.py", line=1, check="import-cycle", message="m", weight=3)]
    assert check_reliability_rating(plain_facts(500), findings) == []


def test_rating_grades_advisory_flags_below_a():
    # v2.18 severity semantics: advisory reliability flags are real
    # signal, so four judgment calls grade B and the finding speaks.
    # Under the pre-v2.18 ratio math this corpus was grade A and silent.
    findings = [
        Finding(path=f"a{i}.py", line=1, check="mutable-default", message="m", weight=1)
        for i in range(4)
    ]
    hits = check_reliability_rating(plain_facts(500), findings)
    assert len(hits) == 1
    assert "reliability rating B (0 blocking + 4 advisory, weighted 8;" in hits[0].message


def test_rating_emits_one_advisory_finding_at_grade_b():
    findings = [
        Finding(path="z.py", line=3, check="bare-except", message="m", weight=1),
        Finding(path="a.py", line=7, check="swallowed-exception", message="m", weight=1),
    ]
    hits = check_reliability_rating(plain_facts(300), findings)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.check == "reliability-rating"
    assert hit.advisory and hit.weight == 2
    assert hit.path == "a.py"  # tie on price: alphabetically first path
    assert "reliability rating C (2 blocking + 0 advisory, weighted 200;" in hit.message
    ratio = 100 * (2 * mod.BLOCKING_MINUTES) / (300 * mod.DEV_MIN_PER_LINE)
    assert f"remediation share {ratio:.1f}% of dev cost" in hit.message
    assert "40m across 2 reliability findings over 300 executable lines" in hit.message
    assert "worst: bare-except at z.py, swallowed-exception at a.py" in hit.message


def test_rating_names_at_most_three_worst_checks():
    dirty = {
        "bare-except": "p_bare.py",
        "swallowed-exception": "p_swallow.py",
        "empty-catch": "p_catch.py",
        "empty-rescue": "p_rescue.py",
        "go-ignored-error": "p_go.py",
        "java-broad-throws": "p_java.py",
        "redundant-try-catch": "p_try.py",
        "silent-recovery": "p_silent.py",
        "unwrap-density": "p_unwrap.py",
        "mutable-default": "p_default.py",
        "unclosed-resource": "p_leak.py",
        "shared-mutable-state": "p_shared.py",
    }
    findings = [
        Finding(path=path, line=1, check=check, message="m", weight=1)
        for check, path in dirty.items()
    ]
    hits = check_reliability_rating(plain_facts(1500), findings)
    assert len(hits) == 1
    hit = hits[0]
    # The registry prices 6 blocking defects at 20 minutes and 6
    # judgment calls at 10 - unclosed-resource blocks with the family.
    # Severity grading: 6 blocking + 6 advisory weighs 612, grade E.
    assert "reliability rating E (6 blocking + 6 advisory, weighted 612;" in hit.message
    assert "3h 00m across 12 reliability findings over 1500 executable lines" in hit.message
    assert (
        "worst: bare-except at p_bare.py, empty-catch at p_catch.py, "
        "empty-rescue at p_rescue.py" in hit.message
    )
    assert "go-ignored-error" not in hit.message  # the render cuts off at 3


def test_malformed_and_empty_sources_degrade_to_silence():
    broken = "def gone(:\n    return\n"
    null_byte = "x = 'a\0b'"
    for check in (check_unclosed_resource, check_shared_mutable_state):
        assert check(Path("broken.py"), broken) == []
        assert check(Path("nul.py"), null_byte) == []
        assert check(Path("empty.py"), "") == []
        assert check(Path("blank.py"), "\n  \n") == []


# ---------------------------------------------------------------------------
# registration: the dimension the registry gave the rating
# ---------------------------------------------------------------------------


def test_reliability_membership_matches_the_dimension():
    """The category table and the rating price exactly the same checks."""
    mapped = {check for check, bucket in mod.CHECK_CATEGORIES.items() if bucket == "reliability"}
    assert mapped == set(mod.RELIABILITY_CHECKS) | {"reliability-rating"}


def test_new_checks_are_registered():
    assert mod.category_of("unclosed-resource") == "reliability"
    assert mod.is_blocking("unclosed-resource")  # a leak blocks like a swallow
    assert mod.category_of("shared-mutable-state") == "reliability"
    assert mod.check_tier("shared-mutable-state") == 0.75  # maintainability tier
    shared = mod.Finding(path="x", line=1, check="shared-mutable-state", message="m", weight=2)
    assert shared.advisory
    assert mod.category_of("reliability-rating") == "reliability"
    rating = mod.Finding(path="x", line=1, check="reliability-rating", message="m", weight=2)
    assert rating.advisory
    assert "unclosed-resource" in mod.REMEDIES
    assert "reliability-rating" in mod.REMEDIES
