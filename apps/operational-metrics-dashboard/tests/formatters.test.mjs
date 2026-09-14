import assert from "node:assert/strict";
import test from "node:test";

import { formatCurrency } from "../src/data/formatters.ts";

test("formats account and trade currencies independently", () => {
  assert.equal(formatCurrency(1234.5, "CAD"), "CA$1,234.50");
  assert.equal(formatCurrency(1234.5, "USD"), "$1,234.50");
});

test("does not guess BASE or missing currencies", () => {
  assert.equal(formatCurrency(12.5, "BASE"), "12.50 (currency unavailable)");
  assert.equal(formatCurrency(12.5, null), "12.50 (currency unavailable)");
  assert.equal(formatCurrency(null, "USD"), "--");
});
