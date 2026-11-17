"""The extension-parity contract: every scoped extension must be scanned.

slop_cpp.py declares ``.cxx``/``.hxx`` inside CPP_ONLY_EXTS and
HEADER_EXTS - but until round-13 neither CODE_EXTS nor FAMILY_OF_EXT
carried them, so a .cxx translation unit or .hxx header never reached
the cpp scanner at all: the extension fell out of the read set
(unknown file type) and out of family dispatch (family ""), and the
rules that named it could never fire on it. The gap was found while
writing the round-13 accuracy fixtures, exactly where the corpus
protocol says it should be found.

The contract here is general, not cpp-only: every extension any family
module names in its rule scope must (a) be a code extension the scanner
reads and (b) map to a family, so the rules can run. A scope the
scanner cannot reach is a rule that lies about its own coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "scripts"))

import slop_cpp  # noqa: E402
from slop_patterns import CODE_EXTS, FAMILY_OF_EXT  # noqa: E402


def test_cpp_rule_scope_extensions_are_dispatched() -> None:
    """Every extension in slop_cpp's scope sets reaches the cpp scanner."""
    scope: set[str] = set(slop_cpp.CPP_ONLY_EXTS) | set(slop_cpp.HEADER_EXTS)
    missing_code = sorted(ext for ext in scope if ext not in CODE_EXTS)
    unscoped = sorted(ext for ext in scope if FAMILY_OF_EXT.get(ext) != "cpp")
    assert missing_code == [], f"scanner never reads: {missing_code}"
    assert unscoped == [], f"no cpp family dispatch: {unscoped}"


def test_family_map_extensions_are_all_code() -> None:
    """FAMILY_OF_EXT must not name an extension the scanner refuses."""
    unread = sorted(ext for ext in FAMILY_OF_EXT if ext not in CODE_EXTS)
    assert unread == [], f"family dispatch over unread files: {unread}"
