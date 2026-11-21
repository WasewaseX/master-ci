"""Contract tests for the round-19 lint-shape gates (capability adoption).

The user's report was concrete: a real app's scan left a bare ``fs``
use, a self-assignment, an empty ``export {}``, impure render calls,
anchored-prefix regexes and unjustified escapes standing while external
linters caught every one. This round gives the engine its own oracles
for those shapes, advisory by registry, quiet by construction. The
documented contracts, test-pinned:

- bare-node-builtin: a node builtin module used as a bare name nothing
    imports reports; the same file with the import, a local binding,
    an ``as`` rename or a parameter of the same name stays silent, and
    globals that need no import (``console``, ``Math``, ``Date``) are
    not builtins here;
- self-assign: ``x = x``, ``a.b = a.b`` and ``const x = x`` report;
    ``==``, ``=>``, ``+=`` and a right-hand side that continues
    (``x = x + 1``) stay silent, as does anything inside a string or
    comment;
- useless-empty-export: ``export {}`` in a file that already imports
    or exports reports; the empty export that is the one module mark
    stays silent;
- impure-render-call: ``Math.random()``/``Date.now()``/``new Date()``
    at a JSX-returning component's own statement depth report; the
    same calls inside callbacks, effects, memos, handlers or deeper
    braces stay silent, and non-component functions stay silent;
- prefer-starts-ends-with: a plain anchored literal used by
    test/match/exec/search reports the string-method suggestion; a
    body with metacharacters, case-insensitive flags or no testing
    use stays silent;
- useless-escape: ``\\"`` in single quotes, ``\\'`` in double quotes,
    an escaped backtick in either and ``\\/`` anywhere report; the
    escapes a string needs (``\\n``, ``\\'`` in single quotes, ``\\\\``)
    stay silent, and templates and regex bodies stay out whole.
"""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod, hits, scan  # noqa: E402
from slop_lint_shapes import (  # noqa: E402
    check_bare_node_builtin,
    check_impure_render_call,
    check_prefer_starts_ends_with,
    check_self_assign,
    check_useless_empty_export,
    check_useless_escape,
)

NEW_CHECKS = (
    "bare-node-builtin",
    "self-assign",
    "useless-empty-export",
    "impure-render-call",
    "prefer-starts-ends-with",
    "useless-escape",
)

LINE = Path("x.ts")


def lint(findings, check):
    return hits(findings, check)


# --- the registry holds the new checks --------------------------------------


def test_new_lint_checks_are_registered_advisory():
    for check in NEW_CHECKS:
        assert check in mod.REMEDIES, f"missing remedy for {check}"
        assert check in mod.CHECK_CATEGORIES
        assert check in mod.ADVISORY_ONLY_CHECKS


def test_the_lint_checks_ride_the_full_scan(tmp_path):
    findings = scan(
        tmp_path,
        {
            "a.ts": "fs.writeFileSync(p, data);\nconst n = Date.now();\n",
        },
    )
    checks = {f.check for f in findings}
    assert "bare-node-builtin" in checks


# --- bare-node-builtin -------------------------------------------------------


def test_bare_fs_use_without_an_import_reports(tmp_path):
    """The user's shape: a script calling fs methods it never imports."""
    code = "const data = fs.readFileSync(path);\nexport {}\n"
    findings = check_bare_node_builtin(LINE, code, "js", "x.ts")
    assert len(findings) == 1
    assert findings[0].line == 1
    assert "'node:fs'" in findings[0].message


def test_an_import_or_a_local_binding_silences_the_builtin(tmp_path):
    for code in (
        "import fs from 'node:fs';\nfs.readFileSync(p);\n",
        "import { readFileSync } from 'fs';\nfs.readFileSync(p);\n",
        "const fs = require('fs');\nfs.readFileSync(p);\n",
        "import * as fs from 'fs';\nfs.readFileSync(p);\n",
        "function handle(fs) {\n  fs.readFileSync(p);\n}\n",
        "let fs;\nfs.readFileSync(p);\n",
    ):
        assert check_bare_node_builtin(LINE, code, "js", "x.ts") == [], code


def test_globals_that_need_no_import_are_not_builtins(tmp_path):
    code = (
        "const stamp = Date.now();\nconst win = Math.floor(Math.random() * 10);\n"
        "console.log(process.cwd(), Buffer.byteLength(b));\n"
    )
    assert check_bare_node_builtin(LINE, code, "js", "x.ts") == []


def test_a_property_receiver_or_string_mention_is_not_a_bare_use(tmp_path):
    code = "config.fs.readFile(p);\nconst doc = 'call fs.readFile here';\n"
    assert check_bare_node_builtin(LINE, code, "js", "x.ts") == []


def test_builtin_check_is_js_only(tmp_path):
    assert check_bare_node_builtin(LINE, "fs.readFileSync(p)\n", "py", "x.py") == []


# --- self-assign -------------------------------------------------------------


def test_self_assignments_report(tmp_path):
    findings = check_self_assign(LINE, "value = value;\nthis.name = this.name;\n", "js", "x.ts")
    assert len(findings) == 2
    assert findings[0].line == 1 and findings[1].line == 2


def test_const_self_declaration_reports(tmp_path):
    findings = check_self_assign(LINE, "const retry = retry;\n", "js", "x.ts")
    assert len(findings) == 1


def test_comparisons_chains_and_arithmetic_are_not_self_assignments(tmp_path):
    for code in (
        "if (a == a) {}\n",
        "if (a === a) {}\n",
        "const f = (x) => x;\n",
        "a += a;\n",
        "x = x + 1;\n",
        "const t = flag ? x : y;\n",
        "obj['k'] = obj['k'];\n",
    ):
        assert check_self_assign(LINE, code, "js", "x.ts") == [], code


def test_self_assign_inside_a_string_is_not_code(tmp_path):
    code = "const doc = 'value = value is pointless';\n"
    assert check_self_assign(LINE, code, "js", "x.ts") == []


# --- useless-empty-export ----------------------------------------------------


def test_empty_export_in_a_module_reports(tmp_path):
    for code in (
        "import { a } from './a.js';\nexport {}\n",
        "export const done = true;\nexport {}\n",
    ):
        findings = check_useless_empty_export(LINE, code, "js", "x.ts")
        assert len(findings) == 1, code


def test_the_module_marking_empty_export_stays_silent(tmp_path):
    code = "export {}\n"
    assert check_useless_empty_export(LINE, code, "js", "x.ts") == []


# --- impure-render-call ------------------------------------------------------


def test_impure_calls_at_render_depth_report(tmp_path):
    code = (
        "export function Today() {\n"
        "  const start = Date.now();\n"
        "  const roll = Math.random();\n"
        "  const now = new Date();\n"
        "  return <p>{start}</p>;\n"
        "}\n"
    )
    findings = check_impure_render_call(LINE, code, "js", "x.tsx", ".tsx")
    assert [f.line for f in findings] == [2, 3, 4]


def test_arrow_components_are_read_too(tmp_path):
    code = (
        "export const Stamp = () => {\n"
        "  const now = new Date();\n"
        "  return <span>{now}</span>;\n"
        "};\n"
    )
    findings = check_impure_render_call(LINE, code, "js", "x.jsx", ".jsx")
    assert [f.line for f in findings] == [2]


def test_calls_behind_callbacks_effects_and_deeper_braces_stay_silent(tmp_path):
    code = (
        "export function Panel() {\n"
        "  useEffect(() => {\n"
        "    const t = Date.now();\n"
        "  }, []);\n"
        "  const onClick = () => {\n"
        "    const roll = Math.random();\n"
        "  };\n"
        "  const cached = useMemo(() => crypto.randomUUID(), []);\n"
        "  if (ready) {\n"
        "    const then = Date.now();\n"
        "  }\n"
        "  const [state, setState] = useState(new Date());\n"
        "  return <p>{state}</p>;\n"
        "}\n"
    )
    assert check_impure_render_call(LINE, code, "js", "x.tsx", ".tsx") == []


def test_non_components_and_non_jsx_bodies_stay_silent(tmp_path):
    for code in (
        "function formatDate(d) {\n  return new Date(d);\n}\n",
        "export function nowMs() {\n  return Date.now();\n}\n",
        "export function Box() {\n  const stamp = Date.now();\n  return stamp;\n}\n",
    ):
        assert check_impure_render_call(LINE, code, "js", "x.tsx", ".tsx") == [], code


def test_purity_check_is_tsx_jsx_only(tmp_path):
    code = "function Now() {\n  return Date.now();\n}\n"
    assert check_impure_render_call(LINE, code, "js", "x.ts", ".ts") == []


# --- prefer-starts-ends-with -------------------------------------------------


def test_anchored_plain_regexes_used_for_testing_report(tmp_path):
    code = "if (/^GET/.test(method)) {\n}\nconst tail = name.match(/app$/);\nre.exec(/^abc/);\n"
    findings = check_prefer_starts_ends_with(LINE, code, "js", "x.ts")
    assert [f.message.split("(")[0] for f in findings] == [
        "prefer startsWith",
        "prefer endsWith",
        "prefer startsWith",
    ]


def test_receiver_position_is_read_too(tmp_path):
    code = "/^Bearer/.test(header);\n"
    findings = check_prefer_starts_ends_with(LINE, code, "js", "x.ts")
    assert len(findings) == 1


def test_metacharacters_flags_and_unused_literals_stay_silent(tmp_path):
    for code in (
        "if (/^a(b|c)/.test(x)) {}\n",
        "if (/^get/i.test(x)) {}\n",
        "const p = /^abc/;\n",
        "if (/\\^abc/.test(x)) {}\n",
        "const half = total / 2;\n",
    ):
        assert check_prefer_starts_ends_with(LINE, code, "js", "x.ts") == [], code


def test_anchored_regex_in_a_string_is_not_code(tmp_path):
    code = "const doc = 'use /^GET/.test(method) here';\n"
    assert check_prefer_starts_ends_with(LINE, code, "js", "x.ts") == []


# --- useless-escape ----------------------------------------------------------


def test_useless_escapes_report(tmp_path):
    code = (
        r"const a = 'say \"hi\"';" + "\n"
        r'const b = "it\'s";' + "\n"
        r"const c = '\`';" + "\n"
        r"const d = '\/';" + "\n"
    )
    findings = check_useless_escape(LINE, code, "js", "x.ts")
    assert len(findings) == 5


def test_escapes_the_string_needs_stay_silent(tmp_path):
    code = (
        r"const a = 'it\'s';" + "\n"
        r'const b = "say \"hi\"";' + "\n"
        r"const n = '\n';" + "\n"
        r"const s = '\\';" + "\n"
    )
    assert check_useless_escape(LINE, code, "js", "x.ts") == []


def test_templates_and_regex_bodies_stay_out(tmp_path):
    code = r"const t = `a \` and \' b`;" + "\n" + r"const re = /\'/g;" + "\n"
    assert check_useless_escape(LINE, code, "js", "x.ts") == []
