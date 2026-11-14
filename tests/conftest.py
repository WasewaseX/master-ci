"""Shared pytest bootstrap: one place puts scripts/ on sys.path.

Eleven test modules used to repeat the same two-line preamble; the
twelfth copy tripped the detector's own boilerplate-duplication rule.
New test modules import the repo's engines directly and skip the
preamble - conftest runs before any collection, so the import works.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_SCRIPTS = HERE.parent / "scripts"
if str(REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS))
