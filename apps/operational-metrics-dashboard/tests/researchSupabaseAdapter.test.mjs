import assert from "node:assert/strict";
import test from "node:test";

import {
  contentRangeTotal,
  researchQueryParameters,
  SupabaseResearchDataAdapter,
} from "../src/data/researchSupabaseAdapter.ts";
import { researchFixtureRows } from "../src/data/researchFixtures.ts";

test("Supabase adapter performs anonymous GET-only paginated reads", async () => {
  const requests = [];
  const adapter = new SupabaseResearchDataAdapter(
    "https://example.supabase.co/",
    "anon-key",
    async (url, options) => {
      requests.push({ url, options });
      return new Response(JSON.stringify(researchFixtureRows.slice(0, 2)), {
        status: 200,
        headers: { "content-range": "10-11/23" },
      });
    },
  );
  const page = await adapter.listPublications({
    page: 2,
    pageSize: 10,
    kind: "run_summary",
    status: "completed",
    sort: "publication_version",
    direction: "asc",
  });

  assert.equal(page.total, 23);
  assert.equal(page.page, 2);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, "GET");
  assert.equal(requests[0].options.headers.Range, "10-19");
  assert.equal(requests[0].options.headers.apikey, "anon-key");
  assert.equal(requests[0].options.headers.Authorization, "Bearer anon-key");
  assert.match(requests[0].url, /research_publications/);
  assert.match(requests[0].url, /aggregate_type=eq.run_summary/);
  assert.match(requests[0].url, /payload-%3E%3Estate=eq.completed/);
  assert.equal("body" in requests[0].options, false);
});

test("Supabase adapter loads one publication by immutable idempotency key", async () => {
  let requestedUrl = "";
  const adapter = new SupabaseResearchDataAdapter(
    "https://example.supabase.co",
    "anon-key",
    async (url) => {
      requestedUrl = String(url);
      return new Response(JSON.stringify([researchFixtureRows[4]]), { status: 200 });
    },
  );

  const publication = await adapter.getPublication("registry:experiment:EXP-104:checksum");
  assert.equal(publication.kind, "registry_record");
  assert.ok(requestedUrl.includes("idempotency_key=eq.registry%3Aexperiment%3AEXP-104%3Achecksum"));
});

test("query builder uses stable ordering and sanitized identity search", () => {
  const query = researchQueryParameters({
    page: 1,
    pageSize: 25,
    kind: "all",
    search: "EXP-104*,()",
    sort: "aggregate_id",
    direction: "desc",
  });
  assert.equal(query.get("aggregate_id"), "ilike.*EXP-104*");
  assert.equal(query.get("order"), "aggregate_id.desc,idempotency_key.asc");
  assert.equal(query.has("aggregate_type"), false);
});

test("content range parsing falls back safely", () => {
  assert.equal(contentRangeTotal("0-9/42", 10), 42);
  assert.equal(contentRangeTotal("0-0/*", 1), 1);
  assert.equal(contentRangeTotal(null, 3), 3);
});

test("analysis adapter reads the catalog and calls only the bounded read RPC", async () => {
  const requests = [];
  const adapter = new SupabaseResearchDataAdapter(
    "https://example.supabase.co",
    "anon-key",
    async (url, options) => {
      requests.push({ url: String(url), options });
      if (String(url).includes("field_catalog")) {
        return new Response(JSON.stringify([{
          field_id: "metric.expectancy_r",
          label: "Expectancy R",
          data_type: "number",
          unit: "ratio",
          cardinality: 2,
          filter_operators: ["eq", "gte"],
          aggregates: ["mean"],
          groupable: true,
          screenable: true,
          trust_restricted: false,
        }]), { status: 200 });
      }
      return new Response(JSON.stringify({
        rows: [],
        metadata: {
          candidateRows: 0,
          estimatedGroups: 0,
          estimatedCost: 0,
          limits: {},
          requiresAggregation: false,
          allowed: true,
        },
      }), { status: 200 });
    },
  );

  const catalog = await adapter.getAnalysisFieldCatalog();
  const estimate = await adapter.estimateAnalysis({
    population: { includeLegacy: false, trustStates: ["current_validated"] },
    filters: { op: "and", children: [] },
    screeningColumns: ["metric.expectancy_r"],
    groupBy: [],
    measures: [],
    orderBy: [],
    chart: { kind: "table" },
  });

  assert.equal(catalog[0].field, "metric.expectancy_r");
  assert.equal(estimate.allowed, true);
  assert.match(requests[0].url, /research_analysis_field_catalog/);
  assert.match(requests[1].url, /rpc\/research_analysis_query/);
  assert.equal(requests[1].options.method, "POST");
  assert.equal(
    JSON.parse(requests[1].options.body).p_estimate_only,
    true,
  );
  assert.equal(requests[1].options.headers.apikey, "anon-key");
  assert.equal(requests[1].options.headers.Authorization, "Bearer anon-key");
});
