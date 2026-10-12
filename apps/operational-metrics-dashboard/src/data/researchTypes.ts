export type ResearchPublicationKind = "run_summary" | "registry_record";
export type ResearchPublicationCompatibility = "supported" | "unsupported" | "invalid";
export type ResearchSortField = "acknowledged_at" | "publication_version" | "aggregate_id";
export type SortDirection = "asc" | "desc";

export type ResearchMetric = {
  key: string;
  value: number | string | boolean | null;
  calculationVersion: number | null;
};

export type ResearchEvidence = {
  aggregateTradeCount: number | null;
  tradeLedgerSha256: string | null;
  equityCurveSha256: string | null;
  equityPointCount: number | null;
  retentionProfile: string | null;
  firstSession: string | null;
  lastSession: string | null;
  startingEquity: number | null;
  endingEquity: number | null;
  grossPnl: number | null;
  totalCosts: number | null;
  netPnl: number | null;
  maxObservedLeverage: number | null;
  minCash: number | null;
};

export type ResearchValidation = {
  profileName: string | null;
  scope: string | null;
  targetState: string | null;
  registryHashAtExecution: string | null;
  currentRegistryHash: string | null;
  leakagePassed: boolean | null;
  findings: {
    blocking: number;
    review: number;
    advisory: number;
  };
};

export type ResearchExecutionIdentity = {
  executionModelVersion: number | null;
  intrabarExitResolution: string | null;
  gapFillPolicy: string | null;
  entryFillPolicy: string | null;
  enforceBuyingPower: boolean | null;
  maxGrossLeverage: number | null;
  commissionModel: string | null;
  exitSlippageModel: string | null;
  exitSlippageTicks: Record<string, number>;
};

export type ResearchLineage = {
  relation: string;
  recordType: string;
  recordId: string;
};

type ResearchPublicationBase = {
  idempotencyKey: string;
  aggregateId: string;
  schemaVersion: number;
  publicationVersion: number;
  payloadSha256: string;
  acknowledgedAt: string;
  compatibility: ResearchPublicationCompatibility;
  compatibilityMessages: string[];
  badges: string[];
  methodologyVersion: string | null;
  executionModelVersion: number | null;
  originDeviceId: string | null;
  status: string | null;
  publishedIdentity: string;
};

export type ResearchRunSummary = ResearchPublicationBase & {
  kind: "run_summary";
  runId: string | null;
  displayAlias: string | null;
  strategyId: string | null;
  fingerprint: string | null;
  fingerprintVersion: number | null;
  metricCalculationVersion: number | null;
  featureSetVersion: number | null;
  datasetFingerprint: string | null;
  splitManifestHash: string | null;
  codeCommit: string | null;
  codeDirty: boolean | null;
  lineageId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  metrics: ResearchMetric[];
  evidence: ResearchEvidence;
  validation: ResearchValidation;
};

export type ResearchRegistryRecord = ResearchPublicationBase & {
  kind: "registry_record";
  recordType: string | null;
  recordId: string | null;
  recordOrigin: string | null;
  canonicalSha256: string | null;
  recordedAt: string | null;
  executionIdentity: ResearchExecutionIdentity;
  lineage: ResearchLineage[];
};

export type ResearchPublication = ResearchRunSummary | ResearchRegistryRecord;

export type ResearchListQuery = {
  page: number;
  pageSize: number;
  kind?: ResearchPublicationKind | "all";
  status?: string;
  search?: string;
  sort?: ResearchSortField;
  direction?: SortDirection;
};

export type ResearchPage = {
  items: ResearchPublication[];
  page: number;
  pageSize: number;
  total: number;
  rejectedCount: number;
};

export type ResearchOverview = {
  total: number;
  runSummaries: number;
  registryRecords: number;
  completedRuns: number;
  validationAttention: number;
};

export type ResearchDataAdapter = {
  readonly source: "fixture" | "supabase";
  getOverview(): Promise<ResearchOverview>;
  listPublications(query: ResearchListQuery): Promise<ResearchPage>;
  getPublication(idempotencyKey: string): Promise<ResearchPublication | null>;
};

export type ResearchPublicationRow = {
  idempotency_key: unknown;
  aggregate_type: unknown;
  aggregate_id: unknown;
  schema_version: unknown;
  publication_version: unknown;
  payload: unknown;
  payload_sha256: unknown;
  acknowledged_at: unknown;
};
