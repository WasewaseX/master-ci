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
    findings = scan(tmp_path, {"a.py": code})
    assert any(f.check == "stub-function" for f in findings)


def test_continue_only_handler_not_swallowed(tmp_path):
    code = (
        "for item in items:\n    try:\n        load(item)\n    except OSError:\n        continue\n"
    )
    findings = scan(tmp_path, {"a.py": code})
    assert not [f for f in findings if f.check == "swallowed-exception"]


def test_bare_except_detected(tmp_path):
    code = "try:\n    risky()\nexcept:\n    pass\n"
    findings = scan(tmp_path, {"a.py": code})
    checks = {f.check for f in findings}
    assert "bare-except" in checks or "swallowed-exception" in checks


def test_py_empty_test_detected(tmp_path):
    findings = scan(tmp_path, {"test_a.py": "def test_nothing():\n    ready = True\n"})
    assert any(f.check == "empty-test" for f in findings)


def test_py_test_with_assert_clean(tmp_path):
    findings = scan(tmp_path, {"test_a.py": "def test_ok():\n    assert 1 + 1 == 2\n"})
    assert not [f for f in findings if f.check == "empty-test"]


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
    findings = scan(tmp_path, {"a.py": code})
    hits = [f for f in findings if f.check == "undeclared-import"]
    assert hits and "slopsquatting" in hits[0].message


def test_declared_py_import_not_flagged(tmp_path):
    (tmp_path / "requirements.txt").write_text("boto3\n", encoding="utf-8")
    code = "import boto3\n\ndef client():\n    return boto3.client\n"
    findings = scan(tmp_path, {"a.py": code})
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
    findings = scan(tmp_path, {"a.py": code})
    assert any(f.check == "phantom-import" for f in findings)


def test_placeholder_credential_flagged(tmp_path):
    code = 'config = {"api_' + 'key": "your-' + 'api-key-here"}\n'
    findings = scan(tmp_path, {"a.py": code})
    assert any(f.check == "placeholder-credential" for f in findings)


def test_placeholder_naming_needs_unquoted_hits(tmp_path):
    code = "\n".join(f"value_{i} = foo + bar" for i in range(4))
    findings = scan(tmp_path, {"a.py": code})
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
