import { mountTimeCharts, mountDiagnosticChart } from "./library-charts.mjs";

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const finite = Number.isFinite;
const r = (value, digits = 3) => (finite(value) ? `${value.toFixed(digits)}R` : "n/a");
const pct = (value) => (finite(value) ? `${(value * 100).toFixed(2)}%` : "n/a");
const interval = (low, high) => (finite(low) && finite(high) ? `[${r(low)}, ${r(high)}]` : "n/a");
const table = (headings, rows) => `<div class="table-wrap"><table><thead><tr>${headings
  .map((h) => `<th scope="col">${escape(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

export const KNOWN_KINDS = ["fixed_rule", "paired_comparison", "platform_validation", "condition_diagnostic"];

/** The researcher's editable one-line conclusion, if one has been recorded. */
export function conclusionHtml(conclusion) {
  const text = typeof conclusion === "string" ? conclusion : conclusion?.text;
  if (!text) return "";
  const updated = typeof conclusion === "object" && conclusion?.updatedAt
    ? ` <small>Updated ${escape(String(conclusion.updatedAt).slice(0, 10))}</small>` : "";
  return `<p class="experiment-conclusion"><strong>Conclusion:</strong> ${escape(text)}${updated}</p>`;
}

export function isKnownKind(kind) {
  return KNOWN_KINDS.includes(kind);
}

const OUTCOME_BADGES = {
  supported: ["Supported", "good"],
  contradicted: ["Contradicted", "danger"],
  underpowered: ["Underpowered", "warning"],
};

export function outcomeBadge(outcome) {
  const [label, tone] = OUTCOME_BADGES[outcome] || ["Not assessed", "warning"];
  return { label, tone };
}

const badgeHtml = ({ label, tone }) => `<span class="badge ${tone}">${escape(label)}</span>`;

/** Running cumulative R and drawdown (in R) from a peak that starts at zero. */
export function cumulativeR(daily) {
  let cumulative = 0;
  let peak = 0;
  return daily.map(({ date, r: value }) => {
    cumulative += value;
    peak = Math.max(peak, cumulative);
    return { date, r: value, cumulative_r: cumulative, drawdown_r: cumulative - peak };
  });
}

export function histogramModel(histogram, { width = 600, height = 180 } = {}) {
  const counts = histogram?.counts || [];
  const edges = histogram?.edges || [];
  if (!counts.length || counts.every((count) => count === 0)) return null;
  const labels = counts.map((_, i) => (i === 0 ? `< ${edges[0]}`
    : i === counts.length - 1 ? `≥ ${edges.at(-1)}`
      : `${edges[i - 1]} to ${edges[i]}`));
  const max = Math.max(...counts);
  const barWidth = width / counts.length;
  return {
    width, height,
    bars: counts.map((count, i) => ({
      label: labels[i], count,
      x: i * barWidth, width: Math.max(1, barWidth - 1),
      height: (count / max) * (height - 20),
      negative: i > 0 && i < counts.length - 1 && edges[i] <= 0,
    })),
  };
}

export function renderHistogram(model) {
  if (!model) return '<p class="empty">R histogram unavailable.</p>';
  return `<svg viewBox="0 0 ${model.width} ${model.height}" role="img" aria-label="Net R histogram">${model.bars.map((bar) =>
    `<rect class="${bar.negative ? "chart-loss" : "chart-gain"}" x="${bar.x.toFixed(1)}" y="${(model.height - 20 - bar.height).toFixed(1)}" width="${bar.width.toFixed(1)}" height="${bar.height.toFixed(1)}"><title>${escape(bar.label)}: ${bar.count}</title></rect>`).join("")}</svg>`;
}

/** Scale one or more dated series onto a shared SVG viewport. */
export function lineChartModel(seriesList, { width = 700, height = 220 } = {}) {
  const dates = [...new Set(seriesList.flatMap((s) => s.points.map((p) => p.date)))].sort();
  if (!dates.length) return null;
  const values = seriesList.flatMap((s) => s.points.map((p) => p.value)).filter(finite);
  if (!values.length) return null;
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const span = high - low || 1;
  const x = (date) => (dates.length === 1 ? width / 2 : (dates.indexOf(date) / (dates.length - 1)) * width);
  const y = (value) => 10 + ((high - value) / span) * (height - 20);
  return {
    width, height, low, high, zeroY: y(0), first: dates[0], last: dates.at(-1),
    series: seriesList.map((s) => ({
      name: s.name,
      path: s.points.map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join(" "),
    })),
  };
}

export function renderLineChart(model, label) {
  if (!model) return `<p class="empty">${escape(label)} unavailable.</p>`;
  return `<svg viewBox="0 0 ${model.width} ${model.height}" role="img" aria-label="${escape(label)}">
    <line class="chart-grid" x1="0" x2="${model.width}" y1="${model.zeroY.toFixed(1)}" y2="${model.zeroY.toFixed(1)}" />
    ${model.series.map((s, i) => `<path class="chart-line series-${i}" d="${s.path}" fill="none"><title>${escape(s.name)}</title></path>`).join("")}
    <text class="chart-label" x="2" y="${model.height - 2}">${escape(model.first)}</text>
    <text class="chart-label" x="${model.width - 2}" y="${model.height - 2}" text-anchor="end">${escape(model.last)}</text>
    <text class="chart-label" x="2" y="12">${escape(r(model.high, 1))}</text>
  </svg>${model.series.length > 1 ? `<p class="chart-note">${model.series.map((s, i) => `<span class="legend series-${i}">${escape(s.name)}</span>`).join(" · ")}</p>` : ""}`;
}

export function scatterModel(rows, { width = 500, height = 300, clip = 6 } = {}) {
  const points = (rows || []).filter((row) => finite(row.mae_r) && finite(row.mfe_r));
  if (!points.length) return null;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const minX = -Math.max(1, Math.min(clip, Math.max(...points.map((p) => -p.mae_r))));
  const maxY = Math.max(1, Math.min(clip, Math.max(...points.map((p) => p.mfe_r))));
  return {
    width, height, minX, maxY,
    points: points.map((p) => ({
      x: ((clamp(p.mae_r, minX, 0) - minX) / -minX) * (width - 20) + 10,
      y: height - 10 - (clamp(p.mfe_r, 0, maxY) / maxY) * (height - 20),
      exitReason: p.exit_reason || "UNKNOWN",
    })),
  };
}

export function renderScatter(model) {
  if (!model) return '<p class="empty">MAE/MFE excursions were not recorded for these trades.</p>';
  const reasons = [...new Set(model.points.map((p) => p.exitReason))].sort();
  return `<svg viewBox="0 0 ${model.width} ${model.height}" role="img" aria-label="MAE versus MFE by exit reason">${model.points.map((p) =>
    `<circle class="scatter-${reasons.indexOf(p.exitReason) % 4}" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="2"><title>${escape(p.exitReason)}</title></circle>`).join("")}</svg>
    <p class="chart-note">x: MAE (R, 0 at right) · y: MFE (R) · ${reasons.map((reason) => `<span class="legend scatter-${reasons.indexOf(reason) % 4}">${escape(reason)}</span>`).join(" · ")}</p>`;
}

export function roleBanner(role) {
  if (role === "exploration" || role === "platform_validation") {
    return '<p class="notice"><strong>Exploration: cannot confirm.</strong> This evidence role can reject or refine an idea but cannot confirm an edge.</p>';
  }
  if (role === "confirmation") return '<p class="notice"><strong>Confirmation run.</strong></p>';
  return "";
}

function barRows(group, label) {
  const rows = Object.entries(group || {}).map(([key, e]) => `<tr><th scope="row">${escape(key)}</th><td>${escape(e.n ?? e.n_sessions)}</td>
    <td>${r(e.mean_r ?? e.mean_diff_r)}</td><td>${interval(e.ci_low_r, e.ci_high_r)}</td></tr>`);
  return rows.length ? table([label, "n", "Mean", "Interval"], rows) : "";
}

export function conditionsHtml(conditions) {
  const entries = Object.entries(conditions || {});
  if (!entries.length) return "";
  return `<h3>Conditions <span class="badge warning">Diagnostic</span></h3>${entries.map(([variable, block]) => {
    const rows = ["low", "mid", "high"].map((t) => {
      const e = block.terciles?.[t];
      return e ? `<tr><th scope="row">${t}</th><td>${escape(e.n)}</td><td>${r(e.mean_r)}</td><td>${interval(e.ci_low_r, e.ci_high_r)}</td></tr>` : "";
    });
    const ix = block.interaction;
    const share = block.consistency || {};
    const sideRows = Object.entries(block.by_side || {}).map(([side, byTercile]) =>
      `<tr><th scope="row">${escape(side)}</th>${["low", "mid", "high"].map((t) => {
        const e = byTercile?.[t];
        return `<td>${e ? `${r(e.mean_r)} (n=${escape(e.n)})` : "n/a"}</td>`;
      }).join("")}</tr>`);
    const sideTable = sideRows.length ? table(["Side", "Low", "Mid", "High"], sideRows) : "";
    return `<h4>${escape(variable)}</h4>${oosHtml(block.oos)}${table(["Tercile", "n", "Mean", "Interval"], rows)}
      <p class="chart-note">In-sample high minus low: ${ix ? `${r(ix.high_minus_low_r)} ${interval(ix.ci_low_r, ix.ci_high_r)} (approximate)` : "n/a"}
      · monotonic: ${escape(block.monotonic ?? "n/a")} · same-sign years: ${pct(share.share_years)} · same-sign symbols: ${pct(share.share_symbols)}</p>${sideTable}`;
  }).join("")}`;
}

const OOS_BADGES = {
  replicated: ["Replicated out of sample", "good"],
  same_sign_not_significant: ["Same sign, not significant", "warning"],
  reversed: ["Reversed out of sample", "danger"],
  insufficient: ["Insufficient data", "warning"],
};

/** Time-split tercile test: cutoffs from the first half, applied to the second. */
export function oosHtml(oos) {
  if (!oos) return "";
  const [label, tone] = OOS_BADGES[oos.verdict] || ["Not assessed", "warning"];
  const row = (name, b, period) => `<tr><th scope="row">${name}</th><td>${escape((period || []).join(" → "))}</td>
    <td>${escape(b?.n_trades ?? "n/a")}</td><td>${r(b?.high_minus_low_r)}</td><td>${interval(b?.ci_low_r, b?.ci_high_r)}</td></tr>`;
  return `<p>Out-of-sample test ${badgeHtml({ label, tone })} <small>split ${escape(oos.split_date)}</small></p>
    ${table(["Half", "Dates", "Trades", "High − low", "Interval"], [row("Train", oos.train, oos.train_period), row("Test", oos.test, oos.test_period)])}`;
}

const usd = (value, digits = 2) => (finite(value) ? `${value < 0 ? "−" : ""}$${Math.abs(value).toFixed(digits)}` : "n/a");
const usdInterval = (low, high) => (finite(low) && finite(high) ? `[${usd(low)}, ${usd(high)}]` : "n/a");

/** Power to detect each true edge, nominal and deflated by Step 0 calibration. */
export function powerHtml(power, unit = "R") {
  if (!power?.rows?.length) return "";
  const rows = power.rows.map((p) => `<tr><th scope="row">${r(p.edge_r)}</th><td>${pct(p.power_nonzero)}</td>
    <td>${pct(p.power_nonzero_deflated)}</td><td>${pct(p.power_supported)}</td><td>${pct(p.power_supported_deflated)}</td></tr>`);
  const excluded = finite(power.excluded_edge_r)
    ? `<p class="chart-note">One-sided: true edges ≥ ${r(power.excluded_edge_r)} are excluded (deflated: ≥ ${r(power.excluded_edge_deflated_r)}).</p>` : "";
  return `<h3>Power</h3>${table([`True edge (${unit})`, "Detect ≠ 0", "Deflated", "Classified supported", "Deflated"], rows)}
    <p class="chart-note">SE ${r(power.se_r, 4)} · deflation ${escape(power.deflation)} (Step 0 calibration).</p>${excluded}`;
}

export function robustnessHtml(rob) {
  if (!rob) return "";
  const pairs = [
    ["Median", r(rob.median_r)], ["Trimmed mean 1%", r(rob.trimmed_mean_1pct_r)],
    ["Trimmed mean 5%", r(rob.trimmed_mean_5pct_r)], ["Skew", finite(rob.skew) ? rob.skew.toFixed(2) : "n/a"],
    ["Win rate", pct(rob.win_rate)],
    ...Object.entries(rob.mean_without_top_days_r || {}).map(([k, v]) => [`Mean without top ${k} days`, r(v)]),
    ...Object.entries(rob.mean_without_top_trades_r || {}).map(([k, v]) => [`Mean without top ${k} trades`, r(v)]),
  ];
  return `<h3>Tail robustness</h3>${table(["Measure", "Value"], pairs.map(([a, b]) => `<tr><th scope="row">${escape(a)}</th><td>${escape(b)}</td></tr>`))}`;
}

export function dollarsHtml(dollars, label = "Dollars") {
  if (!dollars) return "";
  const t = dollars.per_trade || {};
  const s = dollars.per_symbol_session || {};
  const sizing = dollars.sizing || {};
  return `<h3>${escape(label)}</h3><p>${usd(t.mean_usd)}/trade ${usdInterval(t.ci_low_usd, t.ci_high_usd)}
    · ${usd(s.mean_usd)}/symbol-session ${usdInterval(s.ci_low_usd, s.ci_high_usd)}
    · ${finite(dollars.mean_bps_of_notional) ? `${dollars.mean_bps_of_notional.toFixed(2)} bps` : "n/a"} of notional
    · return on equity ${pct(dollars.return_on_equity_per_year)}/yr
    · median risk ${usd(dollars.median_initial_risk_usd, 0)}
    · capped by buying power ${pct(sizing.share_capped_by_buying_power)}</p>`;
}

export function trialHtml(trial, trialsPayload) {
  if (!trial) return "";
  const total = trialsPayload?.total_trials;
  return `<p class="notice"><strong>Programme:</strong> ${escape(trial.programme)} · phase ${escape(trial.phase)} · family ${escape(trial.family)}
    · this study counts ${escape(trial.n_trials ?? 1)} trial(s)${finite(total) ? ` · this database records ${escape(total)} trials (not the full programme ledger)` : ""}</p>`;
}

export function validationBadge(report) {
  if (!report) return { label: "Not assessed", tone: "warning" };
  if (report.kind === "null_calibration") {
    const [low, high] = report.false_positive_wilson || [];
    const ok = finite(low) && finite(high) && low <= report.expected_false_positive_rate
      && report.expected_false_positive_rate <= high;
    return ok ? { label: "Pass", tone: "good" } : { label: "Review", tone: "danger" };
  }
  if (report.kind === "planted_edge") {
    const gap = Math.abs((report.detection_rate ?? NaN) - (report.predicted_power ?? NaN));
    return finite(gap) && gap <= 0.15 ? { label: "Pass", tone: "good" } : { label: "Review", tone: "danger" };
  }
  return { label: "Not assessed", tone: "warning" };
}

function validationHtml(experiment) {
  const report = experiment.result?.summary;
  const rows = report?.kind === "null_calibration" ? [
    ["False-positive rate", pct(report.false_positive_rate), `expected ${pct(report.expected_false_positive_rate)}; Wilson ${(report.false_positive_wilson || []).map(pct).join(" to ")}`],
    ["Interval coverage of 0", pct(report.coverage_rate), `expected ${pct(report.confidence_level)}`],
    ["Mean of study means", r(report.mean_of_means_r), "expected 0R"],
    ["Trades per study", String(Math.round(report.mean_trades_per_study ?? 0)), `${report.n_studies} studies × ${report.n_sessions} sessions`],
  ] : report?.kind === "planted_edge" ? [
    ["Truth", r(report.truth_r), `${report.truth_sessions} sessions, ${report.truth_trades} trades`],
    ["Detection rate", pct(report.detection_rate), `predicted power ${pct(report.predicted_power)}`],
    ["Coverage of truth", pct(report.coverage_of_truth), `expected ${pct(report.confidence_level)}`],
  ] : [];
  return `<h3>Platform validation ${badgeHtml(validationBadge(report))}</h3>${rows.length
    ? table(["Measure", "Observed", "Expected / note"], rows.map(([a, b, c]) => `<tr><th scope="row">${escape(a)}</th><td>${escape(b)}</td><td>${escape(c)}</td></tr>`))
    : '<p class="empty">No validation report recorded.</p>'}`;
}

function fixedRuleHtml(experiment) {
  const s = experiment.result;
  if (!s?.pooled) return '<p class="notice">No summary has been recorded for this study.</p>';
  const p = s.pooled;
  const cost = s.cost || {};
  const exitRows = Object.entries(s.by_exit_reason || {}).map(([k, e]) =>
    `<tr><th scope="row">${escape(k)}</th><td>${escape(e.n)}</td><td>${pct(e.share)}</td><td>${r(e.mean_r)}</td></tr>`);
  return `${roleBanner(s.evidence_role)}
    <h3>Outcome ${badgeHtml(outcomeBadge(s.outcome))}</h3>
    <p>Mean net R ${r(p.mean_r)} over ${escape(p.n)} trades, interval ${interval(p.ci_low_r, p.ci_high_r)}.
      Minimum edge ${r(s.min_edge_r, 2)} · minimum detectable effect ${r(s.mde_r)}.</p>
    ${performanceHtml(experiment)}
    <h3>Cumulative R and drawdown</h3><div data-kind-chart="r-series"><p class="empty">Loading…</p></div>
    <h3>Net R distribution</h3><div data-kind-chart="histogram">${renderHistogram(histogramModel(s.r_histogram))}</div>
    <h3>By year</h3>${barRows(s.by_year, "Year")}
    <h3>By symbol</h3>${barRows(s.by_symbol, "Symbol")}
    <h3>Exit reasons</h3>${table(["Reason", "n", "Share", "Mean"], exitRows)}
    <h3>Excursions</h3><div data-kind-chart="excursions"><p class="empty">Loading…</p></div>
    <h3>Cost sensitivity</h3><p>Mean net R ${r(cost.mean_net_r)} · break-even extra cost ${finite(cost.breakeven_extra_cost_bps) ? `${cost.breakeven_extra_cost_bps.toFixed(1)} bps` : "n/a"}.</p>
    ${powerHtml(s.power)}${robustnessHtml(s.robustness)}${dollarsHtml(s.dollars)}
    ${conditionsHtml(s.conditions)}`;
}

export function performanceHtml(experiment) {
  const summary = experiment.result || {};
  const number = (v) => finite(v) ? v.toFixed(2) : "n/a";
  const rows = (experiment.runs?.items || []).map((run) => {
    const symbol = run.symbols || run.strategyId;
    const estimate = summary.by_symbol?.[symbol] || {};
    const m = run.metrics || {};
    return `<tr><th scope="row">${escape(symbol)}</th><td>${escape(run.tradeCount ?? "n/a")}</td>
      <td>${r(estimate.mean_r ?? run.meanNetR)}</td><td>${interval(estimate.ci_low_r, estimate.ci_high_r)}</td>
      <td>${number(m.sharpe_ratio)}</td><td>${pct(m.max_drawdown)}</td>
      <td>${finite(m.max_drawdown_duration_days) ? m.max_drawdown_duration_days : "n/a"}</td>
      <td>${number(run.profitFactor)}</td><td>${usd(m.total_pnl)}</td>
      <td>${run.equityAvailable ? `<button class="link" data-run="${escape(run.runId)}">Open equity curve</button>` : "Not retained"}</td></tr>`;
  });
  if (!rows.length) return "";
  return `<h3>Performance by symbol</h3>
    <p class="chart-note">Independent per-symbol accounts, not a shared-capital portfolio.
      Sharpe and drawdown are stored equity metrics; profit factor is total winning net dollars / absolute losing net dollars.
      Missing values are unavailable, not zero. Drawdown duration is calendar days.</p>
    ${table(["Symbol", "Trades", "Net expectancy", "95% CI", "Sharpe", "Max drawdown", "DD days", "Profit factor", "Net P&L", "Equity curve"], rows)}
    <p class="notice">Intraday equity curves require retained mark-to-market points. The separate reconstructed
      daily closed-trade equity below uses complete net P&amp;L at exit sessions; it does not recover intraday risk.</p>`;
}

function pairedHtml(experiment) {
  const s = experiment.result;
  if (!s?.difference) return '<p class="notice">No summary has been recorded for this comparison.</p>';
  const d = s.difference;
  return `${roleBanner(s.evidence_role)}
    <h3>Difference ${badgeHtml(outcomeBadge(s.outcome))}</h3>
    <p>Mean per-session difference ${r(d.mean_r)} over ${escape(d.n)} sessions, interval ${interval(d.ci_low_r, d.ci_high_r)}.
      Minimum edge per session ${r(s.min_edge_per_session_r)} · MDE ${r(s.mde_per_session_r)}.</p>
    ${s.difference_usd ? `<p>Dollar difference ${usd(s.difference_usd.mean_diff_usd)}/symbol-session ${usdInterval(s.difference_usd.ci_low_usd, s.difference_usd.ci_high_usd)}
      · capped by buying power: baseline ${pct(s.baseline?.dollars?.sizing?.share_capped_by_buying_power)}, variant ${pct(s.variant?.dollars?.sizing?.share_capped_by_buying_power)}.</p>` : ""}
    ${powerHtml(s.power_per_session, "R/session")}
    ${dollarsHtml(s.baseline?.dollars, "Baseline dollars")}${dollarsHtml(s.variant?.dollars, "Variant dollars")}
    <h3>Cumulative R, baseline vs variant</h3><div data-kind-chart="r-series"><p class="empty">Loading…</p></div>
    <h3>Difference by year</h3>${barRows(s.by_year, "Year")}
    <h3>Difference by symbol</h3>${barRows(s.by_symbol, "Symbol")}`;
}

function conditionDiagnosticHtml(experiment) {
  const summary = experiment.result;
  const protocol = experiment.definition?.protocol;
  if (!summary?.slices || !protocol) return '<p class="notice">Diagnostic setup or results have not been recorded.</p>';
  const definitions = [
    ["Opportunity", protocol.features?.opportunity?.definition, "High minus low opportunity mean net R"],
    ["Persistence", protocol.features?.persistence?.definition, "High minus low persistence mean net R"],
    ["Interaction", "H/L for both measurements; first letter opportunity, second persistence", "HH - HL - LH + LL mean net R"],
  ];
  const setup = table(["Comparison", "Measurement", "Exact contrast"], definitions.map(([name, measurement, contrast]) =>
    `<tr><th scope="row">${escape(name)}</th><td>${escape(measurement)}</td><td>${escape(contrast)}</td></tr>`));
  const cutoffs = table(["Symbol", "Calibration trades", "Opportunity median", "Persistence median"], Object.entries(summary.thresholds || {}).map(([symbol, values]) =>
    `<tr><th scope="row">${escape(symbol)}</th><td>${escape(values.n)}</td><td>${finite(values.opportunity) ? escape(values.opportunity) : "n/a"}</td>
      <td>${finite(values.persistence) ? escape(values.persistence) : "n/a"}</td></tr>`));
  const contrasts = table(["Slice", "Comparison", "Measurable trades", "Group counts", "Difference", "Nominal 95% CI", "Three-comparison interval", "Group sufficiency"], Object.entries(summary.slices).flatMap(([slice, block]) =>
    Object.entries(block.contrasts || {}).map(([name, contrast]) =>
      `<tr><th scope="row">${escape(slice)}</th><td>${escape(name)}</td><td>${escape(block.measurable_n)}</td>
        <td>${escape(Object.entries(contrast.group_sizes || {}).map(([group, count]) => `${group}: ${count}`).join(" / "))}</td>
        <td>${r(contrast.estimate_r, 4)}</td><td>${interval(contrast.ci_low_r, contrast.ci_high_r)}</td>
        <td>${interval(contrast.familywise_interval?.ci_low_r, contrast.familywise_interval?.ci_high_r)}</td>
        <td>${contrast.adequate_group_sizes ? "Adequate" : "Insufficient"}</td></tr>`)));
  const decisions = table(["Candidate", "Advance to separate gate design?", "Recorded checks"], Object.entries(summary.triage || {}).map(([name, decision]) =>
    `<tr><th scope="row">${escape(name)}</th><td>${decision.advance_to_separate_gate_design ? "Yes" : "No"}</td>
      <td>${escape(Object.entries(decision.checks || {}).map(([check, passed]) => `${check}: ${passed ? "pass" : "not met"}`).join(" · "))}</td></tr>`));
  const cells = summary.slices.all?.cells || {};
  return `${roleBanner(summary.evidence_role)}
    <p class="notice"><strong>One diagnostic experiment, three comparisons, no new simulation runs.</strong>
      These are conditional associations on the unchanged source trades, not an executable filtered strategy or three new equity curves.</p>
    <h3>Source experiment</h3><p><button class="link" data-open-experiment="${escape(summary.source_experiment_id)}">${escape(summary.source_experiment_id)}</button>
      · ${escape(summary.coverage?.n_trades)} retained trades · ${escape(summary.coverage?.measurable)} jointly measurable
      · ${escape(summary.coverage?.missing_persistence)} missing persistence.</p>
    <h3>Exact setup: all three comparisons</h3>${setup}
    <p>${escape((protocol.symbols || []).join(", "))} · ${escape(protocol.start)} to ${escape(protocol.end)}
      · calibration through ${escape(protocol.calibration_end)} · later dates are reused-data robustness, not independent confirmation.</p>
    <p>${escape(protocol.thresholds?.method)}. ${escape(protocol.thresholds?.freeze)}.</p>
    <p>Opportunity availability: ${escape(protocol.features?.opportunity?.availability)}.</p>
    <p>${escape(protocol.features?.persistence?.availability)}. ${escape(protocol.features?.persistence?.missing_policy)}.</p>
    <p>${escape(protocol.features?.persistence?.early_entries)}</p>
    <p>Friction standardizer: ${usd(protocol.features?.opportunity?.friction_dollars, 3)}/share.
      ${escape(protocol.features?.opportunity?.friction_definition)}. ${escape(protocol.features?.opportunity?.limitations)}</p>
    <p class="notice"><strong>ATR seed-description erratum:</strong> the original protocol says first-observation EWM seed.
      The pinned shared helper actually seeds with the mean of the first 20 true ranges, then Wilder recursion, shifted one session.
      The original registration is preserved; calculations were not changed after results.</p>
    <h3>Frozen per-symbol cutoffs</h3>${cutoffs}
    <h3>All-period condition cells</h3>${table(["Cell", "Trades", "Mean net R", "95% CI", "Attributed net dollars"], Object.entries(cells).map(([name, cell]) =>
      `<tr><th scope="row">${escape(name)}</th><td>${escape(cell.n)}</td><td>${r(cell.mean_r, 4)}</td>
        <td>${interval(cell.ci_low_r, cell.ci_high_r)}</td><td>${usd(cell.net_dollars)}</td></tr>`))}
    <h3>Registered contrasts and robustness</h3>${contrasts}
    <p class="chart-note">Date-clustered intervals retain shared-date covariance. Three-comparison intervals do not correct the programme's full trial history.
      Missing groups are unavailable, not zero; attributed dollars are not new filtered-account returns.</p>
    <h3>Frozen advance rule and decisions</h3><p>${escape(protocol.analysis?.advance_rule)}</p>
    <p>Minimum per group: ${escape(protocol.analysis?.minimum_group_trades)} trades and ${escape(protocol.analysis?.minimum_group_dates)} dates.</p>${decisions}
    <p>${escape(protocol.analysis?.stopping_rule)}</p>
    <details class="detail-section"><summary>Original registered protocol</summary><pre>${escape(JSON.stringify(protocol, null, 2))}</pre></details>`;
}

/** Compact, collapsed list of the runs this experiment explicitly recorded. */
export function linkedRunsHtml(runsPayload) {
  const runs = runsPayload?.items || [];
  if (!runs.length) return "";
  const dates = (run) => [run.firstEntry, run.lastEntry].map((v) => (v ? String(v).slice(0, 10) : "?")).join(" → ");
  const rows = runs.map((run) => `<tr><td><button class="link" data-run="${escape(run.runId)}">${escape(run.displayAlias)}</button></td>
    <td>${escape(run.symbols || run.strategyId || "—")}</td><td>${escape(run.segmentRole)}</td>
    <td>${escape(run.tradeCount ?? "—")}</td><td>${r(run.meanNetR)}</td>
    <td>${run.firstEntry ? escape(dates(run)) : "—"}</td><td>${escape(run.runState)}</td></tr>`);
  const total = runsPayload.total ?? runs.length;
  return `<details class="detail-section linked-runs"><summary>Linked runs (${escape(total)})</summary>
    <p class="chart-note">Stored runs this experiment recorded. Open one for its trades, equity and provenance.</p>
    ${table(["Run", "Symbol", "Role", "Trades", "Mean net R", "Trade dates", "Stored state"], rows)}
    ${runsPayload.truncated ? `<p class="notice">Showing ${escape(runs.length)} of ${escape(total)} links.</p>` : ""}</details>`;
}

/** Returns HTML for a known experiment kind, or null so the caller falls back. */
export function kindDetail(experiment) {
  const kind = experiment?.definition?.kind;
  if (!isKnownKind(kind)) return null;
  const body = kind === "fixed_rule" ? fixedRuleHtml(experiment)
    : kind === "paired_comparison" ? pairedHtml(experiment)
      : kind === "condition_diagnostic" ? conditionDiagnosticHtml(experiment) : validationHtml(experiment);
  const labels = { fixed_rule: "FIXED-RULE STUDY", paired_comparison: "PAIRED COMPARISON", platform_validation: "PLATFORM VALIDATION", condition_diagnostic: "CONDITION DIAGNOSTICS" };
  return `<header class="experiment-heading"><p class="eyebrow">${labels[kind]}</p>
    <h2 tabindex="-1">${escape(experiment.title)}</h2>
    <div><small>Procedure execution</small> <span class="badge ${experiment.state === "completed" ? "good" : "warning"}">${escape(experiment.state)}</span></div></header>
    ${conclusionHtml(experiment.conclusion)}
    <div data-kind-trial></div>${body}${linkedRunsHtml(experiment.runs)}`;
}

/** Fill the async placeholders of a rendered kind view. */
export function renderKindExtras(host, experiment, { rSeries, excursions, trials, dailySeries } = {}) {
  const disposers = [];
  const trial = host.querySelector("[data-kind-trial]");
  if (trial) trial.innerHTML = trialHtml(experiment.definition?.trial, trials);
  const rHost = host.querySelector('[data-kind-chart="r-series"]');
  if (rHost) {
    if (dailySeries?.runs?.length) {
      disposers.push(mountTimeCharts(rHost, dailySeries.runs.map((run) => ({
        name: run.label, key: run.runId,
        group: JSON.stringify([run.candidateId, experiment.definition?.kind === "fixed_rule" ? null : run.foldIndex, run.segmentRole]),
        points: run.rAvailable ? run.dailyR : [], prices: run.dailyPrices,
        closed: run.dailyClosedEquity, capital: run.startingEquity,
      })), { comparison: true }));
    } else if (dailySeries?.error) rHost.innerHTML = `<p class="notice">Daily comparison unavailable: ${escape(dailySeries.error)}</p>`;
    else if (!rSeries?.available) rHost.innerHTML = '<p class="empty">Trade-level R series are not available for this database.</p>';
    else if (rSeries.inputTruncated) rHost.innerHTML = '<p class="notice">Trade input exceeds the viewer bound. Selected-range calculations are unavailable rather than computed on partial history.</p>';
    else {
      const series = (rSeries.candidates || []).map((c) => ({
        name: c.candidateId, points: (c.daily || []).map((p) => ({ date: p.date, value: p.r })),
      }));
      disposers.push(mountTimeCharts(rHost, series));
    }
  }
  const eHost = host.querySelector('[data-kind-chart="excursions"]');
  if (eHost) {
    eHost.innerHTML = excursions?.available
      ? `<div data-excursion-plot>${renderScatter(scatterModel(excursions.rows))}</div>${excursions.sampled ? `<p class="notice">Sampled ${escape(excursions.rows.length)} of ${escape(excursions.total)} trades.</p>` : ""}`
      : '<p class="empty">Excursions are not available for this database.</p>';
    if (excursions?.available && excursions.rows?.length) {
      const reasons = [...new Set(excursions.rows.map((p) => p.exit_reason || "UNKNOWN"))];
      disposers.push(mountDiagnosticChart(eHost.querySelector("[data-excursion-plot]"), {
        tooltip: { trigger: "item", formatter: (p) => `${escape(p.seriesName)} · MAE ${numberR(p.value[0])} · MFE ${numberR(p.value[1])}` },
        xAxis: { type: "value", name: "MAE (R)", axisLabel: { formatter: (v) => v.toFixed(2) } },
        yAxis: { type: "value", name: "MFE (R)", axisLabel: { formatter: (v) => v.toFixed(2) } },
        series: reasons.map((reason) => ({ name: reason, type: "scatter",
          data: excursions.rows.filter((p) => (p.exit_reason || "UNKNOWN") === reason).map((p) => [p.mae_r, p.mfe_r]) })),
      }));
    }
  }
  const hHost = host.querySelector('[data-kind-chart="histogram"]');
  const histogram = histogramModel(experiment.result?.r_histogram);
  if (hHost && histogram) disposers.push(mountDiagnosticChart(hHost, {
    xAxis: { type: "category", data: histogram.bars.map((b) => b.label) },
    yAxis: { type: "value", name: "Trades" },
    dataZoom: [{ type: "inside" }, { type: "slider" }],
    series: [{ type: "bar", data: histogram.bars.map((b) => b.count) }],
  }));
  return () => disposers.forEach((dispose) => dispose());
}

const numberR = (value) => finite(value) ? `${value.toFixed(3)}R` : "n/a";
