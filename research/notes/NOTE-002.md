---
id: NOTE-002
related_experiment_id: EXP-004
tags:
- optimization
- orb
- parameter_sweep
- summary
created_at: '2026-05-27T23:59:37.942813+00:00'
---

# ORB Parameter Optimization - Grid Sweep Results (2018-2024)

## Key Finding

**ORB 5-minute window consistently dominates.** All top-9 results use ORB=5min.
The gap between ORB=5min and ORB=10min/15min is substantial (~0.69 vs ~0.54-0.59 composite score).

## Top 3 Configurations

| Rank | ORB | TP | Risk | Composite | Expectancy | Sharpe | Win Rate |
|------|-----|----|------|-----------|------------|--------|----------|
| 1    | 5min | 3.0x | 3% | 0.6946 | +0.180R | 2.19 | 45.8% |
| 2    | 5min | 3.0x | 2% | 0.6904 | +0.180R | 2.19 | 45.8% |
| 3    | 5min | 3.0x | 1% | 0.6837 | +0.180R | 2.19 | 45.8% |

Note: Ranks 1-3 are same parameters with different position sizing; composite score is risk-adjusted
so 3% risk scores highest due to compounding effect on total PnL component.

## TP Multiplier Trade-off

- TP=3.0x: Lower win rate (45.8%) but superior tail ratio (2.28) -> highest composite
- TP=2.0x: Higher win rate (56.4%), good balance, +0.156R expectancy
- TP=1.5x: Best win rate (63.6%) but lowest tail ratio (1.22), weakest composite

## Bottom Line

**Recommended production parameters: ORB=5min, TP=2.0x-3.0x, risk 1-2%**
(3% risk is too large for live trading; 1-2% is more prudent for drawdown control)

## Caveats

- Results from backtest cache (2018-2024), all in-sample
- Robustness analysis pending (currently running)
- Regime filter analysis shows both filters have SINGLE_YEAR_STABILITY warnings
  - Neither regime filter is ready for production per promotion checklist

