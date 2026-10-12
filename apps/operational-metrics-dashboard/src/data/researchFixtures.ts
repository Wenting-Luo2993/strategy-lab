import { decodeResearchPublication } from "./researchDecoder.ts";
import type { ResearchPublication, ResearchPublicationRow } from "./researchTypes.ts";

const sha = (character: string) => character.repeat(64);
const executionIdentity = {
  execution_model_version: 6,
  intrabar_exit_resolution: "pessimistic",
  gap_fill_policy: "next_available_open",
  entry_fill_policy: "next_bar_open",
  enforce_buying_power: true,
  max_gross_leverage: 1,
  commission_model: "tiered",
  exit_slippage_model: "ticks_by_reason",
  exit_slippage_ticks: { STOP: 2, EOD: 1, TP: 1 },
};

export const researchFixtureRows: ResearchPublicationRow[] = [
  runRow("run:01JQRESEARCH000000000000001", "01JQRESEARCH000000000000001", "EXP-104", "orb_production", "completed", "2026-10-10T18:45:00Z", {
    expectancy_r: 0.42,
    net_pnl: 3840.25,
    max_drawdown: -0.083,
  }, ["research-store-v1", "execution-model-v6", "survivorship-bias-present"]),
  runRow("run:01JQRESEARCH000000000000002", "01JQRESEARCH000000000000002", "EXP-103", "orb_production", "validation_failed", "2026-10-09T17:30:00Z", {
    expectancy_r: -0.08,
    net_pnl: -812.5,
  }, ["research-store-v1", "execution-model-v6"], 2),
  runRow("run:legacy-EXP-073", "legacy-EXP-073", "EXP-073", "legacy_orb", "completed", "2026-10-08T13:15:00Z", {
    expectancy_r: 0.71,
  }, ["legacy-uncontrolled", "unknown-pre-p2b"]),
  registryRow("hypothesis", "HYP-019", "validated", "2026-10-10T18:40:00Z", []),
  registryRow("experiment", "EXP-104", "completed", "2026-10-10T18:44:00Z", [
    { relation: "tests", record_type: "hypothesis", record_id: "HYP-019" },
  ]),
  registryRow("experiment", "EXP-073", "completed", "2026-10-08T13:14:00Z", [], true),
];

function runRow(
  idempotencyKey: string,
  runId: string,
  displayAlias: string,
  strategyId: string,
  state: string,
  acknowledgedAt: string,
  metricValues: Record<string, number>,
  badges: string[],
  blocking = 0,
): ResearchPublicationRow {
  return {
    idempotency_key: idempotencyKey,
    aggregate_type: "run_summary",
    aggregate_id: runId,
    schema_version: 1,
    publication_version: 1,
    payload_sha256: sha(runId.includes("legacy") ? "7" : runId.endsWith("2") ? "2" : "1"),
    acknowledged_at: acknowledgedAt,
    payload: {
      identity: {
        run_id: runId,
        display_alias: displayAlias,
        fingerprint: sha("a"),
        fingerprint_version: 1,
        strategy_id: strategyId,
        methodology_version: badges[0],
        execution_model_version: badges.includes("execution-model-v6") ? 6 : null,
        metric_calculation_version: 2,
        feature_set_version: 3,
        dataset_fingerprint: "qqq-2022-dev-v3",
        split_manifest_hash: sha("b"),
        code_commit: "6d2c085",
        code_dirty: false,
        lineage_id: `lineage-${displayAlias}`,
        origin_device_id: "research-workstation",
        badges,
      },
      state,
      timestamps: {
        created_at: "2026-10-08T12:00:00Z",
        updated_at: acknowledgedAt,
      },
      metrics: Object.entries(metricValues).map(([key, value]) => ({
        key,
        value,
        calculation_version: 2,
      })),
      evidence: {
        aggregate_trade_count: 124,
        trade_ledger_sha256: sha("c"),
        equity_curve_sha256: sha("d"),
        equity_point_count: 252,
        retention_profile: "summary",
        first_session: "2022-01-03",
        last_session: "2022-12-30",
        starting_equity: 100000,
        ending_equity: 103840.25,
        gross_pnl: 4210.5,
        total_costs: 370.25,
        net_pnl: metricValues.net_pnl ?? null,
        max_observed_leverage: 0.96,
        min_cash: 41250,
      },
      validation: {
        profile_name: "research-default",
        scope: "run",
        target_state: state,
        registry_hash_at_execution: sha("e"),
        current_registry_hash: sha("e"),
        leakage_passed: blocking === 0,
        finding_counts: { blocking, review: state === "completed" ? 1 : 0, advisory: 2 },
      },
    },
  };
}

function registryRow(
  recordType: string,
  recordId: string,
  status: string,
  acknowledgedAt: string,
  lineage: Array<{ relation: string; record_type: string; record_id: string }>,
  legacy = false,
): ResearchPublicationRow {
  return {
    idempotency_key: `registry:${recordType}:${recordId}:${sha(legacy ? "8" : "f")}`,
    aggregate_type: "registry_record",
    aggregate_id: `${recordType}:${recordId}`,
    schema_version: 1,
    publication_version: 1,
    payload_sha256: sha(legacy ? "8" : "f"),
    acknowledged_at: acknowledgedAt,
    payload: {
      identity: {
        record_type: recordType,
        record_id: recordId,
        methodology_version: legacy ? "legacy-uncontrolled" : "research-store-v1",
        execution_model_version: legacy ? null : 6,
        execution_identity: legacy ? {} : executionIdentity,
        record_origin: legacy ? "legacy_yaml" : "research_store",
        origin_device_id: "research-workstation",
      },
      status,
      checksum: { canonical_sha256: sha(legacy ? "8" : "f") },
      lineage,
      badges: legacy
        ? ["legacy-uncontrolled", "unknown-pre-p2b"]
        : ["research-store-v1", "execution-model-v6"],
      timestamps: { recorded_at: acknowledgedAt },
    },
  };
}

export function clonedResearchFixtures(): ResearchPublication[] {
  return researchFixtureRows.map((item) => decodeResearchPublication(structuredClone(item)));
}
