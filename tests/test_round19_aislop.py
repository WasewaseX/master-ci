"""Contract tests for the scanaislop round-19 capability work.

The user-facing scan kept surfacing shapes the engine had no oracle
for, and the undeclared-import check priced resolver features as
packages. This round closes both. The documented contracts, test-pinned:

- undeclared-import trusts every declaration site the resolver trusts:
    the tree's package.json dep sections (workspace member names
    included - a monorepo package is importable by its own name),
    tsconfig/jsconfig ``paths`` aliases (comments in the config are
    real, a ``@app/*`` key covers ``@app/components/x`` but not
    ``@app-data/x``), Node ``#`` subpath imports, ``@/`` and ``~/``
    shorthands, ``node:`` builtins and relative paths;
- a scoped specifier's subpath rides on its package's declaration
    (``@scope/pkg/plugin`` is declared by ``@scope/pkg``);
- a re-export (``export { x } from 'pkg'``, ``export * from 'pkg'``)
    and a dynamic ``import('pkg')`` reach the same undeclared package
    a static import would - all three report;
- the user-facing shape this round started from reports: an
    ``eslint.config.mjs`` importing ``eslint-plugin-import`` that no
    dependency section names;
- no-new-array: the single-argument ``new Array(x)`` reports; the
    zero- and two-argument calls and a nested-call argument stay
    silent, a string or comment mentioning the shape stays silent;
- set-state-in-effect: a synchronous setState in an effect body
    reports - block and concise bodies, ``useLayoutEffect`` too, two
    statements in one body each report; a call behind a ``then``, a
    timer, a listener arrow, a method receiver (``store.setState``,
    ``el.setAttribute``) or deeper braces stays silent, and the timer
    trio the setter shape matches are exempt by name.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod, hits, scan  # noqa: E402
from slop_js_shapes import (  # noqa: E402
    check_no_new_array,
    check_set_state_in_effect,
)


def undeclared(findings):
    return hits(findings, "undeclared-import")


# --- undeclared-import: the resolver's own declaration sites ---------------


def test_eslint_config_importing_an_undeclared_plugin_reports(tmp_path):
    """The shape the round started from: a config file's plugin import."""
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}, "devDependencies": {"eslint": "9"}}',
            "eslint.config.mjs": (
                'import importPlugin from "eslint-plugin-import";\nexport default [importPlugin];\n'
            ),
        },
    )
    found = undeclared(findings)
    assert found and "eslint-plugin-import" in found[0].message


def test_scoped_specifier_subpath_rides_on_the_package_declaration(tmp_path):
    """``@scope/pkg/plugin`` is declared by ``@scope/pkg`` - not a finding."""
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"@scope/pkg": "1"}}',
            "a.js": 'import plugin from "@scope/pkg/plugin";\nexport const p = plugin;\n',
        },
    )
    assert not undeclared(findings)


def test_scoped_specifier_without_the_package_reports(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"@scope/other": "1"}}',
            "a.js": 'import plugin from "@scope/pkg/plugin";\nexport const p = plugin;\n',
        },
    )
    found = undeclared(findings)
    assert found and "@scope/pkg" in found[0].message


def test_tsconfig_paths_alias_is_not_an_npm_package(tmp_path):
    """A ``@app/*`` alias renames the project's own modules, not packages."""
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}}',
            "tsconfig.json": (
                "{\n"
                "  // the app's module layout\n"
                '  "compilerOptions": { "paths": { "@app/*": ["./src/*"] } }\n'
                "}"
            ),
            "a.tsx": 'import { btn } from "@app/components/ui/button";\nexport const x = btn;\n',
        },
    )
    assert not undeclared(findings)


def test_exact_paths_key_quiets_its_own_specifier(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}}',
            "jsconfig.json": '{"compilerOptions": {"paths": {"$lib": ["src/lib"]}}}',
            "a.js": 'import { h } from "$lib/heading";\nexport const x = h;\n',
        },
    )
    assert not undeclared(findings)


def test_alias_prefix_matches_at_a_segment_boundary(tmp_path):
    """``@app`` covers ``@app/x``; ``@app-data`` is a different package."""
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}}',
            "tsconfig.json": '{"compilerOptions": {"paths": {"@app/*": ["./src/*"]}}}',
            "a.js": 'import date from "@app-data/format";\nexport const x = date;\n',
        },
    )
    found = undeclared(findings)
    assert found and "@app-data/format" in found[0].message


def test_at_slash_and_tilde_shorthands_are_never_packages(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}}',
            "a.tsx": (
                'import { btn } from "@/components/ui/button";\n'
                'import { a } from "~/lib/a";\n'
                "export const x = btn || a;\n"
            ),
        },
    )
    assert not undeclared(findings)


def test_hash_subpath_import_is_a_resolver_feature(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}, "imports": {"#app/config": "./cfg.js"}}',
            "a.mjs": 'import cfg from "#app/config";\nexport const x = cfg;\n',
        },
    )
    assert not undeclared(findings)


def test_workspace_member_is_importable_by_its_own_name(tmp_path):
    """A monorepo package is a declaration site, not a hallucinated import."""
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}, "workspaces": ["packages/*"]}',
            "packages/inner/package.json": '{"name": "@scope/inner", "dependencies": {"zod": "3"}}',
            "a.tsx": 'import { x } from "@scope/inner";\nimport { z } from "zod";\nexport const both = [x, z];\n',
        },
    )
    assert not undeclared(findings)


def test_reexport_and_dynamic_import_name_undeclared_packages(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}}',
            "a.js": (
                'export { thing } from "undeclared-pkg";\n'
                'export * from "other-undeclared";\n'
                'export const lazy = () => import("third-undeclared");\n'
            ),
        },
    )
    messages = " | ".join(f.message for f in undeclared(findings))
    assert "undeclared-pkg" in messages
    assert "other-undeclared" in messages
    assert "third-undeclared" in messages


def test_declared_and_builtin_stay_silent(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"left-pad": "1"}}',
            "a.js": (
                'import pad from "left-pad";\n'
                'import fs from "fs";\n'
                'import nodePath from "node:path";\n'
                'import rel from "./relative";\n'
                "export const x = [pad, fs, nodePath, rel];\n"
            ),
        },
    )
    assert not undeclared(findings)


# --- no-new-array: the length trap ------------------------------------------


def test_single_argument_new_array_reports(tmp_path):
    path = tmp_path / "a.ts"
    text = "const slots = new Array(5);\n"
    path.write_text(text, encoding="utf-8")
    hits = check_no_new_array(path, text, "js", "a.ts")
    assert len(hits) == 1
    assert hits[0].line == 1
    assert hits[0].advisory


def test_string_argument_new_array_reports(tmp_path):
    """``new Array('5')`` builds one element - exactly the trap."""
    path = tmp_path / "a.ts"
    text = "const slots = new Array('5');\n"
    path.write_text(text, encoding="utf-8")
    assert len(check_no_new_array(path, text, "js", "a.ts")) == 1


def test_zero_two_and_nested_call_arguments_stay_silent(tmp_path):
    path = tmp_path / "a.ts"
    clean = (
        "const empty = new Array();\n"
        "const pair = new Array(1, 2);\n"
        "const cells = new Array(rows.map((r) => r.size));\n"
    )
    path.write_text(clean, encoding="utf-8")
    assert check_no_new_array(path, clean, "js", "a.ts") == []


def test_shape_inside_a_string_or_comment_is_not_code(tmp_path):
    path = tmp_path / "a.ts"
    text = 'const doc = "use new Array(5) here";\n// the old code used new Array(5)\n'
    path.write_text(text, encoding="utf-8")
    assert check_no_new_array(path, text, "js", "a.ts") == []


def test_non_js_family_stays_silent(tmp_path):
    path = tmp_path / "a.py"
    text = "x = new Array(5)\n"
    path.write_text(text, encoding="utf-8")
    assert check_no_new_array(path, text, "py", "a.py") == []


# --- set-state-in-effect: the cascading-render shape ------------------------


def test_synchronous_setstate_in_a_block_body_reports(tmp_path):
    path = tmp_path / "t.tsx"
    text = "useEffect(() => {\n  setReady(true);\n}, []);\n"
    path.write_text(text, encoding="utf-8")
    hits = check_set_state_in_effect(path, text, "js", "t.tsx")
    assert len(hits) == 1
    assert hits[0].line == 2
    assert hits[0].advisory


def test_concise_body_and_useLayoutEffect_report(tmp_path):
    path = tmp_path / "t.tsx"
    text = (
        "useEffect(() => setReady(true), []);\nuseLayoutEffect(() => {\n  setOpen(id);\n}, [id]);\n"
    )
    path.write_text(text, encoding="utf-8")
    hits = check_set_state_in_effect(path, text, "js", "t.tsx")
    assert [h.line for h in hits] == [1, 3]


def test_two_statements_in_one_body_each_report(tmp_path):
    path = tmp_path / "t.tsx"
    text = "useEffect(() => {\n  setA(1);\n  setB(2);\n}, []);\n"
    path.write_text(text, encoding="utf-8")
    assert [h.line for h in check_set_state_in_effect(path, text, "js", "t.tsx")] == [2, 3]


def test_calls_behind_a_callback_or_a_receiver_stay_silent(tmp_path):
    """A ``then``, a timer, a listener arrow, a method: not render-time."""
    path = tmp_path / "t.tsx"
    text = (
        "useEffect(() => {\n"
        "  load().then((d) => setData(d));\n"
        "  const t = setTimeout(() => setVisible(false), 300);\n"
        "  const id = setInterval(() => tick(), 1000);\n"
        "  const onKey = (e) => setKey(e.key);\n"
        "  window.addEventListener('keydown', onKey);\n"
        "  store.setState({ ready: true });\n"
        "  ref.current.setAttribute('aria-live', 'polite');\n"
        "  return () => clearTimeout(t);\n"
        "}, []);\n"
    )
    path.write_text(text, encoding="utf-8")
    assert check_set_state_in_effect(path, text, "js", "t.tsx") == []


def test_timer_names_are_exempt_and_deeper_braces_stay_silent(tmp_path):
    path = tmp_path / "t.tsx"
    text = "useEffect(() => {\n  if (ready) {\n    setSteps(config.steps);\n  }\n}, [config]);\n"
    path.write_text(text, encoding="utf-8")
    assert check_set_state_in_effect(path, text, "js", "t.tsx") == []


def test_a_setstate_inside_a_string_is_not_a_call(tmp_path):
    path = tmp_path / "t.tsx"
    text = 'const snippet = "useEffect(() => { setX(1) }, [])";\n'
    path.write_text(text, encoding="utf-8")
    assert check_set_state_in_effect(path, text, "js", "t.tsx") == []


def test_python_family_stays_silent(tmp_path):
    path = tmp_path / "a.py"
    text = "useEffect(() => {\n  setX(1)\n}, [])\n"
    path.write_text(text, encoding="utf-8")
    assert check_set_state_in_effect(path, text, "py", "a.py") == []


# --- the registry holds the new checks --------------------------------------


def test_new_checks_are_registered_advisory():
    for check in ("no-new-array", "set-state-in-effect"):
        assert check in mod.REMEDIES, f"missing remedy for {check}"
        assert check in mod.CHECK_CATEGORIES
        assert check in mod.ADVISORY_ONLY_CHECKS


def test_the_checks_ride_the_full_scan(tmp_path):
    findings = scan(
        tmp_path,
        {
            "package.json": '{"dependencies": {"react": "19"}}',
            "widget.tsx": (
                "const slots = new Array(5);\n"
                "export function Widget() {\n"
                "  useEffect(() => {\n"
                "    setReady(true);\n"
                "  }, []);\n"
                "  return null;\n"
                "}\n"
            ),
        },
    )
    checks = {f.check for f in findings}
    assert "no-new-array" in checks
    assert "set-state-in-effect" in checks
