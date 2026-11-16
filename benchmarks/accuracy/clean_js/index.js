// Clean control: a tiny label-printing module with every declared
// dependency actually used and every binding referenced.
import { leftPad } from "left-pad";

export function labelRow(sku, quantity, width) {
  const label = sku.replace(/-/g, " ");
  const padded = leftPad(label, width, ".");
  return `${padded} x${quantity}`;
}

export function shelfLabels(shelves, width) {
  // shelves maps sku to on-shelf quantity; the order is insertion-stable.
  const rows = [];
  for (const [sku, quantity] of Object.entries(shelves)) {
    rows.push(labelRow(sku, quantity, width));
  }
  return rows.join("\n");
}

export function countUnits(shelves) {
  let total = 0;
  for (const quantity of Object.values(shelves)) {
    total += quantity;
  }
  return total;
}
