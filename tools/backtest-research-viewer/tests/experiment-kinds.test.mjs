import test from "node:test";
import assert from "node:assert/strict";
import {
  isKnownKind, outcomeBadge, cumulativeR, histogramModel, renderHistogram,
  conditionsHtml, kindDetail, validationBadge, scatterModel, trialHtml,
} from "../public/experiment-kinds.mjs";
import { experimentDetail } from "../public/experiment-view.mjs";

test("badge choice per outcome", () => {
  assert.deepEqual(outcomeBadge("supported"), { label: "Supported", tone: "good" });
  assert.equal(outcomeBadge("contradicted").tone, "danger");
  assert.equal(outcomeBadge("underpowered").tone, "warning");
  assert.equal(outcomeBadge(undefined).label, "Not assessed");
});

test("cumulative R and drawdown on a hand-built series", () => {
  const rows = cumulativeR([
    { date: "d1", r: 1 }, { date: "d2", r: -2 }, { date: "d3", r: 0.5 }, { date: "d4", r: 2 },
  ]);
  assert.deepEqual(rows.map((x) => x.cumulative_r), [1, -1, -0.5, 1.5]);
  assert.deepEqual(rows.map((x) => x.drawdown_r), [0, -2, -1.5, 0]);
  assert.deepEqual(cumulativeR([{ date: "d", r: -1 }])[0].drawdown_r, 0 - 1);
});

test("histogram model labels bins and flags negative bars", () => {
  const model = histogramModel({ edges: [-1, 0, 1], counts: [1, 4, 2, 1] });
  assert.equal(model.bars.length, 4);
  assert.equal(model.bars[0].label, "< -1");
  assert.equal(model.bars[3].label, "≥ 1");
  assert.equal(model.bars[1].negative, true);
  assert.equal(model.bars[2].negative, false);
  assert.equal(model.bars[1].height > model.bars[0].height, true);
  assert.equal(histogramModel({ edges: [0], counts: [0, 0] }), null);
  assert.match(renderHistogram(null), /unavailable/);
});

test("scatter model returns points for rows with excursions", () => {
  const model = scatterModel([{ mae_r: -0.5, mfe_r: 1, net_r: 0.4, exit_reason: "tp" }]);
  assert.ok(model);
});

test("conditions table is labelled diagnostic and lists terciles", () => {
  const html = conditionsHtml({
    gap: {
      terciles: { low: { n: 10, mean_r: 0.1 }, mid: { n: 11, mean_r: 0 }, high: { n: 12, mean_r: -0.2 } },
      interaction: { high_minus_low_r: -0.3, ci_low_r: -0.6, ci_high_r: 0 },
      monotonic: "decreasing",
      consistency: { share_years: 0.6, share_symbols: 1 },
    },
  });
  assert.match(html, /Diagnostic/);
  assert.match(html, /<h4>gap<\/h4>/);
  assert.match(html, /decreasing/);
  assert.equal(conditionsHtml({}), "");
});

test("validation badge rules", () => {
  assert.equal(validationBadge({ kind: "null_calibration", false_positive_wilson: [0.02, 0.09], expected_false_positive_rate: 0.05 }).tone, "good");
  assert.equal(validationBadge({ kind: "null_calibration", false_positive_wilson: [0.1, 0.2], expected_false_positive_rate: 0.05 }).tone, "danger");
  assert.equal(validationBadge({ kind: "planted_edge", detection_rate: 0.5, predicted_power: 0.46 }).tone, "good");
  assert.equal(validationBadge({ kind: "planted_edge", detection_rate: 0.9, predicted_power: 0.46 }).tone, "danger");
  assert.equal(validationBadge(null).label, "Not assessed");
});

test("known kinds render, unknown kinds fall back", () => {
  assert.equal(isKnownKind("fixed_rule"), true);
  assert.equal(isKnownKind("walk_forward"), false);
  assert.equal(kindDetail({ definition: { kind: "walk_forward" }, title: "x" }), null);
  const html = kindDetail({
    title: "Study", state: "completed", definition: { kind: "fixed_rule", trial: { programme: "p", phase: "exploration", family: "f", n_trials: 1 } },
    result: {
      evidence_role: "exploration", outcome: "underpowered",
      pooled: { n: 50, mean_r: 0.02, ci_low_r: -0.1, ci_high_r: 0.14 }, min_edge_r: 0.05, mde_r: 0.2,
      r_histogram: { edges: [-1, 0, 1], counts: [1, 2, 3, 1] }, by_year: {}, by_symbol: {}, by_exit_reason: {},
    },
  });
  assert.match(html, /Underpowered/);
  assert.match(html, /cannot confirm/);
  assert.match(html, /data-kind-chart="r-series"/);
  assert.match(experimentDetail({ title: "T", state: "completed", definition: { kind: "fixed_rule" }, result: { pooled: { n: 1 } } }), /FIXED-RULE STUDY/);
});

test("trial html reports programme totals", () => {
  assert.match(trialHtml({ programme: "p", phase: "x", family: "f", n_trials: 2 }, { total_trials: 7 }), /programme total 7/);
  assert.equal(trialHtml(null, null), "");
});
