"""Shared detector loader and scan helper for the contract-test suite.

One place executes the detector module so a new test file does not add
the tenth copy of the load preamble - the boilerplate-duplication
oracle counts exactly that pattern. One place also owns the one-dir
scan helper, for the same reason: every contract-test file used to
declare its own copy, and the round-10 tranche was the tenth copy of
the write line. Import both from here:

    from detector_module import detector as mod, scan, hits
"""

import importlib.util
import os
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ai_slop_detector.py"
_spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - layout break
    raise ImportError("cannot load ai_slop_detector from scripts/")
detector = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = detector
_spec.loader.exec_module(detector)


def scan(tmp_path, files=None, sub=None):
    """One full-engine scan over one isolated directory.

    Multi-scan tests MUST pass distinct ``sub`` names: scan() reads the
    whole tree it is pointed at, so two scans of the same tmp_path see
    each other's files and the negative asserts pick up stale findings.
    """
    base = tmp_path / sub if sub else tmp_path
    for name, text in (files or {}).items():
        target = base / name
        os.makedirs(target.parent, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return detector.scan(base, detector.load_phrases())


def hits(findings, check):
    """The findings of one check id, in scan order."""
    return [f for f in findings if f.check == check]
