const state = {
  runs: { page: 1, pageSize: 25 },
  registry: { page: 1, pageSize: 25 },
};

const elements = Object.fromEntries(
  ["app", "loading", "error", "overview", "runs", "run-pagination", "registry", "registry-pagination", "publications", "db-path", "detail", "detail-content"]
    .map((id) => [id, document.getElementById(id)]),
);

document.getElementById("refresh").addEventListener("click", () => loadAll());
document.getElementById("run-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  state.runs = { ...state.runs, page: 1, ...formValues(event.currentTarget) };
  loadRuns();
});
document.getElementById("registry-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  state.registry = { ...state.registry, page: 1, ...formValues(event.currentTarget) };
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
    await Promise.all([loadRuns(false), loadRegistry(false), loadPublications(false)]);
    showError(null);
    elements.app.classList.remove("hidden");
  } catch (error) {
    showError(error);
  } finally {
    showLoading(false);
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
      ${cards("Lineage", detail.relationships, (relation) => `
        <article class="detail-card"><strong>${escapeHtml(relation.relation)}</strong><p>${escapeHtml(relation.targetType)} · ${escapeHtml(relation.targetId)}</p><small>${relation.resolved ? "resolved" : "unresolved"}</small></article>
      `, "No registry relationships.")}
      ${cards("Artifact references", detail.artifacts, (artifact) => `
        <article class="detail-card"><strong>Artifact ${artifact.ordinal + 1}</strong><p>${escapeHtml(artifact.repositoryPath || artifact.externalLocation || "Location unavailable")}</p><small>Reference only; the viewer never reads artifact files.</small></article>
      `, "No artifact locations.")}
    `;
  } catch (error) {
    elements["detail-content"].innerHTML = errorState(error);
  }
}

function selectRegistrySummary(payload) {
  const allowed = [
    "title", "rationale", "content", "idea", "reason_rejected", "strategy_name",
    "strategy_version", "parameters", "dataset_config", "results_summary",
    "conclusion", "tags", "artifact_type", "checksum", "size_bytes",
    "hypothesis_id", "parent_experiment_id", "related_experiment_id", "evidence",
  ];
  return Object.fromEntries(allowed.filter((key) => payload?.[key] !== undefined).map((key) => [humanize(key), formatValue(payload[key])]));
}

function localArtifactSection(localArtifacts) {
  if (!localArtifacts) return "";
  return `
    <section class="detail-section">
      <h3>Local diagnostic/full artifacts</h3>
      <div class="artifact-boundary">
        <p><strong>Trades:</strong> ${escapeHtml(localArtifacts.trades.reason)}</p>
        <p><strong>Equity:</strong> ${escapeHtml(localArtifacts.equity.reason)}</p>
        <p>The current schema v7 stores durable counts and checksums. This viewer does not read arbitrary files or imply that omitted rows exist.</p>
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
