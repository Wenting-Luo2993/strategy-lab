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
