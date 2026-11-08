const DEFAULT_COLUMNS = [
  "identity.display_alias",
  "identity.strategy_id",
  "validation.state",
  "metric.expectancy_r",
  "metric.net_pnl",
];
const CHART_KINDS = ["auto", "table", "bar", "line"];
const MAX_URL_STATE = 4_096;
const MAX_LOCAL_STATE = 16_384;

const CALLOUTS = {
  population: "Select the trusted run population analyzed. Validation attention adds failed or review-required runs; legacy/pre-P2b records remain excluded.",
  filters: "Filters operate on individual run summaries before grouping. AND requires every condition; OR requires any condition.",
  screening: "Screening columns are the fields shown for each matching run when grouping and measures are empty. Only review-relevant identifiers, dimensions, validation fields, and metrics are offered.",
  grouping: "Grouping creates one row for each unique combination. Only stable categorical dimensions with bounded cardinality are offered; metrics, hashes, run IDs, and timestamps are excluded.",
  measures: "Aggregate measures summarize numeric values within each group. Count measures records; mean, median, percentiles, and similar operations summarize the selected metric.",
  actions: "Run analysis applies the selected population, filters, columns, grouping, and measures. Save view stores those controls in this URL and browser without rerunning.",
  chart: "Chart fields come from the latest result, so run the analysis after changing columns, grouping, or measures. The result table remains authoritative.",
};

const STYLESHEET_URL = new URL("./research-analysis-workspace.css", import.meta.url).href;

export class ResearchAnalysisWorkspace extends (globalThis.HTMLElement ?? class {}) {
  #adapter;
  #catalog = [];
  #query = defaultQuery();
  #result = null;
  #cost = null;
  #error = "";
  #loading = true;
  #callout = "";
  #draft = { filterField: "", filterOperator: "eq", filterValue: "", measureField: "", aggregate: "mean" };

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  set adapter(value) {
    this.#adapter = value;
    if (this.isConnected) void this.#initialize();
  }

  get adapter() {
    return this.#adapter;
  }

  connectedCallback() {
    this.#render();
    if (this.#adapter) void this.#initialize();
  }

  async #initialize() {
    this.#loading = true;
    this.#render();
    try {
      this.#catalog = await this.#adapter.getAnalysisFieldCatalog();
      const available = new Set(this.#catalog.map((field) => field.field));
      this.#query.screeningColumns = DEFAULT_COLUMNS.filter((field) => available.has(field));
      this.#restore();
      const filterField = this.#catalog.find((field) => field.filterOperators?.length);
      const measureField = this.#catalog.find((field) => field.aggregates?.some((aggregate) => aggregate !== "count"));
      this.#draft.filterField = filterField?.field ?? "";
      this.#draft.filterOperator = filterField?.filterOperators?.[0] ?? "eq";
      this.#draft.measureField = measureField?.field ?? "";
      this.#draft.aggregate = measureField?.aggregates?.find((aggregate) => aggregate !== "count") ?? "count";
      await this.#execute();
    } catch (error) {
      this.#error = message(error);
    } finally {
      this.#loading = false;
      this.#render();
    }
  }

  #restore() {
    let serialized = new URL(location.href).searchParams.get("analysis");
    if (!serialized) {
      try { serialized = localStorage.getItem(this.getAttribute("storage-key") || "research-analysis-v2"); } catch { serialized = null; }
    }
    if (!serialized || serialized.length > MAX_LOCAL_STATE) return;
    try {
      const parsed = JSON.parse(serialized);
      const available = new Set(this.#catalog.map((field) => field.field));
      const screenable = new Set(this.#screeningFields().map((field) => field.field));
      const groupable = new Set(this.#groupingFields().map((field) => field.field));
      this.#query = {
        ...defaultQuery(),
        ...parsed,
        population: {
          includeLegacy: false,
          trustStates: Array.isArray(parsed.population?.trustStates)
            ? parsed.population.trustStates.filter((state) => ["current_validated", "validation_attention"].includes(state))
            : ["current_validated"],
        },
        screeningColumns: validFields(parsed.screeningColumns, screenable, this.#query.screeningColumns, 24),
        groupBy: validFields(parsed.groupBy, groupable, [], 24),
        measures: Array.isArray(parsed.measures)
          ? parsed.measures.filter((measure) => available.has(measure?.field)).slice(0, 24)
          : [],
        chart: { kind: CHART_KINDS.includes(parsed.chart?.kind) ? parsed.chart.kind : "auto" },
      };
    } catch {
      this.#query = { ...defaultQuery(), screeningColumns: this.#query.screeningColumns };
    }
  }

  #save() {
    const serialized = JSON.stringify(this.#query);
    const storageKey = this.getAttribute("storage-key") || "research-analysis-v2";
    try {
      if (serialized.length <= MAX_LOCAL_STATE) localStorage.setItem(storageKey, serialized);
      else localStorage.removeItem(storageKey);
    } catch { /* URL state remains available. */ }
    const url = new URL(location.href);
    if (serialized.length <= MAX_URL_STATE) {
      url.searchParams.set("analysis", serialized);
      this.#error = "";
    } else {
      url.searchParams.delete("analysis");
      this.#error = "View exceeds the URL limit and was saved in this browser only.";
    }
    history.replaceState(null, "", url);
    this.#render();
  }

  async #execute() {
    if (!this.#adapter || !this.#catalog.length) return;
    this.#loading = true;
    this.#error = "";
    this.#render();
    try {
      this.#query.population.includeLegacy = false;
      this.#cost = await this.#adapter.estimateAnalysis(this.#query);
      if (!this.#cost.allowed) throw new Error("Analysis exceeds policy; narrow filters or grouping.");
      this.#result = await this.#adapter.runAnalysis(this.#query);
      const resultFields = this.#result.rows[0] ? Object.keys(this.#result.rows[0]) : [];
      this.#query.chart = {
        kind: this.#query.chart.kind,
        x: resultFields.includes(this.#query.chart.x) ? this.#query.chart.x : undefined,
        y: resultFields.includes(this.#query.chart.y) ? this.#query.chart.y : undefined,
      };
    } catch (error) {
      this.#error = message(error);
    } finally {
      this.#loading = false;
      this.#render();
    }
  }

  #screeningFields() {
    return this.#catalog.filter((field) => field.screenable && isReviewField(field.field));
  }

  #groupingFields() {
    return this.#catalog.filter((field) => field.groupable && isGroupingField(field));
  }

  #render() {
    if (!this.shadowRoot) return;
    const resultFields = this.#result?.rows[0]
      ? Object.keys(this.#result.rows[0])
      : [...this.#query.groupBy, ...this.#query.screeningColumns, ...this.#query.measures.map(measureName)];
    this.shadowRoot.innerHTML = `<link rel="stylesheet" href="${escapeAttribute(STYLESHEET_URL)}">
      <section class="workspace" aria-labelledby="analysis-title">
        <h2 id="analysis-title">Flexible analysis workspace</h2>
        <p class="subtitle">Screen validated run summaries, group compatible dimensions, aggregate metrics, and inspect every chart mark in the result table.</p>
        <div class="grid">
          ${this.#fieldset("Population and trust", "population", `
            ${check("current", "Current validated runs", this.#query.population.trustStates.includes("current_validated"))}
            ${check("attention", "Validation attention", this.#query.population.trustStates.includes("validation_attention"))}
          `)}
          ${this.#fieldset("Typed composable filters", "filters", `
            <div class="row">
              ${select("filter-field", this.#catalog.filter((field) => field.filterOperators?.length).map((field) => field.field), this.#draft.filterField)}
              ${select("filter-operator", this.#field(this.#draft.filterField)?.filterOperators ?? [], this.#draft.filterOperator)}
              <input id="filter-value" aria-label="Filter value" value="${escapeAttribute(this.#draft.filterValue)}" ${this.#draft.filterOperator === "exists" ? "disabled" : ""} placeholder="${this.#draft.filterOperator === "between" ? "min,max" : "value"}">
              <button id="add-filter" type="button">Add</button>
            </div>
            <div class="row filter-controls">
              ${select("filter-mode", ["and", "or"], this.#query.filters.op)}
              <div class="chips">${this.#filterChildren().map((filter, index) => `<button class="chip" data-remove-filter="${index}">${escapeHtml(formatFilter(filter))} ×</button>`).join("")}</div>
            </div>
          `, "wide")}
          ${this.#fieldset("Screening columns", "screening", checklist(this.#screeningFields(), this.#query.screeningColumns, "screen"))}
          ${this.#fieldset("Grouping dimensions", "grouping", checklist(this.#groupingFields(), this.#query.groupBy, "group"))}
          ${this.#fieldset("Aggregate measures", "measures", `
            <div class="row measure-row">
              ${select("measure-field", this.#catalog.filter((field) => field.aggregates?.length > 1).map((field) => field.field), this.#draft.measureField)}
              ${select("aggregate", this.#field(this.#draft.measureField)?.aggregates ?? [], this.#draft.aggregate)}
              <button id="add-measure" type="button">Add</button>
            </div>
            <div class="measures">${this.#query.measures.map((measure, index) => `<span class="measure">${escapeHtml(measureName(measure))}<button data-remove-measure="${index}">Remove</button></span>`).join("")}</div>
          `)}
        </div>
        <div class="actions">
          <span class="help">Run applies selections; Save stores them without rerunning.</span>
          ${infoButton("actions", this.#callout)}
          <button id="save" type="button">Save view</button>
          <button id="run" class="primary" type="button" ${this.#loading || !this.#catalog.length ? "disabled" : ""}>${this.#loading ? "Running…" : "Run analysis"}</button>
        </div>
        ${this.#callout === "actions" ? callout("actions") : ""}
        ${this.#fieldset("Chart encodings", "chart", `
          <div class="chart-grid">
            <label>Chart ${select("chart-kind", CHART_KINDS, this.#query.chart.kind)}</label>
            <label>X / category ${select("chart-x", ["", ...resultFields], this.#query.chart.x ?? "", true)}</label>
            <label>Y / value ${select("chart-y", ["", ...resultFields], this.#query.chart.y ?? "", true)}</label>
          </div>
        `, "chart-controls")}
        ${this.#error ? `<div class="error" role="alert">${escapeHtml(this.#error)}</div>` : ""}
        ${this.#cost ? costView(this.#cost) : ""}
        ${this.#result ? chartView(this.#query, this.#result) + tableView(this.#result, this.#query) : ""}
      </section>`;
    this.#bind();
  }

  #fieldset(title, key, content, className = "") {
    return `<fieldset class="${className}"><legend><span class="legend">${escapeHtml(title)} ${infoButton(key, this.#callout)}</span></legend>
      ${this.#callout === key ? callout(key) : ""}${content}</fieldset>`;
  }

  #field(name) {
    return this.#catalog.find((field) => field.field === name);
  }

  #filterChildren() {
    return ["and", "or"].includes(this.#query.filters.op) ? this.#query.filters.children : [this.#query.filters];
  }

  #bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-info]").forEach((button) => button.addEventListener("click", () => {
      this.#callout = this.#callout === button.dataset.info ? "" : button.dataset.info;
      this.#render();
    }));
    root.getElementById("current")?.addEventListener("change", (event) => this.#trust("current_validated", event.target.checked));
    root.getElementById("attention")?.addEventListener("change", (event) => this.#trust("validation_attention", event.target.checked));
    root.getElementById("filter-field")?.addEventListener("change", (event) => {
      this.#draft.filterField = event.target.value;
      this.#draft.filterOperator = this.#field(event.target.value)?.filterOperators?.[0] ?? "eq";
      this.#render();
    });
    root.getElementById("filter-operator")?.addEventListener("change", (event) => {
      this.#draft.filterOperator = event.target.value;
      this.#render();
    });
    root.getElementById("filter-value")?.addEventListener("input", (event) => { this.#draft.filterValue = event.target.value; });
    root.getElementById("filter-mode")?.addEventListener("change", (event) => {
      this.#query.filters = { op: event.target.value, children: this.#filterChildren() };
      this.#render();
    });
    root.getElementById("add-filter")?.addEventListener("click", () => this.#addFilter());
    root.querySelectorAll("[data-remove-filter]").forEach((button) => button.addEventListener("click", () => {
      this.#query.filters.children.splice(Number(button.dataset.removeFilter), 1);
      this.#render();
    }));
    root.querySelectorAll("[data-field]").forEach((checkbox) => checkbox.addEventListener("change", () => {
      const key = checkbox.dataset.kind === "group" ? "groupBy" : "screeningColumns";
      this.#query[key] = toggle(this.#query[key], checkbox.dataset.field, checkbox.checked);
      this.#render();
    }));
    root.getElementById("measure-field")?.addEventListener("change", (event) => {
      this.#draft.measureField = event.target.value;
      this.#draft.aggregate = this.#field(event.target.value)?.aggregates?.find((aggregate) => aggregate !== "count") ?? "count";
      this.#render();
    });
    root.getElementById("aggregate")?.addEventListener("change", (event) => { this.#draft.aggregate = event.target.value; });
    root.getElementById("add-measure")?.addEventListener("click", () => {
      const measure = { field: this.#draft.measureField, aggregate: this.#draft.aggregate };
      if (measure.field && measure.aggregate && !this.#query.measures.some((item) => measureName(item) === measureName(measure))) {
        this.#query.measures.push(measure);
        this.#render();
      }
    });
    root.querySelectorAll("[data-remove-measure]").forEach((button) => button.addEventListener("click", () => {
      this.#query.measures.splice(Number(button.dataset.removeMeasure), 1);
      this.#render();
    }));
    root.getElementById("save")?.addEventListener("click", () => this.#save());
    root.getElementById("run")?.addEventListener("click", () => void this.#execute());
    for (const key of ["kind", "x", "y"]) {
      root.getElementById(`chart-${key}`)?.addEventListener("change", (event) => {
        this.#query.chart[key] = event.target.value || undefined;
        this.#render();
      });
    }
  }

  #trust(state, checked) {
    this.#query.population.trustStates = toggle(this.#query.population.trustStates, state, checked);
    if (!this.#query.population.trustStates.length) this.#query.population.trustStates = ["current_validated"];
    this.#render();
  }

  #addFilter() {
    const field = this.#field(this.#draft.filterField);
    if (!field) return;
    try {
      const condition = {
        field: field.field,
        op: this.#draft.filterOperator,
        value: parseValue(this.#draft.filterValue, field, this.#draft.filterOperator),
      };
      this.#query.filters = { op: this.#query.filters.op, children: [...this.#filterChildren(), condition] };
      this.#draft.filterValue = "";
      this.#error = "";
    } catch (error) {
      this.#error = message(error);
    }
    this.#render();
  }
}

function defaultQuery() {
  return {
    population: { includeLegacy: false, trustStates: ["current_validated"] },
    filters: { op: "and", children: [] },
    screeningColumns: [],
    groupBy: [],
    measures: [],
    orderBy: [],
    chart: { kind: "auto" },
  };
}

export function isReviewField(field) {
  if (/(_sha256|_hash|checksum|fingerprint|code_commit|origin_device|identity\.run_id|registry)/i.test(field)) return false;
  return /^(identity\.(display_alias|strategy_id|methodology_version|execution_model_version|metric_calculation_version)|dataset\.(snapshot_id|symbol|universe|interval|split|start|end)|validation\.|lineage\.|parameter\.|metric\.)/.test(field);
}

export function isGroupingField(field) {
  if (field.cardinality > 100 || /^(metric\.|identity\.(run_id|display_alias))/.test(field.field)) return false;
  if (/(_sha256|_hash|checksum|fingerprint|commit|timestamp|created|updated|start|end)/i.test(field.field)) return false;
  return /^(identity\.(strategy_id|methodology_version|execution_model_version|metric_calculation_version|feature_set_version)|dataset\.(snapshot_id|symbol|universe|interval|split)|validation\.|lineage\.|parameter\.)/.test(field.field);
}

function infoButton(key, open) {
  return `<button class="info" type="button" data-info="${key}" aria-label="About ${key}" aria-expanded="${open === key}">i</button>`;
}
function callout(key) { return `<div class="callout" role="note">${escapeHtml(CALLOUTS[key])}</div>`; }
function check(id, label, checked) { return `<label class="check-row"><input id="${id}" type="checkbox" ${checked ? "checked" : ""}><span>${escapeHtml(label)}</span></label>`; }
function checklist(fields, selected, kind) {
  return `<div class="check-list">${fields.map((field) => `<label class="check-row"><input type="checkbox" data-kind="${kind}" data-field="${escapeAttribute(field.field)}" ${selected.includes(field.field) ? "checked" : ""}><span>${escapeHtml(field.field)}</span><span class="cardinality">${field.cardinality}</span></label>`).join("") || `<span class="help">No compatible fields.</span>`}</div>`;
}
function select(id, options, selected, none = false) {
  return `<select id="${id}" aria-label="${escapeAttribute(id.replaceAll("-", " "))}">${options.map((option) => `<option value="${escapeAttribute(option)}" ${option === selected ? "selected" : ""}>${escapeHtml(option || (none ? "None" : ""))}</option>`).join("")}</select>`;
}
function costView(cost) {
  return `<div class="status"><div><strong>${cost.candidateRows}</strong><small>candidate rows</small></div><div><strong>${cost.estimatedGroups}</strong><small>estimated groups</small></div><div><strong>${cost.estimatedCost}</strong><small>cost units</small></div><div><strong>${cost.allowed ? "Within policy" : "Narrow query"}</strong><small>${cost.limits.maxResponseRows} row response cap</small></div></div>`;
}
function chartView(query, result) {
  const kind = query.chart.kind === "auto" ? query.groupBy.length && query.measures.length ? "bar" : "table" : query.chart.kind;
  if (kind === "table" || !result.rows.length) return "";
  const x = query.chart.x ?? query.groupBy[0] ?? Object.keys(result.rows[0])[0];
  const y = query.chart.y ?? (query.measures[0] ? measureName(query.measures[0]) : undefined);
  if (!x || !y) return `<p class="help">Assign X and Y encodings after running the analysis.</p>`;
  const marks = result.rows.slice(0, result.metadata.chartMarks);
  const values = marks.map((row) => typeof row[y] === "number" ? row[y] : 0);
  return kind === "line" ? lineChart(marks, values, x, y) : barChart(marks, values, x, y);
}
function barChart(marks, values, x, y) {
  const width = 720;
  const height = 240;
  const left = 40;
  const bottom = 48;
  const baseline = height - bottom;
  const maximum = Math.max(...values.map((value) => Math.abs(value)), 1);
  const slot = (width - left - 20) / Math.max(1, marks.length);
  const barWidth = Math.max(2, Math.min(48, slot * 0.7));
  return `<div class="chart"><strong>bar · ${escapeHtml(x)} → ${escapeHtml(y)}</strong><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttribute(`${y} by ${x}`)}"><line x1="${left}" x2="${width - 20}" y1="${baseline}" y2="${baseline}" stroke="var(--border,#334155)"/>${marks.map((row, index) => {
    const barHeight = Math.max(2, Math.abs(values[index]) / maximum * (baseline - 20));
    const center = left + slot * index + slot / 2;
    return `<rect x="${center - barWidth / 2}" y="${baseline - barHeight}" width="${barWidth}" height="${barHeight}" rx="3" fill="var(--accent-strong,var(--accent,#60a5fa))" opacity="${values[index] < 0 ? ".55" : ".9"}"><title>${escapeHtml(`${row[x]}: ${values[index]}`)}</title></rect><text x="${center}" y="${baseline + 18}" fill="var(--muted,#94a3b8)" font-size="10" text-anchor="middle">${escapeHtml(String(row[x] ?? "--").slice(0, 12))}</text>`;
  }).join("")}</svg></div>`;
}
function lineChart(marks, values, x, y) {
  const [width, height, left, right, top, bottom] = [720, 240, 54, 20, 20, 48];
  const minimum = Math.min(...values, 0); const maximum = Math.max(...values, 0); const span = maximum - minimum || 1;
  const xAt = (index) => left + (marks.length <= 1 ? 0 : index / (marks.length - 1) * (width - left - right));
  const yAt = (value) => top + (maximum - value) / span * (height - top - bottom);
  const points = values.map((value, index) => `${xAt(index)},${yAt(value)}`).join(" ");
  const step = Math.max(1, Math.ceil(marks.length / 8));
  return `<div class="chart"><strong>line · ${escapeHtml(x)} → ${escapeHtml(y)}</strong><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttribute(`${y} by ${x}`)}"><line x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" stroke="var(--border,#334155)"/><line x1="${left}" x2="${width - right}" y1="${yAt(0)}" y2="${yAt(0)}" stroke="var(--border,#334155)"/><polyline fill="none" stroke="var(--accent-strong,var(--accent,#60a5fa))" stroke-width="3" points="${points}"/>${marks.map((row, index) => `<circle cx="${xAt(index)}" cy="${yAt(values[index])}" r="4" fill="var(--accent-strong,var(--accent,#60a5fa))"><title>${escapeHtml(`${row[x]}: ${values[index]}`)}</title></circle>${index % step === 0 ? `<text x="${xAt(index)}" y="${height - bottom + 18}" fill="var(--muted,#94a3b8)" font-size="10" text-anchor="middle">${escapeHtml(String(row[x] ?? "--").slice(0, 14))}</text>` : ""}`).join("")}</svg></div>`;
}
function tableView(result, query) {
  const columns = result.rows[0] ? Object.keys(result.rows[0]) : [];
  return `<div class="result-head"><h3>Inspectable result table</h3><span class="help">${result.metadata.returnedRows} of ${result.metadata.totalResultRows} rows</span></div>${result.metadata.truncated ? `<div class="error">Result truncated by row or byte policy. Narrow filters or aggregate before drawing conclusions.</div>` : ""}<div class="table-wrap">${columns.length ? `<table><thead><tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${result.rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(formatValue(row[column]))}</td>`).join("")}</tr>`).join("")}</tbody></table>` : emptyResultView(query)}</div>`;
}
// A freshly recorded store holds only runs awaiting validation, so the
// validated-only default legitimately returns nothing. Saying just "no rows
// match" reads as "the tool is broken"; name the population that excluded them
// instead. The default itself stays validated-only -- widening it silently
// would let an unvalidated number be read as validated evidence.
function emptyResultView(query) {
  const validatedOnly = query?.population?.trustStates?.length === 1
    && query.population.trustStates[0] === "current_validated";
  return `<p class="help">No analysis rows match the current controls.${validatedOnly ? ` This population includes only runs that passed validation. Recorded backtests rest in <code>validating</code> until a leakage report and integrity summary promote them, so tick <strong>Validation attention</strong> above to screen them.` : ""}</p>`;
}
function parseValue(raw, field, operator) {
  if (operator === "exists") return raw !== "false";
  const source = ["in", "not_in", "between"].includes(operator) ? raw.split(",") : [raw];
  const values = source.map((part) => {
    const value = part.trim();
    if (["number", "integer"].includes(field.dataType)) {
      const number = Number(value); if (!Number.isFinite(number)) throw new Error(`Expected a numeric value for ${field.field}.`); return number;
    }
    if (field.dataType === "boolean") return value.toLowerCase() === "true";
    return value;
  });
  if (operator === "between" && values.length !== 2) throw new Error("between requires min,max.");
  return source.length > 1 || ["in", "not_in", "between"].includes(operator) ? values : values[0];
}
function validFields(value, available, fallback, limit) { return Array.isArray(value) ? [...new Set(value.filter((field) => available.has(field)))].slice(0, limit) : fallback; }
function toggle(values, value, selected) { return selected ? [...new Set([...values, value])] : values.filter((item) => item !== value); }
function measureName(measure) { return `${measure.aggregate}:${measure.field}`; }
function formatFilter(filter) { return `${filter.field} ${filter.op} ${Array.isArray(filter.value) ? filter.value.join(",") : String(filter.value ?? "")}`; }
function formatValue(value) { if (value == null) return "--"; if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 }); if (typeof value === "boolean") return value ? "Yes" : "No"; return String(value); }
function message(error) { return error instanceof Error ? error.message : "Research analysis failed."; }
function escapeHtml(value) { return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;"); }
function escapeAttribute(value) { return escapeHtml(value).replaceAll("`", "&#96;"); }

if (globalThis.customElements && !customElements.get("research-analysis-workspace")) {
  customElements.define("research-analysis-workspace", ResearchAnalysisWorkspace);
}
