"""The cross-file symbol graph and its first consumer, semantic deadness.

``build_symbol_graph`` lowers every supported file to the shared IR
(native ``ast`` for Python, tree-sitter for TS/TSX/JS/Go), then counts
parse-tree identifier references per name, subtracting each name's own
definition sites. A private symbol with zero such references is dead in
the strongest sense this repo can prove today: no import binds it, no
call reaches it, and - the difference from the textual occurrence
oracle in slop_dead - a mention inside a comment or a string literal
does not keep it alive.

The check stays advisory and capped: ``getattr`` strings, plugin
registration, template engines and framework entry points are outside
what a parse tree can see, so the finding advises a human decision like
every other budget. Dunder methods and test files are exempt - both are
routinely reached dynamically. Parse failures ride out through the
graph's ``parse_failures`` list rather than silently shrinking the
corpus; this module never claims coverage a language does not have.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from slop_dead import _cap
from slop_files import is_ignored, is_test_path
from slop_ir import (
    IR_VERSION,
    FileIR,
    SymbolIR,
    file_ir_from_dict,
    file_ir_to_dict,
    language_of,
)
from slop_ir_python import extract_python
from slop_ir_treesitter import extract_treesitter
from slop_model import Finding

MAX_SEMANTIC_DEAD = 8

#: Persistence (roadmap B25 slice 2): a graph bundle is versioned twice -
#: bundle schema and IR version - and a reader refuses any other version,
#: so a stale cache never silently mixes with new extractors' output.
GRAPH_SCHEMA_VERSION = 1

#: The noise floor, matched to the orphan-module stem rule: names shorter
#: than four characters are noise - single letters collide with math and
#: loop variables, fixtures and prose, and flag nothing a reader would
#: act on. A short private symbol is the textual oracle's jurisdiction.
MIN_NAME_LENGTH = 4

#: Names the runtime invokes without any parse-tree reference: the
#: constructor protocol, the common special methods, and Go's init and
#: main entry points. A def with one of these names is framework-reached
#: by definition.
_DUNDER_EXEMPT = frozenset(
    {
        "__init__",
        "__new__",
        "__call__",
        "__str__",
        "__repr__",
        "__enter__",
        "__exit__",
        "__iter__",
        "__next__",
        "__len__",
        "__contains__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__eq__",
        "__hash__",
        "__bool__",
        "__await__",
        "__aenter__",
        "__aexit__",
        # Go's runtime entry points: init runs at package load, main is
        # the binary's entry - neither is referenced from a parse tree.
        "init",
        "main",
        # Dataclass and class-builder protocols the interpreter calls;
        # all caught by the repo's own self-scan, not by guesswork.
        "__post_init__",
        "__init_subclass__",
        "__set_name__",
        "__class_getitem__",
        "__subclasshook__",
    }
)


@dataclass
class SymbolGraph:
    """Defs, uses and parse outcomes for one lowered corpus."""

    files: dict[str, FileIR] = field(default_factory=dict)
    def_sites: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    use_counts: Counter[str] = field(default_factory=Counter)
    parse_failures: list[tuple[str, str]] = field(default_factory=list)


def _lower(path: Path) -> FileIR | None:
    """Route one file to its extractor; None for unsupported languages."""
    lang = language_of(path)
    if lang is None:
        return None
    if lang == "python":
        return extract_python(path)
    return extract_treesitter(path, lang)


def _rel_ok(path: Path, root: Path, globs: list[str]) -> bool:
    """False for paths outside the root or excluded by the globs."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    return not is_ignored(rel, globs)


def _lower_corpus(root: Path, files: list[Path], globs: list[str], graph: SymbolGraph) -> None:
    """Lower every supported file into the graph, failures recorded."""
    for path in files:
        if not _rel_ok(path, root, globs):
            continue
        ir = _lower(path)
        if ir is None:
            continue
        graph.files[str(path)] = ir
        if not ir.parse_ok:
            graph.parse_failures.append((str(path), ir.parse_error or "parse failed"))
        for sym in ir.symbols:
            graph.def_sites.setdefault(sym.name, []).append((str(path), sym.line))


def _count_uses(graph: SymbolGraph) -> None:
    """Parse-tree references per name minus every definition site."""
    def_site_set = {name: set(sites) for name, sites in graph.def_sites.items()}
    for ir in graph.files.values():
        for name, line in ir.identifiers:
            sites = def_site_set.get(name)
            if sites is None or (ir.path, line) in sites:
                continue
            graph.use_counts[name] += 1


def build_symbol_graph(root: Path, files: list[Path], globs: list[str]) -> SymbolGraph:
    """Lower the corpus and count parse-tree references per name.

    Uses are counted across every lowered file; a reference on a name's
    own definition line is the definition, not a use, so the def sites
    are subtracted before counting. Test files stay in the use pool -
    tests keep production symbols alive - but never become candidates.
    """
    graph = SymbolGraph()
    _lower_corpus(root, files, globs, graph)
    _count_uses(graph)
    return graph


def _candidate_symbol(sym: SymbolIR, graph: SymbolGraph) -> bool:
    """True for a private, non-exempt symbol with zero references."""
    if sym.exported or sym.name in _DUNDER_EXEMPT:
        return False
    if len(sym.name) < MIN_NAME_LENGTH:
        return False
    return graph.use_counts.get(sym.name, 0) == 0


def _dead_candidates(graph: SymbolGraph, root: Path) -> list[tuple[FileIR, SymbolIR]]:
    """Private, non-exempt symbols with zero parse-tree references."""
    out: list[tuple[FileIR, SymbolIR]] = []
    for ir in graph.files.values():
        path = Path(ir.path)
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_test_path(rel):
            continue
        out.extend((ir, sym) for sym in ir.symbols if _candidate_symbol(sym, graph))
    out.sort(key=lambda pair: (pair[0].path, pair[1].line, pair[1].name))
    return out


def save_graph(graph: SymbolGraph, path: Path) -> None:
    """Persist the graph bundle: schema + IR version, files, uses, failures.

    The def sites are rebuilt from the stored files on load, so the
    bundle carries each file's own IR version stamp (per
    ``file_ir_to_dict``) plus the bundle-level ``graph_schema``.
    ``use_counts`` is stored explicitly: recomputing it would require
    the sources, and the point of persistence is to answer without
    them.
    """
    payload = {
        "graph_schema": GRAPH_SCHEMA_VERSION,
        "ir_version": IR_VERSION,
        "files": [file_ir_to_dict(ir) for ir in graph.files.values()],
        "use_counts": dict(graph.use_counts),
        "parse_failures": [[str(p), str(e)] for p, e in graph.parse_failures],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_graph(path: Path) -> SymbolGraph:
    """Read a graph bundle; either version mismatch raises, never mixes."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("graph_schema") != GRAPH_SCHEMA_VERSION:
        raise ValueError(
            f"graph schema mismatch: bundle carries {data.get('graph_schema')!r}, "
            f"reader expects {GRAPH_SCHEMA_VERSION}"
        )
    if data.get("ir_version") != IR_VERSION:
        raise ValueError(
            f"IR version mismatch: bundle carries {data.get('ir_version')!r}, "
            f"reader expects {IR_VERSION}"
        )
    graph = SymbolGraph()
    for file_data in data.get("files", []):
        ir = file_ir_from_dict(file_data)
        graph.files[ir.path] = ir
        for sym in ir.symbols:
            graph.def_sites.setdefault(sym.name, []).append((ir.path, sym.line))
    graph.use_counts = Counter(
        {str(name): int(count) for name, count in data.get("use_counts", {}).items()}
    )
    graph.parse_failures = [
        (str(failed), str(error)) for failed, error in data.get("parse_failures", [])
    ]
    return graph


def check_semantic_dead_symbols(
    root: Path,
    files: list[Path],
    globs: list[str],
) -> list[Finding]:
    """Private symbols no parse-tree reference reaches (advisory)."""
    if not root.is_dir():
        return []
    graph = build_symbol_graph(root, files, globs)
    findings: list[Finding] = []
    for ir, sym in _dead_candidates(graph, root):
        findings.append(
            Finding(
                path=ir.path,
                line=sym.line,
                check="semantic-dead-symbol",
                message=(
                    f"private {sym.kind} '{sym.name}' ({ir.language}) has no "
                    "parse-tree reference outside its definition - comments "
                    "and strings do not keep symbols alive"
                ),
                weight=1,
                advisory=True,
            )
        )
    return _cap(findings, MAX_SEMANTIC_DEAD)
