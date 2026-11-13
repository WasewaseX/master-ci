"""Module coupling: fan-out, fan-in and instability over the import graph.

The roadmap's P1 metric layer, slice four. The cycles check judges the
graph's *shape* - a loop is a defect. The coupling numbers judge its
*texture* - how far a module reaches out, and how much reaches in:

- fan-out - the distinct in-repo modules a file imports. A high fan-out
    module is a config file in code form: every upstream change lands in
    its change set at once.
- fan-in - the distinct in-repo files that import a module. High fan-in
    is earned authority, not debt - until the module itself churns.
- instability I = fan_out / (fan_in + fan_out), Robert Martin's metric.
    0 is a pure provider, 1 a pure consumer. A node many others depend
    on that is itself unstable breaks the stable-dependencies principle:
    its churn ripples through every dependent.

Corpus policy: production coupling is the signal. Test files leave the
judged node set by the same is_test_path oracle the other repo-level
checks use, and edges originating from test nodes are dropped before
the tallies, so a module's fan-in reflects production importers only -
a fixture-heavy suite imports the world and would convict every module
it touches. The metrics run over the induced production subgraph: an
edge into an excluded node (test or generated) counts on neither side.
Generated files leave by NAME through the slop_density GENERATED_NAME
pattern - machine-written code is regenerated wholesale, and its
coupling is not design. build_graph already drops package __init__ and
barrel glue and applies the ignore globs.

Minimum graph: under MIN_GRAPH_NODES production nodes the graph is a
toy, and both metrics stay silent - a fan-out verdict over a handful of
files is noise, not a measurement, the same floor the densities keep.

Both checks are advisory, maintainability dimension, one finding each
per scan, emitted only over budget - a CLEAN scan stays CLEAN. The
high-coupling finding anchors at the worst fan-out offender (highest
fan-out, ties broken by path); the unstable-hub finding anchors at the
highest fan-in violator. Both messages carry the ranked offenders as
evidence, bounded like the density renders. Every render is sorted, so
the report is stable run to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from slop_cycles import Edges, build_graph
from slop_density import GENERATED_NAME
from slop_files import is_test_path
from slop_model import Finding

# Budgets calibrated at v2.14, measured both ways. The whole-repo self-scan
# resolves no edges across scripts/ (flat runtime imports); the scripts-rooted
# numbers sit under every budget: max fan-out 22 (ai_slop_detector.py), max
# fan-in 25 (slop_model.py, a pure provider at instability 0.04). Not a gate.
COUPLING_FAN_OUT_BUDGET = 30  # distinct in-repo imports from one module
HUB_FAN_IN_MIN = 25  # dependents before the instability budget even applies
UNSTABLE_HUB_MAX_INSTABILITY = 0.66  # I above this is churn, not structure
MIN_GRAPH_NODES = 8  # under this the graph is a toy, not a project

MAX_OFFENDERS_RENDER = 4  # offenders shown before the "+N more" tail


@dataclass(frozen=True)
class CouplingFacts:
    """One scan's measured coupling: tallies over the production subgraph."""

    nodes: int  # production nodes the graph saw
    fan_out: dict[str, int]  # node -> distinct in-repo targets it imports
    fan_in: dict[str, int]  # node -> distinct in-repo files importing it
    instability: dict[str, float]  # node -> fan_out / (fan_in + fan_out)
    worst_fan_out: tuple[tuple[str, int], ...]  # (path, fan-out), desc
    hubs: tuple[tuple[str, int, float], ...]  # (path, fan-in, I), fan-in desc


def _production_nodes(nodes: set[str]) -> set[str]:
    """The judged node set: no tests, no generated files."""
    return {
        node
        for node in nodes
        if not is_test_path(node) and not GENERATED_NAME.search(PurePosixPath(node).name)
    }


def _production_edges(edges: Edges, prod: set[str]) -> Edges:
    """Edges between production nodes only: test-origin edges dropped."""
    out: Edges = {}
    for source, targets in edges.items():
        if source not in prod:
            continue
        kept = [(target, line) for target, line in targets if target in prod]
        if kept:
            out[source] = kept
    return out


def _tally(edges: Edges) -> tuple[dict[str, int], dict[str, int]]:
    """Distinct-target fan-out and distinct-source fan-in per node."""
    fan_out: dict[str, int] = {}
    fan_in: dict[str, int] = {}
    for source, targets in edges.items():
        fan_out[source] = len(targets)
        for target, _line in targets:
            fan_in[target] = fan_in.get(target, 0) + 1
    return fan_out, fan_in


def _instability(fan_out: dict[str, int], fan_in: dict[str, int]) -> dict[str, float]:
    """Martin's I for every node with at least one edge."""
    out: dict[str, float] = {}
    for node in set(fan_out) | set(fan_in):
        out_degree = fan_out.get(node, 0)
        in_degree = fan_in.get(node, 0)
        if out_degree or in_degree:
            out[node] = out_degree / (out_degree + in_degree)
    return out


def measure_coupling(root: Path, files: list[Path], globs: list[str]) -> CouplingFacts | None:
    """Coupling tallies over the production subgraph; None under the floor."""
    edges, nodes = build_graph(root, files, globs)
    prod = _production_nodes(nodes)
    if len(prod) < MIN_GRAPH_NODES:
        return None
    fan_out, fan_in = _tally(_production_edges(edges, prod))
    instability = _instability(fan_out, fan_in)
    return CouplingFacts(
        nodes=len(prod),
        fan_out=fan_out,
        fan_in=fan_in,
        instability=instability,
        worst_fan_out=tuple(sorted(fan_out.items(), key=lambda item: (-item[1], item[0]))),
        hubs=tuple(
            sorted(
                ((node, count, instability[node]) for node, count in fan_in.items()),
                key=lambda item: (-item[1], item[0]),
            )
        ),
    )


def _bounded(offenders: list[str]) -> str:
    """Top offenders, bounded like the density renders, with the +N tail."""
    head = ", ".join(offenders[:MAX_OFFENDERS_RENDER])
    if len(offenders) > MAX_OFFENDERS_RENDER:
        head += f", +{len(offenders) - MAX_OFFENDERS_RENDER} more"
    return head


def _high_coupling_finding(over: list[tuple[str, int]]) -> Finding:
    """One finding for the whole scan: worst fan-out offender, ranked proof."""
    worst_path, worst_out = over[0]
    ranked = [f"{path} {count}" for path, count in over]
    message = (
        f"coupling fan-out {worst_out} (budget {COUPLING_FAN_OUT_BUDGET}): "
        f"{worst_path} imports {worst_out} in-repo modules; "
        f"worst offenders: {_bounded(ranked)}"
    )
    return Finding(
        path=worst_path,
        line=1,
        check="high-coupling",
        message=message,
        weight=2,
        advisory=True,
    )


def _hub_finding(facts: CouplingFacts, hubs: list[tuple[str, int, float]]) -> Finding:
    """One finding for the whole scan: highest fan-in churny hub, ranked proof."""
    path, in_degree, rating = hubs[0]
    ranked = [f"{hub_path} {hub_in} ({hub_rating:.2f})" for hub_path, hub_in, hub_rating in hubs]
    message = (
        f"unstable hub {path}: {in_degree} in-repo dependents, instability {rating:.2f} "
        f"(fan-out {facts.fan_out.get(path, 0)}) (budget: dependents {HUB_FAN_IN_MIN}, "
        f"instability {UNSTABLE_HUB_MAX_INSTABILITY}); worst offenders: {_bounded(ranked)}"
    )
    return Finding(
        path=path,
        line=1,
        check="unstable-hub",
        message=message,
        weight=2,
        advisory=True,
    )


def check_coupling_metrics(
    root: Path, files: list[Path], globs: list[str], facts: CouplingFacts | None = None
) -> list[Finding]:
    """The two coupling verdicts: advisory, one finding each, over budget only.

    `facts` is the pipeline's one measured graph, computed once and
    shared with the hotspot ranking; when it is not handed over the
    graph is built here, so direct callers keep the old signature.
    """
    if not root.is_dir():
        return []
    if facts is None:
        facts = measure_coupling(root, files, globs)
    if facts is None:
        return []
    over = [item for item in facts.worst_fan_out if item[1] > COUPLING_FAN_OUT_BUDGET]
    hubs = [
        item
        for item in facts.hubs
        if item[1] >= HUB_FAN_IN_MIN and item[2] > UNSTABLE_HUB_MAX_INSTABILITY
    ]
    findings: list[Finding] = []
    if over:
        findings.append(_high_coupling_finding(over))
    if hubs:
        findings.append(_hub_finding(facts, hubs))
    return findings
