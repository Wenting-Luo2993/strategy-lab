import type { ResearchPublication, ResearchRunSummary } from "./researchTypes.ts";

export type AnalysisScalar = string | number | boolean | null;
export type AnalysisDataType = "string" | "number" | "integer" | "boolean" | "date";
export type FilterOperator =
  | "eq" | "neq" | "in" | "not_in"
  | "gt" | "gte" | "lt" | "lte" | "between"
  | "contains" | "exists";
export type Aggregate =
  | "count" | "mean" | "median" | "min" | "max" | "sum"
  | "stddev" | "p10" | "p25" | "p75" | "p90";
export type ChartKind = "auto" | "table" | "scatter" | "line" | "bar" | "box" | "heatmap" | "parallel_coordinates";

export type AnalysisField = {
  field: string;
  label: string;
  dataType: AnalysisDataType;
  unit: string | null;
  cardinality: number;
  filterOperators: FilterOperator[];
  aggregates: Aggregate[];
  groupable: boolean;
  screenable: boolean;
  trustRestricted: boolean;
};

export type FilterExpression =
  | { op: "and" | "or"; children: FilterExpression[] }
  | { op: FilterOperator; field: string; value?: AnalysisScalar | AnalysisScalar[] };

export type AnalysisQuery = {
  population: {
    includeLegacy: boolean;
    trustStates: string[];
  };
  filters: FilterExpression;
  screeningColumns: string[];
  groupBy: string[];
  measures: Array<{ field: string; aggregate: Aggregate }>;
  orderBy: Array<{ field: string; direction: "asc" | "desc" }>;
  chart: {
    kind: ChartKind;
    x?: string;
    y?: string;
    color?: string;
    size?: string;
    rowFacet?: string;
    columnFacet?: string;
  };
};

export type AnalysisLimits = {
  maxCandidateRows: number;
  maxGroups: number;
  maxResponseRows: number;
  maxChartMarks: number;
  maxResponseBytes: number;
  maxFilterNodes: number;
  maxFilterDepth: number;
  maxScreeningColumns: number;
  maxOrderRules: number;
  maxUrlStateBytes: number;
  maxLocalStateBytes: number;
};

export type AnalysisMetadata = {
  candidateRows: number;
  estimatedGroups: number;
  estimatedCost: number;
  limits: AnalysisLimits;
  requiresAggregation: boolean;
  allowed: boolean;
  returnedRows: number;
  totalResultRows: number;
  truncated: boolean;
  sampled: boolean;
  aggregated: boolean;
  chartMarks: number;
  chartMarksTruncated: boolean;
};

export type AnalysisResult = {
  rows: Array<Record<string, AnalysisScalar>>;
  metadata: AnalysisMetadata;
};

export type AnalysisAdapter = {
  getAnalysisFieldCatalog(): Promise<AnalysisField[]>;
  estimateAnalysis(query: AnalysisQuery): Promise<Omit<AnalysisMetadata,
    "returnedRows" | "totalResultRows" | "truncated" | "sampled" | "aggregated" |
    "chartMarks" | "chartMarksTruncated">>;
  runAnalysis(query: AnalysisQuery): Promise<AnalysisResult>;
};

export type AnalysisRow = {
  runId: string;
  trust: Record<string, AnalysisScalar>;
  dimensions: Record<string, AnalysisScalar>;
  metrics: Record<string, number | null>;
};

export const analysisLimits: AnalysisLimits = {
  maxCandidateRows: 5_000,
  maxGroups: 500,
  maxResponseRows: 500,
  maxChartMarks: 300,
  maxResponseBytes: 512_000,
  maxFilterNodes: 40,
  maxFilterDepth: 5,
  maxScreeningColumns: 24,
  maxOrderRules: 8,
  maxUrlStateBytes: 4_096,
  maxLocalStateBytes: 16_384,
};

export const defaultAnalysisQuery: AnalysisQuery = {
  population: {
    includeLegacy: false,
    trustStates: ["current_validated"],
  },
  filters: { op: "and", children: [] },
  screeningColumns: [
    "identity.display_alias",
    "identity.strategy_id",
    "validation.state",
    "metric.expectancy_r",
    "metric.net_pnl",
  ],
  groupBy: [],
  measures: [],
  orderBy: [],
  chart: { kind: "auto" },
};

export class FixtureAnalysisAdapter implements AnalysisAdapter {
  readonly #rows: AnalysisRow[];

  constructor(publications: ResearchPublication[], rows?: AnalysisRow[]) {
    this.#rows = rows ?? publications
        .filter((item): item is ResearchRunSummary => item.kind === "run_summary")
        .map(publicationToAnalysisRow);
  }

  async getAnalysisFieldCatalog(): Promise<AnalysisField[]> {
    return fieldCatalog(this.#rows);
  }

  async estimateAnalysis(query: AnalysisQuery) {
    const catalog = await this.getAnalysisFieldCatalog();
    const normalized = sanitizeAnalysisQuery(query, catalog);
    const rows = selectRows(this.#rows, normalized);
    const groups = groupCardinality(rows, normalized.groupBy);
    return {
      candidateRows: rows.length,
      estimatedGroups: groups,
      estimatedCost: rows.length * Math.max(1, normalized.groupBy.length)
        + groups * Math.max(1, normalized.measures.length),
      limits: analysisLimits,
      requiresAggregation: rows.length > analysisLimits.maxResponseRows,
      allowed: rows.length <= analysisLimits.maxCandidateRows && groups <= analysisLimits.maxGroups,
    };
  }

  async runAnalysis(query: AnalysisQuery): Promise<AnalysisResult> {
    const catalog = await this.getAnalysisFieldCatalog();
    const normalized = sanitizeAnalysisQuery(query, catalog);
    const estimate = await this.estimateAnalysis(normalized);
    if (!estimate.allowed) {
      throw new Error(
        `Analysis cost exceeds policy (${estimate.candidateRows} rows, `
        + `${estimate.estimatedGroups} groups). Narrow filters or reduce grouping cardinality.`,
      );
    }
    const selected = selectRows(this.#rows, normalized);
    assertCompatibleMetricVersions(selected, normalized);
    let resultRows = normalized.groupBy.length || normalized.measures.length
      ? aggregateRows(selected, normalized)
      : selected.map((row) => Object.fromEntries(
        normalized.screeningColumns.map((field) => [field, rowValue(row, field)]),
      ));
    for (const order of [...normalized.orderBy].reverse()) {
      resultRows = resultRows.toSorted((left, right) => {
        const comparison = compare(left[order.field], right[order.field]);
        return order.direction === "desc" ? -comparison : comparison;
      });
    }
    const totalResultRows = resultRows.length;
    const bounded = boundResponseBytes(
      resultRows.slice(0, analysisLimits.maxResponseRows),
      analysisLimits.maxResponseBytes,
    );
    const chartMarks = Math.min(bounded.length, analysisLimits.maxChartMarks);
    return {
      rows: bounded,
      metadata: {
        ...estimate,
        returnedRows: bounded.length,
        totalResultRows,
        truncated: bounded.length < totalResultRows,
        sampled: false,
        aggregated: normalized.groupBy.length > 0 || normalized.measures.length > 0,
        chartMarks,
        chartMarksTruncated: bounded.length > chartMarks,
      },
    };
  }
}

export function sanitizeAnalysisQuery(
  value: unknown,
  catalog: AnalysisField[],
): AnalysisQuery {
  const fields = new Map(catalog.map((item) => [item.field, item]));
  if (!isObject(value)) throw new Error("Analysis state must be an object.");
  const population = isObject(value.population) ? value.population : {};
  const trustStates = stringArray(population.trustStates, 8, 64);
  const currentTrustStates = trustStates.filter((state) => state !== "legacy");
  const filters = sanitizeFilter(value.filters, fields);
  const screeningColumns = fieldArray(
    value.screeningColumns,
    fields,
    analysisLimits.maxScreeningColumns,
    defaultAnalysisQuery.screeningColumns,
  );
  // Grouping has no product-level experiment or dimension count. Its bounded
  // cost is data-dependent and enforced by maxGroups/maxCandidateRows.
  const groupBy = fieldArray(value.groupBy, fields, Number.MAX_SAFE_INTEGER, []);
  const measures = Array.isArray(value.measures)
    ? value.measures.slice(0, 24).map((measure) => {
      if (!isObject(measure) || typeof measure.field !== "string" || typeof measure.aggregate !== "string") {
        throw new Error("Invalid analysis measure.");
      }
      const field = fields.get(measure.field);
      if (!field || !field.aggregates.includes(measure.aggregate as Aggregate)) {
        throw new Error(`Unsupported aggregate for ${measure.field}.`);
      }
      return { field: measure.field, aggregate: measure.aggregate as Aggregate };
    })
    : [];
  const resultFields = new Set([
    ...screeningColumns,
    ...groupBy,
    ...measures.map(measureName),
  ]);
  const orderBy = Array.isArray(value.orderBy)
    ? value.orderBy.slice(0, analysisLimits.maxOrderRules).map((order) => {
      if (!isObject(order) || typeof order.field !== "string"
        || !resultFields.has(order.field)
        || (order.direction !== "asc" && order.direction !== "desc")) {
        throw new Error("Invalid analysis ordering.");
      }
      return { field: order.field, direction: order.direction as "asc" | "desc" };
    })
    : [];
  const chart = sanitizeChart(value.chart, resultFields);
  return {
    population: {
      includeLegacy: false,
      trustStates: currentTrustStates.length
        ? currentTrustStates
        : ["current_validated"],
    },
    filters,
    screeningColumns,
    groupBy,
    measures,
    orderBy,
    chart,
  };
}

export function encodeAnalysisState(query: AnalysisQuery): string | null {
  const encoded = encodeURIComponent(JSON.stringify(query));
  return encoded.length <= analysisLimits.maxUrlStateBytes ? encoded : null;
}

export function decodeAnalysisState(
  encoded: string | null,
  catalog: AnalysisField[],
  maxBytes = analysisLimits.maxUrlStateBytes,
): AnalysisQuery {
  if (!encoded || encoded.length > maxBytes) {
    return sanitizeAnalysisQuery(defaultAnalysisQuery, catalog);
  }
  try {
    return sanitizeAnalysisQuery(JSON.parse(decodeURIComponent(encoded)), catalog);
  } catch {
    return sanitizeAnalysisQuery(defaultAnalysisQuery, catalog);
  }
}

export function publicationToAnalysisRow(publication: ResearchRunSummary): AnalysisRow {
  const legacy = publication.badges.some((badge) =>
    badge.includes("legacy") || badge.includes("pre-p2b") || badge.includes("unknown-pre")
  ) || publication.executionModelVersion === null;
  const currentValidated = !legacy
    && publication.status === "completed"
    && publication.validation.leakagePassed === true
    && publication.validation.findings.blocking === 0;
  return {
    runId: publication.runId ?? publication.aggregateId,
    trust: {
      "trust.state": currentValidated ? "current_validated" : legacy ? "legacy" : "validation_attention",
      "trust.legacy": legacy,
      "trust.current": !legacy,
      "trust.code_dirty": publication.codeDirty,
    },
    dimensions: {
      "identity.display_alias": publication.displayAlias,
      "identity.strategy_id": publication.strategyId,
      "identity.methodology_version": publication.methodologyVersion,
      "identity.execution_model_version": publication.executionModelVersion,
      "identity.metric_calculation_version": publication.metricCalculationVersion,
      "identity.feature_set_version": publication.featureSetVersion,
      "dataset.snapshot_id": publication.datasetFingerprint,
      "dataset.split_manifest_hash": publication.splitManifestHash,
      "validation.state": publication.status,
      "validation.profile": publication.validation.profileName,
      "validation.leakage_passed": publication.validation.leakagePassed,
      "validation.blocking_findings": publication.validation.findings.blocking,
      "validation.review_findings": publication.validation.findings.review,
      "validation.advisory_findings": publication.validation.findings.advisory,
      "lineage.id": publication.lineageId ?? publication.runId,
    },
    metrics: Object.fromEntries(publication.metrics.map((metric) => [
      `metric.${metric.key}`,
      typeof metric.value === "number" ? metric.value : null,
    ])),
  };
}

function fieldCatalog(rows: AnalysisRow[]): AnalysisField[] {
  const values = new Map<string, AnalysisScalar[]>();
  for (const row of rows) {
    addValue(values, "identity.run_id", row.runId);
    for (const source of [row.trust, row.dimensions, row.metrics]) {
      for (const [field, value] of Object.entries(source)) addValue(values, field, value);
    }
  }
  return [...values.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([field, fieldValues]) => {
    const populated = fieldValues.filter((value) => value !== null);
    const dataType = inferDataType(populated);
    return {
      field,
      label: field.split(".", 2).at(-1)?.replaceAll("_", " ") ?? field,
      dataType,
      unit: metricUnit(field),
      cardinality: new Set(populated.map((value) => JSON.stringify(value))).size,
      filterOperators: operatorsFor(dataType),
      aggregates: dataType === "number" || dataType === "integer"
        ? ["count", "mean", "median", "min", "max", "sum", "stddev", "p10", "p25", "p75", "p90"]
        : ["count"],
      groupable: true,
      screenable: true,
      trustRestricted: field.startsWith("trust."),
    };
  });
}

function selectRows(rows: AnalysisRow[], query: AnalysisQuery): AnalysisRow[] {
  const states = new Set(query.population.trustStates);
  return rows.filter((row) =>
    (query.population.includeLegacy || row.trust["trust.legacy"] !== true)
    && states.has(String(row.trust["trust.state"]))
    && matches(row, query.filters)
  );
}

function matches(row: AnalysisRow, expression: FilterExpression): boolean {
  if (!("field" in expression)) {
    return expression.op === "and"
      ? expression.children.every((child) => matches(row, child))
      : expression.children.some((child) => matches(row, child));
  }
  const actual = rowValue(row, expression.field);
  const expected = expression.value;
  switch (expression.op) {
    case "exists": return (actual !== null) === Boolean(expected);
    case "eq": return actual === expected;
    case "neq": return actual !== expected;
    case "in": return Array.isArray(expected) && expected.includes(actual);
    case "not_in": return Array.isArray(expected) && !expected.includes(actual);
    case "contains": return typeof actual === "string" && actual.toLowerCase().includes(String(expected).toLowerCase());
    case "gt": return compare(actual, expected as AnalysisScalar) > 0;
    case "gte": return compare(actual, expected as AnalysisScalar) >= 0;
    case "lt": return compare(actual, expected as AnalysisScalar) < 0;
    case "lte": return compare(actual, expected as AnalysisScalar) <= 0;
    case "between": return Array.isArray(expected) && expected.length === 2
      && compare(actual, expected[0]) >= 0 && compare(actual, expected[1]) <= 0;
  }
}

function aggregateRows(rows: AnalysisRow[], query: AnalysisQuery): Array<Record<string, AnalysisScalar>> {
  const groups = new Map<string, { key: AnalysisScalar[]; rows: AnalysisRow[] }>();
  for (const row of rows) {
    const key = query.groupBy.map((field) => rowValue(row, field));
    const id = JSON.stringify(key);
    const group = groups.get(id) ?? { key, rows: [] };
    group.rows.push(row);
    groups.set(id, group);
  }
  if (!groups.size && !query.groupBy.length) groups.set("[]", { key: [], rows: [] });
  return [...groups.values()].map((group) => {
    const output: Record<string, AnalysisScalar> = Object.fromEntries(
      query.groupBy.map((field, index) => [field, group.key[index]]),
    );
    for (const measure of query.measures) {
      output[measureName(measure)] = aggregate(
        group.rows.map((row) => rowValue(row, measure.field)),
        measure.aggregate,
      );
    }
    output["analysis.run_count"] = group.rows.length;
    return output;
  });
}

function aggregate(values: AnalysisScalar[], operation: Aggregate): number | null {
  if (operation === "count") return values.filter((value) => value !== null).length;
  const numbers = values.filter((value): value is number =>
    typeof value === "number" && Number.isFinite(value)
  ).toSorted((left, right) => left - right);
  if (!numbers.length) return null;
  if (operation === "sum") return numbers.reduce((total, value) => total + value, 0);
  if (operation === "mean") return numbers.reduce((total, value) => total + value, 0) / numbers.length;
  if (operation === "median") return percentile(numbers, 0.5);
  if (operation === "min") return numbers[0];
  if (operation === "max") return numbers.at(-1) ?? null;
  if (operation === "stddev") {
    const mean = numbers.reduce((total, value) => total + value, 0) / numbers.length;
    return Math.sqrt(numbers.reduce((sum, value) => sum + (value - mean) ** 2, 0) / numbers.length);
  }
  return percentile(numbers, { p10: 0.1, p25: 0.25, p75: 0.75, p90: 0.9 }[operation] ?? 0.5);
}

function percentile(numbers: number[], quantile: number): number {
  const position = (numbers.length - 1) * quantile;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return lower === upper
    ? numbers[lower]
    : numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower);
}

function sanitizeFilter(
  value: unknown,
  fields: Map<string, AnalysisField>,
  depth = 1,
  counter = { count: 0 },
): FilterExpression {
  counter.count += 1;
  if (depth > analysisLimits.maxFilterDepth || counter.count > analysisLimits.maxFilterNodes) {
    throw new Error("Filter exceeds depth or node limits.");
  }
  if (!isObject(value)) return { op: "and", children: [] };
  if (value.op === "and" || value.op === "or") {
    if (!Array.isArray(value.children)) throw new Error("Filter group requires children.");
    return {
      op: value.op,
      children: value.children.map((child) => sanitizeFilter(child, fields, depth + 1, counter)),
    };
  }
  if (typeof value.op !== "string" || typeof value.field !== "string") {
    throw new Error("Invalid filter condition.");
  }
  const field = fields.get(value.field);
  if (!field || !field.filterOperators.includes(value.op as FilterOperator)) {
    throw new Error(`Unsupported filter ${value.op} for ${value.field}.`);
  }
  if (["in", "not_in", "between"].includes(value.op) && !Array.isArray(value.value)) {
    throw new Error(`${value.op} requires an array value.`);
  }
  if (value.op === "between" && (value.value as unknown[]).length !== 2) {
    throw new Error("between requires exactly two values.");
  }
  return {
    op: value.op as FilterOperator,
    field: value.field,
    value: sanitizeFilterValue(value.value),
  };
}

function sanitizeChart(value: unknown, resultFields: Set<string>): AnalysisQuery["chart"] {
  const chartKinds: ChartKind[] = ["auto", "table", "scatter", "line", "bar", "box", "heatmap", "parallel_coordinates"];
  if (!isObject(value) || !chartKinds.includes(value.kind as ChartKind)) return { kind: "auto" };
  const chart: AnalysisQuery["chart"] = { kind: value.kind as ChartKind };
  for (const encoding of ["x", "y", "color", "size", "rowFacet", "columnFacet"] as const) {
    if (typeof value[encoding] === "string" && resultFields.has(value[encoding] as string)) {
      chart[encoding] = value[encoding] as string;
    }
  }
  return chart;
}

function sanitizeFilterValue(value: unknown): AnalysisScalar | AnalysisScalar[] | undefined {
  if (value === undefined) return undefined;
  if (Array.isArray(value)) return value.slice(0, 50).map((item) => scalar(item));
  return scalar(value);
}

function scalar(value: unknown): AnalysisScalar {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    if (typeof value === "string") return value.slice(0, 256);
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("Filter numbers must be finite.");
    return value;
  }
  throw new Error("Filter values must be scalar.");
}

function fieldArray(
  value: unknown,
  fields: Map<string, AnalysisField>,
  limit: number,
  fallback: string[],
): string[] {
  if (!Array.isArray(value)) return fallback.filter((field) => fields.has(field));
  const result = value.map((item) => {
    if (typeof item !== "string" || !fields.has(item)) throw new Error(`Unknown field ${String(item)}.`);
    return item;
  });
  if (result.length > limit || new Set(result).size !== result.length) {
    throw new Error("Field selection exceeds limits or contains duplicates.");
  }
  return result;
}

function stringArray(value: unknown, limit: number, maxLength: number): string[] {
  return Array.isArray(value)
    ? value.slice(0, limit).filter((item): item is string => typeof item === "string").map((item) => item.slice(0, maxLength))
    : [];
}

function assertCompatibleMetricVersions(rows: AnalysisRow[], query: AnalysisQuery): void {
  if (!query.measures.some((measure) => measure.field.startsWith("metric."))) return;
  const versions = new Set(rows.map((row) => row.dimensions["identity.metric_calculation_version"]).filter((value) => value !== null));
  if (versions.size > 1 && !query.groupBy.includes("identity.metric_calculation_version")) {
    throw new Error("Metric aggregation spans incompatible calculation versions; filter or group by version.");
  }
}

function groupCardinality(rows: AnalysisRow[], fields: string[]): number {
  if (!fields.length) return rows.length ? 1 : 0;
  return new Set(rows.map((row) => JSON.stringify(fields.map((field) => rowValue(row, field))))).size;
}

function rowValue(row: AnalysisRow, field: string): AnalysisScalar {
  if (field === "identity.run_id") return row.runId;
  if (field.startsWith("trust.")) return row.trust[field] ?? null;
  if (field.startsWith("metric.")) return row.metrics[field] ?? null;
  return row.dimensions[field] ?? null;
}

function measureName(measure: { field: string; aggregate: Aggregate }): string {
  return `${measure.aggregate}:${measure.field}`;
}

function compare(left: AnalysisScalar | undefined, right: AnalysisScalar | undefined): number {
  if (left === right) return 0;
  if (left === null || left === undefined) return 1;
  if (right === null || right === undefined) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right));
}

function boundResponseBytes(
  rows: Array<Record<string, AnalysisScalar>>,
  maxBytes: number,
): Array<Record<string, AnalysisScalar>> {
  const output = [];
  let bytes = 2;
  const encoder = new TextEncoder();
  for (const row of rows) {
    const size = encoder.encode(JSON.stringify(row)).length + 1;
    if (bytes + size > maxBytes) break;
    output.push(row);
    bytes += size;
  }
  return output;
}

function inferDataType(values: AnalysisScalar[]): AnalysisDataType {
  if (values.length && values.every((value) => typeof value === "boolean")) return "boolean";
  if (values.length && values.every((value) => typeof value === "number" && Number.isInteger(value))) return "integer";
  if (values.length && values.every((value) => typeof value === "number")) return "number";
  return "string";
}

function operatorsFor(type: AnalysisDataType): FilterOperator[] {
  const common: FilterOperator[] = ["eq", "neq", "in", "not_in", "exists"];
  if (type === "number" || type === "integer" || type === "date") {
    return [...common, "gt", "gte", "lt", "lte", "between"];
  }
  return type === "string" ? [...common, "contains"] : common;
}

function metricUnit(field: string): string | null {
  if (/(pnl|equity|profit|cost)/i.test(field)) return "currency";
  if (/(rate|ratio|drawdown|percent)/i.test(field)) return "ratio";
  return field.startsWith("metric.") ? "number" : null;
}

function addValue(target: Map<string, AnalysisScalar[]>, field: string, value: AnalysisScalar): void {
  target.set(field, [...(target.get(field) ?? []), value]);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
