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
