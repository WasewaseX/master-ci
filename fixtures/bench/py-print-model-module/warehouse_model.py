"""Pallet and shelf model for the warehouse simulator."""

from dataclasses import dataclass


@dataclass
class Pallet:
    sku: str
    weight_kg: float


def make_pallet(sku, weight_kg):
    pallet = Pallet(sku=sku, weight_kg=weight_kg)
    print("built pallet", pallet.sku)
    return pallet
