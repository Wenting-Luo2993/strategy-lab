import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("research route is static-export compatible and uses the shared client surface", async () => {
  const [route, component, liveComponent, menu, config] = await Promise.all([
    readFile(new URL("../src/app/research/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/research/ResearchDashboardApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/DashboardApp.tsx", import.meta.url), "utf8"),
    readFile(new URL("../src/components/DashboardSectionMenu.tsx", import.meta.url), "utf8"),
    readFile(new URL("../next.config.ts", import.meta.url), "utf8"),
  ]);

  assert.match(route, /ResearchDashboardApp/);
  assert.doesNotMatch(route, /cookies\(|headers\(|force-dynamic|revalidate/);
  assert.match(component, /Trades, fills, raw bars, ledgers, and equity curves are intentionally unavailable/);
  assert.match(component, /DashboardSectionMenu/);
  assert.match(component, /READ ONLY/);
  assert.match(liveComponent, /READ ONLY/);
  assert.match(menu, /aria-label="Dashboard sections"/);
  assert.match(menu, /aria-label="Open dashboard navigation"/);
  assert.match(menu, /aria-expanded=\{open\}/);
  assert.match(menu, /OverlayDrawer/);
  assert.match(menu, /NavigationRegular/);
  assert.match(menu, /DismissRegular/);
  assert.match(menu, /onOpenChange/);
  assert.match(menu, /position="end"/);
  assert.match(menu, /height: "100dvh"/);
  assert.match(menu, /color: "#ffffff"/);
  assert.match(menu, /padding: "1\.5rem 1\.75rem"/);
  assert.match(config, /output: "export"/);
});
