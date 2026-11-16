"""Warehouse domain models: plain data holders and pure functions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    """One stock keeping unit as stored on a shelf."""

    sku: str
    name: str
    quantity: int

    def is_out_of_stock(self) -> bool:
        """A shelf is empty when its quantity reaches zero."""
        return self.quantity <= 0


@dataclass
class Shipment:
    """A dispatch of items from the warehouse to a customer."""

    order_id: str
    items: list[Item] = field(default_factory=list)

    def total_quantity(self) -> int:
        """The whole shipment's item count across its lines."""
        return sum(item.quantity for item in self.items)

    def add(self, item: Item) -> None:
        """Append one line to the shipment."""
        self.items.append(item)


def pick_replacement(inventory: dict[str, int], sku: str, want: int) -> str | None:
    """The first alternative sku holding at least the wanted quantity.

    Inventory maps sku to on-shelf quantity. Returns None when no
    alternative can cover the want; the caller decides whether to
    back-order.
    """
    for candidate, on_shelf in sorted(inventory.items()):
        if candidate == sku:
            continue
        if on_shelf >= want:
            return candidate
    return None
