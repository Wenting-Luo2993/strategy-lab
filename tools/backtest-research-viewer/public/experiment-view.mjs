const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);
const number = (value) => Number.isFinite(value) ? value.toFixed(3) : "Not recorded";
const riskMultiple = (value) => Number.isFinite(value) ? `${number(value)}R` : "Unavailable";
const parameters = (value) => Object.entries(value || {})
  .map(([key, item]) => `${key} = ${JSON.stringify(item)}`).join(" · ");
const status = (value) => {
  const normalized = value || "not_assessed";
  const tone = ["completed", "reliable", "pass", "passed"].includes(normalized)
    ? "good"
    : ["execution_failed", "validation_failed", "rejected", "fail", "failed"].includes(normalized)
      ? "danger" : "warning";
  return `<span class="badge ${tone}">${escape(normalized === "not_assessed" ? "Not assessed" : normalized)}</span>`;
};
const table = (headings, rows) => `<div class="table-wrap"><table><thead><tr>${headings.map((heading) => `<th scope="col">${escape(heading)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

export function experimentList(items) {
  return table(["Experiment", "Controlled settings", "Folds", "Procedure / reliability"], items.map((item) => `
    <tr><td><button class="link" data-experiment="${escape(item.experimentId)}">${escape(item.title)}</button>
      <small>${escape(item.symbol)} · ${escape(item.createdAt)}</small></td>
      <td>${(item.axes || []).map((axis) => `${escape(axis.name)}: ${escape(axis.values.join(" / "))}`).join("<br>") || "Definition unavailable"}</td>
      <td>${escape(item.completedFolds ?? "—")} / ${escape(item.expectedFolds ?? "—")} completed
        <small>${escape(item.linkedRuns)} linked segment runs</small></td>
      <td><small>Procedure</small>${status(item.state)}
        <small>Research reliability</small>${status(item.reliabilityVerdict)}
        ${item.recordingErrors ? "<small>Recording errors — inspect coverage</small>" : ""}</td></tr>`));
}

function runLink(runs, foldIndex, role, candidateId) {
  const run = runs.find((item) => item.foldIndex === foldIndex
    && item.segmentRole === role && item.candidateId === candidateId);
  return run
    ? `<button class="link" data-run="${escape(run.runId)}">${escape(run.displayAlias)}</button><small>Stored run: ${escape(run.runState)}</small>`
    : "<small>No linked run detail</small>";
}

function evaluationCell(evaluation, link) {
  return evaluation
    ? `<strong>${riskMultiple(evaluation.expectancy_r)}</strong><small>${escape(evaluation.n_trades)} trades · Sharpe ${number(evaluation.sharpe_ratio)}</small><small>Evaluation checks:</small>${status(evaluation.state)}${evaluation.non_finite_metrics?.length ? `<small>Invalid non-finite metrics: ${escape(evaluation.non_finite_metrics.join(", "))}</small>` : ""}<br>${link}`
    : `<small>Evaluation report not recorded</small>${link}`;
}

function findings(evaluation) {
  const items = evaluation?.findings || [];
  return items.length ? `<details class="evaluation-findings"><summary>${escape(items.length)} check finding(s)</summary>${items.map((item) => `<p><strong>${escape(item.code)} · ${escape(item.severity)}</strong> ${escape(item.message)}</p>`).join("")}</details>` : "";
}

export function experimentDetail(experiment) {
  const definition = experiment.definition;
  if (!definition || experiment.payloadUnavailable) {
    return '<p class="error">Experiment payload is missing, invalid or exceeds the viewer limit. No result is inferred.</p>';
  }
  const result = experiment.result;
  const runs = experiment.runs?.items || [];
  const planned = definition.plan?.folds || [];
  const candidates = [...(definition.candidates || [])].sort((left, right) =>
    parameters(left.parameters).localeCompare(parameters(right.parameters), undefined, { numeric: true }));
  const summaries = planned.map((_, index) => {
    const fold = result?.folds?.find((item) => item.fold_index === index);
    return `<tr><td><a href="#experiment-fold-${index}">Fold ${index + 1}</a></td>
      <td>${escape(fold?.selection ? parameters(fold.selection.parameters) : "No selection recorded")}</td>
      <td>${fold?.test ? `${riskMultiple(fold.test.expectancy_r)} · ${escape(fold.test.n_trades)} trades` : "Not evaluated / not recorded"}</td>
      <td>${status(fold?.state)}</td></tr>`;
  });
  const folds = planned.map((manifest, index) => {
    const fold = result?.folds?.find((item) => item.fold_index === index);
    const selected = fold?.selection?.candidate_id;
    const timeline = ["train", "validation", "test"].map((role) => {
      const segment = manifest.segments.find((item) => item.role === role);
      return `<div class="phase ${role}"><strong>${role === "train" ? "1 · Train" : role === "validation" ? "2 · Validate &amp; select" : "3 · Frozen test"}</strong>
        <span>${escape(segment?.start_session)} → ${escape(segment?.end_session)}</span>
        <small>${escape(segment?.session_count ?? "—")} sessions</small></div>`;
    }).join("");
    const rows = candidates.map((candidate) => {
      const train = fold?.train?.find((item) => item.candidate_id === candidate.candidate_id);
      const validation = fold?.validation?.find((item) => item.candidate_id === candidate.candidate_id);
      const isSelected = candidate.candidate_id === selected;
      const trainFailure = fold?.train_failures?.find((item) => item.candidate_id === candidate.candidate_id);
      const validationFailure = fold?.validation_failures?.find((item) => item.candidate_id === candidate.candidate_id);
      const test = isSelected ? fold?.test : null;
      const hasTestLink = runs.some((run) => run.foldIndex === index
        && run.segmentRole === "test" && run.candidateId === candidate.candidate_id);
      return `<tr class="${isSelected ? "selected-candidate" : ""}">
        <th scope="row">${escape(parameters(candidate.parameters))}</th>
        <td>${evaluationCell(train, runLink(runs, index, "train", candidate.candidate_id))}${findings(train)}${trainFailure ? `<p class="error">${escape(trainFailure.message)}</p>` : ""}</td>
        <td>${evaluationCell(validation, runLink(runs, index, "validation", candidate.candidate_id))}${findings(validation)}${validationFailure ? `<p class="error">${escape(validationFailure.message)}</p>` : ""}</td>
        <td>${isSelected ? "<strong>Selected on validation</strong><small>Parameters frozen before test</small>"
          : selected ? "Not selected" : "No selection recorded"}</td>
        <td>${isSelected
          ? evaluationCell(test, runLink(runs, index, "test", candidate.candidate_id)) + findings(test)
          : hasTestLink ? `<small>Test run exists but no matching frozen-selection report is recorded.</small>${runLink(runs, index, "test", candidate.candidate_id)}`
          : selected ? "<small>Not selected — not tested</small>" : "<small>Awaiting selection / no test recorded</small>"}</td></tr>`;
    });
    return `<details class="experiment-fold" id="experiment-fold-${index}" ${index === 0 ? "open" : ""}>
      <summary>Fold ${index + 1} · ${escape(fold?.state || "No outcome recorded")} · ${escape(fold?.selection ? parameters(fold.selection.parameters) : "No selection")}</summary>
      <div class="phase-timeline">${timeline}</div>
      <p class="chart-note">Purge: ${escape(manifest.purge_sessions_applied)} sessions · Embargo: ${escape(manifest.embargo_sessions_applied)} sessions · Warmup: ${escape(manifest.warmup_sessions)} sessions. Expectancy below is net R per trade.</p>
      ${fold?.reason ? `<p class="error">${escape(fold.reason)}</p>` : ""}
      ${table(["Candidate setting", "Training evidence", "Validation evidence", "Selection", "Held-out test"], rows)}
      ${fold?.selection ? `<p class="selection-explanation"><strong>Why this setting?</strong> ${escape(definition.selection_policy)}
        Selected validation score: ${riskMultiple(fold.selection.winner_score)}; runner-up: ${riskMultiple(fold.selection.runner_up_score)}; margin: ${riskMultiple(fold.selection.selection_margin)}.
        Plateau neighborhood: ${escape(fold.selection.neighborhood_candidate_ids.map((id) => parameters(candidates.find((candidate) => candidate.candidate_id === id)?.parameters) || id).join("; "))}.
        A plateau representative need not be the highest individual score.</p>` : ""}
    </details>`;
  }).join("");
  const stitched = result?.stitched_oos;
  const reliability = experiment.reliability;
  const reliabilityCriteria = reliability?.criteria || [];
  return `
    <header class="experiment-heading"><p class="eyebrow">CONTROLLED PARAMETER EXPERIMENT · ${escape(definition.symbol)}</p>
      <h2 tabindex="-1">${escape(experiment.title)}</h2>
      <div><small>Procedure execution</small>${status(experiment.state)}
      <small>Research reliability</small>${status(reliability?.verdict || "not_assessed")}</div>
      <p>${(definition.axes || []).map((axis) => `<strong>${escape(axis.name)}:</strong> ${escape(axis.values.join(" / "))} <small>(${escape(axis.path)})</small>`).join("<br>")}</p></header>
    ${definition.methodology_version === "synthetic_demo/v1" ? '<p class="notice"><strong>SYNTHETIC UI DEMO.</strong> Generated test data, not market backtest results or evidence of strategy performance.</p>' : ""}
    <div class="notice"><strong>How selection works:</strong> ${escape(definition.training_policy)}
      ${escape(definition.selection_policy)} The test window never chooses the parameters.
      Experiment outcomes describe driver evaluation checks; linked segment-run lifecycle states are shown separately.</div>
    <dl class="detail-grid">
      <div><dt>Objective</dt><dd>${escape(definition.objective)}</dd></div>
      <div><dt>Completed folds</dt><dd>${escape(result?.n_folds_completed ?? "Not recorded")} / ${planned.length}</dd></div>
      <div><dt>Stitched held-out expectancy</dt><dd>${stitched ? `${riskMultiple(stitched.expectancy_r)} · ${escape(stitched.n_trades)} trades` : "Unavailable — no completed all-fold OOS aggregate"}</dd></div>
      <div><dt>Test-fold net P&L sum (capital resets per fold)</dt><dd>${stitched ? escape(stitched.fold_net_pnl.reduce((sum, value) => sum + value, 0).toLocaleString(undefined, { style: "currency", currency: "USD" })) : "Unavailable"}</dd></div>
      <div><dt>Reliability policy</dt><dd>${escape(reliability?.policyHash || "Not assessed")}</dd></div>
      <div><dt>Methodology freeze</dt><dd>${experiment.frozen ? `${escape(experiment.frozen.frozenBy)} · ${escape(experiment.frozen.frozenAt)}` : "Not frozen"}</dd></div>
    </dl>
    ${reliabilityCriteria.length ? `<h3>Reliability criteria</h3>${table(
      ["Criterion", "Outcome", "Observed", "Expected"],
      reliabilityCriteria.map((item) => `<tr><td>${escape(item.criterion_id || item.criterionId)}</td>
        <td>${status(item.outcome)}</td><td>${escape(JSON.stringify(item.observed ?? null))}</td>
        <td>${escape(item.expected)}</td></tr>`),
    )}` : '<p class="notice">Research reliability: Not assessed. Historical experiments are not retroactively classified.</p>'}
    ${experiment.finalHoldout ? `<p class="notice"><strong>Final holdout:</strong> ${escape(experiment.finalHoldout.verdict)} · attributed to ${escape(experiment.finalHoldout.requestedBy)} at ${escape(experiment.finalHoldout.executedAt)}</p>` : ""}
    ${result?.reason ? `<p class="error">${escape(result.reason)}</p>` : ""}
    ${definition.record_test_only ? '<p class="notice">Test-only recording: candidate evaluation summaries are retained, but training/validation trade and equity drill-downs were not requested.</p>' : ""}
    ${result?.recording_errors?.length ? `<p class="error">Recording coverage is incomplete: ${escape(result.recording_errors.join("; "))}</p>` : ""}
    ${experiment.runs?.truncated ? `<p class="notice">Run links capped: ${escape(runs.length)} / ${escape(experiment.runs.total)}. Missing links do not mean missing evaluations.</p>` : ""}
    ${!result ? '<p class="notice">No final report has been recorded. The experiment may still be running or may have been interrupted; planned folds below are not claimed as completed.</p>' : ""}
    <h3>Walk-forward overview</h3>${table(["Fold", "Frozen setting", "Held-out test", "Fold outcome"], summaries)}
    ${folds}
    <details class="detail-section"><summary>Controlled configuration & provenance</summary>
      <p>Only the declared axes vary. The reference candidate configuration below includes one set of axis values; it is not a claim that those values were used for every candidate.</p>
      <pre>${escape(JSON.stringify({ experiment_id: experiment.experimentId, plan_hash: definition.plan_hash,
        methodology: definition.methodology_version, axes: definition.axes,
        reference_configuration: definition.reference_configuration,
        reserved_final_holdout: definition.plan?.final_oos }, null, 2))}</pre>
      <p>The reserved final holdout is not evaluated by these walk-forward folds.</p>
    </details>`;
}
