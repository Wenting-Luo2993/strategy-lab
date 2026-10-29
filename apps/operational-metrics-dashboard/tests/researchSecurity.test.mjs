import assert from "node:assert/strict";
import test from "node:test";

import {
  assertSafeDashboardEnvironment,
  isServiceRoleCredential,
} from "../src/data/researchEnvironment.ts";
import { createResearchDataAdapter } from "../src/data/researchAdapter.ts";

function jwt(payload) {
  const encode = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "HS256" })}.${encode(payload)}.signature`;
}

test("dashboard rejects service-role variable names even when not NEXT_PUBLIC", () => {
  assert.throws(
    () => assertSafeDashboardEnvironment({ RESEARCH_SUPABASE_SERVICE_ROLE_KEY: "secret" }),
    /refuses service-role credential variable/,
  );
  assert.throws(
    () => assertSafeDashboardEnvironment({ NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY: "secret" }),
    /refuses service-role credential variable/,
  );
});

test("dashboard rejects a service-role JWT supplied as an anonymous key", () => {
  const serviceRole = jwt({ role: "service_role" });
  assert.equal(isServiceRoleCredential(serviceRole), true);
  assert.throws(
    () => assertSafeDashboardEnvironment({ NEXT_PUBLIC_SUPABASE_ANON_KEY: serviceRole }),
    /only an anonymous key is allowed/,
  );
  assert.throws(
    () => assertSafeDashboardEnvironment({ RESEARCH_SUPABASE_KEY: serviceRole }),
    /refuses a service-role credential value/,
  );
  assert.doesNotThrow(
    () => assertSafeDashboardEnvironment({ NEXT_PUBLIC_SUPABASE_ANON_KEY: jwt({ role: "anon" }) }),
  );
});

test("adapter contract exposes no mutation methods and has exactly two implementations", () => {
  const fixture = createResearchDataAdapter({ NEXT_PUBLIC_RESEARCH_DATA_SOURCE: "fixture" });
  const supabase = createResearchDataAdapter({
    NEXT_PUBLIC_RESEARCH_DATA_SOURCE: "supabase",
    NEXT_PUBLIC_SUPABASE_URL: "https://example.supabase.co",
    NEXT_PUBLIC_SUPABASE_ANON_KEY: "anon-key",
  });

  assert.deepEqual(
    Object.getOwnPropertyNames(Object.getPrototypeOf(fixture)).sort(),
    [
      "constructor",
      "estimateAnalysis",
      "getAnalysisFieldCatalog",
      "getExperimentPublications",
      "getOverview",
      "getPublication",
      "listPublications",
      "runAnalysis",
    ].sort(),
  );
  assert.deepEqual(
    Object.getOwnPropertyNames(Object.getPrototypeOf(supabase)).filter((name) => name !== "constructor").sort(),
    [
      "estimateAnalysis",
      "getAnalysisFieldCatalog",
      "getExperimentPublications",
      "getOverview",
      "getPublication",
      "listPublications",
      "runAnalysis",
    ].sort(),
  );
  for (const adapter of [fixture, supabase]) {
    assert.equal("create" in adapter, false);
    assert.equal("update" in adapter, false);
    assert.equal("delete" in adapter, false);
    assert.equal("publish" in adapter, false);
  }
});
