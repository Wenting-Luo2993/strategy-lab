import { clonedResearchFixtures } from "./researchFixtures.ts";
import type {
  ResearchDataAdapter,
  ResearchListQuery,
  ResearchOverview,
  ResearchPage,
  ResearchPublication,
} from "./researchTypes.ts";

export class FixtureResearchDataAdapter implements ResearchDataAdapter {
  readonly source = "fixture" as const;
  readonly #items: ResearchPublication[];

  constructor(items = clonedResearchFixtures()) {
    this.#items = items;
  }

  async getOverview(): Promise<ResearchOverview> {
    const runs = this.#items.filter((item) => item.kind === "run_summary");
    return {
      total: this.#items.length,
      runSummaries: runs.length,
      registryRecords: this.#items.length - runs.length,
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
        item.kind === "run_summary" ? item.strategyId : item.recordType,
        item.kind === "run_summary" ? item.displayAlias : item.recordId,
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
