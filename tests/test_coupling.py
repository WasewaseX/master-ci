"""Contract tests for the coupling checks (v2.14, P1).

Covers the documented contract in docs/checks.md:
- the greedy module reports high-coupling, the shallow chain stays clean,
- a churny hub reports unstable-hub, a stable leaf hub does not,
- the minimum-graph floor: toy graphs get no coupling verdict,
- test paths, generated names and ignore globs leave the graph,
- a malformed module degrades the graph without breaking the scan,
- registration: advisory, maintainability, tier, remedies.

The fixtures are corpus builders: every verdict test assembles at least
MIN_GRAPH_NODES production modules, and the hub tests raise enough
importers to reach the hub floor before asking for a verdict. Builder
math reads the budgets from slop_coupling directly, so the corpora
build before the integrator wires the re-exports; the message and
registration assertions read them off the detector module, pinning
that surface like the density tests do.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402
from slop_coupling import (  # noqa: E402  # fixture math; assertions use mod.*
    COUPLING_FAN_OUT_BUDGET,
    HUB_FAN_IN_MIN,
    UNSTABLE_HUB_MAX_INSTABILITY,
)


def scan(tmp_path, files=None, extra_globs=None):
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, mod.load_phrases(), extra_globs=extra_globs)


def coupling_hits(findings):
    return [f for f in findings if f.check in ("high-coupling", "unstable-hub")]


# --- corpus builders -------------------------------------------------------
#
# leaf module: a real graph node that imports nothing itself.
LEAF_MODULE = """
VALUE{i} = {i}

def use{i}(a):
    return a + VALUE{i}
"""


def leaf_modules(prefix: str, count: int) -> dict[str, str]:
    return {f"{prefix}{i}.py": LEAF_MODULE.format(i=i) for i in range(count)}


def greedy_module(targets: list[str]) -> str:
    """One module importing every named target - maximum fan-out."""
    lines = "\n".join(f"import {name}" for name in targets)
    return f"{lines}\n\n\ndef consume(a):\n    return a\n"


def hub_importers(prefix: str, count: int) -> dict[str, str]:
    """Modules that import nothing but hub.py - pure fan-in for the hub."""
    body = "from hub import consume\n\n\ndef call{i}(a):\n    return consume(a) + {i}\n"
    return {f"{prefix}{i}.py": body.format(i=i) for i in range(count)}


def chain_modules(count: int) -> dict[str, str]:
    """A linear chain: fan-out 1, fan-in 1, instability 0.5 - clean."""
    files: dict[str, str] = {}
    for i in range(count):
        head = f"import chain{i + 1}\n" if i + 1 < count else ""
        files[f"chain{i}.py"] = f"{head}\ndef step{i}(a):\n    return a + {i}\n"
    return files


def churny_hub_fan_out(fan_in: int) -> int:
    """Smallest hub fan-out whose instability clears the bar with margin."""
    bar = UNSTABLE_HUB_MAX_INSTABILITY
    return int(bar * fan_in / (1 - bar)) + 2


# ---------------------------------------------------------------------------
# the verdicts: over vs under budget
# ---------------------------------------------------------------------------


def test_greedy_module_reports_high_coupling(tmp_path):
    over = COUPLING_FAN_OUT_BUDGET + 1
    files = {
        **leaf_modules("dep", over),
        "greedy.py": greedy_module([f"dep{i}" for i in range(over)]),
    }
    found = [f for f in scan(tmp_path, files) if f.check == "high-coupling"]
    assert len(found) == 1
    assert found[0].advisory and found[0].weight == 2
    assert f"(budget {mod.COUPLING_FAN_OUT_BUDGET})" in found[0].message
    assert f"greedy.py imports {over} in-repo modules" in found[0].message
    assert found[0].path == "greedy.py"


def test_shallow_chain_stays_clean(tmp_path):
    files = chain_modules(9)  # over the floor, nowhere near a budget
    assert not coupling_hits(scan(tmp_path, files))


def test_unstable_hub_reports_unstable_hub(tmp_path):
    importers = HUB_FAN_IN_MIN + 1
    fan_out = churny_hub_fan_out(importers)
    files = {
        **leaf_modules("dep", fan_out),
        **hub_importers("user", importers),
        "hub.py": greedy_module([f"dep{i}" for i in range(fan_out)]),
    }
    found = [f for f in scan(tmp_path, files) if f.check == "unstable-hub"]
    assert len(found) == 1
    assert found[0].path == "hub.py"
    assert found[0].advisory and found[0].weight == 2
    assert f"unstable hub hub.py: {importers} in-repo dependents" in found[0].message
    rating = fan_out / (importers + fan_out)
    assert f"instability {rating:.2f}" in found[0].message
    assert f"(budget: dependents {mod.HUB_FAN_IN_MIN}" in found[0].message


def test_stable_leaf_hub_stays_clean(tmp_path):
    """Martin semantics: high fan-in with zero fan-out is a provider, I = 0."""
    importers = HUB_FAN_IN_MIN  # at the bar - only instability could convict
    files = {
        **leaf_modules("filler", 2),
        **hub_importers("user", importers),
        "hub.py": LEAF_MODULE.format(i=99),
    }
    assert not coupling_hits(scan(tmp_path, files))


def test_high_coupling_offenders_render_bounded(tmp_path):
    over = COUPLING_FAN_OUT_BUDGET + 1
    files = {
        **leaf_modules("dep", over),
        **{f"greedy{i}.py": greedy_module([f"dep{j}" for j in range(over)]) for i in range(5)},
    }
    found = [f for f in scan(tmp_path, files) if f.check == "high-coupling"]
    assert len(found) == 1
    assert found[0].path == "greedy0.py"  # tie on fan-out, path breaks it
    ranked = ", ".join(f"greedy{i}.py {over}" for i in range(4))
    assert f"worst offenders: {ranked}, +1 more" in found[0].message


# ---------------------------------------------------------------------------
# corpus policy: tests, generated names, globs
# ---------------------------------------------------------------------------


def test_test_files_leave_the_graph(tmp_path):
    """The test importer is one dependent short of convicting the hub - iff judged."""
    importers = HUB_FAN_IN_MIN - 1
    fan_out = churny_hub_fan_out(importers + 1)
    over = COUPLING_FAN_OUT_BUDGET + 1
    files = {
        **leaf_modules("dep", fan_out),
        **hub_importers("user", importers),
        "hub.py": greedy_module([f"dep{i}" for i in range(fan_out)]),
        "tests/test_x.py": greedy_module([f"dep{i}" for i in range(over)] + ["hub"]),
    }
    coupling = coupling_hits(scan(tmp_path, files))
    assert not [f for f in coupling if f.path == "tests/test_x.py"]
    assert not [f for f in coupling if f.check == "unstable-hub"]


def test_generated_targets_leave_the_graph(tmp_path):
    """Generated targets are not design: the fan-out tally must skip them."""
    over = COUPLING_FAN_OUT_BUDGET + 1
    files = {
        **{f"gen{i}_pb2.py": LEAF_MODULE.format(i=i) for i in range(over)},
        "greedy.py": greedy_module([f"gen{i}_pb2" for i in range(over)]),
        **chain_modules(9),  # keeps the corpus over the floor
    }
    assert not coupling_hits(scan(tmp_path, files))


def test_ignore_globs_are_respected(tmp_path):
    over = COUPLING_FAN_OUT_BUDGET + 1
    files = {
        **leaf_modules("vendored/dep", over),
        "vendored/greedy.py": greedy_module([f"vendored.dep{i}" for i in range(over)]),
        **chain_modules(9),
    }
    dirty = coupling_hits(scan(tmp_path, files))
    assert [f for f in dirty if f.check == "high-coupling"]  # the corpus is dirty
    assert not coupling_hits(scan(tmp_path, files, extra_globs=["vendored/"]))


# ---------------------------------------------------------------------------
# robustness and registration
# ---------------------------------------------------------------------------


def test_toy_graph_gets_no_verdict(tmp_path):
    """The floor contract: three files with edges are still not a graph."""
    files = {
        "a.py": "import b\nimport c\n\ndef fa():\n    return 1\n",
        "b.py": "import c\n\ndef fb():\n    return 2\n",
        "c.py": "VALUE = 3\n",
    }
    assert not coupling_hits(scan(tmp_path, files))


def test_malformed_module_degrades_without_crashing(tmp_path):
    files = {"broken.py": "def gone(:\n    return 1\n", **chain_modules(9)}
    findings = scan(tmp_path, files)
    assert not coupling_hits(findings)  # the broken file just leaves the edges


def test_empty_repository_is_silent(tmp_path):
    assert not coupling_hits(scan(tmp_path))


def test_coupling_checks_are_registered_advisory_maintainability():
    for check in ("high-coupling", "unstable-hub"):
        assert mod.category_of(check) == "maintainability"
        assert check in mod.REMEDIES
        assert mod.check_tier(check) == 0.75  # maintainability pressure tier
        assert mod.Finding(path="x", line=1, check=check, message="m", weight=2).advisory
