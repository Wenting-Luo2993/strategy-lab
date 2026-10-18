import assert from "node:assert/strict";
import test from "node:test";

import { decodeResearchPublication, findForbiddenPayloadPath } from "../src/data/researchDecoder.ts";
import { FixtureResearchDataAdapter } from "../src/data/researchFixtureAdapter.ts";
import { researchFixtureRows } from "../src/data/researchFixtures.ts";
import { pageCount, trustBadges } from "../src/data/researchPresentation.ts";

test("fixture adapter paginates, filters, and sorts deterministically", async () => {
  const adapter = new FixtureResearchDataAdapter();
  const first = await adapter.listPublications({
    page: 1,
    pageSize: 2,
    kind: "run_summary",
    sort: "acknowledged_at",
    direction: "desc",
  });
  const second = await adapter.listPublications({
    page: 2,
    pageSize: 2,
    kind: "run_summary",
    sort: "acknowledged_at",
    direction: "desc",
  });

  assert.equal(first.total, 3);
  assert.equal(first.items.length, 2);
  assert.equal(second.items.length, 1);
  assert.deepEqual(
    [...first.items, ...second.items].map((item) => item.aggregateId),
    [
      "01JQRESEARCH000000000000001",
      "01JQRESEARCH000000000000002",
      "legacy-EXP-073",
    ],
  );
});

test("fixture overview counts validation attention without event-level data", async () => {
  const adapter = new FixtureResearchDataAdapter();
  const overview = await adapter.getOverview();
  assert.deepEqual(overview, {
    total: 6,
    runSummaries: 3,
    registryRecords: 3,
    completedRuns: 2,
    validationAttention: 1,
  });
  assert.equal(JSON.stringify(researchFixtureRows).includes('"trades"'), false);
  assert.equal(JSON.stringify(researchFixtureRows).includes('"fills"'), false);
  assert.equal(JSON.stringify(researchFixtureRows).includes('"equity_curve"'), false);
});

test("decoder preserves published trust, evidence, validation, and lineage fields", () => {
  const run = decodeResearchPublication(researchFixtureRows[0]);
  assert.equal(run.kind, "run_summary");
  assert.equal(run.methodologyVersion, "research-store-v1");
  assert.equal(run.executionModelVersion, 6);
  assert.equal(run.evidence.aggregateTradeCount, 124);
  assert.equal(run.evidence.tradeLedgerSha256.length, 64);
  assert.equal(run.validation.findings.advisory, 2);
  assert.equal(run.lineageId, "lineage-EXP-104");

  const registry = decodeResearchPublication(researchFixtureRows[4]);
  assert.equal(registry.kind, "registry_record");
  assert.equal(registry.executionIdentity.entryFillPolicy, "next_bar_open");
  assert.deepEqual(registry.lineage, [{
    relation: "tests",
    recordType: "hypothesis",
    recordId: "HYP-019",
  }]);
});

test("decoder keeps integrity, procedure, and reliability as separate statuses", () => {
  const row = structuredClone(researchFixtureRows[0]);
  row.payload.engine_integrity = {
    status: "pass",
    report: {
      findings: [{
        check_id: "ENG-CROSS-001",
        category: "cross_metric_consistency",
        passed: true,
        observed: 12,
        expected: 12,
        residual: 0,
        applicable: true,
        context: { ledger: "aggregate" },
      }],
    },
  };
  row.payload.procedure_execution = "completed";
  row.payload.research_reliability = "rejected";

  const decoded = decodeResearchPublication(row);

  assert.equal(decoded.kind, "run_summary");
  assert.equal(decoded.engineIntegrity.status, "pass");
  assert.equal(decoded.engineIntegrity.findings[0].checkId, "ENG-CROSS-001");
  assert.deepEqual(decoded.engineIntegrity.findings[0].context, { ledger: "aggregate" });
  assert.equal(decoded.procedureExecution, "completed");
  assert.equal(decoded.researchReliability, "rejected");
});

test("decoder refuses prohibited event-level aliases instead of exposing raw data", () => {
  const row = structuredClone(researchFixtureRows[0]);
  row.payload.evidence.raw_bars = [{ close: 1 }];
  const decoded = decodeResearchPublication(row);

  assert.equal(findForbiddenPayloadPath(row.payload), "payload.evidence.raw_bars");
  assert.equal(decoded.compatibility, "unsupported");
  assert.equal(decoded.metrics.length, 0);
  assert.equal(decoded.evidence.aggregateTradeCount, null);
});

test("trust badges expose legacy, pre-P2b, survivorship, and validation state", () => {
  const current = decodeResearchPublication(researchFixtureRows[0]);
  const legacy = decodeResearchPublication(researchFixtureRows[2]);
  const failed = decodeResearchPublication(researchFixtureRows[1]);

  assert.ok(trustBadges(current).some((badge) => badge.label.includes("survivorship")));
  assert.ok(trustBadges(legacy).some((badge) => badge.label === "Execution model unknown"));
  assert.ok(trustBadges(legacy).some((badge) => badge.label === "legacy-uncontrolled"));
  assert.ok(trustBadges(failed).some((badge) => badge.label === "2 blocking findings"));
  assert.equal(pageCount(21, 10), 3);
});
