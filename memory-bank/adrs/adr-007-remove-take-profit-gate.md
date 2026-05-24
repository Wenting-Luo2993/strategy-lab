# ADR-007: Remove Take-Profit Gate (EOD-Only Exits)

**Date**: 2026-05-16

**Status**: ✅ Accepted

## Context

ORB strategy had -0.012R expectancy with 2R take-profit gate. Hypothesis: TP caps tail winners, destroying edge.

## Decision

Remove take-profit gate entirely. Use EOD exits only (no TP, no trailing stop).

## Alternatives Considered

- **Wider TP** (e.g., 3R or 4R) - Still caps returns, defeats purpose
- **Trailing stop** (e.g., 1.5 ATR) - Cuts winners early, conflicts with convex payoff
- **Time-based exits** (e.g., close at 3:00 PM) - Not analyzed, may leave profits on table

## Reasoning

- Distribution analysis shows extreme tail dependence (top 10% = 60% of winner profits)
- EOD exits contribute +1,327R (702% of total profits) with 90.9% win rate
- TP cuts many trades at 2R that would have run 3R, 4R, 5R+
- ORB has convex payoff - few big winners carry the strategy

## Consequences

- ✅ Expectancy improved from -0.012R to +0.11R (+1017% improvement)
- ✅ All regimes now positive (9/9 vs 3/9 with TP)
- ✅ Works in 6 of 7 years (only fails 2020 COVID)
- ⚠️ Lower win rate (29.2% vs 49.3%) - psychologically harder to trade
- ⚠️ Higher drawdowns due to letting losers run to stops

## Related Analysis

- `reports/regime-filter/orb-production-2018-2024-20260516/no_tp/analysis_tp_impact.md`
- User memory: "regime-research-framework.md"
