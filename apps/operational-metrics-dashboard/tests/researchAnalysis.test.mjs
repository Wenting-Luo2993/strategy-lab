import assert from "node:assert/strict";
import test from "node:test";

import {
  analysisLimits,
  decodeAnalysisState,
  defaultAnalysisQuery,
  encodeAnalysisState,
  FixtureAnalysisAdapter,
  sanitizeAnalysisQuery,
} from "../src/data/researchAnalysis.ts";
import { analysisFixtureRows, clonedResearchFixtures } from "../src/data/researchFixtures.ts";

test("field-catalog analysis defaults to current validated RUN-000003-shaped data", async () => {
  const adapter = new FixtureAnalysisAdapter(clonedResearchFixtures(), analysisFixtureRows);
  const catalog = await adapter.getAnalysisFieldCatalog();
  const result = await adapter.runAnalysis(defaultAnalysisQuery);

  assert.ok(catalog.some((field) =>
    field.field === "parameter.opening_range_minutes"
    && field.dataType === "integer"
    && field.filterOperators.includes("between")
  ));
  assert.equal(result.rows.length, 1);
  assert.equal(result.rows[0]["identity.display_alias"], "RUN-000003");
  assert.equal(result.metadata.sampled, false);
  assert.equal(result.metadata.truncated, false);
});

test("typed filters, arbitrary grouping fields, and aggregate measures compose", async () => {
  const adapter = new FixtureAnalysisAdapter(clonedResearchFixtures(), analysisFixtureRows);
  const query = {
    ...structuredClone(defaultAnalysisQuery),
    population: {
      includeLegacy: true,
      trustStates: ["current_validated", "validation_attention", "legacy"],
    },
    filters: {
      op: "and",
      children: [
        { op: "gte", field: "parameter.opening_range_minutes", value: 5 },
        {
          op: "or",
          children: [
            { op: "eq", field: "dataset.symbol", value: "QQQ" },
            { op: "contains", field: "identity.strategy_id", value: "other" },
          ],
        },
      ],
    },
    groupBy: [
      "identity.strategy_id",
      "dataset.symbol",
      "parameter.opening_range_minutes",
    ],
    measures: [
      { field: "metric.expectancy_r", aggregate: "mean" },
      { field: "identity.run_id", aggregate: "count" },
    ],
    orderBy: [{ field: "parameter.opening_range_minutes", direction: "asc" }],
    chart: {
      kind: "bar",
      x: "parameter.opening_range_minutes",
      y: "mean:metric.expectancy_r",
    },
  };
  const result = await adapter.runAnalysis(query);

  assert.equal(result.rows.length, 3);
  assert.deepEqual(
    result.rows.map((row) => row["parameter.opening_range_minutes"]),
    [5, 15, 30],
  );
  assert.equal(result.metadata.aggregated, true);
  assert.equal(result.metadata.estimatedGroups, 3);
});

test("analysis state decoding is bounded and rejects unknown or malformed fields", async () => {
  const adapter = new FixtureAnalysisAdapter(clonedResearchFixtures(), analysisFixtureRows);
  const catalog = await adapter.getAnalysisFieldCatalog();
  const encoded = encodeAnalysisState(defaultAnalysisQuery);

  assert.ok(encoded);
  assert.deepEqual(decodeAnalysisState(encoded, catalog), sanitizeAnalysisQuery(defaultAnalysisQuery, catalog));
  assert.deepEqual(
    decodeAnalysisState("x".repeat(analysisLimits.maxUrlStateBytes + 1), catalog),
    sanitizeAnalysisQuery(defaultAnalysisQuery, catalog),
  );
  assert.deepEqual(
    decodeAnalysisState("%7Bnot-json", catalog),
    sanitizeAnalysisQuery(defaultAnalysisQuery, catalog),
  );
  assert.throws(
    () => sanitizeAnalysisQuery({ ...defaultAnalysisQuery, groupBy: ["metric.secret"] }, catalog),
    /Unknown field/,
  );
});

test("metric versions cannot be silently combined", async () => {
  const rows = structuredClone(analysisFixtureRows);
  rows[1].dimensions["identity.metric_calculation_version"] = 99;
  const adapter = new FixtureAnalysisAdapter(clonedResearchFixtures(), rows);
  await assert.rejects(
    () => adapter.runAnalysis({
      ...structuredClone(defaultAnalysisQuery),
      population: {
        includeLegacy: false,
        trustStates: ["current_validated", "validation_attention"],
      },
      measures: [{ field: "metric.expectancy_r", aggregate: "mean" }],
    }),
    /incompatible calculation versions/,
  );
});

test("response truncation and cost metadata are explicit", async () => {
  const rows = Array.from({ length: analysisLimits.maxResponseRows + 2 }, (_, index) => ({
    ...structuredClone(analysisFixtureRows[0]),
    runId: `run-${index}`,
    dimensions: {
      ...analysisFixtureRows[0].dimensions,
      "identity.display_alias": `RUN-${String(index).padStart(6, "0")}`,
    },
  }));
  const adapter = new FixtureAnalysisAdapter(clonedResearchFixtures(), rows);
  const estimate = await adapter.estimateAnalysis(defaultAnalysisQuery);
  const result = await adapter.runAnalysis(defaultAnalysisQuery);

  assert.equal(estimate.requiresAggregation, true);
  assert.equal(result.rows.length, analysisLimits.maxResponseRows);
  assert.equal(result.metadata.totalResultRows, analysisLimits.maxResponseRows + 2);
  assert.equal(result.metadata.truncated, true);
  assert.equal(result.metadata.limits.maxGroups, 500);
});
