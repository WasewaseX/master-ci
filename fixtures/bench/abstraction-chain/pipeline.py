"""Order intake ladder (fixture: four pass-through rungs, one target)."""


def intake(order):
    return persist(order)


def expedite(order):
    return persist(order)


def backorder(order):
    return persist(order)


def restock(order):
    return persist(order)


def persist(order):
    receipt = write_ledger(order)
    return receipt


def write_ledger(order):
    return f"order {order.reference} recorded"
