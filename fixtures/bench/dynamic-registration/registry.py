"""Operation registry: handlers resolve dynamically by name."""


import operations

REGISTERED = ("reconcile_ledger", "void_transaction")


def dispatch(name, entries):
    handler = getattr(operations, name)
    return handler(entries)


if __name__ == "__main__":
    dispatch(REGISTERED[0], [])
