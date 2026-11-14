"""Ledger totals (fixture: imports the report module)."""


from report import summarize


def balance(entries):
    return sum(entry.amount for entry in entries)


def statement(entries):
    return summarize(balance(entries))
