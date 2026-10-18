import { mountPerformanceCharts } from "./run-charts.mjs";
import { experimentList, experimentDetail } from "./experiment-view.mjs";

const state = {
  runs: { page: 1, pageSize: 25 },
  registry: { page: 1, pageSize: 25 },
  experiments: { page: 1, pageSize: 10 },
};
let detailRequest = 0;
let experimentRequest = 0;
let selectedExperiment;
let disposeRunCharts;

const elements = Object.fromEntries(
  [
    "app", "loading", "error", "overview", "runs", "run-pagination", "registry",
    "registry-pagination", "publications", "db-path", "detail", "detail-content",
    "experiments", "experiment-pagination", "experiment-detail", "experiment-dialog",
  ]
    .map((id) => [id, document.getElementById(id)]),
);
elements.detail.addEventListener("close", () => {
  detailRequest += 1;
  disposeRunCharts?.();
  disposeRunCharts = undefined;
});
elements["experiment-dialog"].addEventListener("close", () => {
  experimentRequest += 1;
  selectedExperiment = undefined;
});

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
    ]);
    await loadExperiments();
    if (selectedExperiment) await showExperiment(selectedExperiment, false);
    showError(null);
    elements.app.classList.remove("hidden");
  } catch (error) {
    showError(error);
  } finally {
    showLoading(false);
  }
}

async function loadExperiments() {
  try {
    const result = await api("/api/experiments", state.experiments);
    document.querySelector("#overview-experiments strong").textContent = result.total.toLocaleString();
    elements.experiments.innerHTML = result.items.length
      ? experimentList(result.items)
      : emptyState("No explicitly recorded experiments yet. Existing segment runs remain below; the viewer does not guess study membership from notes. New nested walk-forwards with --record-runs record the full experiment.");
    document.getElementById("ungrouped-runs").textContent =
      `${result.ungroupedRuns.toLocaleString()} runs have no explicit experiment membership. They remain inspectable, not silently regrouped.`;
    elements.experiments.querySelectorAll("[data-experiment]").forEach((button) => {
      button.addEventListener("click", () => showExperiment(button.dataset.experiment));
    });
    renderPagination(elements["experiment-pagination"], result, (page) => {
      state.experiments.page = page;
      loadExperiments();
    });
  } catch (error) {
    elements.experiments.innerHTML = errorState(error);
  }
}

async function showExperiment(experimentId, focus = true) {
  const request = ++experimentRequest;
  selectedExperiment = experimentId;
  const host = elements["experiment-detail"];
  host.innerHTML = loadingState();
  if (!elements["experiment-dialog"].open) elements["experiment-dialog"].showModal();
  if (focus) host.scrollTop = 0;
  try {
    const experiment = await api(`/api/experiments/${encodeURIComponent(experimentId)}`);
    if (request !== experimentRequest) return;
    host.innerHTML = experimentDetail(experiment);
    host.querySelectorAll("[data-run]").forEach((button) => {
      button.addEventListener("click", () => showRun(button.dataset.run));
    });
    host.querySelectorAll('a[href^="#experiment-fold-"]').forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        const fold = document.getElementById(link.hash.slice(1));
        if (fold) {
          fold.open = true;
          fold.scrollIntoView({ block: "start" });
        }
      });
    });
    if (focus) {
      host.querySelector("h2")?.focus({ preventScroll: true });
    }
  } catch (error) {
    if (request === experimentRequest) host.innerHTML = errorState(error);
  }
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
  const experimentCard = metricCard("Experiments", "—");
  experimentCard.id = "overview-experiments";
  // `validating` is where the recorder deliberately leaves a finished run:
  // promoting it to `completed` requires a real leakage report and a
  // bar-measured integrity summary. Without its own card these runs vanish
  // from the summary entirely, so a store holding 40 of them reads as
  // "0 completed, 0 needing attention" -- as though nothing had been run.
  elements.overview.replaceChildren(
    experimentCard,
    metricCard("Segment runs", totalRuns),
    metricCard("Awaiting validation", data.runs.validating || 0),
    metricCard("Completed", data.runs.completed || 0),
    metricCard("Validation attention", (data.runs.review_required || 0) + (data.runs.validation_failed || 0), true),
    metricCard("Execution failed", data.runs.execution_failed || 0, true),
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
        <td>
          <button class="link" data-run="${escapeAttribute(run.runId)}">${escapeHtml(run.displayAlias)}</button>
          <small>${escapeHtml(run.notes || run.strategyId)}</small>
          <small>${escapeHtml(run.state)}</small>
        </td>
        <td>
          ${escapeHtml(run.symbol || "—")}
          <small>${escapeHtml(formatSessionRange(run))}</small>
          <small>Engine integrity: ${escapeHtml(run.engineIntegrity === "not_assessed" ? "Not assessed" : run.engineIntegrity)}</small>
          <small>Procedure: ${escapeHtml(run.procedureExecution)}</small>
        </td>
        <td class="${signClass(run.netReturnPct)}">
          ${escapeHtml(run.netReturnPct === null || run.netReturnPct === undefined
            ? "—"
            : `${run.netReturnPct >= 0 ? "+" : ""}${run.netReturnPct.toFixed(2)}%`)}
          <small>${escapeHtml(run.netPnl === null || run.netPnl === undefined ? "—" : formatMoney(run.netPnl))}</small>
        </td>
        <td class="${signClass(run.metrics?.expectancy_r)}">
          ${escapeHtml(formatR(run.metrics?.expectancy_r))}
          <small>${escapeHtml(formatRate(run.metrics?.win_rate))} win</small>
        </td>
        <td>
          ${escapeHtml(formatDrawdown(run.metrics?.max_drawdown))}
          <small>${escapeHtml(`${run.tradeCount ?? "—"} trades`)}</small>
        </td>
        <td>${badges(exceptionBadges(run))}</td>
        <td>${formatDate(run.updatedAt)}</td>
      </tr>
    `).join("");
    elements.runs.innerHTML = table(
      ["Run", "Scope", "Net return", "Expectancy", "Risk", "Attention", "Updated"],
      rows,
    );
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
  const request = detailRequest;
  try {
    const detail = await api(`/api/runs/${encodeURIComponent(runId)}`);
    if (request !== detailRequest || !elements.detail.open) return;
    const run = detail.run;
    elements.detail.classList.add("run-dialog");
    elements["detail-content"].innerHTML = `
      <p class="eyebrow">LOCAL RUN · ${escapeHtml(run.state)}</p>
      <h2>${escapeHtml(run.displayAlias)}</h2>
      <p class="run-subtitle">${escapeHtml(run.strategyId)} · ${escapeHtml(detail.evidence?.first_session || "Unknown start")} to ${escapeHtml(detail.evidence?.last_session || "unknown end")}</p>
      <div class="badge-list">${badges(detail.badges)}</div>
      <p class="sample-notice">${escapeHtml(sampleExplanation(run.segmentRole))}</p>
      ${runHeadline(detail)}
      <div class="run-tabs" role="tablist" aria-label="Run detail">
        ${[
          ["performance", "Performance"], ["trades", "Trades"],
          ["configuration", "Configuration"], ["validation", "Validation"],
          ["audit", "Provenance & lifecycle"],
        ].map(([id, label], index) => `<button role="tab" id="tab-${id}" aria-controls="panel-${id}" aria-selected="${index === 0}" tabindex="${index === 0 ? 0 : -1}" data-run-tab="${id}">${label}</button>`).join("")}
      </div>
      <section role="tabpanel" id="panel-performance" aria-labelledby="tab-performance">
        <div id="run-equity-charts">${detail.localArtifacts?.equity?.available ? loadingState() : emptyState(detail.localArtifacts?.equity?.reason || "Equity observations were not retained. Summary statistics remain available below.")}</div>
        <div id="run-risk-summary"></div>
        <section class="detail-section">
          <h3>Recorded statistics</h3>
          <p class="chart-note">Stored metrics, not recalculated from the displayed subset. R = initial risk per trade. Rates below are fractions converted to percentages.</p>
          ${recordedMetrics(detail.metrics)}
        </section>
        <p class="empty">Benchmark, exposure, portfolio margin and turnover series are not recorded for this run. No substitute or zero-valued series is shown.</p>
      </section>
      <section role="tabpanel" id="panel-trades" aria-labelledby="tab-trades" hidden>
        <div id="run-trades">${detail.localArtifacts?.trades?.available ? loadingState() : emptyState(detail.localArtifacts?.trades?.reason || "Trade rows were not retained. Counts and checksums are available in Provenance.")}</div>
      </section>
      <section role="tabpanel" id="panel-configuration" aria-labelledby="tab-configuration" hidden>
        ${detailGrid("Parameters", detail.identity.parameters || {})}
        ${detailGrid("Execution and data", {
          "Strategy": run.strategyId,
          "Methodology": run.methodologyVersion,
          "Sample role": run.segmentRole || "unlabelled",
          "Dataset": detail.identity.data_snapshot_id,
          "Split manifest": detail.identity.split_manifest_hash,
          "Feature set": detail.identity.feature_set_version,
          "Execution model": detail.identity.execution_model_version,
          "Metric calculation": detail.identity.metric_calculation_version,
          "Notes": run.notes,
        })}
      </section>
      <section role="tabpanel" id="panel-validation" aria-labelledby="tab-validation" hidden>
        ${detailGrid("Decision boundaries", {
          "Engine integrity": detail.engineIntegrity?.status || "Not assessed",
          "Procedure execution": run.state,
          "Research reliability": "Study-level only; open the owning experiment",
        })}
        ${cards("Engine-integrity findings", detail.engineIntegrity?.report?.findings || [], (finding) => `
          <article class="finding ${finding.passed ? "" : "blocking"}"><strong>${escapeHtml(finding.check_id)} · ${escapeHtml(finding.category)}</strong>
          <p>${finding.passed ? "Passed" : "Failed"}</p>
          <small>Observed ${escapeHtml(formatValue(finding.observed))} · expected ${escapeHtml(formatValue(finding.expected))} · residual ${escapeHtml(formatValue(finding.residual))}</small></article>
        `, "Engine integrity: Not assessed. Historical runs are not retroactively classified.")}
        ${detailGrid("Validation", {
          "Profile / scope": detail.validation ? `${detail.validation.profileName} / ${detail.validation.scope}` : "No report",
          "Target state": detail.validation?.targetState,
          "Leakage passed": detail.validation ? String(detail.validation.leakagePassed) : "—",
          "Registry hash at execution": detail.validation?.registryHashAtExecution,
          "Current registry hash": detail.validation?.currentRegistryHash,
          "Finding counts": formatJson(detail.findingCounts),
        })}
        ${cards("Validation findings", detail.findings, (finding) => `
          <article class="finding ${escapeAttribute(finding.severity)}"><strong>${escapeHtml(finding.code)} · ${escapeHtml(finding.severity)}</strong><p>${escapeHtml(finding.message)}</p><small>${escapeHtml(finding.category)}${finding.metricKey ? ` · ${escapeHtml(finding.metricKey)}` : ""}</small>
          <details class="finding-evidence"><summary>Observed values, expected bounds & diagnostic context</summary>
            ${statGrid({
              "Observed": finding.observed == null ? "Not recorded" : formatValue(finding.observed),
              "Expected": finding.expected || "Not recorded",
              "Context": formatJson(finding.context),
            })}
          </details></article>
        `, "No validation findings recorded. This alone is not proof of validation; check the report above.")}
      </section>
      <section role="tabpanel" id="panel-audit" aria-labelledby="tab-audit" hidden>
      ${detailGrid("Run identity", {
        "Run ID": run.runId,
        "Strategy": run.strategyId,
        "Methodology": run.methodologyVersion,
        "Sample role": run.segmentRole
          ? run.segmentRole
          : "unlabelled (sample role was not persisted)",
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
      </section>
    `;
    bindRunTabs();
    await renderRetainedRows(runId, detail, request);
  } catch (error) {
    if (request === detailRequest && elements.detail.open) elements["detail-content"].innerHTML = errorState(error);
  }
}

function sampleExplanation(role) {
  if (role === "test") return "Sample role: test (held-out window). Validation and provenance still matter; this label is not a profitability guarantee.";
  if (role === "train") return "IN-SAMPLE · Training data used to compare parameters. These results are not out-of-sample evidence.";
  if (role === "validation") return "IN-SAMPLE · Validation data used for candidate selection or acceptance. Inspect the held-out test runs separately.";
  return "UNKNOWN SAMPLE ROLE · This run has no persisted role. It may predate role tracking or come from an exploratory caller. Do not treat it as held-out evidence.";
}

function runHeadline(detail) {
  const metrics = Object.fromEntries(detail.metrics.items.map((metric) => [metric.key, metric.value]));
  const evidence = detail.evidence || {};
  const totalReturn = evidence.starting_equity > 0 && Number.isFinite(evidence.ending_equity)
    ? (evidence.ending_equity - evidence.starting_equity) / evidence.starting_equity : metrics.total_return;
  const items = [
    ["Ending equity", formatMoney(evidence.ending_equity)],
    ["Net profit", formatMoney(evidence.net_pnl), evidence.net_pnl],
    ["Return", formatPercent(totalReturn), totalReturn],
    ["Total costs", formatMoney(evidence.total_costs)],
    ["Trades", formatValue(evidence.trade_count ?? metrics.n_trades)],
    ["Win rate", formatPercent(metrics.win_rate)],
    ["Expectancy", formatR(metrics.expectancy_r), metrics.expectancy_r],
    ["Sharpe ratio", Number.isFinite(metrics.sharpe_ratio) ? metrics.sharpe_ratio.toFixed(3) : "—"],
  ];
  return `<dl class="run-headline">${items.map(([label, value, sign]) => `
    <div><dt>${escapeHtml(label)}</dt><dd class="${signClass(sign)}">${escapeHtml(value)}</dd></div>
  `).join("")}</dl>`;
}

function recordedMetrics(metrics) {
  const percentages = new Set(["win_rate", "total_return", "annualized_return", "max_drawdown"]);
  const money = new Set(["total_pnl", "gross_pnl", "net_pnl", "total_costs"]);
  const rows = metrics.items.map((metric) => `
    <tr><th scope="row">${escapeHtml(humanize(metric.key))}</th>
      <td>${escapeHtml(percentages.has(metric.key) ? formatPercent(metric.value)
        : metric.key === "top10_pct" ? `${Number(metric.value).toFixed(2)}%`
          : money.has(metric.key) ? formatMoney(metric.value)
            : metric.key.endsWith("_r") ? formatR(metric.value) : formatValue(metric.value))}</td>
      <td>v${escapeHtml(metric.calculationVersion)}</td></tr>
  `).join("");
  return rows
    ? `${table(["Metric", "Value", "Calculation"], rows)}${metrics.truncated ? emptyState(`Showing ${metrics.items.length} of ${metrics.total} recorded metrics.`) : ""}`
    : emptyState("No metrics stored.");
}

function bindRunTabs() {
  const tabs = [...elements["detail-content"].querySelectorAll("[data-run-tab]")];
  function activate(tab) {
    for (const button of tabs) {
      const active = button === tab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      document.getElementById(button.getAttribute("aria-controls")).hidden = !active;
    }
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      let target;
      if (event.key === "ArrowRight") target = tabs[(index + 1) % tabs.length];
      if (event.key === "ArrowLeft") target = tabs[(index + tabs.length - 1) % tabs.length];
      if (event.key === "Home") target = tabs[0];
      if (event.key === "End") target = tabs.at(-1);
      if (target) {
        event.preventDefault();
        activate(target);
        target.focus();
      }
    });
  });
}

// Fetched after the summary is painted: the curve can be thousands of points,
// and a slow or failed detail fetch must not blank the run summary.
async function renderRetainedRows(runId, detail, request) {
  const encoded = encodeURIComponent(runId);
  await Promise.all(["equity", "trades"].map(async (kind) => {
    if (!detail.localArtifacts?.[kind]?.available) return;
    const host = document.getElementById(kind === "equity" ? "run-equity-charts" : "run-trades");
    try {
      const payload = await api(`/api/runs/${encoded}/${kind}`);
      if (request !== detailRequest || !host?.isConnected) return;
      if (kind === "equity") {
        disposeRunCharts = mountPerformanceCharts(host, payload, payload.startingCapital);
        document.getElementById("run-risk-summary").innerHTML = detailGrid("Risk & curve coverage", {
          "Starting capital (evidence)": formatMoney(payload.startingCapital),
          "First retained equity mark": formatMoney(payload.startingEquity),
          "Max drawdown (intraday)": formatPercent(payload.maxDrawdown),
          "Intraday trough at": formatMarketTime(payload.maxDrawdownAt),
          "Max drawdown (session close)": formatPercent(payload.maxSessionDrawdown),
          "Worst close at": formatMarketTime(payload.maxSessionDrawdownAt),
          "Sessions retained": payload.sessionCount,
        });
      } else {
        host.innerHTML = tradeDistributionSection(payload);
        bindTradeTable(host, payload);
      }
    } catch (error) {
      if (request === detailRequest && host?.isConnected) host.innerHTML = errorState(error);
    }
  }));
}

async function showRegistry(recordType, recordId) {
  openDetailLoading();
  const request = detailRequest;
  try {
    const detail = await api(`/api/registry/${encodeURIComponent(recordType)}/${encodeURIComponent(recordId)}`);
    if (request !== detailRequest || !elements.detail.open) return;
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
    if (request === detailRequest && elements.detail.open) elements["detail-content"].innerHTML = errorState(error);
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
  const describe = (entry, label) => {
    if (entry?.available) {
      return `<p><strong>${label}:</strong> ${Number(entry.rowCount).toLocaleString()} rows retained locally.</p>`;
    }
    return `<p><strong>${label}:</strong> ${escapeHtml(entry?.reason || "Unavailable.")}</p>`;
  };
  return `
    <section class="detail-section">
      <h3>Local diagnostic/full artifacts</h3>
      <div class="artifact-boundary">
        ${describe(localArtifacts.trades, "Trades")}
        ${describe(localArtifacts.equity, "Equity")}
        <p>Schema v9 retains trade and equity rows for runs recorded with full or
        diagnostic retention. Availability reflects rows actually present, not the
        declared retention profile. This viewer does not read arbitrary files or
        imply that omitted rows exist.</p>
      </div>
    </section>
  `;
}

function tradeDistributionSection(payload) {
  const trades = payload?.trades || [];
  if (!trades.length) {
    return `<section class="detail-section"><h3>Trades</h3><p class="empty">No retained trade rows.</p></section>`;
  }
  const pnls = trades.map((trade) => trade.pnl).filter(Number.isFinite);
  const wins = pnls.filter((pnl) => pnl > 0);
  const losses = pnls.filter((pnl) => pnl < 0);
  const grossWin = wins.reduce((sum, pnl) => sum + pnl, 0);
  const grossLoss = Math.abs(losses.reduce((sum, pnl) => sum + pnl, 0));

  // Fixed bucket count over the observed range; a trade exactly at the maximum
  // belongs in the last bucket rather than one past the end.
  const bucketCount = 20;
  const minimum = pnls.length ? Math.min(...pnls) : 0;
  const maximum = pnls.length ? Math.max(...pnls) : 0;
  const width = (maximum - minimum) || 1;
  const buckets = new Array(bucketCount).fill(0);
  for (const pnl of pnls) {
    const index = Math.min(bucketCount - 1, Math.floor(((pnl - minimum) / width) * bucketCount));
    buckets[index] += 1;
  }
  const tallest = Math.max(...buckets, 1);
  const barWidth = 720 / bucketCount;
  const bars = buckets.map((count, index) => {
    const height = (count / tallest) * 140;
    const bucketFloor = minimum + (index / bucketCount) * width;
    const colour = bucketFloor < 0 ? "#e06c75" : "#56b16a";
    return `<rect x="${(index * barWidth).toFixed(2)}" y="${(140 - height).toFixed(2)}" width="${(barWidth - 1).toFixed(2)}" height="${height.toFixed(2)}" fill="${colour}"><title>${escapeHtml(`${formatMoney(bucketFloor)}: ${count} trades`)}</title></rect>`;
  }).join("");

  return `
    <section class="detail-section">
      <h3>Trades</h3>
      <p class="chart-note">Statistics and distribution use ${pnls.length.toLocaleString()} realized-P&amp;L rows from ${trades.length.toLocaleString()} loaded / ${payload.total.toLocaleString()} retained trades.${payload.truncated ? " This is a capped subset, not full-run statistics; use recorded statistics on Performance for the run totals." : ""} Missing P&amp;L is excluded, not counted as breakeven.</p>
      ${pnls.length ? statGrid({
        "Loaded trades": trades.length.toLocaleString(),
        "Win rate": formatPercent(wins.length / pnls.length),
        "Wins / losses": `${wins.length} / ${losses.length}`,
        "Breakeven": pnls.length - wins.length - losses.length,
        "Average win": wins.length ? formatMoney(grossWin / wins.length) : "—",
        "Average loss": losses.length ? formatMoney(-grossLoss / losses.length) : "—",
        "Profit factor": grossLoss > 0 ? (grossWin / grossLoss).toFixed(2) : grossWin > 0 ? "No losing trades" : "—",
        "Net P&L": formatMoney(pnls.reduce((sum, pnl) => sum + pnl, 0)),
      }) : emptyState("Retained trades have no realized P&L. Win/loss statistics and distribution are unavailable; trade rows remain inspectable below.")}
      ${pnls.length ? `<h4>P&amp;L distribution</h4>
      <svg class="chart" viewBox="0 0 720 140" preserveAspectRatio="none" role="img" aria-label="Trade P&amp;L distribution">${bars}</svg>
      <small>${escapeHtml(`${formatMoney(minimum)} to ${formatMoney(maximum)} across ${bucketCount} buckets.`)}</small>` : ""}
      <form class="trade-filters">
        <label>Symbol / exit reason<input name="search" type="search" placeholder="Filter loaded trades"></label>
        <label>Outcome<select name="outcome"><option value="">All</option><option value="win">Winning</option><option value="loss">Losing</option><option value="flat">Breakeven</option><option value="unknown">Missing P&amp;L</option></select></label>
      </form>
      <div class="trade-ledger"></div>
      <div class="pagination trade-pagination"></div>
    </section>
  `;
}

function bindTradeTable(host, payload) {
  const form = host.querySelector(".trade-filters");
  if (!form) return;
  let page = 1;
  const pageSize = 50;
  function render() {
    const search = form.elements.search.value.trim().toLowerCase();
    const outcome = form.elements.outcome.value;
    const filtered = payload.trades.filter((trade) => {
      if (!`${trade.symbol} ${trade.exitReason || ""}`.toLowerCase().includes(search)) return false;
      if (outcome === "unknown") return !Number.isFinite(trade.pnl);
      if (outcome && !Number.isFinite(trade.pnl)) return false;
      return !outcome || (outcome === "win" ? trade.pnl > 0 : outcome === "loss" ? trade.pnl < 0 : trade.pnl === 0);
    });
    const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
    page = Math.min(page, pageCount);
    const rows = filtered.slice((page - 1) * pageSize, page * pageSize).map((trade) => `
      <tr>
        <td>${escapeHtml(trade.sequence)}</td><td>${escapeHtml(trade.symbol)}</td><td>${escapeHtml(trade.side)}</td>
        <td>${escapeHtml(formatValue(trade.quantity))}</td>
        <td>${escapeHtml(formatMoney(trade.entryPrice))}</td><td>${escapeHtml(formatMoney(trade.exitPrice))}</td>
        <td class="${signClass(trade.pnl)}">${escapeHtml(formatMoney(trade.pnl))}</td>
        <td>${escapeHtml(trade.exitReason || "—")}</td>
        <td>${escapeHtml(formatMarketTime(trade.entryTime))}</td><td>${escapeHtml(formatMarketTime(trade.exitTime))}</td>
      </tr>
    `).join("");
    host.querySelector(".trade-ledger").innerHTML = rows
      ? table(["#", "Symbol", "Side", "Qty", "Entry", "Exit", "P&L", "Reason", "Entered", "Exited"], rows)
      : emptyState("No loaded trades match these filters.");
    host.querySelector(".trade-pagination").innerHTML = `
      <button data-trade-page="-1" ${page === 1 ? "disabled" : ""}>Previous</button>
      <span>Page ${page} / ${pageCount} · ${filtered.length.toLocaleString()} matching loaded trades</span>
      <button data-trade-page="1" ${page === pageCount ? "disabled" : ""}>Next</button>
    `;
    host.querySelectorAll("[data-trade-page]").forEach((button) => button.addEventListener("click", () => {
      page += Number(button.dataset.tradePage);
      render();
    }));
  }
  form.addEventListener("submit", (event) => event.preventDefault());
  form.addEventListener("input", () => { page = 1; render(); });
  render();
}

function formatMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString(undefined, { style: "currency", currency: "USD" });
}

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

// -- run list formatting -------------------------------------------------
//
// A study fills the list with dozens of runs that share a strategy, a state
// and a badge set. These helpers surface the handful of values that actually
// differ between them, so a run can be recognised without opening it.

function formatSessionRange(run) {
  if (!run.firstSession && !run.lastSession) return "—";
  if (run.firstSession === run.lastSession) return String(run.firstSession);
  return `${run.firstSession ?? "?"} → ${run.lastSession ?? "?"}`;
}

function formatR(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(3)}R`;
}

// Rates are recorded as fractions, but a stored 27.6 would mean 27.6% not
// 2760%. Treat anything above 1 as already-scaled rather than silently
// rendering an absurd number.
function formatRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${(number > 1 ? number : number * 100).toFixed(1)}%`;
}

// Drawdown is recorded as a negative fraction. It is always a loss, so render
// it with an explicit minus and never let a sign convention make a bad run
// look good.
function formatDrawdown(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const magnitude = Math.abs(Number(value));
  return `-${(magnitude > 1 ? magnitude : magnitude * 100).toFixed(2)}%`;
}

function signClass(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "";
  return Number(value) < 0 ? "negative" : "positive";
}

// Show only what needs attention. A row that repeats the same four reassuring
// badges on every run trains the eye to skip the column entirely, which is
// precisely when a real warning gets missed.
function exceptionBadges(run) {
  const flagged = (run.badges || []).filter(
    // Retention is a local storage choice, not a defect. Flagging it on every
    // full-retention row would bury the badges that do mean something.
    (item) => item.tone !== "good" && !/ retention$/.test(item.label),
  );
  if (run.publicationState && run.publicationState !== "local") {
    flagged.push({ label: run.publicationState, tone: publicationTone(run.publicationState) });
  }
  return flagged.length ? flagged : [{ label: "clean", tone: "good" }];
}

// Market timestamps are stored with the exchange's UTC offset. Rendering them
// through toLocaleString() would convert them to the viewing device's timezone,
// so a 09:35 ET entry reads as 06:35 on a Pacific machine -- unrecognisable in a
// tool where the opening range is defined in exchange time. Display the wall
// clock exactly as recorded instead, and never construct a Date.
function formatMarketTime(value) {
  if (!value) return "—";
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/.exec(String(value));
  if (!match) return String(value);
  const [, date, time, offset] = match;
  const label = !offset ? "" : offset === "Z" ? " UTC" : ` ${offset}`;
  return `${date} ${time}${label}`;
}

function statGrid(values) {
  const items = Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== "");
  return `
    <dl class="detail-grid">
      ${items.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(formatValue(value))}</dd></div>`).join("")}
    </dl>
  `;
}

function detailGrid(title, values) {
  return `
    <section class="detail-section">
      <h3>${escapeHtml(title)}</h3>
      ${statGrid(values)}
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
  detailRequest += 1;
  disposeRunCharts?.();
  disposeRunCharts = undefined;
  elements.detail.classList.remove("run-dialog");
  elements["detail-content"].innerHTML = loadingState();
  if (!elements.detail.open) elements.detail.showModal();
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
