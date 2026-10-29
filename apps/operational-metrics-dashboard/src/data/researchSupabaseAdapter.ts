import { decodeResearchPublication } from "./researchDecoder.ts";
import { assertSafeDashboardEnvironment } from "./researchEnvironment.ts";
import type {
  AnalysisField,
  AnalysisMetadata,
  AnalysisQuery,
  AnalysisResult,
} from "./researchAnalysis.ts";
import type {
  ResearchDataAdapter,
  ResearchExperimentEvent,
  ResearchListQuery,
  ResearchOverview,
  ResearchPage,
  ResearchPublication,
  ResearchPublicationRow,
} from "./researchTypes.ts";

type Fetch = typeof fetch;

export class SupabaseResearchDataAdapter implements ResearchDataAdapter {
  readonly source = "supabase" as const;
  readonly #url: string;
  readonly #anonKey: string;
  readonly #fetch: Fetch;

  constructor(url: string, anonKey: string, fetchImplementation: Fetch = fetch) {
    assertSafeDashboardEnvironment({ NEXT_PUBLIC_RESEARCH_SUPABASE_ANON_KEY: anonKey });
    if (!url || !anonKey) {
      throw new Error("Research Supabase public URL and anonymous key are required.");
    }
    this.#url = url.replace(/\/$/, "");
    this.#anonKey = anonKey;
    this.#fetch = (input, init) =>
      fetchImplementation.call(globalThis, input, init);
  }

  async getOverview(): Promise<ResearchOverview> {
    const [total, runSummaries, registryRecords, experimentEvents, completedRuns, validationAttention] = await Promise.all([
      this.#count(""),
      this.#count("aggregate_type=eq.run_summary"),
      this.#count("aggregate_type=eq.registry_record"),
      this.#count("aggregate_type=not.in.(run_summary,registry_record)"),
      this.#count("aggregate_type=eq.run_summary&payload->>state=eq.completed"),
      this.#count("aggregate_type=eq.run_summary&or=(payload->validation->finding_counts->>blocking.gt.0,payload->validation->>leakage_passed.eq.false)"),
    ]);
    return { total, runSummaries, registryRecords, experimentEvents, completedRuns, validationAttention };
  }

  async listPublications(query: ResearchListQuery): Promise<ResearchPage> {
    const page = Math.max(1, query.page);
    const pageSize = Math.min(100, Math.max(1, query.pageSize));
    const parameters = researchQueryParameters(query);
    const start = (page - 1) * pageSize;
    const response = await this.#requestPublications(parameters, {
      Range: `${start}-${start + pageSize - 1}`,
      "Range-Unit": "items",
      Prefer: "count=exact",
    });
    const rows = await response.json() as ResearchPublicationRow[];
    const decoded = rows.map(decodeResearchPublication);
    return {
      items: decoded,
      page,
      pageSize,
      total: contentRangeTotal(response.headers.get("content-range"), decoded.length),
      rejectedCount: decoded.filter((item) => item.compatibility !== "supported").length,
    };
  }

  async getPublication(idempotencyKey: string): Promise<ResearchPublication | null> {
    const parameters = new URLSearchParams({
      select: "*",
      idempotency_key: `eq.${idempotencyKey}`,
      limit: "1",
    });
    const response = await this.#requestPublications(parameters);
    const rows = await response.json() as ResearchPublicationRow[];
    return rows[0] ? decodeResearchPublication(rows[0]) : null;
  }

  async getExperimentPublications(
    experimentId: string,
  ): Promise<ResearchExperimentEvent[]> {
    const parameters = new URLSearchParams({
      select: "*",
      aggregate_id: `eq.${experimentId}`,
      aggregate_type: "not.in.(run_summary,registry_record)",
      order: "acknowledged_at.asc,idempotency_key.asc",
    });
    const rows: ResearchPublicationRow[] = [];
    const pageSize = 1000;
    for (let start = 0; ; start += pageSize) {
      const response = await this.#requestPublications(parameters, {
        Range: `${start}-${start + pageSize - 1}`,
        "Range-Unit": "items",
      });
      const page = await response.json() as ResearchPublicationRow[];
      rows.push(...page);
      if (page.length < pageSize) break;
    }
    return rows.map(decodeResearchPublication).filter(
      (item): item is ResearchExperimentEvent =>
        item.kind !== "run_summary" && item.kind !== "registry_record",
    ).sort((left, right) =>
      String(left.recordedAt ?? left.acknowledgedAt).localeCompare(
        String(right.recordedAt ?? right.acknowledgedAt),
      ),
    );
  }

  async getAnalysisFieldCatalog(): Promise<AnalysisField[]> {
    const response = await this.#request(
      "/rest/v1/research_analysis_field_catalog"
      + "?select=field_id,label,data_type,unit,cardinality,filter_operators,aggregates,groupable,screenable,trust_restricted"
      + "&order=field_id.asc",
    );
    const rows = await response.json() as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      field: requiredString(row.field_id, "field_id"),
      label: requiredString(row.label, "label"),
      dataType: requiredString(row.data_type, "data_type") as AnalysisField["dataType"],
      unit: typeof row.unit === "string" ? row.unit : null,
      cardinality: finiteInteger(row.cardinality),
      filterOperators: stringList(row.filter_operators) as AnalysisField["filterOperators"],
      aggregates: stringList(row.aggregates) as AnalysisField["aggregates"],
      groupable: row.groupable === true,
      screenable: row.screenable === true,
      trustRestricted: row.trust_restricted === true,
    }));
  }

  async estimateAnalysis(query: AnalysisQuery) {
    const result = await this.#analysisRpc(query, true);
    return result.metadata as Omit<AnalysisMetadata,
      "returnedRows" | "totalResultRows" | "truncated" | "sampled" | "aggregated" |
      "chartMarks" | "chartMarksTruncated">;
  }

  async runAnalysis(query: AnalysisQuery): Promise<AnalysisResult> {
    return this.#analysisRpc(query, false);
  }

  async #count(filters: string): Promise<number> {
    const query = `select=idempotency_key${filters ? `&${filters}` : ""}&limit=1`;
    const response = await this.#requestPublications(query, {
      Range: "0-0",
      "Range-Unit": "items",
      Prefer: "count=exact",
    });
    return contentRangeTotal(response.headers.get("content-range"), 0);
  }

  #requestPublications(
    query: URLSearchParams | string,
    additionalHeaders: Record<string, string> = {},
  ): Promise<Response> {
    return this.#request(`/rest/v1/research_publications?${query}`, {
      headers: additionalHeaders,
    });
  }

  async #analysisRpc(query: AnalysisQuery, estimateOnly: boolean): Promise<AnalysisResult> {
    const response = await this.#request("/rest/v1/rpc/research_analysis_query", {
      method: "POST",
      body: JSON.stringify({ p_query: query, p_estimate_only: estimateOnly }),
    });
    const payload = await response.json() as unknown;
    if (!isObject(payload) || !isObject(payload.metadata) || !Array.isArray(payload.rows)) {
      throw new Error("Supabase analysis RPC returned an invalid response.");
    }
    return payload as unknown as AnalysisResult;
  }

  async #request(
    path: string,
    options: {
      method?: "GET" | "POST";
      body?: string;
      headers?: Record<string, string>;
    } = {},
  ): Promise<Response> {
    const response = await this.#fetch(`${this.#url}${path}`, {
      method: options.method ?? "GET",
      ...(options.body === undefined ? {} : { body: options.body }),
      headers: {
        apikey: this.#anonKey,
        Authorization: `Bearer ${this.#anonKey}`,
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...options.headers,
      },
    });
    if (!response.ok) {
      throw new Error(`Supabase research query failed with status ${response.status}.`);
    }
    return response;
  }
}

export function researchQueryParameters(query: ResearchListQuery): URLSearchParams {
  const parameters = new URLSearchParams({ select: "*" });
  if (query.kind && query.kind !== "all") {
    parameters.set("aggregate_type", `eq.${query.kind}`);
  }
  if (query.status) {
    if (!query.kind || query.kind === "all") {
      parameters.set("or", [
        `payload->>state.eq.${query.status}`,
        `payload->>status.eq.${query.status}`,
        `payload->definition->>state.eq.${query.status}`,
        `payload->membership->>segment_role.eq.${query.status}`,
        `payload->outcome->>state.eq.${query.status}`,
        `payload->reliability->>verdict.eq.${query.status}`,
        `payload->methodology->>state.eq.${query.status}`,
        `payload->authorization->>state.eq.${query.status}`,
        `payload->claim->>state.eq.${query.status}`,
        `payload->terminal->>state.eq.${query.status}`,
        `payload->archive->>state.eq.${query.status}`,
      ].join(",").replace(/^/, "(").concat(")"));
    } else {
      const path = researchStatusPath(query.kind);
      parameters.set(path, `eq.${query.status}`);
    }

    function researchStatusPath(kind: Exclude<ResearchListQuery["kind"], "all" | undefined>): string {
      if (kind === "registry_record") return "payload->>status";
      if (kind === "run_summary") return "payload->>state";
      if (kind === "study_reliability") return "payload->reliability->>verdict";
      if (kind === "methodology_freeze") return "payload->methodology->>state";
      if (kind === "experiment_membership") return "payload->membership->>segment_role";
      const section = kind.replace("experiment_", "").replace("holdout_", "");
      return `payload->${section}->>state`;
    }
  }
  if (query.search?.trim()) {
    const escaped = query.search.trim().replace(/[%*,()]/g, "");
    if (escaped) parameters.set("aggregate_id", `ilike.*${escaped}*`);
  }
  const sort = query.sort ?? "acknowledged_at";
  const direction = query.direction ?? "desc";
  parameters.set("order", `${sort}.${direction},idempotency_key.asc`);
  return parameters;
}

export function contentRangeTotal(contentRange: string | null, fallback: number): number {
  if (!contentRange) return fallback;
  const total = Number(contentRange.split("/")[1]);
  return Number.isInteger(total) && total >= 0 ? total : fallback;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value) throw new Error(`Invalid analysis catalog ${field}.`);
  return value;
}

function finiteInteger(value: unknown): number {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 ? number : 0;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
