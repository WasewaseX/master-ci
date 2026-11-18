"""Import cycles: the module graph, and the loops that close in on it.

scanaislop-style repo-level structural verification, following the same
"deeper analysis, not another heuristic" guidance that produced the
deadness module. A cycle is not a style complaint: it forces every member
into one change set, makes isolated testing impossible and is the shape
dependency direction rules exist to prevent. Where slop_arch enforces a
*declared* law (opt-in config), cycles are unconditional - a module loop
is a defect even where no law was written.

The graph is native and conservative. Import edges are resolved strictly
against files the scan already sees: Python via ast, JS/TS via static
``import``/``require``/``export-from`` specifiers, Go via the root
go.mod module path, Ruby via ``require_relative`` only. The per-language
extractors live in slop_cycle_edges.py - split out when this file
outgrew its own 400-line budget. JVM, PHP, C# and C/C++ resolve through
classpathes, autoloaders and build graphs where a cycle is a property of
the built artifact - out of scope by design, like the other repo-level
oracles.

Namespace glue is excluded before the search: a package's
``__init__.py`` and a JS barrel that is nothing but re-exports encode the
one-directional parent-imports-child idiom, and counting them turns the
most common correct package shape into a false cycle - an edge into glue
never completes one. Self-imports are ignored; test files participate
like any other module. Findings are advisory and capped; the SCC search
(Tarjan) is deterministic, so the report is stable run to run.
"""

from __future__ import annotations

import posixpath
from collections import deque
from pathlib import Path

from slop_cycle_edges import (
    _GO_EXTS,
    _JS_EXTS,
    _PY_EXTS,
    _RB_EXTS,
    _go_edges,
    _go_module,
    _is_glue,
    _js_edges,
    _py_edges,
    _rb_edges,
)
from slop_files import is_ignored, read_text_lax
from slop_model import Finding

MAX_CYCLES = 8
MAX_CHAIN_RENDER = 8  # hops shown before "..." - a 200-module SCC is not a message

# Node = graph key. Files keep their rel path; Go nodes are package dirs.
Edges = dict[str, list[tuple[str, int]]]


def _rel_of(root: Path, path: Path) -> str | None:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _collect(root: Path, files: list[Path], globs: list[str]) -> dict[str, str]:
    """rel path -> text, for every graph-family file the scan sees."""
    texts: dict[str, str] = {}
    keep = _PY_EXTS + _JS_EXTS + _RB_EXTS + _GO_EXTS
    for path in files:
        if path.suffix.lower() not in keep:
            continue
        rel = _rel_of(root, path)
        if rel is None or is_ignored(rel, globs):
            continue
        text = read_text_lax(path)
        if not _is_glue(rel, text):
            texts[rel] = text
    return texts


def _go_context(root: Path, file_set: set[str]) -> tuple[set[str], str]:
    """Go package dirs and module name; empty when there is no go.mod."""
    if not (root / "go.mod").is_file():
        return set(), ""
    dirs = {posixpath.dirname(rel) or "." for rel in file_set if rel.endswith(".go")}
    return dirs, _go_module(root) or ""


def _edges_for(
    rel: str, text: str, file_set: set[str], module: str, go_dirs: set[str]
) -> tuple[str, list[tuple[str, int]]]:
    """One file's node identity and its outgoing edges, by family."""
    if rel.endswith(".py"):
        return rel, _py_edges(rel, text, file_set)
    if rel.endswith(_JS_EXTS):
        return rel, _js_edges(rel, text, file_set)
    if rel.endswith(".go"):
        node = posixpath.dirname(rel) or "."
        return node, _go_edges(rel, text, module, go_dirs) if module else []
    if rel.endswith(".rb"):
        return rel, _rb_edges(rel, text, file_set)
    return rel, []


def build_graph(root: Path, files: list[Path], globs: list[str]) -> tuple[Edges, set[str]]:
    """The dependency graph: node -> [(target, import line)]. Exported for tests.

    Nodes are file rel paths for Python, JS and Ruby; Go's compilation
    unit is the package, so its nodes are directories.
    """
    texts = _collect(root, files, globs)
    file_set = set(texts)
    go_dirs, module = _go_context(root, file_set)
    edges: Edges = {}
    for rel, text in sorted(texts.items()):
        node, found = _edges_for(rel, text, file_set, module, go_dirs)
        kept = [
            (target, line)
            for target, line in found
            if target != node and (target in texts or target in go_dirs)
        ]
        if kept:
            edges.setdefault(node, []).extend(kept)
    return {node: sorted(set(kept)) for node, kept in edges.items()}, set(texts)


class _Scc:
    """Tarjan's working state; result collects the multi-node components."""

    def __init__(self) -> None:
        self.index_of: dict[str, int] = {}
        self.low: dict[str, int] = {}
        self.stack: list[str] = []
        self.on_stack: set[str] = set()
        self.counter = 0
        self.result: list[list[str]] = []

    def discover(self, node: str) -> None:
        """Assign an SCC index and push the node."""
        self.index_of[node] = self.low[node] = self.counter
        self.counter += 1
        self.stack.append(node)
        self.on_stack.add(node)

    def absorb(self, node: str, targets: list[str]) -> None:
        """Tarjan's back-edge lowlink: min with every on-stack target's index."""
        for target in targets:
            if target in self.on_stack:
                self.low[node] = min(self.low[node], self.index_of[target])

    def pop_component(self, node: str) -> list[str]:
        """Pop the SCC rooted at node off the Tarjan stack."""
        component: list[str] = []
        while True:
            member = self.stack.pop()
            self.on_stack.discard(member)
            component.append(member)
            if member == node:
                return component

    def finish(self, node: str, targets: list[str], work: list) -> None:
        """Complete a frame: absorb, emit the component, feed the parent."""
        self.absorb(node, targets)
        if self.low[node] == self.index_of[node]:
            component = self.pop_component(node)
            if len(component) > 1:
                self.result.append(sorted(component))
        if work:
            self.low[work[-1][0]] = min(self.low[work[-1][0]], self.low[node])


def _next_unvisited(targets: list[str], cursor: int, index_of: dict) -> int:
    """Advance past targets already indexed by a previous visit."""
    while cursor < len(targets) and targets[cursor] in index_of:
        cursor += 1
    return cursor


def _strongly_connected(edges: Edges) -> list[list[str]]:
    """Iterative Tarjan SCC over the graph keys; multi-node components only."""
    state = _Scc()
    for start in sorted(edges):
        if start in state.index_of:
            continue
        state.discover(start)
        work: list[tuple[str, list[str], int]] = [(start, [t for t, _ in edges[start]], 0)]
        while work:
            node, targets, cursor = work.pop()
            cursor = _next_unvisited(targets, cursor, state.index_of)
            child = targets[cursor] if cursor < len(targets) else None
            if child is None:
                state.finish(node, targets, work)
                continue
            state.discover(child)
            work.append((node, targets, cursor + 1))
            work.append((child, [t for t, _ in edges.get(child, ())], 0))
    return state.result


def _walk_back(node: str, start: str, prev: dict) -> list[str]:
    """Parent links from node back to start, returned start-first."""
    path: list[str] = []
    back: str | None = node
    while back is not None and back != start:
        path.append(back)
        back = prev[back]
    path.append(start)
    path.reverse()
    return path


def _chain(scc: list[str], edges: Edges) -> list[str]:
    """Shortest cycle through the SCC, starting at its first node."""
    start = scc[0]
    members = set(scc)
    prev: dict[str, str | None] = {start: None}
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        for target, _ in edges.get(node, ()):
            if target not in members:
                continue
            if target == start:
                return _walk_back(node, start, prev) + [start]
            if target not in prev:
                prev[target] = node
                queue.append(target)
    return scc + [start]


def _render(chain: list[str]) -> str:
    """The loop as text, bounded so pathological rings stay readable."""
    if len(chain) <= MAX_CHAIN_RENDER:
        return " -> ".join(chain)
    head = " -> ".join(chain[:MAX_CHAIN_RENDER])
    return f"{head} -> ... -> {chain[-1]} ({len(chain) - 1} modules)"


def check_import_cycles(root: Path, files: list[Path], globs: list[str]) -> list[Finding]:
    """Import loops among in-repo modules (advisory, capped)."""
    if not root.is_dir():
        return []
    edges, _nodes = build_graph(root, files, globs)
    findings: list[Finding] = []
    for scc in sorted(_strongly_connected(edges)):
        chain = _chain(scc, edges)
        source, target = chain[0], chain[1]
        line = next((ln for dst, ln in edges.get(source, ()) if dst == target), 1)
        findings.append(
            Finding(
                path=source,
                line=line,
                check="import-cycle",
                message="import cycle: " + _render(chain),
                weight=3,
                advisory=True,
            )
        )
        if len(findings) >= MAX_CYCLES:
            break
    return findings
