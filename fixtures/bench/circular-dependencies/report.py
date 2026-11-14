"""Report rendering (fixture: imports the ledger module)."""


from ledger import balance


def summarize(total):
    return f"ledger total {total}"
