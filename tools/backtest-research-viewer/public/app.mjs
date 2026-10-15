const state = {
  runs: { page: 1, pageSize: 25 },
  registry: { page: 1, pageSize: 25 },
};

const elements = Object.fromEntries(
  [
    "app", "loading", "error", "overview", "runs", "run-pagination", "registry",
    "registry-pagination", "publications", "db-path", "detail", "detail-content",
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
      initializeAnalysisWorkspace(),
    ]);
    showError(null);
    elements.app.classList.remove("hidden");
  } catch (error) {
    showError(error);
  } finally {
    showLoading(false);
  }
}

async function initializeAnalysisWorkspace() {
  const workspace = document.getElementById("analysis-workspace");
  if (workspace.adapter) return;
  workspace.adapter = {
    getAnalysisFieldCatalog: () => api("/api/analysis/catalog"),
    estimateAnalysis: (query) => api("/api/analysis/estimate", { query: JSON.stringify(query) }),
    runAnalysis: (query) => api("/api/analysis/query", { query: JSON.stringify(query) }),
  };
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
  // `validating` is where the recorder deliberately leaves a finished run:
  // promoting it to `completed` requires a real leakage report and a
  // bar-measured integrity summary. Without its own card these runs vanish
  // from the summary entirely, so a store holding 40 of them reads as
  // "0 completed, 0 needing attention" -- as though nothing had been run.
  elements.overview.replaceChildren(
    metricCard("Runs", totalRuns),
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
        "Sample role": run.segmentRole
          ? run.segmentRole
          : "unlabelled (recorded before roles were tracked)",
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
      <div id="run-detail-rows"></div>
    `;
    await renderRetainedRows(runId, detail.localArtifacts);
  } catch (error) {
    elements["detail-content"].innerHTML = errorState(error);
  }
}

// Fetched after the summary is painted: the curve can be thousands of points,
// and a slow or failed detail fetch must not blank the run summary.
async function renderRetainedRows(runId, localArtifacts) {
  const host = document.getElementById("run-detail-rows");
  if (!host) return;
  const wantsEquity = Boolean(localArtifacts?.equity?.available);
  const wantsTrades = Boolean(localArtifacts?.trades?.available);
  if (!wantsEquity && !wantsTrades) return;

  host.innerHTML = `<p class="empty">Loading retained rows…</p>`;
  try {
    const encoded = encodeURIComponent(runId);
    const [equity, trades] = await Promise.all([
      wantsEquity ? api(`/api/runs/${encoded}/equity`) : Promise.resolve(null),
      wantsTrades ? api(`/api/runs/${encoded}/trades`) : Promise.resolve(null),
    ]);
    host.innerHTML = `
      ${wantsEquity ? equitySection(equity) : ""}
      ${wantsTrades ? tradeDistributionSection(trades) : ""}
    `;
  } catch (error) {
    host.innerHTML = errorState(error);
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

// Inline SVG keeps the viewer dependency-light: no chart library, no network
// fetch for assets, and nothing to keep in sync with a package manifest.
function polylinePath(values, width, height, minimum, maximum) {
  if (!values.length) return "";
  const span = maximum - minimum;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  return values
    .map((value, index) => {
      const x = (index * step).toFixed(2);
      const y = (span > 0 ? height - ((value - minimum) / span) * height : height / 2).toFixed(2);
      return `${x},${y}`;
    })
    .join(" ");
}

function equitySection(equity) {
  if (!equity || !equity.points?.length) {
    return `<section class="detail-section"><h3>Equity curve</h3><p class="empty">No retained equity points.</p></section>`;
  }
  const values = equity.points.map((point) => point.equity);
  const drawdowns = equity.points.map((point) => point.drawdown);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const totalReturn = equity.startingEquity > 0
    ? (equity.endingEquity - equity.startingEquity) / equity.startingEquity
    : null;
  const sampling = equity.downsampled
    ? `Showing ${equity.returned.toLocaleString()} of ${equity.total.toLocaleString()} points (every ${equity.stride}). Drawdown is computed across all ${equity.total.toLocaleString()} points.`
    : `Showing all ${equity.total.toLocaleString()} points.`;
  return `
    <section class="detail-section">
      <h3>Equity curve</h3>
      ${statGrid({
        "Starting equity": formatMoney(equity.startingEquity),
        "Ending equity": formatMoney(equity.endingEquity),
        "Total return": totalReturn === null ? "—" : formatPercent(totalReturn),
        "Sessions": equity.sessionCount?.toLocaleString(),
        "Max drawdown (daily close)": formatPercent(equity.maxSessionDrawdown),
        "Worst close at": formatMarketTime(equity.maxSessionDrawdownAt),
        "Max drawdown (intraday)": formatPercent(equity.maxDrawdown),
        "Intraday trough at": formatMarketTime(equity.maxDrawdownAt),
      })}
      <svg class="chart" viewBox="0 0 720 200" preserveAspectRatio="none" role="img" aria-label="Equity curve">
        <polyline fill="none" stroke="#4c8dff" stroke-width="1.5"
          points="${polylinePath(values, 720, 200, minimum, maximum)}" />
      </svg>
      <h4>Drawdown (intraday, mark-to-market)</h4>
      <svg class="chart" viewBox="0 0 720 120" preserveAspectRatio="none" role="img" aria-label="Drawdown">
        <polyline fill="none" stroke="#e06c75" stroke-width="1.5"
          points="${polylinePath(drawdowns, 720, 120, Math.min(...drawdowns), 0)}" />
      </svg>
      <small>${escapeHtml(sampling)}</small>
    </section>
  `;
}

function tradeDistributionSection(payload) {
  const trades = payload?.trades || [];
  if (!trades.length) {
    return `<section class="detail-section"><h3>Trades</h3><p class="empty">No retained trade rows.</p></section>`;
  }
  const pnls = trades.map((trade) => Number(trade.pnl ?? 0));
  const wins = pnls.filter((pnl) => pnl > 0);
  const losses = pnls.filter((pnl) => pnl < 0);
  const grossWin = wins.reduce((sum, pnl) => sum + pnl, 0);
  const grossLoss = Math.abs(losses.reduce((sum, pnl) => sum + pnl, 0));

  // Fixed bucket count over the observed range; a trade exactly at the maximum
  // belongs in the last bucket rather than one past the end.
  const bucketCount = 20;
  const minimum = Math.min(...pnls);
  const maximum = Math.max(...pnls);
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
    return `<rect x="${(index * barWidth).toFixed(2)}" y="${(140 - height).toFixed(2)}" width="${(barWidth - 1).toFixed(2)}" height="${height.toFixed(2)}" fill="${colour}" />`;
  }).join("");

  const rows = trades.slice(0, 200).map((trade) => `
    <tr>
      <td>${escapeHtml(trade.sequence)}</td>
      <td>${escapeHtml(trade.symbol)}</td>
      <td>${escapeHtml(trade.side)}</td>
      <td>${escapeHtml(formatValue(trade.quantity))}</td>
      <td>${escapeHtml(formatValue(trade.entryPrice))}</td>
      <td>${escapeHtml(formatValue(trade.exitPrice))}</td>
      <td class="${Number(trade.pnl) < 0 ? "negative" : "positive"}">${escapeHtml(formatMoney(trade.pnl))}</td>
      <td>${escapeHtml(trade.exitReason || "—")}</td>
      <td>${escapeHtml(formatMarketTime(trade.entryTime))}</td>
    </tr>
  `).join("");

  return `
    <section class="detail-section">
      <h3>Trades</h3>
      ${statGrid({
        "Trades": payload.total.toLocaleString(),
        "Win rate": formatPercent(wins.length / pnls.length),
        "Wins / losses": `${wins.length} / ${losses.length}`,
        "Average win": wins.length ? formatMoney(grossWin / wins.length) : "—",
        "Average loss": losses.length ? formatMoney(-grossLoss / losses.length) : "—",
        "Profit factor": grossLoss > 0 ? (grossWin / grossLoss).toFixed(2) : "—",
        "Net P&L": formatMoney(pnls.reduce((sum, pnl) => sum + pnl, 0)),
      })}
      <h4>P&amp;L distribution</h4>
      <svg class="chart" viewBox="0 0 720 140" preserveAspectRatio="none" role="img" aria-label="Trade P&amp;L distribution">${bars}</svg>
      <small>${escapeHtml(`${formatMoney(minimum)} to ${formatMoney(maximum)} across ${bucketCount} buckets.`)}</small>
      ${table(
        ["#", "Symbol", "Side", "Qty", "Entry", "Exit", "P&L", "Reason", "Entered"],
        rows,
      )}
      <small>${escapeHtml(
        payload.truncated || trades.length > 200
          ? `Showing ${Math.min(200, trades.length)} of ${payload.total.toLocaleString()} trades.`
          : `Showing all ${payload.total.toLocaleString()} trades.`,
      )}</small>
    </section>
  `;
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
