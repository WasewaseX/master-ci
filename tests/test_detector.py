"""Contract tests for the AI-integrity detector.

Covers the documented contract in docs/checks.md:
- blocking vs advisory scoring (prose style never blocks),
- ignore-file and CLI glob opt-outs,
- empty and malformed inputs do not crash.
"""

import importlib.util
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ai_slop_detector.py"
spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

PHRASES = mod.load_phrases()


def slop_code() -> str:
    """Build a slop fixture at runtime so this file stays detector-clean."""
    phrases = PHRASES["code_phrases"][:8]
    body = "\n".join(f"// Trigger phrase for detector testing: {p}" for p in phrases)
    return body + "\n\nfunction sample(items) {\n  return items;\n}\n"


SLOP_CODE = slop_code()


def scan(tmp_path, files=None, extra_globs=None):
    for name, text in (files or {}).items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return mod.scan(tmp_path, mod.load_phrases(), extra_globs=extra_globs)


def test_slop_detected(tmp_path):
    findings = scan(tmp_path, {"x.js": SLOP_CODE * 12})
    assert mod.blocking_score(findings) > 0
    checks = {f.check for f in findings}
    assert "ai-vocab" in checks


def test_clean_code(tmp_path):
    findings = scan(tmp_path, {"y.py": "def add(a, b):\n    return a + b\n"})
    assert not findings


def test_empty_project_does_not_crash(tmp_path):
    findings = scan(tmp_path, {})
    assert findings == []
    assert mod.blocking_score(findings) == 0


def test_prose_signals_are_advisory_only(tmp_path):
    prose = (
        "# Title\n"
        "\n"
        "It is important to note that this is a test. Is it not?\n"
        "Let's dive in! Yes! Truly!\n"
        "\n"
        "- one\n"
        "- two\n"
        "- three\n"
        "\n"
        "- alpha\n"
        "- beta\n"
        "- gamma\n"
        "\n"
        "- first\n"
        "- second\n"
        "- third\n"
        "\n"
        "This sentence has an em-dash \u2014 and so \u2014 does \u2014 this \u2014 one.\n"
    )
    findings = scan(tmp_path, {"README.md": prose})
    assert findings, "prose fixture should still produce findings"
    assert all(f.advisory for f in findings), findings
    assert mod.blocking_score(findings) == 0
    assert mod.advisory_score(findings) > 0


def test_slop_code_is_blocking_not_advisory(tmp_path):
    findings = scan(tmp_path, {"x.js": SLOP_CODE * 12})
    blocking = [f for f in findings if not f.advisory]
    assert blocking, "vocabulary hits in code comments must be blocking"
    assert all(f.check in ("ai-vocab", "boilerplate-duplication") for f in blocking), findings


def test_ignore_file_glob_suppresses_paths(tmp_path):
    slop = {"sloppy.js": SLOP_CODE, "kept.js": "const ok = 1;\n"}
    (tmp_path / ".ai-integrity-ignore").write_text("sloppy.js\n", encoding="utf-8")
    findings = scan(tmp_path, slop)
    assert all(f.path != "sloppy.js" for f in findings)


def test_ignore_file_check_disable(tmp_path):
    slop = {"x.js": SLOP_CODE}
    (tmp_path / ".ai-integrity-ignore").write_text("check:ai-vocab\n", encoding="utf-8")
    findings = scan(tmp_path, slop)
    assert all(f.check != "ai-vocab" for f in findings)


def test_cli_ignore_glob(tmp_path):
    findings = scan(tmp_path, {"sub/x.js": SLOP_CODE}, extra_globs=["sub/*"])
    assert not findings


def test_ignore_file_comments_and_blanks(tmp_path):
    slop = {"x.js": SLOP_CODE}
    (tmp_path / ".ai-integrity-ignore").write_text(
        "# a comment\n\n  \nnot-a-check-line-that-matches\n", encoding="utf-8"
    )
    findings = scan(tmp_path, slop)
    assert mod.blocking_score(findings) > 0


def test_exit_code_is_blocking_only(tmp_path):
    prose = "- one\n- two\n- three\n" * 6
    findings = scan(tmp_path, {"README.md": prose})
    assert mod.blocking_score(findings) == 0
    assert mod.main([str(tmp_path), "--max-score", "0"]) == 0


def test_malformed_input_file_is_skipped(tmp_path):
    (tmp_path / "binary.js").write_bytes(b"\xff\xfe\x00binary")
    findings = scan(tmp_path, {})
    assert findings == []


# ---------------------------------------------------------------------------
# Structural slop rules (multi-language)
# ---------------------------------------------------------------------------


def test_elided_code_detected(tmp_path):
    # fixture built at runtime so this file stays detector-clean
    marker = "// ... " + "existing " + "code ..."
    findings = scan(tmp_path, {"a.js": "function f() {\n  " + marker + "\n}\n"})
    assert any(f.check == "elided-code" and not f.advisory for f in findings)


def test_elided_code_clean_file_not_flagged(tmp_path):
    findings = scan(tmp_path, {"a.js": "function f(x) {\n  return x + 1;\n}\n"})
    assert not findings


def test_placeholder_impl_detected(tmp_path):
    code = "// In a " + "real " + "app you would query the database here\nconst x = 1;\n"
    findings = scan(tmp_path, {"a.js": code})
    assert any(f.check == "placeholder-impl" for f in findings)


def test_py_stub_function_detected(tmp_path):
    findings = scan(tmp_path, {"a.py": "def handler(event, ctx):\n    pass\n"})
    checks = {f.check for f in findings}
    assert "stub-function" in checks
    assert all(not f.advisory for f in findings if f.check == "stub-function")


def test_notimplemented_stub_detected_but_abstract_style_flagged_once(tmp_path):
    code = "class Base:\n    def run(self):\n        raise NotImplementedError\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "stub-function" for f in findings)


def test_continue_only_handler_not_swallowed(tmp_path):
    code = (
        "for item in items:\n    try:\n        load(item)\n    except OSError:\n        continue\n"
    )
    findings = scan_py(tmp_path, code)
    assert not [f for f in findings if f.check == "swallowed-exception"]


def test_bare_except_detected(tmp_path):
    code = "try:\n    risky()\nexcept:\n    pass\n"
    findings = scan_py(tmp_path, code)
    checks = {f.check for f in findings}
    assert "bare-except" in checks or "swallowed-exception" in checks


def test_py_empty_test_detected(tmp_path):
    findings = scan(tmp_path, {"test_a.py": "def test_nothing():\n    ready = True\n"})
    assert any(f.check == "empty-test" for f in findings)


def test_py_test_with_assert_clean(tmp_path):
    findings = scan(tmp_path, {"test_a.py": "def test_ok():\n    assert 1 + 1 == 2\n"})
    assert not [f for f in findings if f.check == "empty-test"]


def scan_py(tmp_path, code):
    """Scan one Python snippet - the common shape of the AST-check tests."""
    return scan(tmp_path, {"a.py": code})


def test_py_raises_context_is_an_assertion(tmp_path):
    code = (
        "import pytest\n\n\ndef test_rejects():\n"
        "    with pytest.raises(ValueError):\n        clamp(1, 10, 0)\n"
    )
    findings = scan(tmp_path, {"test_a.py": code})
    assert not [f for f in findings if f.check == "empty-test"]


def test_chained_dict_get_detected(tmp_path):
    code = 'def walk(cfg):\n    return cfg.get("a", {}).get("b", {})\n'
    findings = scan_py(tmp_path, code)
    assert any(f.check == "chained-dict-get" for f in findings)


def test_boundary_normalized_get_clean(tmp_path):
    code = 'def walk(cfg):\n    a = cfg.get("a") or {}\n    return a.get("b") or {}\n'
    findings = scan_py(tmp_path, code)
    assert not [f for f in findings if f.check == "chained-dict-get"]


def test_param_budget_is_six(tmp_path):
    code = "def f(a, b, c, d, e, f_arg, g):\n    return a\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "bloat-param-count" for f in findings)


def test_six_params_pass_budget(tmp_path):
    code = "def f(a, b, c, d, e, f_arg):\n    return a\n"
    findings = scan_py(tmp_path, code)
    assert not [f for f in findings if f.check == "bloat-param-count"]


def test_ui_eyebrow_label_detected(tmp_path):
    findings = scan(tmp_path, {"hero.tsx": '<div className="eyebrow">NEW</div>\n'})
    assert any(f.check == "ui-eyebrow-label" for f in findings)


def test_ui_italic_headline_detected(tmp_path):
    findings = scan(tmp_path, {"hero.html": "<h1>Ship <em>faster</em></h1>\n"})
    assert any(f.check == "ui-italic-serif-hero" for f in findings)


def test_ui_fake_stats_detected(tmp_path):
    findings = scan(tmp_path, {"landing.tsx": "<p>Trusted by 10,000+ customers worldwide</p>\n"})
    assert any(f.check == "ui-fake-stats" for f in findings)


def test_ui_generic_testimonial_detected(tmp_path):
    findings = scan(tmp_path, {"social.tsx": '<p>"Amazing!" - Sarah, CEO</p>\n'})
    assert any(f.check == "ui-generic-testimonial" for f in findings)


def test_ui_not_just_x_detected(tmp_path):
    findings = scan(
        tmp_path,
        {"landing.tsx": "<p>It's not just a tool, it's a partner.</p>\n"},
    )
    assert any(f.check == "ui-not-just-x" for f in findings)


def test_ui_triple_slogan_detected(tmp_path):
    findings = scan(tmp_path, {"landing.tsx": "<h2>Simple. Fast. Efficient.</h2>\n"})
    assert any(f.check == "ui-triple-slogan" for f in findings)


def test_ui_glass_glow_needs_two_or_more(tmp_path):
    one = scan(tmp_path / "one", {"a.tsx": "<div className='backdrop-blur'>x</div>\n"})
    two = scan(tmp_path / "two", {"a.tsx": "backdrop-filter: blur(8px)\nbackdrop-blur-lg\n"})
    assert not [f for f in one if f.check == "ui-glass-glow"]
    assert any(f.check == "ui-glass-glow" for f in two)


def test_ui_decorative_numbering_detected(tmp_path):
    findings = scan(
        tmp_path,
        {
            "steps.tsx": "<li><span>01</span></li>\n<li><span>02</span></li>\n<li><span>03</span></li>\n"
        },
    )
    assert any(f.check == "ui-decorative-numbering" for f in findings)


def test_plain_ui_copy_stays_clean(tmp_path):
    findings = scan(
        tmp_path,
        {"app.tsx": "<h1>Postgres replication lag, explained</h1>\n<p>Read the guide.</p>\n"},
    )
    ui_checks = {
        "ui-eyebrow-label",
        "ui-italic-serif-hero",
        "ui-fake-stats",
        "ui-generic-testimonial",
        "ui-not-just-x",
        "ui-triple-slogan",
        "ui-glass-glow",
        "ui-decorative-numbering",
    }
    assert not [f for f in findings if f.check in ui_checks]


def test_py_unittest_assertraises_context_clean(tmp_path):
    code = (
        "def test_rejects(self):\n"
        "    with self.assertRaises(ValueError):\n        clamp(1, 10, 0)\n"
    )
    findings = scan(tmp_path, {"test_a.py": code})
    assert not [f for f in findings if f.check == "empty-test"]


def test_dependency_groups_declare_test_deps(tmp_path):
    pyproject = (
        '[project]\nname = "sample"\nversion = "0.1.0"\n\n'
        '[dependency-groups]\ntest = ["pytest>=8"]\n'
    )
    findings = scan(
        tmp_path,
        {"pyproject.toml": pyproject, "test_a.py": "import pytest\n"},
    )
    assert not [f for f in findings if f.check == "undeclared-import"]


def test_optional_dependencies_declare_test_deps(tmp_path):
    pyproject = (
        '[project]\nname = "sample"\nversion = "0.1.0"\n\n'
        '[project.optional-dependencies]\ntest = ["pytest>=8"]\n'
    )
    findings = scan(
        tmp_path,
        {"pyproject.toml": pyproject, "test_a.py": "import pytest\n"},
    )
    assert not [f for f in findings if f.check == "undeclared-import"]


def test_undeclared_import_still_flagged_without_table(tmp_path):
    findings = scan(tmp_path, {"test_a.py": "import pytest\n"})
    assert any(f.check == "undeclared-import" for f in findings)


def test_go_ignored_error_detected(tmp_path):
    code = "func Save() error {\n    if err := write(); err != nil {\n        return nil\n    }\n    return nil\n}\n"
    findings = scan(tmp_path, {"a.go": code})
    assert any(f.check == "go-ignored-error" for f in findings)


def test_go_clean_error_flow_not_flagged(tmp_path):
    code = "func Save() error {\n    if err := write(); err != nil {\n        return err\n    }\n    return nil\n}\n"
    findings = scan(tmp_path, {"a.go": code})
    assert not [f for f in findings if f.check == "go-ignored-error"]


def test_js_empty_catch_detected(tmp_path):
    findings = scan(tmp_path, {"a.js": "try {\n  run();\n} catch (e) {}\n"})
    assert any(f.check == "empty-catch" for f in findings)


def test_rust_todo_macro_detected(tmp_path):
    findings = scan(tmp_path, {"a.rs": "fn later() {\n    todo!()\n}\n"})
    assert any(f.check == "rust-stub" for f in findings)


def test_debugger_left_detected(tmp_path):
    findings = scan(tmp_path, {"a.js": "function f() {\n  debugger;\n  return 1;\n}\n"})
    assert any(f.check == "debugger-left" for f in findings)


def test_console_log_advisory(tmp_path):
    findings = scan(tmp_path, {"a.js": "function f() {\n  console.log('hi');\n}\n"})
    hits = [f for f in findings if f.check == "debug-print"]
    assert hits and all(f.advisory for f in hits)


def test_undeclared_py_import_flagged_without_manifest(tmp_path):
    code = "import notapackage\n\ndef main():\n    return notapackage.thing\n"
    findings = scan_py(tmp_path, code)
    hits = [f for f in findings if f.check == "undeclared-import"]
    assert hits and "slopsquatting" in hits[0].message


def test_declared_py_import_not_flagged(tmp_path):
    (tmp_path / "requirements.txt").write_text("boto3\n", encoding="utf-8")
    code = "import boto3\n\ndef client():\n    return boto3.client\n"
    findings = scan_py(tmp_path, code)
    assert not [f for f in findings if f.check == "undeclared-import"]


def test_js_undeclared_import_flagged_with_manifest(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "19"}}', encoding="utf-8")
    code = "import { leftPad } from 'left-pad';\nexport const p = leftPad;\n"
    findings = scan(tmp_path, {"src.js": code})
    assert any(f.check == "undeclared-import" for f in findings)


def test_js_declared_import_clean(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"left-pad": "1"}}', encoding="utf-8")
    code = "import { leftPad } from 'left-pad';\nexport const p = leftPad;\n"
    findings = scan(tmp_path, {"src.js": code})
    assert not [f for f in findings if f.check == "undeclared-import"]


def test_phantom_import_py(tmp_path):
    code = "import os\n\ndef main():\n    return 42\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "phantom-import" for f in findings)


def test_placeholder_credential_flagged(tmp_path):
    code = 'config = {"api_' + 'key": "your-' + 'api-key-here"}\n'
    findings = scan_py(tmp_path, code)
    assert any(f.check == "placeholder-credential" for f in findings)


def test_placeholder_naming_needs_unquoted_hits(tmp_path):
    code = "\n".join(f"value_{i} = foo + bar" for i in range(4))
    findings = scan_py(tmp_path, code)
    assert any(f.check == "placeholder-naming" for f in findings)


def test_step_comments_advisory(tmp_path):
    code = "\n".join(
        [
            "// Step 1: load the config file from disk",
            "// Step 2: parse the values into memory",
            "// Step 3: validate the result set",
            "const x = 1;",
        ]
    )
    findings = scan(tmp_path, {"a.js": code})
    assert any(f.check == "step-comments" and f.advisory for f in findings)


def test_ui_gradient_detected(tmp_path):
    css = ".hero { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }\n"
    findings = scan(tmp_path, {"styles.css": css})
    assert any(f.check == "ui-ai-gradient" and f.advisory for f in findings)


def test_md_residue_detected(tmp_path):
    findings = scan(tmp_path, {"README.md": "Certainly! Here's a comprehensive guide.\n"})
    assert any(f.check == "md-residue" and f.advisory for f in findings)


def test_chatbot_residue_in_comment_blocking(tmp_path):
    code = "// Certainly! Here is the fixed function\nconst x = 1;\n"
    findings = scan(tmp_path, {"a.js": code})
    hits = [f for f in findings if f.check == "chatbot-residue"]
    assert hits and not hits[0].advisory


# ---------------------------------------------------------------------------
# Scoring, SARIF, baseline
# ---------------------------------------------------------------------------


def test_file_deficit_orders_by_blocking_weight(tmp_path):
    clean = mod.Finding("a.py", 1, "x", "m", 0, advisory=True)
    dirty = mod.Finding("b.py", 1, "elided-code", "m", 3)
    assert mod.file_deficit([clean]) < mod.file_deficit([dirty])
    assert mod.file_deficit([dirty]) > 50


def test_project_score_grades_and_verdicts():
    assert mod.project_score([]) == (0, "A", "CLEAN")
    score, grade, verdict = mod.project_score([0, 0, 5])
    assert score <= 5 and grade == "A" and verdict == "CLEAN"
    score, grade, verdict = mod.project_score([100])
    assert score == 100 and grade == "F" and verdict == "CRITICAL"


def test_project_score_conservative_aggregation():
    # 0.6*worst + 0.4*mean: one dirty file must dominate many clean ones
    score, _, verdict = mod.project_score([0] * 9 + [100])
    assert score >= 60 and verdict in ("INFLATED", "CRITICAL")


def test_sarif_output_valid(tmp_path):
    slop = {"a.py": "def handler():\n    pass\n"}
    scan(tmp_path, slop)
    out = tmp_path / "out.sarif"
    findings = mod.scan(tmp_path, mod.load_phrases())
    mod.write_sarif(str(out), findings)
    import json as _json

    data = _json.loads(out.read_text(encoding="utf-8"))
    run = data["runs"][0]
    assert data["version"] == "2.1.0"
    assert {r["ruleId"] for r in run["results"]} <= {
        r["id"] for r in run["tool"]["driver"]["rules"]
    }


def test_baseline_roundtrip(tmp_path):
    slop = {"a.py": "def handler():\n    pass\n"}
    scan(tmp_path, slop)
    findings = mod.scan(tmp_path, mod.load_phrases(), provenance=False)
    baseline = tmp_path / "baseline.json"
    mod.write_baseline(str(baseline), findings)
    kept, suppressed = mod.apply_baseline(
        mod.scan(tmp_path, mod.load_phrases(), provenance=False), str(baseline)
    )
    assert suppressed == len(findings)
    assert kept == []


def test_blocking_classification_is_rule_based():
    assert "elided-code" in mod.BLOCKING_CHECKS
    assert "ai-prose" not in mod.BLOCKING_CHECKS
    finding = mod.Finding("x.py", 1, "elided-code", "m", 3, advisory=True)
    assert not finding.advisory, "blocking-grade rules cannot be advisory"


# ---------------------------------------------------------------------------
# Bloat budget: the "100 lines of work spread over 300 lines" guards
# ---------------------------------------------------------------------------


def test_java_god_function_and_clean_negative(tmp_path):
    big = "\n".join(
        ["public class Big {", "    public void huge(int a) {"]
        + [f"        step{i} = a + {i};" for i in range(115)]
        + ["    }", "}", ""]
    )
    findings = scan(tmp_path, {"Big.java": big})
    god = [f for f in findings if f.check == "god-function"]
    assert god and god[0].advisory, "cross-language god-function is advisory"

    findings = scan(tmp_path / "clean", {"Small.java": "public class S {\n    int x;\n}\n"})
    assert not findings


def test_go_deep_nesting_flagged(tmp_path):
    lines = ["package main", "", "func deep(x int) error {", "\tif x > 0 {"]
    lines += ["\t".ljust(2 + i) + "for j := 0; j < 10; j++ {" for i in range(6)]
    lines += ["\t".ljust(8 - i) + "}" for i in range(6)]
    lines += ["\t\treturn nil", "\t}", "\treturn nil", "}", ""]
    findings = scan(tmp_path, {"deep.go": "\n".join(lines)})
    assert any(f.check == "deep-nesting" for f in findings)


def test_param_count_flagged_across_families(tmp_path):
    cs = (
        "public class P {\n"
        "    public void Many(int a1, int a2, int a3, int a4, int a5, int a6, int a7, int a8, int a9) {\n"
        "        Do(a1);\n"
        "    }\n"
        "}\n"
    )
    py = "def f(a1, a2, a3, a4, a5, a6, a7, a8, a9):\n    return a1\n"
    for name, text in (("P.cs", cs), ("f.py", py)):
        findings = scan(tmp_path, {name: text})
        assert any(f.check == "bloat-param-count" for f in findings), name


def test_ruby_def_length(tmp_path):
    lines = ["class Big", "  def huge(a)"]
    lines += [f"    x{i} = a + {i}" for i in range(105)]
    lines += ["  end", "end", ""]
    findings = scan(tmp_path, {"big.rb": "\n".join(lines)})
    assert any(f.check == "god-function" for f in findings)


def test_comment_padding_flags_ratio_and_banners(tmp_path):
    banner = "// ============================================"
    lines = []
    for i in range(15):
        lines.append(banner)
        lines.append(f"// section {i} narrative that pads the file out with talk")
        lines += ["// more narration line", ""] * 3
        lines.append(f"const v{i} = {i};")
    findings = scan(tmp_path, {"padded.js": "\n".join(lines) + "\n"})
    checks = {f.check for f in findings}
    assert "bloat-comment-ratio" in checks
    assert "bloat-banner-comments" in checks


def test_spread_banners_are_navigation_not_padding(tmp_path):
    banner = "# -----------------------------------------------------------"
    lines = []
    for i in range(5):
        lines += [banner, f"# Section {i} title", banner, ""]
        lines += [f"value_{j} = {j}" for j in range(i * 30, i * 30 + 30)]
    findings = scan(tmp_path, {"spread.py": "\n".join(lines) + "\n"})
    assert not any(f.check == "bloat-banner-comments" for f in findings)


def test_long_file_budget_and_test_exemption(tmp_path):
    code = "\n".join(f"const item{i} = {i};" for i in range(410)) + "\n"
    findings = scan(tmp_path, {"long.js": code})
    assert any(f.check == "bloat-long-file" for f in findings)
    findings = scan(tmp_path / "tests", {"long.test.js": code})
    assert not any(f.check == "bloat-long-file" for f in findings), (
        "test files are exempt from the file-length budget"
    )


def test_scoped_ignore_check_path_glob(tmp_path):
    code = "\n".join(f"const item{i} = {i};" for i in range(410)) + "\n"
    files = {"long.js": code, "keep.js": code}
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    (tmp_path / ".ai-integrity-ignore").write_text(
        "check:bloat-long-file:long.js\n", encoding="utf-8"
    )
    findings = mod.scan(tmp_path, mod.load_phrases(), provenance=False)
    flagged = {f.path for f in findings if f.check == "bloat-long-file"}
    assert any(p.endswith("keep.js") for p in flagged)
    assert not any(p.endswith("long.js") for p in flagged)


def test_bloat_is_never_blocking():
    for check in (
        "bloat-long-file",
        "bloat-param-count",
        "bloat-comment-ratio",
        "bloat-banner-comments",
    ):
        finding = mod.Finding("x.js", 1, check, "m", 1, advisory=False)
        assert finding.advisory, f"{check} must stay advisory"
        assert check not in mod.BLOCKING_CHECKS


# Round-2 scanaislop adoption: dead residue, dispatch shapes, commentary.
# Every new check is advisory; fixtures are runtime-built so this file
# itself stays detector-clean.


def test_unreachable_code_flagged(tmp_path):
    code = "def f():\n    return 1\n    print('dead')\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "unreachable-code" for f in findings)
    assert all(f.advisory for f in findings if f.check == "unreachable-code")


def test_clean_function_has_no_unreachable(tmp_path):
    code = "def f(items):\n    if not items:\n        return 0\n    return len(items)\n"
    findings = scan_py(tmp_path, code)
    assert not any(f.check == "unreachable-code" for f in findings)


def test_constant_condition_if_true_flagged_while_true_not(tmp_path):
    code = (
        "def f():\n    if True:\n        return 1\n    while False:\n        pass\n    return 2\n"
    )
    findings = scan_py(tmp_path, code)
    hits = [f for f in findings if f.check == "constant-condition"]
    assert len(hits) == 2, "if True and while False both flagged"


def test_while_true_loop_is_not_flagged(tmp_path):
    code = "def f(stop):\n    while True:\n        if stop():\n            break\n    return None\n"
    findings = scan_py(tmp_path, code)
    assert not any(f.check == "constant-condition" for f in findings)


def test_range_len_loop_flagged(tmp_path):
    code = "def f(items):\n    total = 0\n    for i in range(len(items)):\n        total += i\n    return total\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "range-len-loop" for f in findings)


def test_isinstance_ladder_at_four_branches(tmp_path):
    lines = ["def f(x):"]
    lines.append("    if isinstance(x, int):")
    lines.append("        return 1")
    for name in ("str", "float", "bytes"):
        lines.append(f"    elif isinstance(x, {name}):")
        lines.append("        return 2")
    lines.append("    return 3")
    findings = scan(tmp_path, {"a.py": "\n".join(lines) + "\n"})
    assert any(f.check == "isinstance-ladder" for f in findings)


def test_three_branch_isinstance_chain_not_flagged(tmp_path):
    code = (
        "def f(x):\n"
        "    if isinstance(x, int):\n"
        "        return 1\n"
        "    elif isinstance(x, str):\n"
        "        return 2\n"
        "    elif isinstance(x, float):\n"
        "        return 3\n"
        "    return 4\n"
    )
    findings = scan_py(tmp_path, code)
    assert not any(f.check == "isinstance-ladder" for f in findings)


def test_repetitive_string_dispatch_flagged(tmp_path):
    lines = ["def f(cmd):"]
    for idx, cmd in enumerate(("run", "stop", "pause", "resume")):
        keyword = "if" if idx == 0 else "elif"
        lines.append(f'    {keyword} cmd == "{cmd}":')
        lines.append(f"        return {len(cmd)}")
    lines.append("    return 0")
    findings = scan(tmp_path, {"a.py": "\n".join(lines) + "\n"})
    assert any(f.check == "repetitive-dispatch" for f in findings)


def test_duplicate_top_level_import_flagged(tmp_path):
    code = "import os\nimport sys\nimport os\n\n\ndef f():\n    return os.sep\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "duplicate-import" for f in findings)


def test_function_local_import_is_not_duplicate(tmp_path):
    code = "import re\n\n\ndef f():\n    import re as _re\n    return _re.escape('x')\n"
    findings = scan_py(tmp_path, code)
    assert not any(f.check == "duplicate-import" for f in findings)


def test_redundant_rethrow_flagged(tmp_path):
    code = "def f():\n    try:\n        return 1\n    except ValueError:\n        raise\n"
    findings = scan_py(tmp_path, code)
    assert any(f.check == "redundant-try-catch" for f in findings)


def test_silent_recovery_flagged(tmp_path):
    code = (
        "def f():\n"
        "    try:\n"
        "        return load()\n"
        "    except OSError as exc:\n"
        "        logging.info('load failed, continuing')\n"
        "    return None\n"
    )
    findings = scan_py(tmp_path, code)
    assert any(f.check == "silent-recovery" for f in findings)


def test_handler_using_error_not_flagged(tmp_path):
    code = (
        "def f():\n"
        "    try:\n"
        "        return load()\n"
        "    except OSError as exc:\n"
        "        logging.warning('load failed: %s', exc)\n"
        "        raise\n"
    )
    findings = scan_py(tmp_path, code)
    assert not any(f.check == "silent-recovery" for f in findings)


def test_tautological_test_flagged_only_in_tests(tmp_path):
    taut = "def test_ok():\n    assert True\n"
    findings = scan(tmp_path, {"tests/test_a.py": taut, "src_helper.py": taut})
    flagged_paths = {f.path for f in findings if f.check == "tautological-test"}
    assert any(p.endswith("test_a.py") for p in flagged_paths)
    assert not any(p.endswith("src_helper.py") for p in flagged_paths)


def test_assert_same_name_flagged(tmp_path):
    code = "def test_x():\n    value = compute()\n    assert value == value\n"
    findings = scan(tmp_path, {"tests/test_a.py": code})
    assert any(f.check == "tautological-test" for f in findings)


def test_generic_top_level_name_flagged(tmp_path):
    code = "data = load()\nresult = process(data)\n\n\ndef run():\n    return result\n"
    findings = scan(tmp_path, {"mod.py": code})
    hits = [f for f in findings if f.check == "generic-name"]
    assert hits and "data" in hits[0].message


def test_local_variables_not_flagged_as_generic(tmp_path):
    code = "def run(items):\n    data = transform(items)\n    return data\n"
    findings = scan(tmp_path, {"mod.py": code})
    assert not any(f.check == "generic-name" for f in findings)


def test_meta_comment_flagged(tmp_path):
    code = (
        "def f():\n    # Removed per the request; previously this called the v1 API\n    return 1\n"
    )
    findings = scan_py(tmp_path, code)
    assert any(f.check == "meta-comment" for f in findings)


def test_prose_block_at_five_lines_flagged_four_not(tmp_path):
    five = "\n".join(f"# narration line {i} explaining the next step" for i in range(5))
    four = "\n".join(f"# narration line {i} explaining the next step" for i in range(4))
    findings = scan(
        tmp_path,
        {
            "five.py": five + "\n\ndef f():\n    return 1\n",
            "four.py": four + "\n\ndef f():\n    return 1\n",
        },
    )
    flagged = {f.path for f in findings if f.check == "prose-block"}
    assert any(p.endswith("five.py") for p in flagged)
    assert not any(p.endswith("four.py") for p in flagged)


def test_hardcoded_localhost_url_flagged_public_not(tmp_path):
    code = "ENDPOINT = 'http://localhost:8080/api'\nDOCS = 'https://api.example.com/v1'\n"
    findings = scan(tmp_path, {"cfg.py": code})
    hits = [f for f in findings if f.check == "hardcoded-url"]
    assert hits and "1 environment-specific" in hits[0].message


def test_hardcoded_url_skipped_in_tests(tmp_path):
    code = "BASE = 'http://127.0.0.1:9000'\n\ndef test_base():\n    assert BASE\n"
    findings = scan(tmp_path, {"tests/test_cfg.py": code})
    assert not any(f.check == "hardcoded-url" for f in findings)


def test_js_constant_condition_flagged(tmp_path):
    code = "function f(x) {\n  if (true) {\n    return x;\n  }\n  return x + 1;\n}\n"
    findings = scan(tmp_path, {"a.js": code})
    assert any(f.check == "constant-condition" and f.path.endswith("a.js") for f in findings)


def test_ts_as_any_counts_as_suppression(tmp_path):
    code = "const el = input as any;\n"
    findings = scan(tmp_path, {"a.ts": code})
    assert any(f.check == "ts-suppression" for f in findings)


def test_round2_checks_all_advisory():
    for check in (
        "unreachable-code",
        "constant-condition",
        "range-len-loop",
        "isinstance-ladder",
        "repetitive-dispatch",
        "duplicate-import",
        "redundant-try-catch",
        "silent-recovery",
        "tautological-test",
        "generic-name",
        "meta-comment",
        "prose-block",
        "hardcoded-url",
    ):
        finding = mod.Finding("x.py", 1, check, "m", 3, advisory=False)
        assert finding.advisory, f"{check} must stay advisory"
        assert check not in mod.BLOCKING_CHECKS
        assert check in mod.ADVISORY_ONLY_CHECKS


def test_round2_remedies_exist():
    for check in (
        "unreachable-code",
        "constant-condition",
        "range-len-loop",
        "isinstance-ladder",
        "repetitive-dispatch",
        "duplicate-import",
        "redundant-try-catch",
        "silent-recovery",
        "tautological-test",
        "generic-name",
        "meta-comment",
        "prose-block",
        "hardcoded-url",
    ):
        assert mod.REMEDIES.get(check), f"missing remedy for {check}"


# scanaislop-aligned scoring: per-rule saturation, impact tiers, and
# the slop-score ceiling gate.


def test_per_rule_saturation_caps_one_noisy_check(tmp_path):
    noisy = "\n".join(f"# narration line {i} padding the block" for i in range(10))
    code = noisy + "\n\n" + "import os\nimport sys\nimport os\nimport re\nimport re\n"
    findings = scan(tmp_path, {"a.py": code * 3})
    assert findings


def test_saturation_bounds_advisory_weight():
    findings = [
        mod.Finding("x.py", i, "phantom-import", f"m{i}", 1, advisory=True) for i in range(60)
    ]
    deficit = mod.file_deficit(findings)
    # 60 mechanical findings saturate at MAX_PER_RULE (40), tier 0.5 -> 20
    import math

    assert deficit == round(8 * math.log1p(20))
    # the same weight without saturation would press harder (60 * 0.5 = 30)
    assert deficit < round(8 * math.log1p(30))


def test_impact_tiers_press_less_than_standard():
    style = mod.Finding("ui.tsx", 1, "ui-triple-slogan", "m", 4, advisory=True)
    strict = mod.Finding("a.py", 1, "undeclared-import", "m", 4, advisory=True)
    assert mod.file_deficit([style]) < mod.file_deficit([strict])


def test_blocking_weight_is_never_tiered():
    blocking = mod.Finding("a.py", 1, "empty-test", "m", 4)
    assert mod.check_tier("empty-test") == 1.0
    assert mod.file_deficit([blocking]) == mod.file_deficit([blocking])


def test_max_slop_score_gate_fails_dirty_project(tmp_path):
    noisy = "\n".join(f"# narration line {i} padding the block out" for i in range(6))
    code = (
        noisy
        + "\n\nimport os\nimport sys\nimport os\nimport re\nimport re\nimport json\nimport json\n"
    )
    scan_py(tmp_path, code)
    rc = mod.main([str(tmp_path), "--max-score", "0", "--max-slop-score", "0"])
    assert rc == 1


def test_max_slop_score_gate_keeps_clean_project(tmp_path):
    scan(tmp_path, {"a.py": "def add(a, b):\n    return a + b\n"})
    rc = mod.main([str(tmp_path), "--max-score", "0", "--max-slop-score", "0"])
    assert rc == 0
