---
id: NOTE-003
related_experiment_id: EXP-004
tags:
- robustness
- surface
- optimization
- orb
- analysis
created_at: '2026-05-28T00:34:12.257487+00:00'
---

# ORB Optimization - Robustness & Surface Analysis

## Robustness Analysis (Best Params: ORB=5min, TP=3.0x, risk=3%)

- **Robustness Score: 0.887** (out of 1.0) - STRONG
- Expectancy Std: +/-0.023R across 10 noise-injected runs
- Interpretation: Results are stable under small data perturbations.
  A score >0.80 indicates high confidence parameters are not overfit.

## Parameter Surface Analysis (ORB duration vs TP multiplier)

- Grid: orb_duration_minutes x tp_multiplier
- **Cliffs detected: 0** - No abrupt performance dropoffs
- **Plateaus detected: 6** - Multiple parameter regions with similar performance
- Interpretation: The strategy is robust across a range of parameter values.
  Plateaus are favorable - they mean you're not on a knife's edge.

## Combined Verdict

Both analyses confirm the grid sweep winner:
- ORB=5min, TP=3.0x is stable (robustness 0.887) and sits on a performance plateau
- No parameter cliffs means the strategy won't catastrophically break if params drift slightly
- The 5min ORB window is clearly superior; the plateau suggests even ORB=4-6min would work

## Next Steps

1. **Regime cross-analysis**: Test if best params (ORB=5min, TP=3.0x) hold in trending regimes
   - Regime filter H1 (atr_pctile < 0.80) showed SINGLE_YEAR_STABILITY - not production ready
   - Regime filter H2 (no ranging_high_vol) barely moves the needle
2. **Consider out-of-sample test**: Use 2025 data as true OOS validation
3. **Live paper trade**: ORB=5min, TP=2.0x (safer), risk=1% as conservative starting config

