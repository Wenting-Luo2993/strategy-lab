import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("research route is static-export compatible and uses the shared client surface", async () => {
  const [route, component, config] = await Promise.all([
    readFile(new URL("../src/app/research/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/research/ResearchDashboardApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../next.config.ts", import.meta.url), "utf8"),
  ]);

  assert.match(route, /ResearchDashboardApp/);
  assert.doesNotMatch(route, /cookies\(|headers\(|force-dynamic|revalidate/);
  assert.match(component, /Trades, fills, raw bars, ledgers, and equity curves are intentionally unavailable/);
  assert.match(component, /aria-label="Dashboard sections"/);
  assert.match(config, /output: "export"/);
});
