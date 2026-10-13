const state = {
  runs: { page: 1, pageSize: 25 },
  registry: { page: 1, pageSize: 25 },
  analysis: {
    catalog: [],
    filters: [],
    screeningColumns: [],
    groupBy: [],
    measures: [],
    result: null,
  },
};

const elements = Object.fromEntries(
  [
    "app", "loading", "error", "overview", "runs", "run-pagination", "registry",
    "registry-pagination", "publications", "db-path", "detail", "detail-content",
    "analysis-current", "analysis-attention", "analysis-filter-field",
    "analysis-filter-operator", "analysis-filter-value", "analysis-filter-mode",
    "analysis-filter-chips", "analysis-screening", "analysis-grouping",
    "analysis-measure-field", "analysis-measure-operation", "analysis-measures",
    "analysis-chart-kind", "analysis-chart-x", "analysis-chart-y",
    "analysis-save", "analysis-run", "analysis-error", "analysis-cost",
    "analysis-chart", "analysis-results",
  ]
    .map((id) => [id, document.getElementById(id)]),
);

document.getElementById("refresh").addEventListener("click", () => loadAll());
document.getElementById("run-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  state.runs = { page: 1, pageSize: state.runs.pageSize, ...formValues(event.currentTarget) };
  loadRuns();
});
document.getElementById("registry-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  state.registry = { page: 1, pageSize: state.registry.pageSize, ...formValues(event.currentTarget) };
  loadRegistry();
});
elements["analysis-filter-field"].addEventListener("change", renderAnalysisFilterOperators);
elements["analysis-filter-operator"].addEventListener("change", () => {
  elements["analysis-filter-value"].disabled = elements["analysis-filter-operator"].value === "exists";
});
elements["analysis-measure-field"].addEventListener("change", renderAnalysisMeasureOperations);
document.getElementById("analysis-add-filter").addEventListener("click", addAnalysisFilter);
document.getElementById("analysis-add-measure").addEventListener("click", addAnalysisMeasure);
elements["analysis-save"].addEventListener("click", saveAnalysisView);
elements["analysis-run"].addEventListener("click", runLocalAnalysis);

function formValues(form) {
  return Object.fromEntries(
    [...new FormData(form).entries()].filter(([, value]) => String(value).trim() !== ""),
  );
}

async function api(path, query = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(query).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") url.searchParams.set(key, value);
  });
  const response = await fetch(url, { headers: { accept: "application/json" } });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function loadAll() {
  showLoading(true);
  try {
    const [meta, overview] = await Promise.all([api("/api/meta"), api("/api/overview")]);
    elements["db-path"].textContent = `Database: ${meta.database.displayPath}`;
    renderOverview(overview);
    await Promise.all([
      loadRuns(false),
      loadRegistry(false),
      loadPublications(false),
      state.analysis.catalog.length ? Promise.resolve() : initializeAnalysis(),
    ]);
    showError(null);
    elements.app.classList.remove("hidden");
  } catch (error) {
    showError(error);
  } finally {
    showLoading(false);
  }
}

async function initializeAnalysis() {
    state.analysis.catalog = await api("/api/analysis/catalog");
    const available = new Set(state.analysis.catalog.map((field) => field.field));
    const defaults = [
      "identity.display_alias",
      "identity.strategy_id",
      "validation.state",
      "metric.expectancy_r",
      "metric.net_pnl",
    ].filter((field) => available.has(field));
    state.analysis.screeningColumns = defaults;
    restoreAnalysisView();
    renderAnalysisControls();
    await runLocalAnalysis();
  }

  function restoreAnalysisView() {
    const urlValue = new URL(window.location.href).searchParams.get("analysis");
    let value = urlValue;
    if (!value) {
      try {
        value = localStorage.getItem("local-research-analysis-v1");
      } catch {
        value = null;
      }
    }
    if (!value || value.length > 16_384) return;
    try {
      const decoded = JSON.parse(value);
      const available = new Set(state.analysis.catalog.map((field) => field.field));
      state.analysis.filters = Array.isArray(decoded.filters)
        ? decoded.filters.filter((filter) => available.has(filter.field)).slice(0, 40)
        : [];
      state.analysis.screeningColumns = validFields(
        decoded.screeningColumns,
        available,
        state.analysis.screeningColumns,
      ).slice(0, 24);
      state.analysis.groupBy = validFields(decoded.groupBy, available, []);
      state.analysis.measures = Array.isArray(decoded.measures)
        ? decoded.measures.filter((measure) => available.has(measure.field)).slice(0, 24)
        : [];
      elements["analysis-current"].checked = decoded.current !== false;
      elements["analysis-attention"].checked = decoded.attention === true;
      elements["analysis-filter-mode"].value = decoded.filterMode === "or" ? "or" : "and";
      elements["analysis-chart-kind"].value = ["auto", "table", "bar", "line"].includes(decoded.chartKind)
        ? decoded.chartKind : "auto";
      elements["analysis-chart-x"].dataset.saved = decoded.chartX || "";
      elements["analysis-chart-y"].dataset.saved = decoded.chartY || "";
    } catch {
      // Invalid saved state falls back to bounded defaults.
    }
  }

  function validFields(value, available, fallback) {
    return Array.isArray(value)
      ? [...new Set(value.filter((field) => available.has(field)))]
      : fallback;
  }

  function analysisViewState() {
    return {
      current: elements["analysis-current"].checked,
      attention: elements["analysis-attention"].checked,
      filterMode: elements["analysis-filter-mode"].value,
      filters: state.analysis.filters,
      screeningColumns: state.analysis.screeningColumns,
      groupBy: state.analysis.groupBy,
      measures: state.analysis.measures,
      chartKind: elements["analysis-chart-kind"].value,
      chartX: elements["analysis-chart-x"].value,
      chartY: elements["analysis-chart-y"].value,
    };
  }

  function saveAnalysisView() {
    const serialized = JSON.stringify(analysisViewState());
    try {
      localStorage.setItem("local-research-analysis-v1", serialized);
    } catch {
      // The URL remains available if local storage is disabled.
    }
    const url = new URL(window.location.href);
    if (serialized.length <= 4_096) {
      url.searchParams.set("analysis", serialized);
      window.history.replaceState(null, "", url);
      showAnalysisError(null);
    } else {
      url.searchParams.delete("analysis");
      window.history.replaceState(null, "", url);
      showAnalysisError(new Error("View exceeds the URL limit and was saved in this browser only."));
    }
  }

  function renderAnalysisControls() {
    const catalog = state.analysis.catalog;
    fillSelect(elements["analysis-filter-field"], catalog.map((field) => [field.field, field.field]));
    fillSelect(
      elements["analysis-measure-field"],
      catalog.filter((field) => field.aggregates.length).map((field) => [field.field, field.field]),
    );
    renderAnalysisFilterOperators();
    renderAnalysisMeasureOperations();
    renderAnalysisChecklist(elements["analysis-screening"], "screeningColumns");
    renderAnalysisChecklist(elements["analysis-grouping"], "groupBy");
    renderAnalysisFilterChips();
    renderAnalysisMeasures();
  }

  function fillSelect(select, options, includeNone = false) {
    const previous = select.dataset.saved ?? select.value;
    select.replaceChildren();
    if (includeNone) select.append(new Option("None", ""));
    for (const [value, label] of options) select.append(new Option(label, value));
    if ([...select.options].some((option) => option.value === previous)) select.value = previous;
    delete select.dataset.saved;
  }

  function renderAnalysisFilterOperators() {
    const field = analysisField(elements["analysis-filter-field"].value);
    fillSelect(
      elements["analysis-filter-operator"],
      (field?.filterOperators || []).map((operator) => [operator, operator]),
    );
    elements["analysis-filter-value"].disabled = elements["analysis-filter-operator"].value === "exists";
  }

  function renderAnalysisMeasureOperations() {
    const field = analysisField(elements["analysis-measure-field"].value);
    fillSelect(
      elements["analysis-measure-operation"],
      (field?.aggregates || []).map((operation) => [operation, operation]),
    );
  }

  function analysisField(name) {
    return state.analysis.catalog.find((field) => field.field === name);
  }

  function renderAnalysisChecklist(container, key) {
    container.replaceChildren();
    const selected = state.analysis[key];
    for (const field of state.analysis.catalog) {
      const label = node("label", "check-row");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.includes(field.field);
      checkbox.addEventListener("change", () => {
        state.analysis[key] = checkbox.checked
          ? [...selected, field.field]
          : selected.filter((value) => value !== field.field);
        renderAnalysisChecklist(container, key);
      });
      label.append(
        checkbox,
        node("span", "check-label", field.field),
        node("span", "cardinality", String(field.cardinality)),
      );
      container.append(label);
    }
  }

  function addAnalysisFilter() {
    const field = analysisField(elements["analysis-filter-field"].value);
    const operator = elements["analysis-filter-operator"].value;
    if (!field || !field.filterOperators.includes(operator)) return;
    try {
      state.analysis.filters.push({
        field: field.field,
        op: operator,
        value: parseAnalysisValue(elements["analysis-filter-value"].value, field, operator),
      });
      elements["analysis-filter-value"].value = "";
      renderAnalysisFilterChips();
      showAnalysisError(null);
    } catch (error) {
      showAnalysisError(error);
    }
  }

  function parseAnalysisValue(raw, field, operator) {
    if (operator === "exists") return raw !== "false";
    const source = ["in", "not_in", "between"].includes(operator) ? raw.split(",") : [raw];
    const values = source.map((part) => {
      const value = part.trim();
      if (field.dataType === "number" || field.dataType === "integer") {
        const number = Number(value);
        if (!Number.isFinite(number)) throw new Error(`Expected a numeric value for ${field.field}.`);
        return number;
      }
      if (field.dataType === "boolean") return value.toLowerCase() === "true";
      return value;
    });
    if (operator === "between" && values.length !== 2) throw new Error("between requires min,max.");
    return source.length > 1 || ["in", "not_in", "between"].includes(operator) ? values : values[0];
  }

  function renderAnalysisFilterChips() {
    elements["analysis-filter-chips"].replaceChildren();
    state.analysis.filters.forEach((filter, index) => {
      const button = node(
        "button",
        "chip",
        `${filter.field} ${filter.op} ${Array.isArray(filter.value) ? filter.value.join(",") : String(filter.value)} ×`,
      );
      button.type = "button";
      button.addEventListener("click", () => {
        state.analysis.filters.splice(index, 1);
        renderAnalysisFilterChips();
      });
      elements["analysis-filter-chips"].append(button);
    });
  }

  function addAnalysisMeasure() {
    const field = elements["analysis-measure-field"].value;
    const aggregate = elements["analysis-measure-operation"].value;
    if (!field || !aggregate || state.analysis.measures.some(
      (measure) => measure.field === field && measure.aggregate === aggregate
    )) return;
    state.analysis.measures.push({ field, aggregate });
    renderAnalysisMeasures();
  }

  function renderAnalysisMeasures() {
    elements["analysis-measures"].replaceChildren();
    state.analysis.measures.forEach((measure, index) => {
      const row = node("div", "measure-item");
      row.append(node("span", "", `${measure.aggregate}:${measure.field}`));
      const remove = node("button", "", "Remove");
      remove.type = "button";
      remove.addEventListener("click", () => {
        state.analysis.measures.splice(index, 1);
        renderAnalysisMeasures();
      });
      row.append(remove);
      elements["analysis-measures"].append(row);
    });
  }

  function buildAnalysisQuery() {
    const trustStates = [];
    if (elements["analysis-current"].checked) trustStates.push("current_validated");
    if (elements["analysis-attention"].checked) trustStates.push("validation_attention");
    return {
      population: {
        includeLegacy: false,
        trustStates: trustStates.length ? trustStates : ["current_validated"],
      },
      filters: {
        op: elements["analysis-filter-mode"].value === "or" ? "or" : "and",
        children: state.analysis.filters,
      },
      screeningColumns: state.analysis.screeningColumns,
      groupBy: state.analysis.groupBy,
      measures: state.analysis.measures,
      orderBy: [],
      chart: {
        kind: elements["analysis-chart-kind"].value,
        x: elements["analysis-chart-x"].value || undefined,
        y: elements["analysis-chart-y"].value || undefined,
      },
    };
  }

  async function runLocalAnalysis() {
    elements["analysis-run"].disabled = true;
    elements["analysis-run"].textContent = "Running…";
    try {
      const query = buildAnalysisQuery();
      const encoded = JSON.stringify(query);
      const estimate = await api("/api/analysis/estimate", { query: encoded });
      renderAnalysisCost(estimate);
      if (!estimate.allowed) throw new Error("Analysis exceeds the local policy; narrow filters or grouping.");
      state.analysis.result = await api("/api/analysis/query", { query: encoded });
      renderAnalysisResult(state.analysis.result);
      showAnalysisError(null);
    } catch (error) {
      showAnalysisError(error);
    } finally {
      elements["analysis-run"].disabled = false;
      elements["analysis-run"].textContent = "Run analysis";
    }
  }

  function renderAnalysisCost(cost) {
    elements["analysis-cost"].innerHTML = `
      <div class="analysis-cost">
        <div><strong>${cost.candidateRows}</strong><small>candidate rows</small></div>
        <div><strong>${cost.estimatedGroups}</strong><small>estimated groups</small></div>
        <div><strong>${cost.estimatedCost}</strong><small>cost units</small></div>
        <div><strong>${cost.allowed ? "Within policy" : "Narrow query"}</strong><small>${cost.limits.maxResponseRows} row response cap</small></div>
      </div>
    `;
  }

  function renderAnalysisResult(result) {
    const columns = result.rows[0] ? Object.keys(result.rows[0]) : [];
    const options = columns.map((column) => [column, column]);
    elements["analysis-chart-x"].dataset.saved ||= elements["analysis-chart-x"].value;
    elements["analysis-chart-y"].dataset.saved ||= elements["analysis-chart-y"].value;
    fillSelect(elements["analysis-chart-x"], options, true);
    fillSelect(elements["analysis-chart-y"], options, true);
    const rows = result.rows.map((row) => `<tr>${columns.map(
      (column) => `<td>${escapeHtml(formatValue(row[column]))}</td>`
    ).join("")}</tr>`).join("");
    elements["analysis-results"].innerHTML = `
      <div class="panel-heading analysis-result-heading">
        <h3>Inspectable result table</h3>
        <small>${result.metadata.returnedRows} of ${result.metadata.totalResultRows} rows</small>
      </div>
      ${result.metadata.truncated ? '<p class="error">Result truncated by row or byte policy. Narrow filters or aggregate before drawing conclusions.</p>' : ""}
      ${columns.length ? table(columns, rows) : emptyState("No analysis rows match the current controls.")}
    `;
    renderAnalysisChart(result);
  }

  function renderAnalysisChart(result) {
    const kind = elements["analysis-chart-kind"].value === "auto"
      ? state.analysis.groupBy.length && state.analysis.measures.length ? "bar" : "table"
      : elements["analysis-chart-kind"].value;
    const x = elements["analysis-chart-x"].value || state.analysis.groupBy[0] || "";
    const y = elements["analysis-chart-y"].value
      || (state.analysis.measures[0]
        ? `${state.analysis.measures[0].aggregate}:${state.analysis.measures[0].field}`
        : "");
    if (kind === "table" || !x || !y || !result.rows.length) {
      elements["analysis-chart"].replaceChildren();
      return;
    }
    const marks = result.rows.slice(0, result.metadata.chartMarks);
    const values = marks.map((row) => typeof row[y] === "number" ? row[y] : 0);
    elements["analysis-chart"].className = "analysis-chart";
    elements["analysis-chart"].innerHTML = kind === "line"
      ? lineChart(marks, values, x, y)
      : barChart(marks, values, x, y);
  }

  function barChart(marks, values, x, y) {
    const maximum = Math.max(...values.map((value) => Math.abs(value)), 1);
    return `
      <strong>bar · ${escapeHtml(x)} → ${escapeHtml(y)}</strong>
      <div class="bar-chart">${marks.map((row, index) => `
        <div class="bar-mark" title="${escapeAttribute(`${row[x]}: ${values[index]}`)}">
          <div class="bar" style="height:${Math.max(2, Math.abs(values[index]) / maximum * 190)}px;opacity:${values[index] < 0 ? ".55" : ".9"}"></div>
          <span class="bar-label">${escapeHtml(row[x])}</span>
        </div>
      `).join("")}</div>
    `;
  }

  function lineChart(marks, values, x, y) {
    const width = 720;
    const height = 240;
    const left = 54;
    const right = 20;
    const top = 20;
    const bottom = 48;
    const minimum = Math.min(...values, 0);
    const maximum = Math.max(...values, 0);
    const span = maximum - minimum || 1;
    const xAt = (index) => left + (marks.length <= 1 ? 0 : index / (marks.length - 1) * (width - left - right));
    const yAt = (value) => top + (maximum - value) / span * (height - top - bottom);
    const points = values.map((value, index) => `${xAt(index)},${yAt(value)}`).join(" ");
    const labelStep = Math.max(1, Math.ceil(marks.length / 8));
    return `
      <strong>line · ${escapeHtml(x)} → ${escapeHtml(y)}</strong>
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttribute(`${y} by ${x}`)}">
        <line x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" stroke="var(--border)"></line>
        <line x1="${left}" x2="${width - right}" y1="${yAt(0)}" y2="${yAt(0)}" stroke="var(--border)"></line>
        <polyline fill="none" stroke="var(--accent)" stroke-width="3" points="${points}"></polyline>
        ${marks.map((row, index) => `
          <circle cx="${xAt(index)}" cy="${yAt(values[index])}" r="4" fill="var(--accent)">
            <title>${escapeHtml(`${row[x]}: ${values[index]}`)}</title>
          </circle>
          ${index % labelStep === 0 ? `<text x="${xAt(index)}" y="${height - bottom + 18}" fill="var(--muted)" font-size="10" text-anchor="middle">${escapeHtml(String(row[x] ?? "—").slice(0, 14))}</text>` : ""}
        `).join("")}
      </svg>
    `;
  }

function showAnalysisError(error) {
  elements["analysis-error"].classList.toggle("hidden", !error);
  elements["analysis-error"].textContent = error ? error.message || String(error) : "";
}

async function loadRuns(showBusy = true) {
  if (showBusy) elements.runs.innerHTML = loadingState();
  try {
    const result = await api("/api/runs", state.runs);
    renderRuns(result);
  } catch (error) {
    elements.runs.innerHTML = errorState(error);
  }
}

async function loadRegistry(showBusy = true) {
  if (showBusy) elements.registry.innerHTML = loadingState();
  try {
    const result = await api("/api/registry", state.registry);
    renderRegistry(result);
  } catch (error) {
    elements.registry.innerHTML = errorState(error);
  }
}

async function loadPublications(showBusy = true) {
  if (showBusy) elements.publications.innerHTML = loadingState();
  try {
    const result = await api("/api/publications", { page: 1, pageSize: 50 });
    renderPublications(result);
  } catch (error) {
    elements.publications.innerHTML = errorState(error);
  }
}

function renderOverview(data) {
  const totalRuns = sumValues(data.runs);
  const totalRegistry = sumValues(data.registry);
  elements.overview.replaceChildren(
    metricCard("Runs", totalRuns),
    metricCard("Completed", data.runs.completed || 0),
    metricCard("Validation attention", (data.runs.review_required || 0) + (data.runs.validation_failed || 0), true),
    metricCard("Registry records", totalRegistry),
    metricCard("Pending / failed", (data.publications.pending || 0) + (data.publications.failed || 0), true),
    metricCard("Dead letters", data.publications.dead_letter || 0, true),
  );
}

function metricCard(label, value, warning = false) {
  const card = node("article", `metric${warning && value ? " warning" : ""}`);
  card.append(node("span", "metric-label", label), node("strong", "", String(value)));
  return card;
}

function renderRuns(result) {
  if (!result.items.length) {
    elements.runs.innerHTML = emptyState("No runs match these filters.");
  } else {
    const rows = result.items.map((run) => `
      <tr>
        <td><button class="link" data-run="${escapeAttribute(run.runId)}">${escapeHtml(run.displayAlias)}</button><small>${escapeHtml(run.runId)}</small></td>
        <td>${escapeHtml(run.strategyId)}<small>${escapeHtml(run.state)}</small></td>
        <td>${badges(run.badges)}</td>
        <td>${badge(run.publicationState, publicationTone(run.publicationState))}</td>
        <td>${formatDate(run.updatedAt)}</td>
      </tr>
    `).join("");
    elements.runs.innerHTML = table(["Identity", "Strategy / state", "Trust", "Publication", "Updated"], rows);
    elements.runs.querySelectorAll("[data-run]").forEach((button) => {
      button.addEventListener("click", () => showRun(button.dataset.run));
    });
  }
  renderPagination(elements["run-pagination"], result, (page) => {
    state.runs.page = page;
    loadRuns();
  });
}

function renderRegistry(result) {
  if (!result.items.length) {
    elements.registry.innerHTML = emptyState("No registry records match these filters.");
  } else {
    const rows = result.items.map((record) => `
      <tr>
        <td><button class="link" data-record-type="${escapeAttribute(record.recordType)}" data-record-id="${escapeAttribute(record.recordId)}">${escapeHtml(record.recordId)}</button></td>
        <td>${escapeHtml(record.recordType)}<small>${escapeHtml(record.status || "status unknown")}</small></td>
        <td>${badges([
          { label: record.methodologyVersion, tone: record.methodologyVersion === "legacy-uncontrolled" ? "danger" : "good" },
          executionBadge(record.executionModelVersion),
        ])}</td>
        <td>${badge(record.publicationState, publicationTone(record.publicationState))}</td>
        <td>${formatDate(record.recordedAt)}</td>
      </tr>
    `).join("");
    elements.registry.innerHTML = table(["Identity", "Type / status", "Trust", "Publication", "Recorded"], rows);
    elements.registry.querySelectorAll("[data-record-id]").forEach((button) => {
      button.addEventListener("click", () => showRegistry(button.dataset.recordType, button.dataset.recordId));
    });
  }
  renderPagination(elements["registry-pagination"], result, (page) => {
    state.registry.page = page;
    loadRegistry();
  });
}

function renderPublications(result) {
  if (!result.items.length) {
    elements.publications.innerHTML = emptyState("No publication events are queued.");
    return;
  }
  const rows = result.items.map((publication) => `
    <tr>
      <td>${escapeHtml(publication.aggregateId)}<small>${escapeHtml(publication.aggregateType)}</small></td>
      <td>${badge(publication.status, publicationTone(publication.status))}<small>${escapeHtml(publication.reconciliation)}</small></td>
      <td>${publication.attempts}</td>
      <td>${escapeHtml(publication.lastError || "—")}</td>
      <td>${formatDate(publication.enqueuedAt)}</td>
    </tr>
  `).join("");
  elements.publications.innerHTML = table(["Aggregate", "State / reconciliation", "Attempts", "Last error", "Enqueued"], rows);
}

async function showRun(runId) {
  openDetailLoading();
  try {
    const detail = await api(`/api/runs/${encodeURIComponent(runId)}`);
    const run = detail.run;
    elements["detail-content"].innerHTML = `
      <p class="eyebrow">LOCAL RUN · ${escapeHtml(run.state)}</p>
      <h2>${escapeHtml(run.displayAlias)}</h2>
      <p class="mono">${escapeHtml(run.runId)}</p>
      <div class="badge-list">${badges(detail.badges)}</div>
      ${detailGrid("Run identity", {
        "Strategy": run.strategyId,
        "Methodology": run.methodologyVersion,
        "Lineage": run.lineageId,
        "Code commit": run.codeCommit,
        "Origin device": run.originDeviceId,
        "Created": formatDate(run.createdAt),
        "Updated": formatDate(run.updatedAt),
      })}
      ${detailGrid("Fingerprint and split identity", {
        "Fingerprint": run.fingerprint,
        "Dataset": detail.identity.data_snapshot_id,
        "Split manifest": detail.identity.split_manifest_hash,
        "Feature set": detail.identity.feature_set_version,
        "Execution model": detail.identity.execution_model_version,
        "Metric calculation": detail.identity.metric_calculation_version,
        "Parameters": formatJson(detail.identity.parameters),
      })}
      ${detailGrid("Evidence and checksums", {
        "Trade count": detail.evidence?.trade_count,
        "Equity points counted": detail.evidence?.equity_points,
        "Trade ledger SHA-256": detail.evidence?.trade_ledger_sha256,
        "Equity curve SHA-256": detail.evidence?.equity_curve_sha256,
        "Retention profile": detail.evidence?.retention_profile,
        "Sessions": `${detail.evidence?.first_session || "—"} to ${detail.evidence?.last_session || "—"}`,
        "Net P&L": detail.evidence?.net_pnl,
        "Total costs": detail.evidence?.total_costs,
      })}
      ${detailGrid("Validation", {
        "Profile / scope": detail.validation ? `${detail.validation.profileName} / ${detail.validation.scope}` : "No report",
        "Target state": detail.validation?.targetState,
        "Leakage passed": detail.validation ? String(detail.validation.leakagePassed) : "—",
        "Registry hash at execution": detail.validation?.registryHashAtExecution,
        "Current registry hash": detail.validation?.currentRegistryHash,
        "Finding counts": formatJson(detail.findingCounts),
      })}
      ${cards("Metrics", detail.metrics, (metric) => `
        <article class="detail-card"><span>${escapeHtml(metric.key)}</span><strong>${escapeHtml(formatValue(metric.value))}</strong><small>calculation v${metric.calculationVersion}</small></article>
      `, "No metrics stored.")}
      ${cards("Validation findings", detail.findings, (finding) => `
        <article class="finding ${escapeAttribute(finding.severity)}"><strong>${escapeHtml(finding.code)} · ${escapeHtml(finding.severity)}</strong><p>${escapeHtml(finding.message)}</p><small>${escapeHtml(finding.category)}${finding.metricKey ? ` · ${escapeHtml(finding.metricKey)}` : ""}</small></article>
      `, "No validation findings.")}
      ${cards("Lifecycle", detail.transitions, (transition) => `
        <article class="detail-card"><strong>${escapeHtml(transition.fromState || "created")} → ${escapeHtml(transition.toState)}</strong><p>${escapeHtml(transition.reason || "—")}</p><small>${formatDate(transition.occurredAt)}</small></article>
      `, "No transitions stored.")}
      ${detailGrid("Publication and reconciliation", {
        "State": detail.publication.state,
        "Reconciliation": detail.publication.reconciliation,
        "Attempts": detail.publication.attempts,
        "Payload SHA-256": detail.publication.payloadSha256,
        "Remote checksum": detail.publication.remoteChecksum,
        "Last error": detail.publication.lastError,
        "Published": formatDate(detail.publication.publishedAt),
      })}
      ${localArtifactSection(detail.localArtifacts)}
    `;
  } catch (error) {
    elements["detail-content"].innerHTML = errorState(error);
  }
}

async function showRegistry(recordType, recordId) {
  openDetailLoading();
  try {
    const detail = await api(`/api/registry/${encodeURIComponent(recordType)}/${encodeURIComponent(recordId)}`);
    const record = detail.record;
    elements["detail-content"].innerHTML = `
      <p class="eyebrow">LOCAL REGISTRY · ${escapeHtml(record.recordType)}</p>
      <h2>${escapeHtml(record.recordId)}</h2>
      ${badges([
        { label: record.methodologyVersion, tone: record.methodologyVersion === "legacy-uncontrolled" ? "danger" : "good" },
        executionBadge(record.executionModelVersion),
        { label: record.recordOrigin, tone: "neutral" },
      ])}
      ${detailGrid("Registry identity", {
        "Status": record.status,
        "Canonical SHA-256": record.canonicalSha256,
        "Recorded": formatDate(record.recordedAt),
        "Dirty code": String(record.gitDirty),
        "Publication": detail.publication.state,
      })}
      ${detailGrid("Record data (secret-redacted)", selectRegistrySummary(detail.payload))}
      ${resultSummaryTable(detail.payload?.results_summary)}
      ${tagList(detail.payload?.tags)}
      ${cards("Lineage", detail.relationships, (relation) => `
        <article class="detail-card"><strong>${escapeHtml(relation.relation)}</strong><p>${escapeHtml(relation.targetType)} · ${escapeHtml(relation.targetId)}</p><small>${relation.resolved ? "resolved" : "unresolved"}</small></article>
      `, "No registry relationships.")}
      ${cards("Artifact references", detail.artifacts, (artifact) => `
        <article class="detail-card">
          <strong>Artifact ${artifact.ordinal + 1}</strong>
          <p>${escapeHtml(artifact.repositoryPath || artifact.externalLocation || "Location unavailable")}</p>
          ${artifact.preview?.available
            ? `<button class="artifact-preview-button" data-artifact-endpoint="${escapeAttribute(artifact.preview.endpoint)}">Preview ${escapeHtml(artifact.preview.fileName)}</button>`
            : `<small>${escapeHtml(artifact.preview?.reason || "Preview unavailable.")}</small>`}
        </article>
      `, "No artifact locations.")}
      <div id="artifact-preview"></div>
    `;
    elements["detail-content"].querySelectorAll("[data-artifact-endpoint]").forEach((button) => {
      button.addEventListener("click", () => showArtifactPreview(button.dataset.artifactEndpoint));
    });
  } catch (error) {
    elements["detail-content"].innerHTML = errorState(error);
  }
}

function selectRegistrySummary(payload) {
  const allowed = [
    "title", "rationale", "content", "idea", "reason_rejected", "strategy_name",
    "strategy_version", "parameters", "dataset_config",
    "conclusion", "artifact_type", "checksum", "size_bytes",
    "hypothesis_id", "parent_experiment_id", "related_experiment_id", "evidence",
  ];
  return Object.fromEntries(allowed.filter((key) => payload?.[key] !== undefined).map((key) => [humanize(key), formatValue(payload[key])]));
}

function resultSummaryTable(summary) {
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) return "";
  const rows = Object.entries(summary).map(([key, value]) => `
    <tr><th scope="row">${escapeHtml(humanize(key))}</th><td>${escapeHtml(formatValue(value))}</td></tr>
  `).join("");
  return `
    <section class="detail-section">
      <h3>Results summary</h3>
      <div class="table-wrap detail-table"><table><tbody>${rows}</tbody></table></div>
    </section>
  `;
}

function tagList(tags) {
  if (!Array.isArray(tags) || tags.length === 0) return "";
  return `
    <section class="detail-section">
      <h3>Tags</h3>
      <div class="badge-list">${tags.map((tag) => badge(tag, "neutral")).join("")}</div>
    </section>
  `;
}

async function showArtifactPreview(endpoint) {
  const container = document.getElementById("artifact-preview");
  if (!container) return;
  container.innerHTML = loadingState();
  try {
    const preview = await api(endpoint);
    container.innerHTML = `
      <section class="detail-section artifact-preview">
        <h3>${escapeHtml(preview.fileName)}</h3>
        <p class="mono">${escapeHtml(preview.contentType)} · ${preview.sizeBytes.toLocaleString()} bytes</p>
        ${renderArtifactContent(preview)}
      </section>
    `;
    container.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    container.innerHTML = errorState(error);
  }
}

function renderArtifactContent(preview) {
  if (preview.kind === "image") {
    return `<img src="${escapeAttribute(preview.content)}" alt="Preview of ${escapeAttribute(preview.fileName)}">`;
  }
  if (preview.contentType === "text/csv") return renderCsvPreview(preview.content);
  if (preview.contentType === "text/markdown") return renderMarkdownPreview(preview.content);
  if (preview.contentType === "application/json") {
    try {
      return `<pre>${escapeHtml(JSON.stringify(JSON.parse(preview.content), null, 2))}</pre>`;
    } catch {
      return `<pre>${escapeHtml(preview.content)}</pre>`;
    }
  }
  return `<pre>${escapeHtml(preview.content)}</pre>`;
}

function renderCsvPreview(content) {
  const rows = parseCsv(content);
  const visibleRows = rows.slice(0, 200);
  const width = Math.min(30, Math.max(0, ...visibleRows.map((row) => row.length)));
  if (visibleRows.length === 0 || width === 0) return '<p class="empty">The CSV file is empty.</p>';
  const [header, ...body] = visibleRows;
  const truncated = rows.length > visibleRows.length || rows.some((row) => row.length > width);
  return `
    <div class="table-wrap artifact-csv">
      <table>
        <thead><tr>${header.slice(0, width).map((value) => `<th>${escapeHtml(value)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((row) => `<tr>${Array.from({ length: width }, (_, index) => `<td>${escapeHtml(row[index] || "")}</td>`).join("")}</tr>`).join("")}</tbody>
      </table>
    </div>
    ${truncated ? '<p class="empty">Preview limited to 200 rows and 30 columns.</p>' : ""}
  `;
}

function parseCsv(content) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < content.length; index += 1) {
    const character = content[index];
    if (character === '"' && quoted && content[index + 1] === '"') {
      field += '"';
      index += 1;
    } else if (character === '"') {
      quoted = !quoted;
    } else if (character === "," && !quoted) {
      row.push(field);
      field = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && content[index + 1] === "\n") index += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }
  if (field || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function renderMarkdownPreview(content) {
  let inCode = false;
  const rendered = [];
  for (const line of content.split(/\r?\n/)) {
    if (line.startsWith("```")) {
      rendered.push(inCode ? "</code></pre>" : '<pre><code>');
      inCode = !inCode;
    } else if (inCode) {
      rendered.push(`${escapeHtml(line)}\n`);
    } else {
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      const listItem = line.match(/^\s*[-*]\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        rendered.push(`<h${level}>${escapeHtml(heading[2])}</h${level}>`);
      } else if (listItem) {
        rendered.push(`<div class="markdown-list-item">• ${escapeHtml(listItem[1])}</div>`);
      } else if (line.trim()) {
        rendered.push(`<p>${escapeHtml(line)}</p>`);
      } else {
        rendered.push("");
      }
    }
  }
  if (inCode) rendered.push("</code></pre>");
  return `<div class="markdown-preview">${rendered.join("\n")}</div>`;
}

function localArtifactSection(localArtifacts) {
  if (!localArtifacts) return "";
  return `
    <section class="detail-section">
      <h3>Local diagnostic/full artifacts</h3>
      <div class="artifact-boundary">
        <p><strong>Trades:</strong> ${escapeHtml(localArtifacts.trades.reason)}</p>
        <p><strong>Equity:</strong> ${escapeHtml(localArtifacts.equity.reason)}</p>
        <p>The current schema v8 stores durable counts, checksums, and the private artifact publication outbox. This viewer does not read arbitrary files or imply that omitted rows exist.</p>
      </div>
    </section>
  `;
}

function detailGrid(title, values) {
  const items = Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== "");
  return `
    <section class="detail-section">
      <h3>${escapeHtml(title)}</h3>
      <dl class="detail-grid">
        ${items.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(formatValue(value))}</dd></div>`).join("")}
      </dl>
    </section>
  `;
}

function cards(title, items, render, emptyMessage) {
  const collection = Array.isArray(items)
    ? { items, total: items.length, truncated: false }
    : items || { items: [], total: 0, truncated: false };
  return `
    <section class="detail-section">
      <h3>${escapeHtml(title)}</h3>
      ${collection.items.length ? `<div class="card-grid">${collection.items.map(render).join("")}</div>` : `<p class="empty">${escapeHtml(emptyMessage)}</p>`}
      ${collection.truncated ? `<p class="empty">Showing the first ${collection.items.length} of ${collection.total} records.</p>` : ""}
    </section>
  `;
}

function renderPagination(container, result, onPage) {
  const totalPages = Math.max(1, Math.ceil(result.total / result.pageSize));
  container.replaceChildren();
  const previous = node("button", "", "Previous");
  previous.disabled = result.page <= 1;
  previous.addEventListener("click", () => onPage(result.page - 1));
  const next = node("button", "", "Next");
  next.disabled = result.page >= totalPages;
  next.addEventListener("click", () => onPage(result.page + 1));
  container.append(previous, node("span", "", `Page ${result.page} of ${totalPages} · ${result.total} records`), next);
}

function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function badges(items) {
  return `<div class="badge-list">${items.filter(Boolean).map((item) => badge(item.label, item.tone)).join("")}</div>`;
}

function badge(label, tone = "neutral") {
  return `<span class="badge ${escapeAttribute(tone)}">${escapeHtml(label || "unknown")}</span>`;
}

function executionBadge(version) {
  return version == null
    ? { label: "Execution unknown", tone: "danger" }
    : Number(version) < 6
      ? { label: `Pre-P2b v${version}`, tone: "warning" }
      : { label: `Execution v${version}`, tone: "good" };
}

function publicationTone(value) {
  if (value === "published" || value === "reconciled") return "good";
  if (value === "dead_letter" || value === "failed") return "danger";
  if (value === "pending" || value === "publishing") return "warning";
  return "neutral";
}

function openDetailLoading() {
  elements["detail-content"].innerHTML = loadingState();
  elements.detail.showModal();
}

function showLoading(loading) {
  elements.loading.classList.toggle("hidden", !loading);
}

function showError(error) {
  elements.error.classList.toggle("hidden", !error);
  elements.error.textContent = error ? `Unable to load local research: ${error.message || error}` : "";
}

function loadingState() {
  return '<p class="loading">Loading…</p>';
}

function emptyState(message) {
  return `<p class="empty">${escapeHtml(message)}</p>`;
}

function errorState(error) {
  return `<p class="error">Unable to load: ${escapeHtml(error.message || String(error))}</p>`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return formatJson(value);
  return String(value);
}

function formatJson(value) {
  return value == null ? "—" : JSON.stringify(value, null, 2);
}

function humanize(value) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function sumValues(value) {
  return Object.values(value).reduce((total, item) => total + Number(item || 0), 0);
}

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

loadAll();
