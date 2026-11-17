"""Invoice billing service for the storefront."""

from dataclasses import dataclass


@dataclass
class Invoice:
    order_id: str
    total_cents: int


class BillingService:
    def __init__(self, gateway):
        self._gateway = gateway

    def charge(self, invoice):
        print(f"charging order {invoice.order_id}")
        return self._gateway.charge(invoice.total_cents)
