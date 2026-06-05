# ORB Research Roadmap (Living Document)

## Purpose
Track ORB research end-to-end in one place:
- what we did
- what we found
- what we plan to test next
- what was promoted, paused, or rejected

Use this as the working roadmap and session handoff for strategy research.

## Current State (as of 2026-06-04)
- Baseline direction remains no take-profit (TP=0) to preserve convex right-tail behavior.
- Trailing-stop focused research was completed for HYP-004 variant set (EXP-069 to EXP-072).
- Best trailing candidate in tested set is EXP-072 (trigger_r=3.0, plus_ticks=1).
- Paper/live promotion remains conditional due to drawdown and pending 2026 H1 re-validation cadence.

## What We Did

### Phase A: Convexity and TP Validation
- Corrected parameter sweep to include TP=0 option.
- Re-ran optimization and confirmed no-TP baseline outperforms fixed TP variants on expectancy and tail capture.
- Recorded conclusion in NOTE-004: ORB edge depends on right-tail winners; fixed TP truncates edge.

### Phase B: Trailing Stop Extension (HYP-004)
- Built reusable experiment infra and journal workflow (avoided one-off scripts).
- Fixed R-metric integrity bug in portfolio close logic by anchoring initial risk to immutable initial stop.
- Re-ran trailing variants cleanly:
  - EXP-070: trigger_r=2.0, plus_ticks=3
  - EXP-071: trigger_r=2.5, plus_ticks=1
  - EXP-072: trigger_r=3.0, plus_ticks=1
- Added deterministic top-tail preservation analysis tooling and cache-first reuse.

### Phase C: Benchmark Sanity Check
- Compared EXP-072 backtest return versus QQQ buy-and-hold over same date window.
- Result: EXP-072 outperformed buy-and-hold in this backtest window.
- Caveat: data is raw (ex-dividends), so buy-and-hold is somewhat understated versus dividend-adjusted published performance.

## What We Found

### Key Findings
1. Tail preservation is the core success criterion for ORB.
2. Tighter/earlier trailing behavior can reduce losers but often clips right-tail winners.
3. EXP-072 preserved top-tail best among tested trailing variants while maintaining strongest expectancy/PnL.
4. Drawdown remains the main trade-off for EXP-072 and must be controlled with guardrails.

### Parameter Behavior (Observed)
- Larger plus_ticks at lower trigger (for example 2.0/+3) over-tightened exits and clipped tail.
- Smaller plus_ticks with higher trigger (for example 3.0/+1) preserved trend participation better.
- Moving trigger from 2.5 to 3.0 improved tail capture and expectancy in tested sample, with drawdown cost.

## Suggested Next Evaluations (Priority Order)

### 1) Reality Check: Promoted Config Under Realistic Fill (Immediate Next Step)
Goal:
- Run the current promoted config with realistic execution enabled and quantify degradation versus legacy execution.

Deliverables:
- Legacy vs realistic A/B report on the same date window and symbol.
- Metrics comparison for expectancy, max drawdown, tail preservation, total PnL, and fill quality (fill rate, partial-fill rate, unfilled/cancelled behavior if any).
- Decision note: keep promoted config as-is, add guardrails, or iterate parameters for realistic mode.

Promotion check for this phase:
- Strategy remains acceptable after realistic fills (no unexpected edge collapse and risk profile remains within tolerance).

### 2) Cohort Commonality Mapping (Top 20/10/5 and Losers)
Goal:
- Identify repeatable entry-time signatures shared by top winners and by losers.

Deliverables:
- Cohort table keyed by trade opportunity (date, symbol, entry timestamp).
- Labels: loser, top20, top10, top5.
- Cross-variant overlap matrix (EXP-069/070/071/072) for top cohorts and loser cohorts.

Promotion check for this phase:
- We can clearly describe which entry conditions are common in losers versus top-tail winners.

### 3) Loser-Avoidance First Pass (Before Winner-Only Selection)
Goal:
- Remove structurally bad entries without damaging convex tail.

Targets:
- Reduce loser count by at least 20%.
- Preserve at least 85-90% of top-20 winner cohort from baseline.

Approach:
- Use simple threshold gates first, no black-box model.

### 4) Entry Feature Separation Study
Goal:
- Quantify which entry-time features separate losers from non-losers and top-tail from rest.

Initial feature set:
- ORB range percentile
- gap size and direction
- opening-drive momentum proxies
- first-window relative volume
- selected regime tags already in pipeline

Outputs:
- Distribution plots and summary stats by cohort.
- Rank-ordered feature effect sizes.
- Candidate gate rules with rationale.

### 5) Rule Gate Backtest Loop
Goal:
- Convert candidate features into 2-3 explicit entry gates and re-test.

Evaluate:
- expectancy
- max drawdown
- tail preservation metrics
- loser reduction
- stability by year and by regime

### 6) Lightweight Ranking Model (Optional After Rule Gates)
Goal:
- Improve trade selection ranking quality while retaining explainability.

Constraints:
- walk-forward validation only
- focus on ranking metrics (precision at k, recall for top-tail)
- keep hard-rule fallback when confidence is low

## Open Risks
- Overfitting risk is high if optimization is driven only by recent periods.
- Winner-only targeting can unintentionally remove convex tail if signals are unstable.
- Drawdown improvements may come at hidden cost to long-run expectancy.

## Execution Plan (Near-Term)

### Sprint 0
- Run current promoted config in realistic mode and produce legacy-vs-realistic comparison report.
- Decide whether promoted config is still promotable under realistic execution assumptions.

### Sprint 1
- Build cohort commonality dataset and overlap matrix.
- Produce loser vs top-tail commonality report.

### Sprint 2
- Implement and test first loser-avoidance gates.
- Measure tail-preservation impact and drawdown change.

### Sprint 3
- Refine gates or test lightweight ranking model (if rule-only results plateau).
- Run walk-forward robustness and regime-sliced validation.

## Decision Rules for Promotion
A candidate entry filter/gating approach is promotable only if all pass:
1. Expectancy non-decreasing versus current baseline/trailing candidate.
2. Top-tail preservation remains high (no major convexity erosion).
3. Drawdown is improved or acceptable under explicit risk guardrails.
4. Results are stable across multiple time slices, not single-period luck.

## Operating Notes
- Prefer reusable hypothesis infrastructure and experiment lineage over one-off scripts.
- Default to quiet logging for long research runs unless debugging internals.
- Keep this file concise; move deep implementation details to experiment notes and docs.

## Recent Updates

### Update 2026-06-04 (Sprint 0 started)
- Objective: Start reality check for promoted config (EXP-072) under realistic fill.
- Experiments run:
  - Legacy vs realistic A/B on EXP-072 config for 2024-01-01 to 2024-12-31 (QQQ).
- Main findings:
  - Legacy: 251 trades, expectancy +0.3334R, total PnL +$117,642, max drawdown -16.19%.
  - Realistic: 251 trades, expectancy -0.4920R, total PnL -$98,778, max drawdown -98.78%.
  - Delta (realistic - legacy): expectancy -0.8254R, PnL -$216,420, max drawdown -82.59%.
- Decision:
  - Keep Sprint 0 in progress; this degradation is too large to accept without deeper investigation.
- Next actions:
  - Run full-window A/B (2018-2024) in batched fashion with artifact checkpoints.
  - Diagnose realism calibration/implementation sensitivity (slippage_k, participation_rate, impact_k, ADV handling).
  - Add acceptance guardrails for realistic mode promotion.
- Links: notes, experiment YAMLs, reports
  - `research/experiments/EXP-072.yaml`
  - `reports/optimization/orb_reality_check_exp072/legacy_vs_realistic_exp072_summary_2024.json`
  - `reports/optimization/orb_reality_check_exp072/legacy_vs_realistic_exp072_report_2024.md`

### Update 2026-06-04 (Sprint 0 decomposition matrix)
- Objective: Isolate which realistic-fill components drive the 2024 degradation.
- Experiments run:
  - Fast decomposition matrix on EXP-072 (2024) across scenarios:
    - legacy
    - realistic_default
    - no_impact (`impact_k=0`)
    - no_slippage (`slippage_k=0`)
    - no_volume_cap (`participation_rate=1.0`)
- Main findings:
  - `realistic_default` remained severely degraded vs legacy: expectancy `-0.8254R` delta, PnL `-$216,420` delta, drawdown `-82.59%` worse.
  - `no_impact` and `no_slippage` both improved only modestly (still strongly negative), suggesting either component alone is sufficient to keep outcomes poor under current calibration.
  - `no_volume_cap` matched `realistic_default` in this slice, indicating participation cap was not the primary driver of the collapse here.
  - Trade-level diagnostics: degradation is entry-drift dominated and concentrated near the open (9:35-9:50 ET), with many legacy winners flipping to losses.
- Decision:
  - Keep Sprint 0 open; focus next on calibration and denominator diagnostics before any promotion decision.
- Next actions:
  - Run parameter sensitivity on `slippage_k` and `impact_k` (low-to-high grid) with realistic mode.
  - Validate ADV/volume scaling assumptions around open bars and confirm intended magnitude range.
  - Add realistic-mode acceptance thresholds for maximum tolerable degradation.
- Links: notes, experiment YAMLs, reports
  - `reports/optimization/orb_reality_check_exp072/decomposition_matrix_2024.json`
  - `reports/optimization/orb_reality_check_exp072/decomposition_matrix_2024.csv`

### Update 2026-06-05 (Sprint 0 focused calibration)
- Objective: Find the most plausible realistic-fill calibration for promoted EXP-072 and evaluate practical baseline behavior.
- Experiments run:
  - Focused 2024 grid on realistic execution parameters (`slippage_k`, `impact_k`, `participation_rate`) for EXP-072.
  - Candidate set: symmetric k values (`0.0025, 0.005, 0.0075, 0.01, 0.015`) across participation rates (`0.1, 0.5, 1.0`) plus asymmetric low-impact variants.
- Main findings:
  - Participation cap had negligible effect in this setup (results unchanged across tested participation values for each k pair).
  - Best practical in-band candidate (realistic drift without reverting to near-legacy assumptions): `slippage_k=0.005`, `impact_k=0.0025`.
  - Selected candidate metrics (2024, QQQ):
    - trades: 251
    - expectancy: `-0.0895R`
    - total PnL: `-$19,316`
    - max drawdown: `-32.51%`
    - mean entry drift: `$0.4787` (`47.9` ticks, `10.3` bps)
  - Comparison anchors:
    - legacy: `+0.3334R`, `+$117,642`, `-16.19%` MDD
    - realistic default (post ADV-key fix): `-0.4560R`, `-$97,054`, `-97.05%` MDD
- Decision:
  - Use calibrated realistic profile as baseline realism for ongoing ORB research (`slippage_k=0.005`, `impact_k=0.0025`), and keep default realistic profile as stress scenario.
  - Promotion from legacy-only evidence remains blocked until strategy improvements restore positive expectancy under calibrated realism.
  - Why this choice (concise): `k=0.0025` was too close to legacy behavior (lower drift but weaker realism), while `k>=0.0075` was overly punitive; `0.005/0.0025` was the best middle-ground that kept non-trivial drift and materially reduced default-model over-penalization.
- Next actions:
  - Run 2018-2024 evaluation under selected calibrated realism.
  - Run open-window targeted diagnostics (9:35-9:55 ET) to reduce adverse entry drift.
  - Test small entry-quality guards and compare to baseline with strict tail-preservation checks.
- Links: notes, experiment YAMLs, reports
  - `reports/optimization/orb_reality_check_exp072/realistic_fill_calibration_grid_2024_focused.json`

### Update 2026-06-05 (EXP-073 realistic validation)
- Objective: Re-run EXP-073 on the full 2018-2024 window (purging the prior 2024-only run) and compare with EXP-072 baseline.
- Experiments run:
  - EXP-073: EXP-072 parameter set + calibrated realistic execution (`slippage_k=0.005`, `impact_k=0.0025`, `participation_rate=0.1`).
  - Comparison baseline: EXP-072 experiment record over full window (`research/experiments/EXP-072.yaml`).
- Main findings (EXP-073 minus EXP-072 baseline, 2018-2024):
  - trades: `1677` vs `1677` (no count change)
  - expectancy: `-0.0552R` vs `+0.2929R` (delta `-0.3481R`)
  - total PnL: `-$45,088.57` vs `+$9,376,698.55` (delta `-$9,421,787.12`)
  - max drawdown: `-76.33%` vs `-27.44%` (delta `-48.89%`)
- Decision:
  - Full-window EXP-073 confirms the same conclusion as 2024 checks: calibrated realism is less punitive than default realistic, but still leaves a large degradation versus legacy; execution realism remains a hard blocker for promotion.
- Next actions:
  - Improve entry quality around open (9:35-9:55 ET) and re-test against EXP-073 baseline.
  - Keep both reference modes in reports: calibrated realistic (primary) and default realistic (stress).
- Links: notes, experiment YAMLs, reports
  - `research/experiments/EXP-073.yaml`
  - `reports/optimization/orb_reality_check_exp073/exp073_vs_exp072_summary_full_2018_2024.json`
  - `reports/optimization/orb_reality_check_exp073/exp073_vs_exp072_report_full_2018_2024.md`

## Update Template (append each research cycle)
Use this block for each update:

### Update YYYY-MM-DD
- Objective:
- Experiments run:
- Main findings:
- Decision:
- Next actions:
- Links: notes, experiment YAMLs, reports

---

Last Updated: 2026-06-05
Owner: ORB research workflow
