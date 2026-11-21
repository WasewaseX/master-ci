"""Contract tests for the round-19 seventh greptile pass.

Twenty findings were re-listed on the PR; thirteen were already closed
by earlier passes and verified finding-by-finding against this tree.
Seven were real; this file pins the six code fixes (the seventh, the
push-branch test's own docstring and unchanged comparison, is pinned by
test_workflow_contracts itself):

- the js manifest walk never re-enters a visited directory and pays
    its `MAX_WALKED_DIRS` budget, so linked folders cannot stall a scan;
- `_split_entries` never descends a directory symlink;
- the JSONC read takes tsconfig's trailing commas and keeps a comma
    inside a string value;
- the undeclared-import scan reads a comment-blanked, string-kept,
    regex-body-blanked copy, so a commented-out or documented import and
    a specifier quoted inside a longer literal import nothing;
- the set-state arrow exclusion ends where the arrow's body ends, so
    a later synchronous update is still the finding (the use-mobile
    listener shape);
- the effect's first-argument scan counts every bracket kind, so the
    comma inside a body's `[1, 2]` is not an argument separator;
- the span mask consumes a regex literal whole - the apostrophe in
    `/'/g` cannot blank the rest of the file.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import hits, scan  # noqa: E402
from slop_js_manifests import (  # noqa: E402
    MAX_WALKED_DIRS,
    _bounded_named_files,
    collect_js_alias_prefixes,
    collect_js_dependencies,
)
from slop_js_shapes import check_no_new_array, check_set_state_in_effect  # noqa: E402
from slop_span_mask import mask_span_forms  # noqa: E402

LINE = Path("x.ts")


def undeclared(tmp_path, files):
    return hits(scan(tmp_path, files), "undeclared-import")


def flags(code: str) -> list[int]:
    return [f.line for f in check_set_state_in_effect(LINE, code, "js", "x.tsx")]


# --- finding 1: the manifest walk's cycle and budget -------------------------


def test_the_manifest_walk_never_reenters_a_visited_directory(tmp_path, monkeypatch):
    """A link back to a parent must not revisit the subtree once per path."""
    import slop_js_manifests as m

    (tmp_path / "a").mkdir()
    (tmp_path / "package.json").write_text('{"name": "x"}')
    original = m._split_entries

    def cyclic(current, names):
        if current.name == "a":
            return [tmp_path], []  # the link hands the walk its own parent
        return original(current, names)

    monkeypatch.setattr(m, "_split_entries", cyclic)
    assert collect_js_dependencies(tmp_path) == {"x"}


def test_the_manifest_walk_pays_its_directory_budget(tmp_path, monkeypatch):
    """An endless descendant generator stops at MAX_WALKED_DIRS, not never."""
    import slop_js_manifests as m

    (tmp_path / "a").mkdir()
    original = m._split_entries
    seen: list[str] = []

    def endless(current, names):
        seen.append(str(current))
        if current.name == "a":
            kids = [tmp_path / f"n{i}" for i in range(20)]
            for kid in kids:
                kid.mkdir(exist_ok=True)
            return kids, []
        return original(current, names)

    monkeypatch.setattr(m, "_split_entries", endless)
    # Returns (a hang here is the finding): the budget ends the walk.
    assert _bounded_named_files(tmp_path, frozenset({"package.json"}), 64) == []
    assert len(seen) <= MAX_WALKED_DIRS


def test_the_walk_budget_is_a_real_cap(tmp_path, monkeypatch):
    """The visited count caps the walk even when every dir is distinct."""
    import slop_js_manifests as m

    original = m._split_entries
    visited: list[str] = []

    def counting(current, names):
        visited.append(str(current))
        return original(current, names)

    monkeypatch.setattr(m, "_split_entries", counting)
    _bounded_named_files(tmp_path, frozenset({"package.json"}), 64)
    assert len(visited) <= MAX_WALKED_DIRS


# --- finding 12: trailing commas in the JSONC read ---------------------------


def test_trailing_commas_in_a_tsconfig_parse(tmp_path):
    (tmp_path / "tsconfig.json").write_text(
        '{\n  /* c */\n  "compilerOptions": {\n    "paths": {\n'
        '      "@app/*": ["./src/*"],\n    },\n  },\n}\n'
    )
    assert "@app" in collect_js_alias_prefixes(tmp_path)


def test_a_comma_inside_a_string_value_stays(tmp_path):
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"paths": {"@a/*": ["./x,}"],}}}')
    assert collect_js_alias_prefixes(tmp_path) == {"@a"}


def test_a_config_that_still_fails_to_parse_contributes_nothing(tmp_path):
    (tmp_path / "tsconfig.json").write_text('{"compilerOptions": {"paths": {"@a/*": [}}}}')
    assert collect_js_alias_prefixes(tmp_path) == set()


# --- finding 13: comments, strings and regex bodies are not imports ----------


def test_commented_out_imports_import_nothing(tmp_path):
    files = {
        "package.json": '{"dependencies": {"react": "19"}}',
        "a.ts": "// export { foo } from 'ghost-package'\n/* import x from 'ghost2' */\n",
    }
    assert undeclared(tmp_path, files) == []


def test_a_string_or_template_example_is_not_a_declaration(tmp_path):
    files = {
        "package.json": '{"dependencies": {"react": "19"}}',
        "a.ts": (
            "const doc = \"import x from 'ghost3'\";\n"
            "const tpl = `require('ghost4')`;\n"
            "const re = /import x from 'ghost5'/;\n"
        ),
    }
    assert undeclared(tmp_path, files) == []


def test_real_imports_still_report_and_lines_are_real(tmp_path):
    files = {
        "package.json": '{"dependencies": {"react": "19"}}',
        "a.ts": "const a = 1;\n// ghost mention\nimport { b } from 'ghost6';\n",
    }
    rows = undeclared(tmp_path, files)
    assert len(rows) == 1 and rows[0].line == 3 and "ghost6" in rows[0].message


# --- finding 14: the arrow exclusion ends with the arrow's body --------------


def test_a_nested_callback_does_not_mute_a_later_synchronous_set(tmp_path):
    code = (
        "function C() {\n"
        "  useEffect(() => {\n"
        "    load().then(d => setData(d));\n"
        "    setReady(true);\n"
        "  }, []);\n"
        "}\n"
    )
    assert flags(code) == [4]


def test_the_use_mobile_listener_shape_reports(tmp_path):
    code = (
        "function useIsMobile() {\n"
        "  useEffect(() => {\n"
        "    const mql = window.matchMedia('(max-width: 768px)');\n"
        "    const onChange = () => {\n"
        "      setIsMobile(window.innerWidth < 768);\n"
        "    };\n"
        "    mql.addEventListener('change', onChange);\n"
        "    setIsMobile(window.innerWidth < 768);\n"
        "    return () => mql.removeEventListener('change', onChange);\n"
        "  }, []);\n"
        "}\n"
    )
    assert flags(code) == [8]


def test_a_concise_arrow_body_ends_at_its_semicolon_or_line_end(tmp_path):
    for code, want in (
        (
            "function C() {\n"
            "  useEffect(() => {\n"
            "    const f = () => setX(1);\n"
            "    setReady(true);\n"
            "  }, []);\n"
            "}\n",
            [4],
        ),
        (
            "function C() {\n"
            "  useEffect(() => {\n"
            "    const f = () => setX(1)\n"
            "    setReady(true)\n"
            "  }, []);\n"
            "}\n",
            [4],
        ),
    ):
        assert flags(code) == want, code


def test_a_block_arrow_body_ends_at_its_closing_brace(tmp_path):
    code = (
        "function C() {\n"
        "  useEffect(() => {\n"
        "    const f = () => { setData(1); };\n"
        "    setReady(true);\n"
        "  }, []);\n"
        "}\n"
    )
    assert flags(code) == [4]


def test_the_quiet_direction_still_holds_behind_a_then(tmp_path):
    code = "function C() {\n  useEffect(() => {\n    load().then(d => setData(d));\n  }, []);\n}\n"
    assert flags(code) == []


# --- finding 15: every bracket kind counts in the first argument -------------


def test_array_literal_commas_are_not_argument_separators(tmp_path):
    code = (
        "function C() {\n"
        "  useEffect(() => {\n"
        "    const pair = [1, 2];\n"
        "    setReady(true);\n"
        "  }, []);\n"
        "}\n"
    )
    assert flags(code) == [4]


def test_object_literal_commas_are_not_argument_separators(tmp_path):
    code = (
        "function C() {\n"
        "  useEffect(() => {\n"
        "    const opts = { a: 1, b: 2 };\n"
        "    setReady(true);\n"
        "  }, []);\n"
        "}\n"
    )
    assert flags(code) == [4]


def test_the_deps_array_comma_still_ends_the_first_argument(tmp_path):
    code = "function C() {\n  useEffect(() => {\n    setX(1);\n  }, [a, b]);\n}\n"
    assert flags(code) == [3]


# --- finding 16: the mask consumes a regex literal whole ---------------------


def test_a_regex_quote_cannot_blank_the_rest_of_the_file(tmp_path):
    code = "const apostrophe = /'/g;\nconst slots = new Array(5);\n"
    rows = check_no_new_array(LINE, code, "js", "x.ts")
    assert len(rows) == 1 and rows[0].line == 2


def test_division_stays_division_in_the_mask(tmp_path):
    masked = mask_span_forms("const half = total / 2;")
    assert "total / 2" in masked


def test_an_unterminated_slash_falls_back_to_division(tmp_path):
    raw = "const odd = a /\n  b;"
    masked = mask_span_forms(raw)
    assert len(masked) == len(raw) and "b;" in masked
