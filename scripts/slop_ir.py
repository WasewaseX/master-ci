"""The shared, versioned intermediate representation for symbol facts.

docs/nextgen-architecture.md lands its first vertical slice here: each
extractor (Python's native ``ast`` module, tree-sitter for the TS/JS/Go
families) lowers one source file into the same IR records, and the
symbol graph consumes IR without caring which parser produced it. The
IR is versioned on purpose - a graph built from IR version N must never
be silently mixed with version N+1 output, so every serialized bundle
carries IR_VERSION and readers refuse anything else.

The IR records what a parser can prove, not what a heuristic guesses:
symbol definitions with kind and export status, import edges, call
names, and the full identifier pool that forms the parse-tree use
oracle. Comments and strings never enter that pool - that is the
precision difference from a textual occurrence count, and it is the
property the dead-symbol check and the architecture benchmark measure.

The EXTRACTORS table is the machine-readable half of the language
capability matrix: an absent language is an unsupported language, and
nothing downstream may claim coverage the table does not grant.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

IR_VERSION = 1

#: One row per language the IR accepts, with the parser family that
#: lowers it and the one file suffix it claims. This table is the
#: coverage claim - the graph and the capability matrix read it,
#: never prose.
EXTRACTORS: dict[str, dict[str, str]] = {
    "python": {"parser": "native-ast", "suffix": ".py"},
    "typescript": {"parser": "tree-sitter", "suffix": ".ts"},
    "tsx": {"parser": "tree-sitter", "suffix": ".tsx"},
    "javascript": {"parser": "tree-sitter", "suffix": ".js"},
    "go": {"parser": "tree-sitter", "suffix": ".go"},
}

SYMBOL_KINDS = frozenset({"function", "class", "method"})


@dataclass
class SymbolIR:
    """One definition the parser proved exists.

    ``exported`` is the extractor's honest reading of the language's
    own export rule (Python underscore convention and ``__all__``, Go
    capitalization, TS/JS ``export``), not a guess from usage.
    """

    kind: str  # 'function' | 'class' | 'method'
    name: str
    qualname: str  # file-scoped dotted path, e.g. 'Parser.visit'
    line: int
    exported: bool


@dataclass
class ImportIR:
    """One import edge with the names it binds."""

    module: str  # dotted module or package path, '' when unknown
    names: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class FileIR:
    """The lowered form of one source file.

    ``parse_ok=False`` with a non-empty ``parse_error`` is a first-class
    state: a failed parse can never masquerade as a clean file, and the
    symbol graph records it instead of silently shrinking its corpus.
    """

    path: str
    language: str
    parser: str  # 'native-ast' | 'tree-sitter'
    parse_ok: bool
    parse_error: str = ""
    symbols: list[SymbolIR] = field(default_factory=list)
    imports: list[ImportIR] = field(default_factory=list)
    #: every (name, line) the parse tree carries - the use oracle. Lines
    #: let the graph subtract definition sites instead of guessing.
    identifiers: list[tuple[str, int]] = field(default_factory=list)
    #: callee names at call sites, in source order
    calls: list[str] = field(default_factory=list)


def language_of(path: Path) -> str | None:
    """The IR language a file lowers to, or None when unsupported."""
    suffix = path.suffix.lower()
    for lang, spec in EXTRACTORS.items():
        if suffix == spec["suffix"]:
            return lang
    return None


def file_ir_to_dict(ir: FileIR) -> dict:
    """Serialize one FileIR with its IR version stamped on."""
    payload = asdict(ir)
    payload["ir_version"] = IR_VERSION
    return payload


def file_ir_from_dict(data: dict) -> FileIR:
    """Rebuild one FileIR; a version mismatch raises rather than mixing."""
    if data.get("ir_version") != IR_VERSION:
        raise ValueError(
            f"IR version mismatch: bundle carries {data.get('ir_version')!r}, "
            f"reader expects {IR_VERSION}"
        )
    symbols = [SymbolIR(**sym) for sym in data.get("symbols", [])]
    imports = [ImportIR(**imp) for imp in data.get("imports", [])]
    return FileIR(
        path=data["path"],
        language=data["language"],
        parser=data["parser"],
        parse_ok=data["parse_ok"],
        parse_error=data.get("parse_error", ""),
        symbols=symbols,
        imports=imports,
        identifiers=[(str(n), int(ln)) for n, ln in data.get("identifiers", [])],
        calls=list(data.get("calls", [])),
    )


def write_ir(ir: FileIR, path: Path) -> None:
    """Write one FileIR bundle to a JSON file."""
    path.write_text(json.dumps(file_ir_to_dict(ir), indent=2), encoding="utf-8")


def read_ir(path: Path) -> FileIR:
    """Read one FileIR bundle; version mismatches raise."""
    return file_ir_from_dict(json.loads(path.read_text(encoding="utf-8")))
