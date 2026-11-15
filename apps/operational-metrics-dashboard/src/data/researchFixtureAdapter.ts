import { FixtureAnalysisAdapter } from "./researchAnalysis.ts";
import {
  analysisFixtureRows,
  clonedResearchFixtures,
  researchConclusionFixtures,
} from "./researchFixtures.ts";
import type {
  ResearchDataAdapter,
  ResearchExperimentConclusion,
  ResearchExperimentEvent,
  ResearchListQuery,
  ResearchOverview,
  ResearchPage,
  ResearchPublication,
} from "./researchTypes.ts";
import { decodeDailySeries } from "./researchSeries.ts";

export class FixtureResearchDataAdapter implements ResearchDataAdapter {
  readonly source = "fixture" as const;
  readonly #items: ResearchPublication[];
  readonly #analysis: FixtureAnalysisAdapter;
  readonly #conclusions: ResearchExperimentConclusion[];

  constructor(
    items = clonedResearchFixtures(),
    conclusions: ResearchExperimentConclusion[] = researchConclusionFixtures,
  ) {
    this.#items = items;
    this.#analysis = new FixtureAnalysisAdapter(items, analysisFixtureRows);
    this.#conclusions = conclusions;
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

  async getExperimentDailySeries(experimentId: string) {
    // Synthetic preview only; production never falls back to fixture results.
    if (experimentId !== "fixture-daily") return null;
    return decodeDailySeries({
      version: 1, experimentId, basis: "Synthetic daily preview, not research evidence",
      runs: [{
        runId: "fixture-run", label: "Synthetic preview", candidateId: "fixture",
        foldIndex: 0, segmentRole: "test", tradeCount: 4, profitFactor: 1.2, netExpectancyR: 0.275,
        metrics: { sharpe_ratio: 0.5, max_drawdown: -0.03, max_drawdown_duration_days: 10 },
        startingEquity: 100000, tradeChecksum: "", equityChecksum: "",
        rAvailable: true, equityAvailable: true,
        dailyR: [
          { date: "2026-10-01", value: 1 }, { date: "2026-10-02", value: -0.5 },
          { date: "2026-10-05", value: 0.2 }, { date: "2026-10-06", value: 0.4 },
        ],
        dailyEquity: [
          { date: "2026-10-01", value: 101000 }, { date: "2026-10-02", value: 99500 },
          { date: "2026-10-05", value: 99700 }, { date: "2026-10-06", value: 100100 },
        ],
        dailyClosedEquity: [
          { date: "2026-10-01", value: 101000 }, { date: "2026-10-02", value: 99500 },
          { date: "2026-10-05", value: 99700 }, { date: "2026-10-06", value: 100100 },
        ],
        dailyPrices: [
          { date: "2026-10-01", value: 100 }, { date: "2026-10-02", value: 102 },
          { date: "2026-10-05", value: 101 }, { date: "2026-10-06", value: 105 },
        ],
        priceBasis: "Synthetic prices, not a real ticker benchmark.",
      }],
    }, experimentId);
  }

  async getExperimentConclusions(
    experimentIds: readonly string[],
  ): Promise<Record<string, ResearchExperimentConclusion>> {
    const wanted = new Set(experimentIds);
    return Object.fromEntries(
      this.#conclusions
        .filter((item) => wanted.has(item.experimentId))
        .map((item) => [item.experimentId, { ...item }]),
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
