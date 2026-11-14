"""Retention sweep for expired dockets (fixture: one dead helper)."""


def quarantine_expired(docket):
    """Park one expired docket in the cold-storage annex."""
    return f"annexed {docket}"
