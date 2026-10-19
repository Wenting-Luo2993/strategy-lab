import assert from "node:assert/strict";
import test from "node:test";
import { experimentDetail, experimentList } from "../public/experiment-view.mjs";

function fixture() {
  const candidate = (value) => ({ candidate_id: `c${value}`, parameters: { orb_minutes: value } });
  const evaluation = (value) => ({
    ...candidate(value), expectancy_r: value / 100, n_trades: 20,
    sharpe_ratio: 1.2, state: "completed", findings: [],
  });
  return {
    experimentId: "study-1", title: "ORB duration comparison", state: "completed",
    definition: {
      symbol: "QQQ",
      axes: [{ name: "orb_minutes", path: "strategy.orb_duration_minutes", values: [5, 15, 30] }],
      candidates: [candidate(30), candidate(5), candidate(15)],
      training_policy: "All candidates; no training shortlist.",
      selection_policy: "Validation plateau medoid.",
      objective: "Net expectancy R",
      plan: { folds: [{
        segments: ["train", "validation", "test"].map((role) => ({
          role, start_session: "2023-01-02", end_session: "2023-02-01", session_count: 20,
        })),
        purge_sessions_applied: 2, embargo_sessions_applied: 2, warmup_sessions: 20,
      }] },
    },
    result: {
      n_folds_completed: 1, recording_errors: [],
      stitched_oos: { expectancy_r: 0.15, n_trades: 20, fold_net_pnl: [300] },
      folds: [{
        fold_index: 0, state: "completed",
        train: [evaluation(5), evaluation(15), evaluation(30)],
        validation: [evaluation(5), evaluation(15), evaluation(30)],
        selection: { ...candidate(15), neighborhood_candidate_ids: ["c5", "c15"],
          winner_score: 0.15, runner_up_score: 0.3, selection_margin: -0.15 },
        test: evaluation(15),
      }],
    },
    runs: { items: [{
      foldIndex: 0, segmentRole: "test", candidateId: "c15",
      runId: "run-15", displayAlias: "RUN-000015", runState: "validating",
    }], total: 1, truncated: false },
  };
}

test("shows the controlled axis, roles, selection and frozen test together", () => {
  const html = experimentDetail(fixture());
  for (const text of ["5 / 15 / 30", "1 · Train", "2 · Validate &amp; select",
    "3 · Frozen test", "Selected on validation", "Parameters frozen before test",
    "run-15", "RUN-000015", "Stored run: validating", "no training shortlist",
    "A plateau representative need not be the highest individual score"]) {
    assert.ok(html.includes(text), text);
  }
  assert.equal((html.match(/Not selected — not tested/g) || []).length, 2);
  assert.ok(html.indexOf('scope="row">orb_minutes = 5') < html.indexOf('scope="row">orb_minutes = 15'));
  assert.ok(html.indexOf('scope="row">orb_minutes = 15') < html.indexOf('scope="row">orb_minutes = 30'));
});

test("does not invent outcomes, selections or aggregates for interrupted studies", () => {
  const value = fixture();
  value.result = null;
  value.state = "running";
  const html = experimentDetail(value);
  assert.match(html, /may still be running or may have been interrupted/);
  assert.match(html, /No selection recorded/);
  assert.match(html, /Unavailable — no completed all-fold OOS aggregate/);
  assert.doesNotMatch(html, /Selected on validation/);
});

test("discloses recording limits and unavailable train detail under test-only retention", () => {
  const value = fixture();
  value.definition.record_test_only = true;
  value.runs.truncated = true;
  value.runs.total = 6000;
  value.result.recording_errors = ["disk full"];
  const html = experimentDetail(value);
  assert.match(html, /training\/validation trade and equity drill-downs were not requested/);
  assert.match(html, /Run links capped: 1 \/ 6000/);
  assert.match(html, /Recording coverage is incomplete: disk full/);
  assert.match(html, /No linked run detail/);
});

test("failed and missing outcomes remain explicit", () => {
  const value = fixture();
  value.result.stitched_oos = null;
  value.result.folds[0].selection = null;
  value.result.folds[0].test = null;
  value.result.folds[0].validation = [];
  value.result.folds[0].validation_failures = [{ candidate_id: "c5", message: "engine failed" }];
  value.result.folds[0].reason = "No selectable candidate";
  const html = experimentDetail(value);
  assert.match(html, /engine failed/);
  assert.match(html, /No selectable candidate/);
  assert.doesNotMatch(html, /Selected on validation/);
});

test("escapes all experiment data and refuses unreadable payloads", () => {
  const value = fixture();
  value.title = '<img src=x onerror="alert(1)">';
  value.result.folds[0].train[0].findings = [{code: "<script>", message: "<img>", severity: "blocking"}];
  const html = experimentDetail(value);
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("<script>"));
  assert.match(html, /&lt;img/);
  value.payloadUnavailable = true;
  assert.match(experimentDetail(value), /No result is inferred/);
});

test("experiment list preserves the question, axis and fold completion", () => {
  const html = experimentList([{
    experimentId: "study-1", title: "ORB duration", symbol: "QQQ",
    axes: fixture().definition.axes, expectedFolds: 4, completedFolds: 3,
    linkedRuns: 27, state: "inconclusive",
  }]);
  assert.match(html, /data-experiment="study-1"/);
  assert.match(html, /3 \/ 4 completed/);
  assert.match(html, /5 \/ 15 \/ 30/);
  assert.match(html, /inconclusive/);
});

test("reliability criteria use Fluent UI info icons with explanatory callouts", () => {
  const value = fixture();
  value.reliability = {
    verdict: "rejected",
    policyHash: "policy-1",
    criteria: [{
      criterion_id: "REL-OOS-TRADES",
      outcome: "inconclusive",
      observed: 80,
      expected: ">= 100 held-out trades",
    }, {
      criterion_id: '<img src=x onerror="alert(1)">',
      outcome: "review_required",
      observed: null,
      expected: "<script>future rule</script>",
    }],
  };

  const html = experimentDetail(value);

  assert.match(html, /popovertarget="criterion-help-REL-OOS-TRADES"/);
  assert.match(html, /aria-label="Explain REL-OOS-TRADES"/);
  assert.match(html, /M8\.5 7\.5a\.5\.5/);
  assert.match(html, /Held-out trade sufficiency/);
  assert.match(html, /estimate performance with useful precision/);
  assert.match(html, /Current rule: &gt;= 100 held-out trades/);
  assert.match(html, /A pre-registered deterministic check/);
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /<script/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;script&gt;future rule&lt;\/script&gt;/);
});

test("shows a compact statistical confidence table and stored verdict reasoning", () => {
  const value = fixture();
  value.result.stitched_oos.fold_net_pnl = [3931.43];
  value.reliability = {
    verdict: "inconclusive",
    policyHash: "policy-2",
    report: {
      verdict_reason: "Inconclusive because the expectancy confidence interval crosses 0R.",
      statistical_confidence: {
        sample_size: 80,
        mean_r: -0.0245,
        sample_std_r: 1.477,
        standard_error_r: 0.165,
        t_statistic: -0.15,
        degrees_of_freedom: 79,
        confidence_level: 0.95,
        evidence_confidence_above_threshold: 0.441,
        t_interval_lower_r: -0.353,
        t_interval_upper_r: 0.304,
        bootstrap_interval_lower_r: -0.331,
        bootstrap_interval_upper_r: 0.312,
        bootstrap_replicates: 200,
      },
    },
    criteria: [],
  };

  const html = experimentDetail(value);

  assert.match(html, /Statistical confidence profile/);
  assert.match(html, /t-statistic versus threshold/);
  assert.match(html, /-0\.150/);
  assert.match(html, /Evidence confidence above threshold/);
  assert.match(html, /44\.1%/);
  assert.match(html, /Confidence level/);
  assert.match(html, /95\.0%/);
  assert.match(html, /Verdict reasoning/);
  assert.match(html, /Diagnostic cash total/);
  assert.match(html, /diagnostic only, not the R-based promotion metric/);
  assert.doesNotMatch(html, /<dl class="detail-grid">/);
});

test("experiment names are explicitly left aligned", () => {
  const html = experimentList([{
    experimentId: "study-1",
    title: "ORB duration comparison - full decision evidence",
    state: "completed",
    createdAt: "2026-10-18",
    symbol: "QQQ",
    axes: [],
    expectedFolds: 2,
    completedFolds: 2,
    linkedRuns: 14,
    reliabilityVerdict: "rejected",
  }]);

  assert.match(html, /<td class="experiment-name">/);
});
