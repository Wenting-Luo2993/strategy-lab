---
name: report-experiment-results
description: "Report and explain backtest/research experiment results in chat. Use whenever presenting a completed, partial or inconclusive study, diagnostic comparison, attribution analysis or experiment summary to the user. Explain new indicators with worked examples first, distinguish labels on existing trades from new simulations, then give results, conclusions and concrete next steps connected to the research goal."
---

# Report experiment results

Make the research understandable in the chat itself, not just in linked
artifacts or a dashboard. Assume the reader is learning quantitative research:
define notation, explain the comparison and separate observations from claims.
Use the five sections below, in order. Prefer short paragraphs and compact
tables; do not omit the teaching explanation just to make the report brief.

This skill governs communication. It does not authorize new simulations,
threshold tuning, reserved-data access, publication, deployment or trading.
For preserving research findings and task state, also follow
`document-project-work`; do not duplicate or rewrite historical evidence.

## Prepare from evidence

Read the relevant recorded definition/protocol, results and canonical research
summary. Check the actual implementation when a formula or decision-time
availability is unclear; do not guess from an indicator's name. Report missing
or unavailable evidence explicitly rather than rerunning a study to fill a
report. Verify counts, comparison direction and units against the saved result.
Preserve and disclose deviations or errata instead of silently correcting a
frozen protocol.

Use the current project goal from `docs/taskboard/projects.json` and its
canonical plan. For ORB, read the living findings in
`docs/backtest-research-summaries/orb-exploration-results.md` and the
`2026-10-31-backtest-research-north-star.md` in that directory.

## 1. Indicators: what they mean and how they work

For every newly introduced or changed indicator:

- Give its plain-language name, what it measures and why it relates to the
  hypothesis. Explain what high/low values mean and what they do not imply.
- Show the actual formula and define every symbol, index, input and unit.
  For example, `C[t]` is a candle's closing price, not an unexplained letter.
- Specify timeframe, lookback, session/timezone, warm-up/seed where relevant,
  earliest decision-time availability and material missing/flat-data behavior.
  Distinguish causal inputs from hindsight-only diagnostic measurements.
- Work through small numeric inputs, intermediate arithmetic and the result.
  Label made-up numbers **Illustrative example, not measured results**. If
  using a real observation instead, identify its recorded source.
- Explain the threshold, its calibration period and high/low boundary if used.
  A measurement is not itself a trading rule; make that distinction explicit.

Example teaching style (synthetic): seven consecutive 5-minute closes
`100, 101, 102, 103, 104, 105, 106` give six price changes. Close-path efficiency
is `abs(106 - 100) / (1 + 1 + 1 + 1 + 1 + 1) = 1`.
The alternating path `100, 101, 100, 101, 100, 101, 100` travels the same total
distance but scores `0 / 6 = 0`. A steady downward path also scores 1:
this measures consistency, not bullishness or guaranteed future continuation.
Those are example inputs, not evidence that an indicator predicts returns.

If there are no new or changed indicators, say so briefly; do not invent one.

## 2. Experiment: what we actually did

State the hypothesis/question and the predicted observation in ordinary
language. Then explicitly identify the work:

- **Existing-trade attribution/diagnostics:** reused a named source ledger and
  labelled or grouped its already simulated trades. State that no new trades
  were simulated. Count statistical comparisons separately from simulations.
- **New backtest:** reran the execution engine with specified rules, producing
  new simulation runs. Name the baseline, what changed and what stayed fixed
  in entries, exits, costs, sizing and capital.
- **Mixed study:** identify which outputs came from each method.

Give experiment/source IDs, number of experiments, linked simulation runs and
comparison trials without conflating them. Include symbols, dates, timeframe,
evidence role, split/calibration policy, thresholds and execution/cost
assumptions relevant to the question. Reused later-period data is not untouched
out-of-sample confirmation. Label planned, incomplete and completed work.

Explain each comparison's operands and direction: e.g. high minus low mean
net R, or `HH - HL - LH + LL` with both letters defined. When introducing an
unfamiliar comparison, include a small labelled illustrative calculation as
well as the indicator example. For interaction, explain the difference between
the effect in one condition and the effect in the other, not just the formula.
Do not silently substitute equal-weighted cell means for trade-weighted means.

State whether rejected signals could lead to different later trades or sizing.
Deleting rows from a retained ledger does not simulate an executable filter.

## 3. Results: what the data showed

Give a compact table per comparison with its estimate, units, uncertainty and
sample/coverage. Explain what the interval means in plain language; do not
describe it as the probability that the true effect is inside that interval.
Define R using the actual risk denominator if reporting R-based outcomes.

Include the baseline/reference, total versus usable trades and independent
clusters/dates where relevant. Identify missing or early-unavailable inputs and
how their cohort differs. Highlight the few robustness findings that materially
change the decision: time/ticker stability, costs, tail dependence, thin groups
or sensitivity to assumptions. State the multiple-testing scope and distinguish
local database counts from the full programme trial ledger.

Use available measures appropriate to the experiment, not a mandatory metric
dump. Conditional mean R is not portfolio return; cumulative R is not equity.
Separate retained, reconstructed and unmeasured risk/return evidence. Do not
invent filtered equity, drawdown or complementarity from labelled trade groups.

## 4. Conclusion: what we learned and what remains unknown

Separate measured observations, plausible mechanisms and unsupported claims.
Say whether the evidence is supportive, contradictory or inconclusive relative
to the predeclared meaningful effect and decision rule; do not reduce it to
"successful/failed" or call a positive point estimate a proven edge.

Explain the most important lesson and limitation. An interval crossing zero
does not prove no effect, and a significant diagnostic association does not
prove an executable, generalizable strategy. Apply the frozen stop/advance
rule. Inconclusive or negative research can be complete and useful.

## 5. Next step: concrete action and connection to the end goal

Recommend the smallest specific next action justified by the evidence, or
explicitly recommend stopping/parking the idea. State what uncertainty it
addresses, the deliverable/decision it would produce and how that would change
the research direction. Avoid vague "research more" or an automatic new sweep.
Distinguish a recommendation from an already approved or executed action.
Respect failed advancement rules; a changed window, normalization or threshold
requires a distinct protocol rather than disguised post-result tuning.

Connect that action to the relevant high-level goal. The ORB objective is:

> Positive net returns with less drawdown, limited overnight exposure, and
> returns that complement buy-and-hold.

Say which parts this study actually measured, which it only informs indirectly
and which remain untested. Do not imply that every-year buy-and-hold
outperformance is required, or that equal starting capital means equal risk.
Close with the concrete next decision, not an unrelated engineering backlog.

## Before sending

Check that the chat contains the indicator examples, an unambiguous account of
what ran, actual results and uncertainty, the lesson and a goal-linked next
step. Links supplement this explanation; they do not replace it. Keep examples
visibly separate from real results and do not claim a dashboard/publication
update unless it was verified. For a narrow follow-up explanation, answer only
the requested part using these same clarity and evidence rules.
