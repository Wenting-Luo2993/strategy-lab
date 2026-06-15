# Immediate Actions - EXP-073/EXP-074 (2026-06-12)

## Objective
Close the key evidence gaps identified by advisor review while preserving current realistic-fill baseline discipline.

## Action 1 - Validate Execution Assumptions Against QQQ Microstructure
Status: In progress (partially satisfied).

Scope:
- Validate realistic execution assumptions against observable QQQ characteristics:
  - liquidity (bar volume / ADV behavior)
  - spread behavior (time-of-day)
  - opening-volume dynamics

Current evidence:
- Diagnostics artifact exists:
  - reports/optimization/orb_reality_check_exp073/exp073_execution_diagnostics_full_2018_2024.md
  - reports/optimization/orb_reality_check_exp073/exp073_execution_diagnostics_full_2018_2024.json
- Current model includes volume participation and impact/slippage models, but no explicit spread model calibration.

Required work:
- Add explicit spread assumption/calibration path for QQQ (at least opening window vs regular session).
- Re-run EXP-073 with calibrated spread assumption and compare deltas vs current realistic profile.

Deliverable:
- Short validation memo with assumptions, calibration method, and sensitivity table.

Done criteria:
- Quantified spread assumption documented.
- Sensitivity impact on expectancy/drawdown reported.
- Recommendation: keep/adjust current realistic parameters.

## Action 2 - Implement True Feature-Mask Replay + Out-of-Sample Evaluation
Status: Not started.

Scope:
- Replace proxy A/B bottom-tail-removal implementation with true feature-mask replay for candidate filters:
  - A: dist_sma20_bps in (-3.128, 8.698]
  - B: rel_vol_so_far in (0.742, 0.895]
  - C: gap_pct in (0.589, 5.501]
- Evaluate in out-of-sample split(s), not only full-period aggregate.

Required work:
- Join causal feature snapshots to trades at decision time.
- Exclude trades via actual bucket masks (not PnL-ranked tail truncation).
- Run OOS evaluation and report stability by split.

Deliverable:
- EXP-074 true-mask replay report (JSON + MD) with IS/OOS breakdown.

Done criteria:
- No outcome-conditioned selection logic remains in A/B path.
- OOS results reported for all candidates.
- Promotion recommendation includes retention + expectancy + robustness checks.

## Action 3 - Decompose Execution Impact by Time and Regime
Status: Not started.

Scope:
- Break down execution degradation (legacy -> realistic) by:
  - year
  - volatility regime
  - market regime

Required work:
- Extend diagnostics pipeline to compute per-segment deltas:
  - win rate
  - expectancy (R)
  - estimated execution cost components
  - quantity reduction / fill-pressure proxy

Deliverable:
- Segmented execution impact report with ranked worst segments and confidence notes.

Done criteria:
- Structural vs segment-specific degradation conclusion stated.
- Clear recommendation whether to pursue regime-conditioned execution or strategy gating.

## Execution Order (Recommended)
1. Action 1 (assumption validation/calibration)
2. Action 3 (decomposition for structural diagnosis)
3. Action 2 (true-mask replay with OOS once execution baseline is trusted)

## Risks To Track
- Overfitting execution parameters to historical outcomes.
- Inferring spread from proxies without external validation.
- Confusing quantity reduction effects with strategy alpha changes.

## References
- reports/optimization/orb_reality_check_exp073/midpoint_handoff_2026-06-12.md
- reports/optimization/orb_reality_check_exp073/exp073_vs_exp072_report_full_2018_2024.md
- reports/optimization/orb_reality_check_exp073/exp073_execution_diagnostics_full_2018_2024.md
- reports/optimization/orb_reality_check_exp073/exp074_causal_feature_bucket_scan_full_2018_2024.md
- reports/optimization/orb_reality_check_exp073/exp074_ab_results_full_2018_2024.md
