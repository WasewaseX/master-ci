"""Warehouse inventory service: the operations callers drive."""

from __future__ import annotations

from warehouse.models import Item, Shipment, pick_replacement

LOW_STOCK_FLOOR = 5


class Inventory:
    """The shelf map: sku to on-shelf quantity."""

    def __init__(self) -> None:
        self._shelves: dict[str, int] = {}

    def restock(self, sku: str, quantity: int) -> None:
        """Add stock for one sku."""
        self._shelves[sku] = self._shelves.get(sku, 0) + quantity

    def take(self, sku: str, quantity: int) -> int:
        """Remove stock, never below zero; the taken amount is returned."""
        on_shelf = self._shelves.get(sku, 0)
        taken = min(on_shelf, quantity)
        self._shelves[sku] = on_shelf - taken
        return taken

    def low_stock(self) -> list[str]:
        """Skus sitting under the low-stock floor, sorted for stability."""
        return sorted(sku for sku, on_shelf in self._shelves.items() if on_shelf < LOW_STOCK_FLOOR)


def build_shipment(inventory: Inventory, order_id: str, lines: list[tuple[str, int]]) -> Shipment:
    """Take every line from the shelves and pack the shipment.

    Lines the shelves cannot fully cover ship partially; the caller
    inspects each item's quantity to see what actually moved.
    """
    shipment = Shipment(order_id=order_id)
    for sku, want in lines:
        taken = inventory.take(sku, want)
        shipment.add(Item(sku=sku, name=sku.replace("-", " ").title(), quantity=taken))
    return shipment


def suggest_backfill(inventory: Inventory, catalog: dict[str, int]) -> list[str]:
    """Skus to reorder: low on the shelves with a covering alternative.

    The catalog maps sku to its replacement's on-shelf quantity; a sku
    with no covering alternative stays off the list, since a reorder
    of the same sku would be the decision then.
    """
    suggestions: list[str] = []
    for sku in inventory.low_stock():
        want = LOW_STOCK_FLOOR
        alternative = pick_replacement(catalog, sku, want)
        if alternative is not None:
            suggestions.append(sku)
    return suggestions
