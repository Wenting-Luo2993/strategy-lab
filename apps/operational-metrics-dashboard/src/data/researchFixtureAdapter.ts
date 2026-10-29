import { FixtureAnalysisAdapter } from "./researchAnalysis.ts";
import { analysisFixtureRows, clonedResearchFixtures } from "./researchFixtures.ts";
import type {
  ResearchDataAdapter,
  ResearchExperimentEvent,
  ResearchListQuery,
  ResearchOverview,
  ResearchPage,
  ResearchPublication,
} from "./researchTypes.ts";

export class FixtureResearchDataAdapter implements ResearchDataAdapter {
  readonly source = "fixture" as const;
  readonly #items: ResearchPublication[];
  readonly #analysis: FixtureAnalysisAdapter;

  constructor(items = clonedResearchFixtures()) {
    this.#items = items;
    this.#analysis = new FixtureAnalysisAdapter(items, analysisFixtureRows);
  }

  async getOverview(): Promise<ResearchOverview> {
    const runs = this.#items.filter((item) => item.kind === "run_summary");
    const registry = this.#items.filter((item) => item.kind === "registry_record");
    return {
      total: this.#items.length,
      runSummaries: runs.length,
      registryRecords: registry.length,
      experimentEvents: this.#items.length - runs.length - registry.length,
      completedRuns: runs.filter((item) => item.status === "completed").length,
      validationAttention: runs.filter((item) =>
        item.validation.findings.blocking > 0 || item.validation.leakagePassed === false
      ).length,
    };
  }

  async listPublications(query: ResearchListQuery): Promise<ResearchPage> {
    const page = Math.max(1, query.page);
    const pageSize = Math.min(100, Math.max(1, query.pageSize));
    const search = query.search?.trim().toLowerCase();
    const filtered = this.#items
      .filter((item) => !query.kind || query.kind === "all" || item.kind === query.kind)
      .filter((item) => !query.status || item.status === query.status)
      .filter((item) => !search || [
        item.aggregateId,
        item.idempotencyKey,
        item.methodologyVersion,
        item.originDeviceId,
        item.kind === "run_summary"
          ? item.strategyId
          : item.kind === "registry_record"
            ? item.recordType
            : item.experimentId,
        item.kind === "run_summary"
          ? item.displayAlias
          : item.kind === "registry_record"
            ? item.recordId
            : item.experimentId,
      ].some((value) => value?.toLowerCase().includes(search)))
      .sort((left, right) => comparePublications(left, right, query));
    const start = (page - 1) * pageSize;
    return {
      items: filtered.slice(start, start + pageSize),
      page,
      pageSize,
      total: filtered.length,
      rejectedCount: filtered.filter((item) => item.compatibility !== "supported").length,
    };
  }

  async getPublication(idempotencyKey: string): Promise<ResearchPublication | null> {
    return this.#items.find((item) => item.idempotencyKey === idempotencyKey) ?? null;
  }

  async getExperimentPublications(experimentId: string) {
    return this.#items.filter((item): item is ResearchExperimentEvent =>
      item.kind !== "run_summary"
      && item.kind !== "registry_record"
      && item.experimentId === experimentId
    );
  }

  getAnalysisFieldCatalog() {
    return this.#analysis.getAnalysisFieldCatalog();
  }

  estimateAnalysis(query: Parameters<FixtureAnalysisAdapter["estimateAnalysis"]>[0]) {
    return this.#analysis.estimateAnalysis(query);
  }

  runAnalysis(query: Parameters<FixtureAnalysisAdapter["runAnalysis"]>[0]) {
    return this.#analysis.runAnalysis(query);
  }
}

function comparePublications(
  left: ResearchPublication,
  right: ResearchPublication,
  query: ResearchListQuery,
): number {
  const field = query.sort ?? "acknowledged_at";
  const direction = query.direction === "asc" ? 1 : -1;
  const leftValue = field === "publication_version"
    ? left.publicationVersion
    : field === "aggregate_id" ? left.aggregateId : left.acknowledgedAt;
  const rightValue = field === "publication_version"
    ? right.publicationVersion
    : field === "aggregate_id" ? right.aggregateId : right.acknowledgedAt;
  if (typeof leftValue === "number" && typeof rightValue === "number") {
    return (leftValue - rightValue) * direction;
  }
  return String(leftValue).localeCompare(String(rightValue)) * direction;
}
