// The consumer that exercises the label module: a shelf-report CLI
// entry point that imports and drives every exported function.
import { countUnits, shelfLabels } from "./index.js";

export function report(shelves, width) {
  const total = countUnits(shelves);
  const body = shelfLabels(shelves, width);
  return `${body}\ntotal: ${total}`;
}
