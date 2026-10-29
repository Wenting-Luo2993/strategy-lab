import assert from "node:assert/strict";
import test from "node:test";

import { decodeResearchPublication, findForbiddenPayloadPath } from "../src/data/researchDecoder.ts";
import { FixtureResearchDataAdapter } from "../src/data/researchFixtureAdapter.ts";
import { researchFixtureRows } from "../src/data/researchFixtures.ts";
import {
  pageCount,
  researchStatusOptions,
  trustBadges,
} from "../src/data/researchPresentation.ts";

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

test("status filter choices expose decision states and stay kind-aware", () => {
  const allValues = researchStatusOptions("all").map(([value]) => value);
  for (const value of [
    "reliable",
    "rejected",
    "inconclusive",
    "review_required",
    "not_assessed",
    "authorized",
    "claimed",
    "interrupted",
    "frozen",
    "archived",
  ]) {
    assert.ok(allValues.includes(value), `missing all-kind status ${value}`);
  }

  assert.deepEqual(
    researchStatusOptions("study_reliability").map(([value]) => value),
    ["", "reliable", "rejected", "inconclusive", "review_required", "not_assessed"],
  );
  assert.deepEqual(
    researchStatusOptions("holdout_claim").map(([value]) => value),
    ["", "claimed"],
  );
});

test("fixture overview counts validation attention without event-level data", async () => {
  const adapter = new FixtureResearchDataAdapter();
  const overview = await adapter.getOverview();
  assert.deepEqual(overview, {
    total: 6,
    runSummaries: 3,
    registryRecords: 3,
    experimentEvents: 0,
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

test("decoder exposes experiment decision states without promoting missing evidence", () => {
  const cases = [
    ["study_reliability", { reliability: { verdict: "reliable", report: {} } }, "reliable"],
    ["study_reliability", { reliability: { verdict: "rejected", report: {} } }, "rejected"],
    ["study_reliability", { reliability: { verdict: "inconclusive", report: {} } }, "inconclusive"],
    ["study_reliability", { reliability: { report: {} } }, "not_assessed"],
    ["holdout_terminal", { terminal: { state: "interrupted" } }, "interrupted"],
    ["experiment_archive", { archive: { state: "archived" } }, "archived"],
  ];
  for (const [aggregateType, section, expectedStatus] of cases) {
    const decoded = decodeResearchPublication({
      idempotency_key: `experiment:fixture:${aggregateType}`,
      aggregate_type: aggregateType,
      aggregate_id: "experiment-fixture",
      schema_version: 1,
      publication_version: 1,
      payload_sha256: "a".repeat(64),
      acknowledged_at: "2026-10-28T00:00:00Z",
      payload: {
        identity: {
          experiment_id: "experiment-fixture",
          origin_device_id: "fixture-device",
        },
        timestamps: { recorded_at: "2026-10-28T00:00:00Z" },
        ...section,
      },
    });

    assert.equal(decoded.kind, aggregateType);
    assert.equal(decoded.status, expectedStatus);
    assert.equal(decoded.experimentId, "experiment-fixture");
  }
});

test("decoder preserves complete reliability and authorization decision evidence", () => {
  const reliability = decodeResearchPublication({
    idempotency_key: "experiment:exp-1:reliability",
    aggregate_type: "study_reliability",
    aggregate_id: "exp-1",
    schema_version: 1,
    publication_version: 1,
    payload_sha256: "a".repeat(64),
    acknowledged_at: "2026-10-28T00:00:00Z",
    payload: {
      identity: { experiment_id: "exp-1" },
      reliability: {
        verdict: "rejected",
        report: { verdict_reason: "Confidence interval crossed zero." },
      },
      criteria: [{ criterion_id: "REL-STAT-001", outcome: "fail" }],
      robustness: {
        regime_observations: [{ regime: "high_volatility", expectancy_r: -0.1 }],
      },
      timestamps: { recorded_at: "2026-10-28T00:00:00Z" },
    },
  });
  assert.equal(reliability.kind, "study_reliability");
  assert.equal(reliability.eventData.criteria[0].criterion_id, "REL-STAT-001");
  assert.equal(
    reliability.eventData.robustness.regime_observations[0].regime,
    "high_volatility",
  );

  const authorization = decodeResearchPublication({
    idempotency_key: "experiment:exp-1:holdout:authorized",
    aggregate_type: "holdout_authorization",
    aggregate_id: "exp-1",
    schema_version: 1,
    publication_version: 1,
    payload_sha256: "b".repeat(64),
    acknowledged_at: "2026-10-28T01:00:00Z",
    payload: {
      identity: { experiment_id: "exp-1" },
      eligibility: {
        policy_hash: "policy",
        definition_hash: "definition",
        report_hash: "report",
      },
      authorization: { state: "authorized", requested_by: "Bloom" },
      timestamps: { recorded_at: "2026-10-28T01:00:00Z" },
    },
  });
  assert.equal(authorization.kind, "holdout_authorization");
  assert.deepEqual(authorization.eventData.eligibility, {
    policy_hash: "policy",
    definition_hash: "definition",
    report_hash: "report",
  });
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
