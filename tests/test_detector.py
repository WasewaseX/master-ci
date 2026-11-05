"""Smoke tests for the AI-integrity detector."""

import importlib.util
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ai_slop_detector.py"
spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def test_slop_detected(tmp_path):
    slop = "# This function does the thing " + "sea" + "mlessly\n" * 12
    (tmp_path / "x.py").write_text(slop, encoding="utf-8")
    findings = mod.scan(tmp_path, mod.load_phrases())
    assert mod.total_score(findings) > 0
    checks = {f.check for f in findings}
    assert "ai-vocab" in checks


def test_clean_code(tmp_path):
    (tmp_path / "y.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    findings = mod.scan(tmp_path, mod.load_phrases())
    assert not findings
