import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  GUIDE_SCOPES,
  GUIDE_STEPS,
  GUIDE_STORAGE_KEY,
  findStepTarget,
  matchesHeading,
  ownText,
  readGuidePreference,
  stepsForScope,
  writeGuidePreference,
} from "../public/guide.mjs";

const PUBLIC_DIR = fileURLToPath(new URL("../public/", import.meta.url));

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
    values,
  };
}

function textNode(text) {
  return { nodeType: 3, textContent: text };
}

function elementNode(text, children = []) {
  return { nodeType: 1, textContent: text, childNodes: children };
}

test("every guide step is complete and uniquely identified", () => {
  const ids = new Set();
  for (const step of GUIDE_STEPS) {
    assert.ok(step.id, "step has an id");
    assert.ok(!ids.has(step.id), `duplicate step id ${step.id}`);
    ids.add(step.id);
    assert.ok(Object.hasOwn(GUIDE_SCOPES, step.scope), `${step.id} has a known scope`);
    for (const field of ["title", "what", "read"]) {
      assert.equal(typeof step[field], "string", `${step.id}.${field} is text`);
      assert.ok(step[field].trim().length > 0, `${step.id}.${field} is not empty`);
    }
    const hasSelector = typeof step.selector === "string" && step.selector.length > 0;
    const hasHeadings = Array.isArray(step.headings) && step.headings.length > 0;
    assert.ok(hasSelector !== hasHeadings, `${step.id} targets by selector or by headings, not both`);
    if (step.placement !== undefined) assert.equal(step.placement, "corner");
  }
});

test("each scope has steps and stepsForScope keeps guide order", () => {
  for (const scope of Object.keys(GUIDE_SCOPES)) {
    const steps = stepsForScope(scope);
    assert.ok(steps.length > 0, `${scope} has steps`);
    const order = steps.map((step) => GUIDE_STEPS.indexOf(step));
    assert.deepEqual(order, [...order].sort((a, b) => a - b));
  }
  assert.deepEqual(stepsForScope("missing"), []);
});

test("guide headings still exist in the viewer markup and renderers", async () => {
  const sources = await Promise.all(
    ["index.html", "app.mjs", "experiment-view.mjs", "experiment-kinds.mjs", "run-charts.mjs"].map((name) =>
      readFile(join(PUBLIC_DIR, name), "utf8"),
    ),
  );
  const corpus = sources.join("\n");
  // Headings rendered from a template literal appear in source only as their static prefix.
  const templated = { "Strategy equity": "<h3>Strategy ${", "Strategy return": "<h3>Strategy ${" };
  for (const step of GUIDE_STEPS.filter((item) => item.headings)) {
    const found = step.headings.some((heading) => corpus.includes(heading) || (templated[heading] && corpus.includes(templated[heading])));
    assert.ok(found, `${step.id}: none of ${JSON.stringify(step.headings)} appear in the viewer source`);
  }
});

test("guide preference defaults on and round-trips through storage", () => {
  assert.equal(readGuidePreference(memoryStorage()), true);
  assert.equal(readGuidePreference(undefined), true);
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(readGuidePreference(throwing), true);
  assert.doesNotThrow(() => writeGuidePreference(throwing, false));

  const storage = memoryStorage();
  writeGuidePreference(storage, false);
  assert.equal(storage.values.get(GUIDE_STORAGE_KEY), "off");
  assert.equal(readGuidePreference(storage), false);
  writeGuidePreference(storage, true);
  assert.equal(readGuidePreference(storage), true);
});

test("ownText ignores child elements such as badges and hint buttons", () => {
  const heading = elementNode("Cumulative R ? Pass", [
    textNode("  Cumulative\n R "),
    elementNode("?", [textNode("?")]),
    elementNode("Pass", [textNode("Pass")]),
  ]);
  assert.equal(ownText(heading), "Cumulative R");
  assert.equal(ownText(null), "");
});

test("matchesHeading requires an exact heading", () => {
  const step = { headings: ["By year", "Per-year breakdown"] };
  assert.equal(matchesHeading(step, "By year"), true);
  assert.equal(matchesHeading(step, "By year (2024)"), false);
  assert.equal(matchesHeading({ selector: "#x" }, "By year"), false);
});

test("findStepTarget honours selector, contains and heading matching", () => {
  const plain = elementNode("Trades", [textNode("Trades")]);
  const wanted = elementNode("Exit reasons", [textNode("Exit reasons")]);
  const root = {
    querySelectorAll(selector) {
      if (selector === ".panel") return [plain, wanted];
      if (selector === "h2, h3, h4, summary") return [plain, wanted];
      return [];
    },
  };
  assert.equal(findStepTarget(root, { selector: ".panel" }), plain);
  assert.equal(findStepTarget(root, { selector: ".panel", contains: "Exit" }), wanted);
  assert.equal(findStepTarget(root, { selector: ".panel", contains: "Nope" }), null);
  assert.equal(findStepTarget(root, { headings: ["Exit reasons"] }), wanted);
  assert.equal(findStepTarget(root, { headings: ["Missing"] }), null);
  assert.equal(findStepTarget(null, { selector: ".panel" }), null);
});
