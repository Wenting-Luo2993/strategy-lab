import type {
  ResearchEvidence,
  ResearchExperimentEventKind,
  ResearchExecutionIdentity,
  ResearchLineage,
  ResearchMetric,
  ResearchPublication,
  ResearchPublicationRow,
  ResearchRunSummary,
  ResearchValidation,
} from "./researchTypes.ts";

const supportedSchemaVersion = 1;
const experimentEventKinds = new Set<ResearchExperimentEventKind>([
  "experiment_definition",
  "experiment_membership",
  "experiment_outcome",
  "study_reliability",
  "methodology_freeze",
  "holdout_authorization",
  "holdout_claim",
  "holdout_terminal",
  "experiment_archive",
]);
const forbiddenPayloadKeys = new Set([
  "trade",
  "trades",
  "fill",
  "fills",
  "tradeledger",
  "individualfills",
  "equitycurve",
  "equitycurves",
  "equitypoint",
  "equitypoints",
  "rawbar",
  "rawbars",
  "pricebar",
  "pricebars",
  "barevents",
  "orderevents",
  "perevent",
  "events",
  "tradedata",
  "fillrecords",
  "equityhistory",
  "barseries",
]);
const approvedChecksumKeys = new Set([
  "tradeledgersha256",
  "equitycurvesha256",
  "payloadsha256",
  "canonicalsha256",
]);

export function decodeResearchPublication(row: ResearchPublicationRow): ResearchPublication {
  const messages: string[] = [];
  const rawKind = string(row.aggregate_type);
  const kind = rawKind === "registry_record"
    ? "registry_record"
    : experimentEventKinds.has(rawKind as ResearchExperimentEventKind)
      ? rawKind as ResearchExperimentEventKind
      : "run_summary";
  if (
    rawKind !== "run_summary"
    && rawKind !== "registry_record"
    && !experimentEventKinds.has(rawKind as ResearchExperimentEventKind)
  ) {
    messages.push("Unknown aggregate type.");
  }
  const schemaVersion = integer(row.schema_version) ?? 0;
  if (schemaVersion !== supportedSchemaVersion) {
    messages.push(`Schema version ${schemaVersion || "unknown"} is not supported.`);
  }
  const payload = record(row.payload);
  const forbiddenPath = findForbiddenPayloadPath(payload);
  if (forbiddenPath) {
    messages.push(`Publication contains prohibited event-level data at ${forbiddenPath}.`);
  }
  if (!payload) {
    messages.push("Payload is not an object.");
  }
  const safePayload = forbiddenPath ? null : payload;
  const identity = record(safePayload?.identity);
  const badges = kind === "run_summary"
    ? strings(identity?.badges)
    : kind === "registry_record"
      ? strings(safePayload?.badges)
      : [kind.replaceAll("_", " ")];
  const eventSection = kind !== "run_summary" && kind !== "registry_record"
    ? experimentEventSection(kind, safePayload)
    : null;
  const eventData = experimentEventData(safePayload);
  const base = {
    idempotencyKey: string(row.idempotency_key) ?? "invalid",
    aggregateId: string(row.aggregate_id) ?? "invalid",
    schemaVersion,
    publicationVersion: integer(row.publication_version) ?? 0,
    payloadSha256: string(row.payload_sha256) ?? "",
    acknowledgedAt: string(row.acknowledged_at) ?? "",
    compatibility: messages.length
      ? schemaVersion === supportedSchemaVersion && forbiddenPath === null ? "invalid" as const : "unsupported" as const
      : "supported" as const,
    compatibilityMessages: messages,
    badges,
    methodologyVersion: string(identity?.methodology_version),
    executionModelVersion: integer(identity?.execution_model_version),
    originDeviceId: string(identity?.origin_device_id),
    status: kind === "run_summary"
      ? string(safePayload?.state)
      : kind === "registry_record"
        ? string(safePayload?.status)
        : eventStatus(kind, eventSection),
    publishedIdentity: `${string(row.aggregate_id) ?? "invalid"} · v${integer(row.publication_version) ?? 0}`,
  };

  if (kind !== "run_summary" && kind !== "registry_record") {
    return {
      ...base,
      kind,
      experimentId: string(identity?.experiment_id),
      recordedAt: string(record(safePayload?.timestamps)?.recorded_at)
        ?? string(record(safePayload?.timestamps)?.created_at)
        ?? string(record(safePayload?.timestamps)?.executed_at),
      eventData,
    };
  }

  function experimentEventData(
    payload: Record<string, unknown> | null,
  ): Record<string, unknown> {
    if (!payload) return {};
    return Object.fromEntries(
      Object.entries(payload).filter(
        ([key]) => key !== "identity" && key !== "timestamps",
      ),
    );
  }

  if (kind === "registry_record") {
    return {
      ...base,
      kind,
      recordType: string(identity?.record_type),
      recordId: string(identity?.record_id),
      recordOrigin: string(identity?.record_origin),
      canonicalSha256: string(record(safePayload?.checksum)?.canonical_sha256),
      recordedAt: string(record(safePayload?.timestamps)?.recorded_at),
      executionIdentity: decodeExecutionIdentity(record(identity?.execution_identity)),
      lineage: decodeLineage(safePayload?.lineage),
    };
  }

  function experimentEventSection(
    kind: ResearchExperimentEventKind,
    payload: Record<string, unknown> | null,
  ): Record<string, unknown> | unknown[] | null {
    const key = ({
      experiment_definition: "definition",
      experiment_membership: "membership",
      experiment_outcome: "outcome",
      study_reliability: "reliability",
      methodology_freeze: "methodology",
      holdout_authorization: "authorization",
      holdout_claim: "claim",
      holdout_terminal: "terminal",
      experiment_archive: "archive",
    } as const)[kind];
    const section = payload?.[key];
    if (Array.isArray(section)) return section;
    return record(section);
  }

  function eventStatus(
    kind: ResearchExperimentEventKind,
    section: Record<string, unknown> | unknown[] | null,
  ): string | null {
    const data = Array.isArray(section) ? null : section;
    if (kind === "study_reliability") return string(data?.verdict) ?? "not_assessed";
    if (kind === "methodology_freeze") return "frozen";
    if (kind === "experiment_archive") return "archived";
    if (kind === "experiment_membership") return string(data?.segment_role);
    return string(data?.state);
  }

  return {
    ...base,
    kind,
    runId: string(identity?.run_id),
    displayAlias: string(identity?.display_alias),
    strategyId: string(identity?.strategy_id),
    fingerprint: string(identity?.fingerprint),
    fingerprintVersion: integer(identity?.fingerprint_version),
    metricCalculationVersion: integer(identity?.metric_calculation_version),
    featureSetVersion: integer(identity?.feature_set_version),
    datasetFingerprint: string(identity?.dataset_fingerprint),
    splitManifestHash: string(identity?.split_manifest_hash),
    codeCommit: string(identity?.code_commit),
    codeDirty: boolean(identity?.code_dirty),
    lineageId: string(identity?.lineage_id),
    createdAt: string(record(safePayload?.timestamps)?.created_at),
    updatedAt: string(record(safePayload?.timestamps)?.updated_at),
    metrics: decodeMetrics(safePayload?.metrics),
    evidence: decodeEvidence(record(safePayload?.evidence)),
    validation: decodeValidation(record(safePayload?.validation)),
    engineIntegrity: decodeEngineIntegrity(record(safePayload?.engine_integrity)),
    procedureExecution: string(safePayload?.procedure_execution),
    researchReliability: decodeReliability(safePayload?.research_reliability),
  };
}

function decodeReliability(value: unknown): ResearchRunSummary["researchReliability"] {
  return ["reliable", "rejected", "inconclusive", "review_required"].includes(String(value))
    ? value as ResearchRunSummary["researchReliability"] : "not_assessed";
}

function decodeEngineIntegrity(value: Record<string, unknown> | null): ResearchRunSummary["engineIntegrity"] {
  const status = value?.status === "pass" || value?.status === "fail"
    ? value.status : "not_assessed";
  const report = record(value?.report);
  const rawFindings = Array.isArray(report?.findings) ? report.findings : [];
  return {
    status,
    findings: rawFindings.flatMap((item) => {
      const finding = record(item);
      const checkId = string(finding?.check_id);
      if (!checkId) return [];
      return [{
        checkId,
        category: string(finding?.category) ?? "unknown",
        passed: Boolean(finding?.passed),
        observed: finding?.observed ?? null,
        expected: finding?.expected ?? null,
        residual: number(finding?.residual),
        applicable: finding?.applicable !== false,
        context: record(finding?.context) ?? {},
      }];
    }),
  };
}

export function findForbiddenPayloadPath(value: unknown, path = "payload"): string | null {
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      const found = findForbiddenPayloadPath(value[index], `${path}[${index}]`);
      if (found) return found;
    }
    return null;
  }
  const object = record(value);
  if (!object) return null;
  for (const [key, nested] of Object.entries(object)) {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, "");
    const current = `${path}.${key}`;
    if (forbiddenPayloadKeys.has(normalized) && !approvedChecksumKeys.has(normalized)) {
      return current;
    }
    const found = findForbiddenPayloadPath(nested, current);
    if (found) return found;
  }
  return null;
}

function decodeMetrics(value: unknown): ResearchMetric[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const metric = record(item);
    const key = string(metric?.key);
    const metricValue = scalar(metric?.value);
    return key === null ? [] : [{
      key,
      value: metricValue,
      calculationVersion: integer(metric?.calculation_version),
    }];
  });
}

function decodeEvidence(value: Record<string, unknown> | null): ResearchEvidence {
  return {
    aggregateTradeCount: integer(value?.aggregate_trade_count),
    tradeLedgerSha256: string(value?.trade_ledger_sha256),
    equityCurveSha256: string(value?.equity_curve_sha256),
    equityPointCount: integer(value?.equity_point_count),
    retentionProfile: string(value?.retention_profile),
    firstSession: string(value?.first_session),
    lastSession: string(value?.last_session),
    startingEquity: number(value?.starting_equity),
    endingEquity: number(value?.ending_equity),
    grossPnl: number(value?.gross_pnl),
    totalCosts: number(value?.total_costs),
    netPnl: number(value?.net_pnl),
    maxObservedLeverage: number(value?.max_observed_leverage),
    minCash: number(value?.min_cash),
  };
}

function decodeValidation(value: Record<string, unknown> | null): ResearchValidation {
  const findings = record(value?.finding_counts);
  return {
    profileName: string(value?.profile_name),
    scope: string(value?.scope),
    targetState: string(value?.target_state),
    registryHashAtExecution: string(value?.registry_hash_at_execution),
    currentRegistryHash: string(value?.current_registry_hash),
    leakagePassed: boolean(value?.leakage_passed),
    findings: {
      blocking: integer(findings?.blocking) ?? 0,
      review: integer(findings?.review) ?? 0,
      advisory: integer(findings?.advisory) ?? 0,
    },
  };
}

function decodeExecutionIdentity(value: Record<string, unknown> | null): ResearchExecutionIdentity {
  const ticks = record(value?.exit_slippage_ticks);
  return {
    executionModelVersion: integer(value?.execution_model_version),
    intrabarExitResolution: string(value?.intrabar_exit_resolution),
    gapFillPolicy: string(value?.gap_fill_policy),
    entryFillPolicy: string(value?.entry_fill_policy),
    enforceBuyingPower: boolean(value?.enforce_buying_power),
    maxGrossLeverage: number(value?.max_gross_leverage),
    commissionModel: string(value?.commission_model),
    exitSlippageModel: string(value?.exit_slippage_model),
    exitSlippageTicks: Object.fromEntries(
      Object.entries(ticks ?? {}).flatMap(([key, raw]) => {
        const decoded = number(raw);
        return decoded === null ? [] : [[key, decoded]];
      }),
    ),
  };
}

function decodeLineage(value: unknown): ResearchLineage[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const relation = record(item);
    const relationName = string(relation?.relation);
    const recordType = string(relation?.record_type);
    const recordId = string(relation?.record_id);
    return relationName && recordType && recordId
      ? [{ relation: relationName, recordType, recordId }]
      : [];
  });
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function string(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function boolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function scalar(value: unknown): string | number | boolean | null {
  return typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))
    ? value
    : null;
}
