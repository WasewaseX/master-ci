"""The facade contract: ai_slop_detector.py re-exports the public surface.

The helper is the single file foreign repos vendor (master-ci.yml probes
exactly ``scripts/ai_slop_detector.py``), so its import surface is the
product contract. "Every public name is re-exported here" is asserted
here, machine-checked, not narrated in a docstring - the scanaislop
unused-import claims on the facade's noqa lines (2026-11-18) were
triaged against this contract: every re-export either has an in-repo
consumer or is the contractual surface itself, and the one genuinely
dead binding (slop_scan's ArchConfigError) was removed.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
from detector_module import detector as mod  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _public_check_names() -> set[str]:
    """Every top-level ``check_*`` function defined in a slop_* module."""
    names: set[str] = set()
    for path in sorted(SCRIPTS.glob("slop_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "check_"
            ):
                names.add(node.name)
    return names


@pytest.mark.parametrize("name", sorted(_public_check_names()))
def test_facade_reexports_every_public_check(name):
    assert hasattr(mod, name), (
        f"{name} is defined in a slop_* module but missing from the "
        "ai_slop_detector facade - foreign consumers import by this file"
    )


def test_facade_surface_is_not_empty():
    # A glob or AST regression that silently yields zero names must not
    # turn this suite into a vacuous pass.
    assert len(_public_check_names()) >= 20
