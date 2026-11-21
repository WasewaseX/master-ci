"""The benchmark probe table: per-fixture engines and their registration.

Each fixture under fixtures/bench/<slug>/ declares its expectation in
manifest.json; the Probe table maps the slug to the engine function that
judges it, the check id the row reads, and - for the context-aware
verdicts - the check ids that must stay silent for a detected
declaration to pass. The data-driven tranches (round 8 family columns,
round 9 Python column, round 10 corpus deepening, round 13 C/C++
column) register their rows at import: one shared probe answers every
row in a tranche, the manifest names the check id it judges.

A probe raises Pending when a row cannot be judged right now (git or
lizard unavailable) - PENDING with a reason, never a guessed count.
(Split out of fixture_benchmark.py when that file outgrew the same
400-line budget the engine enforces on its own source.)
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fixture_bench_fixtures import (
    build_git_fixture,
    probe_abstraction,
    probe_cycles,
    probe_dead,
    probe_deps,
    probe_lcom4,
    probe_test_redundancy,
    probe_type_decls,
    py_pairs,
)
from fixture_bench_round8 import _ROUND8_CLEAN_RULES, _ROUND8_DETECTED, _ROUND8_RENAMED
from fixture_bench_round9 import ROUND9_CLEAN_RULES, ROUND9_DETECTED, ROUND9_RENAMED
from fixture_bench_round10 import (
    ROUND10_FAMILY_CLEAN_RULES,
    ROUND10_FAMILY_DETECTED,
    ROUND10_FAMILY_RENAMED,
    ROUND10_PY_CLEAN_RULES,
    ROUND10_PY_DETECTED,
    ROUND10_PY_RENAMED,
)
from fixture_bench_round13 import (
    ROUND13_CPP_CLEAN_RULES,
    ROUND13_CPP_DETECTED,
    ROUND13_CPP_RENAMED,
)
from fixture_bench_round15 import (
    ROUND15_CSHARP_CLEAN_RULES,
    ROUND15_CSHARP_DETECTED,
    ROUND15_CSHARP_RENAMED,
)
from slop_bloat import check_bloat
from slop_comments import extract_comments
from slop_cpp import check_cpp_slop
from slop_csharp import check_csharp_slop
from slop_density import measure as measure_density
from slop_duplicates import scan_duplicates
from slop_family import check_go, check_jvm_slop, check_rust
from slop_files import read_text_lax
from slop_hotspot import check_hotspot_metrics, churn_map
from slop_lizard import lizard_available
from slop_model import Finding
from slop_patterns import FAMILY_OF_EXT
from slop_py_checks import check_py_ast
from slop_py_family import check_py_print_debug
from slop_similarity import check_near_duplicates
from slop_ts_checks import check_ts_slop


class ConfigError(Exception):
    """A benchmark misconfiguration: fail closed with exit code 2."""


class Pending(Exception):
    """A fixture that cannot be judged right now; message is the reason."""


ProbeFn = Callable[[Path, list[Path]], list[Finding]]


@dataclass(frozen=True)
class Probe:
    """One fixture's engine contract: the finding engine and its check id.

    `absent` names check ids that must stay silent for a detected
    declaration to pass - the long-but-cohesive row proves not only
    that the context-aware verdict fired but that the ordinary
    `bloat-long-file` verdict did not.
    """

    engine: str
    rule: str
    run: ProbeFn
    absent: tuple[str, ...] = ()


def _probe_duplicates(root: Path, files: list[Path]) -> list[Finding]:
    del root  # the duplication oracle reads only the file list
    return scan_duplicates(files)


def _probe_similarity(root: Path, files: list[Path]) -> list[Finding]:
    """The A9 similarity oracle: root-scoped so the fixtures judge alone."""
    return check_near_duplicates(root, files, [])


def _probe_family_scan(root: Path, files: list[Path]) -> list[Finding]:
    """The round-8 per-file family columns over one fixture tree.

    One shared probe for the whole tranche: the manifest names the check
    id it judges, so a single scanner sweep answers every row. Repo-level
    engines (duplication, density) stay out - the probe judges the named
    family oracle, not the whole scan.
    """
    del root
    scanners: dict[str, Callable[..., list[Finding]]] = {
        "csharp": check_csharp_slop,
        "cpp": check_cpp_slop,
        "js": check_ts_slop,
        "jvm": check_jvm_slop,
        "go": check_go,
        "rust": check_rust,
    }
    findings: list[Finding] = []
    for path in sorted(files):
        family = FAMILY_OF_EXT.get(path.suffix.lower(), "")
        runner = scanners.get(family)
        if runner is None:
            continue
        text = read_text_lax(path)
        if family == "cpp":
            findings.extend(runner(path, text, family))
        elif family == "rust":
            findings.extend(check_rust(path, text, family))
        else:
            findings.extend(runner(path, text, family, path.name))
    return findings


def _probe_cyclomatic(root: Path, files: list[Path]) -> list[Finding]:
    """slop_bloat's budgets over the slop_lizard parse (high-cyclomatic here)."""
    if not lizard_available():
        raise Pending("lizard analyzer unavailable")
    findings: list[Finding] = []
    for path, rel in py_pairs(root, files):
        text = read_text_lax(path)
        findings.extend(check_bloat(path, text, "py", rel, extract_comments(text, "py")))
    return findings


def _probe_hotspot(root: Path, files: list[Path]) -> list[Finding]:
    """Real history in a temp repo; PENDING on git trouble, never a guess."""
    del root, files  # the checked-in dir is manifest-only; history is built below
    if shutil.which("git") is None:
        raise Pending("git unavailable")
    if not lizard_available():
        raise Pending("lizard analyzer unavailable")
    with tempfile.TemporaryDirectory(prefix="fixture-bench-") as tmp:
        repo = Path(tmp) / "warehouse"
        if not build_git_fixture(repo):
            raise Pending("git unavailable")
        sources = sorted(repo.rglob("*.py"))
        churn, defects = churn_map(repo)
        facts = measure_density(repo, sources, [])
        if facts is None:
            return []
        return check_hotspot_metrics(facts, None, churn, defects)


def _probe_py_family(root: Path, files: list[Path]) -> list[Finding]:
    """The round-9 Python family column over one fixture tree.

    One shared probe for the tranche: broad-except is driven by the
    check_py_ast walk, print-debug by its own module - the manifest
    names the check id it judges, so one sweep answers every row. The
    fixture filename is the rel path, which keeps the scoping
    exemptions (entrypoints, script dirs) honest for the print rows.
    """
    del root
    findings: list[Finding] = []
    for path in sorted(files):
        if path.suffix.lower() != ".py":
            continue
        text = read_text_lax(path)
        findings.extend(check_py_ast(path, text, "py"))
        findings.extend(check_py_print_debug(path, text, "py", path.name))
    return findings


PROBES: dict[str, Probe] = {
    "abstraction-chain": Probe("slop_abstraction", "forwarding-chain", probe_abstraction),
    "churn-hotspot": Probe("slop_hotspot", "hotspot-risk", _probe_hotspot),
    "circular-dependencies": Probe("slop_cycles", "import-cycle", probe_cycles),
    "duplicate-implementation": Probe(
        "slop_duplicates", "boilerplate-duplication", _probe_duplicates
    ),
    "near-duplicate-clone": Probe("slop_similarity", "near-duplicate-clone", _probe_similarity),
    "near-duplicate-clone-clean": Probe(
        "slop_similarity", "near-duplicate-clone", _probe_similarity
    ),
    "dynamic-registration": Probe("slop_dead", "unused-declaration", probe_dead),
    "framework-wrapper": Probe("slop_abstraction", "no-behavior-class", probe_abstraction),
    "long-but-cohesive": Probe(
        "slop_bloat",
        "long-but-cohesive",
        _probe_cyclomatic,
        absent=("bloat-long-file",),
    ),
    "low-cohesion": Probe("slop_lcom4", "low-cohesion", probe_lcom4),
    "redundant-tests": Probe("slop_test_redundancy", "redundant-test", probe_test_redundancy),
    "short-but-complex": Probe("slop_bloat", "high-cyclomatic", _probe_cyclomatic),
    "unnecessary-dependency": Probe("slop_deps", "unused-dependency", probe_deps),
    "unused-function": Probe("slop_dead", "unused-declaration", probe_dead),
    # --- round-8 tranche: the family columns + the cross-file oracle ---
    # Data-driven: every detected slug's rule is its own name; the clean
    # rows and the cross-file oracle carry their explicit mapping.
}
PROBES["duplicate-type-declaration"] = Probe(
    "slop_type_decls", "duplicate-type-declaration", probe_type_decls
)
for _SLUG in _ROUND8_DETECTED:
    PROBES[_SLUG] = Probe("slop_family", _ROUND8_RENAMED.get(_SLUG, _SLUG), _probe_family_scan)
for _CLEAN_SLUG, _CLEAN_RULE in _ROUND8_CLEAN_RULES.items():
    if _CLEAN_SLUG.startswith("duplicate"):
        PROBES[_CLEAN_SLUG] = Probe("slop_type_decls", _CLEAN_RULE, probe_type_decls)
    else:
        PROBES[_CLEAN_SLUG] = Probe("slop_family", _CLEAN_RULE, _probe_family_scan)

for _SLUG in ROUND9_DETECTED:
    PROBES[_SLUG] = Probe("slop_py_family", ROUND9_RENAMED.get(_SLUG, _SLUG), _probe_py_family)
for _CLEAN_SLUG, _CLEAN_RULE in ROUND9_CLEAN_RULES.items():
    PROBES[_CLEAN_SLUG] = Probe("slop_py_family", _CLEAN_RULE, _probe_py_family)

# --- round-10 tranche: the accuracy corpus deepening ------------------------
for _SLUG in ROUND10_FAMILY_DETECTED:
    PROBES[_SLUG] = Probe(
        "slop_family", ROUND10_FAMILY_RENAMED.get(_SLUG, _SLUG), _probe_family_scan
    )
for _SLUG in ROUND10_PY_DETECTED:
    PROBES[_SLUG] = Probe("slop_py_family", ROUND10_PY_RENAMED.get(_SLUG, _SLUG), _probe_py_family)
for _CLEAN_SLUG, _CLEAN_RULE in ROUND10_FAMILY_CLEAN_RULES.items():
    PROBES[_CLEAN_SLUG] = Probe("slop_family", _CLEAN_RULE, _probe_family_scan)
for _CLEAN_SLUG, _CLEAN_RULE in ROUND10_PY_CLEAN_RULES.items():
    PROBES[_CLEAN_SLUG] = Probe("slop_py_family", _CLEAN_RULE, _probe_py_family)

# --- round-13 tranche: the C/C++ accuracy column deepening -------------------
for _SLUG in ROUND13_CPP_DETECTED:
    PROBES[_SLUG] = Probe("slop_family", ROUND13_CPP_RENAMED.get(_SLUG, _SLUG), _probe_family_scan)
for _CLEAN_SLUG, _CLEAN_RULE in ROUND13_CPP_CLEAN_RULES.items():
    PROBES[_CLEAN_SLUG] = Probe("slop_family", _CLEAN_RULE, _probe_family_scan)

# --- round-15 tranche: the C# accuracy column deepening ----------------------
for _SLUG in ROUND15_CSHARP_DETECTED:
    PROBES[_SLUG] = Probe(
        "slop_family", ROUND15_CSHARP_RENAMED.get(_SLUG, _SLUG), _probe_family_scan
    )
for _CLEAN_SLUG, _CLEAN_RULE in ROUND15_CSHARP_CLEAN_RULES.items():
    PROBES[_CLEAN_SLUG] = Probe("slop_family", _CLEAN_RULE, _probe_family_scan)
