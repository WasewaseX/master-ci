"""Formatting helpers for the nightly report."""

def fmt_row(name, amount):
    padded = name.ljust(24)
    return f"{padded} {amount:>10.2f}"


def dump(rows):
    for name, amount in rows:
        line = fmt_row(name, amount)
        print(line)
