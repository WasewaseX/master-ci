"""Regression tests for audit finding A (2026-11-17): binding-aware JS phantom imports.

The pre-v2.18 check deleted the import statement and searched for the
PACKAGE NAME in the file's remaining text, so every shape that uses the
imported binding but never spells the package string false-positived -
8 of the 10 repro cases were wrong, including side-effect imports,
which bind nothing and are intentional by definition. The contract
now: an import is a phantom only when NONE of the names it binds
appears outside the file's import and require statements, and a shape
the parser cannot parse confidently is never flagged (a false negative
is the quieter failure direction for an advisory check).
"""

from pathlib import Path

from ai_slop_detector import scan
from slop_files import load_phrases
from slop_imports import check_phantom_imports

USED_SHAPES = [
    (
        "named import, binding used (the audit's own example)",
        'import { calculate } from "math-library";\n\nconst result = calculate(10);\n',
    ),
    ("default import, binding used", 'import fs from "fs-extra";\nfs.copySync(src, dst);\n'),
    (
        "namespace import, binding used",
        'import * as path from "path-extra";\nconsole.log(path.join(a, b));\n',
    ),
    (
        "aliased named import, alias used",
        'import { combineReducers as combine } from "redux-toolkit";\ncombine(reducers);\n',
    ),
    (
        "mixed named list, one binding used",
        'import { map, filter } from "fp-ts";\nconst out = map(f, xs);\n',
    ),
    (
        "CommonJS const require, binding used",
        'const lodash = require("lodash-es");\nconsole.log(lodash.map(x, f));\n',
    ),
    (
        "CommonJS destructured require, binding used",
        'const { build } = require("esbuild");\nbuild(config);\n',
    ),
    (
        "CommonJS destructured require with rename, alias used",
        'const { build: compile } = require("esbuild");\ncompile(cfg);\n',
    ),
    (
        "side-effect import binds nothing (intentional)",
        'import "polyfill-everything";\nstartApp();\n',
    ),
    (
        "side-effect require binds nothing (intentional)",
        'require("dotenv-patch").config();\nmain();\n',
    ),
    (
        "multi-line named import list, binding used",
        'import {\n  prepareAuthToken,\n  refreshSession,\n} from "auth-kit";\nconst t = prepareAuthToken(u);\n',
    ),
    (
        "trailing comma in named list, binding used",
        'import { first, second, } from "pair-box";\nconst x = first(y);\n',
    ),
    (
        "TypeScript import type, type used",
        'import type { Token } from "auth-kit";\nconst t: Token = mk();\n',
    ),
    (
        "TypeScript import-equals require, binding used",
        'import lib = require("legacy-lib");\nlib.run();\n',
    ),
    (
        "default plus named list, both used",
        'import def, { named } from "combo-pack";\nconst a = def(), b = named();\n',
    ),
    (
        "default plus namespace, both used",
        'import def, * as ns from "combo-pack";\nconst a = def(), b = ns.x;\n',
    ),
]

UNUSED_SHAPES = [
    (
        "named import, binding never used",
        'import { calculate } from "math-library";\n\nconsole.log("hello");\n',
    ),
    ("default import, binding never used", 'import fs from "fs-extra";\nconsole.log("hello");\n'),
    (
        "namespace import, binding never used",
        'import * as path from "path-extra";\nconsole.log("hello");\n',
    ),
    (
        "CommonJS const require, binding never used",
        'const lodash = require("lodash-es");\nconsole.log("hello");\n',
    ),
    (
        "multi-line named list, no binding used",
        'import {\n  prepareAuthToken,\n  refreshSession,\n} from "auth-kit";\nconsole.log("hello");\n',
    ),
]


def _flagged(code: str) -> list[str]:
    findings = check_phantom_imports(Path("src.js"), code, "js")
    return [f.message for f in findings if f.check == "phantom-import"]


def test_every_used_binding_shape_is_clean():
    for label, code in USED_SHAPES:
        assert _flagged(code) == [], f"false positive on: {label}"


def test_every_unused_shape_is_flagged():
    for label, code in UNUSED_SHAPES:
        flagged = _flagged(code)
        assert len(flagged) == 1, f"missed true positive on: {label}"
        assert "no binding" in flagged[0]


def test_binding_mention_in_another_import_does_not_count_as_usage():
    code = 'import { calculate } from "lib-one";\nimport { calculate } from "lib-two";\n'
    flagged = _flagged(code)
    # Both imports bind `calculate` and neither binding is used, so both
    # are phantoms: one import statement can never rescue another's.
    assert len(flagged) == 2
    assert "lib-one" in flagged[0] and "lib-two" in flagged[1]


def test_relative_and_node_specs_stay_exempt():
    code = (
        "import { helper } from './lib.js';\n"
        "import { readFileSync } from 'node:fs';\n"
        "import { unused } from '/abs/path.js';\n"
        "console.log('hello');\n"
    )
    assert _flagged(code) == []


def test_exotic_shape_is_never_flagged():
    # A clause the parser does not trust (computed/string key without an
    # alias) must stay silent - false negative beats false positive here.
    code = 'import { "literal-key" } from "weird-lib";\nconsole.log("hello");\n'
    assert _flagged(code) == []


def test_cap_of_five_findings_per_file():
    imports = "".join(f'import {{ sym{i} }} from "pkg-{i}";\n' for i in range(8))
    assert len(_flagged(imports)) == 5


def test_scan_wiring_end_to_end(tmp_path):
    # The detector's js-family wiring picks the fixed engine up through
    # scan() itself, not just the unit-level function call.
    (tmp_path / "app.js").write_text(
        'import { calculate } from "math-library";\nconst result = calculate(10);\n',
        encoding="utf-8",
    )
    findings = scan(tmp_path, load_phrases())
    assert not [f for f in findings if f.check == "phantom-import"]
