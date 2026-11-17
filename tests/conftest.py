"""Shared pytest bootstrap: one place puts scripts/ on sys.path.

Eleven test modules used to repeat the same two-line preamble; the
twelfth copy tripped the detector's own boilerplate-duplication rule.
Round-13 migrated the ten grandfathered carriers off it - every test
module now imports the repo's engines directly and skips the preamble;
conftest runs before any collection, so the import works. The paid
debt sits in .quality-baseline.json's resolved_history.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_SCRIPTS = HERE.parent / "scripts"
if str(REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS))
