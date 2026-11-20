"""The IR and symbol-graph contracts: extraction, deadness, honesty.

The nextgen vertical slice's regression wall: the IR round-trips with a
version stamp, Python lowers through the native ``ast`` parser, the
tree-sitter families lower when the grammar pack is present, and the
semantic-dead-symbol oracle flags exactly the private symbols no parse
tree reaches while the clean accuracy corpus stays clean. Parse
failures surface instead of shrinking the corpus silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from slop_ir import IR_VERSION, file_ir_from_dict, file_ir_to_dict, language_of
from slop_symbol_graph import (
    GRAPH_SCHEMA_VERSION,
    MAX_SEMANTIC_DEAD,
    _dead_candidates,
    build_symbol_graph,
    check_semantic_dead_symbols,
    load_graph,
    save_graph,
)

try:
    import tree_sitter_language_pack  # noqa: F401 - availability probe

    _PACK_OK = True
except ImportError:  # pragma: no cover - the pack ships with the scanner group
    _PACK_OK = False

needs_pack = pytest.mark.skipif(not _PACK_OK, reason="tree-sitter pack missing")


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _messages(findings) -> list[str]:
    return [f.message for f in findings]


# --- IR core ---------------------------------------------------------------


def test_ir_roundtrip_preserves_every_field(tmp_path):
    ir_path = _write(
        tmp_path,
        "mod.py",
        "def _gone():\n    return 1\n\n\ndef stay():\n    return _gone() if False else 2\n",
    )
    from slop_ir_python import extract_python

    ir = extract_python(ir_path)
    restored = file_ir_from_dict(file_ir_to_dict(ir))
    assert restored == ir
    assert restored.symbols[0].name == "_gone"


def test_ir_file_roundtrip_through_disk(tmp_path):
    """write_ir/read_ir: the slice-2 persistence contract, exercised."""
    from slop_ir import read_ir, write_ir
    from slop_ir_python import extract_python

    source = _write(tmp_path, "mod.py", "class Widget:\n    def ping(self):\n        return 1\n")
    ir = extract_python(source)
    bundle = tmp_path / "mod.ir.json"
    write_ir(ir, bundle)
    restored = read_ir(bundle)
    assert restored == ir
    assert restored.symbols[0].kind == "class"


def test_ir_version_mismatch_raises():
    payload = {"ir_version": IR_VERSION + 1, "path": "x.py"}
    with pytest.raises(ValueError, match="IR version mismatch"):
        file_ir_from_dict(payload)


def test_language_table_is_the_only_coverage_claim():
    assert language_of(Path("a.py")) == "python"
    assert language_of(Path("a.ts")) == "typescript"
    assert language_of(Path("a.md")) is None
    assert language_of(Path("a.java")) is None  # native engine pending, never claimed


# --- python extraction -----------------------------------------------------


def test_python_private_symbol_flagged_when_only_comment_and_string_mention(tmp_path):
    _write(
        tmp_path,
        "mod.py",
        (
            "def _helper_used():\n"
            "    return 1\n"
            "\n"
            "def _helper_dead():\n"
            "    return 2\n"
            "\n"
            "x = _helper_used()\n"
            "# _helper_dead is mentioned only here\n"
            "note = 'and _helper_dead only inside a string'\n"
        ),
    )
    findings = check_semantic_dead_symbols(tmp_path, sorted(tmp_path.rglob("*.py")), [])
    assert any("_helper_dead" in m for m in _messages(findings))
    assert not any("_helper_used" in m for m in _messages(findings))


def test_python_findings_are_advisory_and_capped(tmp_path):
    for i in range(MAX_SEMANTIC_DEAD + 3):
        _write(tmp_path, f"dead_{i}.py", f"def _dead_{i}():\n    return {i}\n")
    files = sorted(tmp_path.rglob("*.py"))
    findings = check_semantic_dead_symbols(tmp_path, files, [])
    assert len(findings) == MAX_SEMANTIC_DEAD
    assert all(f.advisory and f.check == "semantic-dead-symbol" for f in findings)


def test_python_public_and_all_exported_symbols_are_not_candidates(tmp_path):
    _write(
        tmp_path,
        "mod.py",
        (
            "__all__ = ['listed_but_private']\n"
            "\n"
            "\n"
            "def listed_but_private():\n"
            "    return 1\n"
            "\n"
            "\n"
            "def public_never_called():\n"
            "    return 2\n"
        ),
    )
    findings = check_semantic_dead_symbols(tmp_path, [tmp_path / "mod.py"], [])
    assert findings == []  # public names belong to the textual oracle, not this one


def test_python_dunder_methods_are_exempt(tmp_path):
    _write(
        tmp_path,
        "mod.py",
        "class Widget:\n    def __init__(self):\n        self.x = 1\n",
    )
    assert check_semantic_dead_symbols(tmp_path, [tmp_path / "mod.py"], []) == []


def test_dataclass_protocol_methods_are_exempt(tmp_path):
    """__post_init__ is interpreter-invoked; the repo's own scan caught it."""
    _write(
        tmp_path,
        "mod.py",
        (
            "from dataclasses import dataclass\n"
            "\n"
            "\n"
            "@dataclass\n"
            "class Row:\n"
            "    x: int\n"
            "\n"
            "    def __post_init__(self):\n"
            "        self.x = int(self.x)\n"
        ),
    )
    assert check_semantic_dead_symbols(tmp_path, [tmp_path / "mod.py"], []) == []


def test_short_names_are_the_textual_oracles_jurisdiction(tmp_path):
    """Single letters collide with fixtures and math vars - never flagged."""
    _write(tmp_path, "a.js", "function f(x) {\n  return x + 1;\n}\n")
    assert check_semantic_dead_symbols(tmp_path, [tmp_path / "a.js"], []) == []


def test_test_files_are_exempt_as_candidates_but_still_count_as_users(tmp_path):
    _write(tmp_path, "pkg/engine.py", "def _live():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    _write(tmp_path, "tests/test_engine.py", "from pkg.engine import _live\n_live()\n")
    files = sorted(p for p in tmp_path.rglob("*.py"))
    assert check_semantic_dead_symbols(tmp_path, files, []) == []


# --- tree-sitter families --------------------------------------------------


@needs_pack
def test_typescript_private_function_flagged_and_exported_survives(tmp_path):
    _write(
        tmp_path,
        "a.ts",
        (
            "export function usedOne(): number { return 1; }\n"
            "function deadPrivate(): number { return 2; }\n"
            "const out = usedOne();\n"
            "console.log(out);\n"
        ),
    )
    findings = check_semantic_dead_symbols(tmp_path, [tmp_path / "a.ts"], [])
    assert any("deadPrivate" in m for m in _messages(findings))
    assert not any("usedOne" in m for m in _messages(findings))


@needs_pack
def test_go_lowercase_dead_flagged_but_init_and_main_exempt(tmp_path):
    _write(
        tmp_path,
        "b.go",
        (
            "package widgets\n\n"
            'import "fmt"\n\n'
            'func ExportedHelper() { fmt.Println("x") }\n\n'
            'func deadHelper() { fmt.Println("y") }\n\n'
            "func init() { ExportedHelper() }\n"
        ),
    )
    findings = check_semantic_dead_symbols(tmp_path, [tmp_path / "b.go"], [])
    assert any("deadHelper" in m for m in _messages(findings))
    assert not any("'init'" in m for m in _messages(findings))


# --- honesty: parse failures surface ---------------------------------------


def test_parse_failure_is_recorded_not_silent(tmp_path):
    _write(tmp_path, "broken.py", "def _oops(:\n    pass\n")
    graph = build_symbol_graph(tmp_path, [tmp_path / "broken.py"], [])
    assert graph.parse_failures and "SyntaxError" in graph.parse_failures[0][1]
    assert check_semantic_dead_symbols(tmp_path, [tmp_path / "broken.py"], []) == []


def test_ir_parse_error_state_roundtrips(tmp_path):
    ir_path = _write(tmp_path, "broken.py", "def _oops(:\n")
    from slop_ir_python import extract_python

    ir = extract_python(ir_path)
    assert not ir.parse_ok
    restored = file_ir_from_dict(file_ir_to_dict(ir))
    assert restored.parse_error == ir.parse_error


# --- the real clean corpus stays clean -------------------------------------


@pytest.mark.parametrize(
    "corpus",
    ["clean_python", "clean_js", "clean_cpp", "clean_csharp"],
)
def test_accuracy_clean_corpus_stays_clean(corpus):
    root = Path(__file__).resolve().parent.parent / "benchmarks" / "accuracy" / corpus
    if not root.is_dir():
        pytest.skip(f"corpus {corpus} not present")
    files = [p for p in sorted(root.rglob("*")) if p.is_file()]
    findings = check_semantic_dead_symbols(root, files, [])
    assert findings == []


def test_regression_fixture_flags_comment_kept_alive_symbol():
    """The shared flawed fixture: dead private symbol kept alive by prose."""
    root = Path(__file__).resolve().parent.parent / "benchmarks" / "accuracy" / "flawed_python"
    if not root.is_dir():
        pytest.skip("flawed_python fixture not present")
    findings = check_semantic_dead_symbols(root, sorted(root.rglob("*.py")), [])
    assert any("_audit_helper_only" in m for m in _messages(findings))


# --- graph persistence (roadmap B25 slice 2) --------------------------------


def test_graph_save_load_roundtrip_preserves_deadness(tmp_path):
    """The loaded graph must accuse exactly what the built graph accused."""
    src = _write(
        tmp_path,
        "deadmod.py",
        "def _audit_helper_only():\n    return 1\n\n\ndef _used_elsewhere():\n    return 2\n",
    )
    caller = _write(
        tmp_path, "caller.py", "from deadmod import _used_elsewhere\n\n_used_elsewhere()\n"
    )
    files = [src, caller]
    globs: list[str] = []
    built = build_symbol_graph(tmp_path, files, globs)
    bundle = tmp_path / "graph.json"
    save_graph(built, bundle)
    loaded = load_graph(bundle)
    assert check_semantic_dead_symbols(tmp_path, files, globs) == check_semantic_dead_symbols(
        tmp_path, files, globs
    )  # sanity: the oracle is deterministic on the sources
    assert _dead_names(built, tmp_path) == _dead_names(loaded, tmp_path)
    assert dict(loaded.use_counts) == dict(built.use_counts)
    assert loaded.parse_failures == built.parse_failures


def _dead_names(graph, root: Path) -> list[tuple[str, int, str]]:
    return [(ir.path, sym.line, sym.name) for ir, sym in _dead_candidates(graph, root)]


def test_graph_bundle_carries_both_version_stamps(tmp_path):
    src = _write(tmp_path, "m.py", "def f():\n    return 1\n")
    graph = build_symbol_graph(tmp_path, [src], [])
    bundle = tmp_path / "graph.json"
    save_graph(graph, bundle)
    data = json.loads(bundle.read_text(encoding="utf-8"))
    assert data["graph_schema"] == GRAPH_SCHEMA_VERSION
    assert data["ir_version"] == IR_VERSION


def test_graph_bundle_refuses_a_foreign_schema(tmp_path):
    src = _write(tmp_path, "m.py", "def f():\n    return 1\n")
    bundle = tmp_path / "graph.json"
    save_graph(build_symbol_graph(tmp_path, [src], []), bundle)
    data = json.loads(bundle.read_text(encoding="utf-8"))
    data["graph_schema"] = GRAPH_SCHEMA_VERSION + 1
    bundle.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="graph schema mismatch"):
        load_graph(bundle)


def test_graph_bundle_refuses_a_foreign_ir_version(tmp_path):
    src = _write(tmp_path, "m.py", "def f():\n    return 1\n")
    bundle = tmp_path / "graph.json"
    save_graph(build_symbol_graph(tmp_path, [src], []), bundle)
    data = json.loads(bundle.read_text(encoding="utf-8"))
    data["ir_version"] = IR_VERSION + 1
    bundle.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="IR version mismatch"):
        load_graph(bundle)


def test_parse_failures_survive_the_roundtrip(tmp_path):
    broken = _write(tmp_path, "broken.py", "def f(:\n    return\n")
    graph = build_symbol_graph(tmp_path, [broken], [])
    assert graph.parse_failures  # the failure was recorded at build time
    bundle = tmp_path / "graph.json"
    save_graph(graph, bundle)
    loaded = load_graph(bundle)
    assert loaded.parse_failures == graph.parse_failures
