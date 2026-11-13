"""Shared detector loader for the contract-test suite.

One place executes the detector module so a new test file does not add
the tenth copy of the load preamble - the boilerplate-duplication
oracle counts exactly that pattern. Import `detector` from here:

    from detector_module import detector as mod
"""

import importlib.util
import sys
from pathlib import Path

MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ai_slop_detector.py"
_spec = importlib.util.spec_from_file_location("ai_slop_detector", MOD_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - layout break
    raise ImportError("cannot load ai_slop_detector from scripts/")
detector = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = detector
_spec.loader.exec_module(detector)
