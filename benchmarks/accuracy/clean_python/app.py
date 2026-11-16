"""The warehouse CLI: one restock-and-report pass over stdin lines."""

from __future__ import annotations

import sys

from warehouse.service import Inventory, build_shipment, suggest_backfill


def parse_line(line: str) -> tuple[str, int]:
    """One '<sku> <quantity>' input line, stripped and typed."""
    sku, raw = line.strip().split(" ")
    return sku, int(raw)


def main() -> int:
    """Restock from stdin lines, pack one shipment, print the advice."""
    inventory = Inventory()
    lines = []
    for raw in sys.stdin:
        if raw.strip():
            lines.append(parse_line(raw))
    inventory.restock("widget-a", 12)
    inventory.restock("widget-b", 3)
    shipment = build_shipment(inventory, "order-1", lines)
    print(f"shipped {shipment.total_quantity()} units for {shipment.order_id}")
    catalog = {"widget-a": 40, "widget-b": 9}
    for sku in suggest_backfill(inventory, catalog):
        print(f"reorder: {sku}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
