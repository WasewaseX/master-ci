"""Ledger operations invoked by name at runtime."""


def reconcile_ledger(entries):
    total = sum(entry.amount for entry in entries)
    return f"reconciled {total}"


def void_transaction(entry):
    return f"voided {entry.reference}"
