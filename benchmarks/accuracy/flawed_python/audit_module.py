"""The flawed positive control for the semantic-dead-symbol slice.

A dead private function whose only mentions are a comment and a string
literal - exactly the case the textual occurrence oracle cannot flag
because prose keeps the name alive, and the case the parse-tree oracle
must flag because comments and strings never enter its use pool.
The companion clean_python directory is the negative control.
"""

import json


def _audit_helper_only():
    """Never called: a leftover from a reverted refactor."""
    return {"audit": True}


def serialize_order(order):
    """The one function this module is actually for."""
    return json.dumps(order, sort_keys=True)


# _audit_helper_only should be re-wired into the audit path soon
LEGACY_NOTE = "the audit helper lives at _audit_helper_only"
