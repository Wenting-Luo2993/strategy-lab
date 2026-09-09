# Constraint: Portfolio Simulation Is Not Supported

**Status**: ⏸️ Deferred (tracked, not scheduled)
**Date recorded**: 2026-09-09
**Decision**: Cross-sectional evidence is sufficient for now. Portfolio
simulation is deferred but must not be silently assumed to work.
**Design doc**: `docs/backtest-research-summaries/2026-09-09-backtest-pipeline-implementation-plan.md`
(increments P5b and P10b)

---

## What it is

Two different multi-symbol capabilities are often confused. Only the first is
in scope.

| Capability | Meaning | Status |
|---|---|---|
| **Cross-sectional** (P5b) | Run the same strategy independently per symbol, then aggregate. Each symbol gets its own notional capital. No interaction between symbols. | ✅ In scope |
| **Portfolio simulation** (P10b) | One shared capital pool across N symbols. Symbols compete for capital, and taking one trade can prevent another. | ⏸️ Deferred |

The distinction matters because cross-sectional results **overstate** what a
real portfolio would earn. If ten symbols each fire a signal at 09:35 and
capital only supports three, the cross-sectional result books all ten.

## What it is for

- Answering "what would I actually have made trading this universe?" rather than
  "what was the average per-symbol edge?"
- Measuring realized concentration and correlated drawdown, which is the risk
  that actually ends accounts.
- Producing an equity curve that can be compared to a real brokerage statement.

Until it exists, **no result in this repo may be described as a portfolio
return.** Cross-sectional output is per-symbol evidence with dispersion.

## What it needs to be implemented

1. **Merged multi-symbol event stream.** Bars from N symbols interleaved on one
   chronological timeline, with per-symbol session alignment, halts, and missing
   bars handled explicitly.
2. **Buying-power enforcement.** A hard cash/margin bound on position opening.
3. **Capital contention rule.** When signals exceed available capital, a
   deterministic, declared ranking decides which are taken.
4. **Concentration caps.** Limits on simultaneous exposure, plus reporting of
   realized concentration and max observed leverage.
5. **Per-symbol and pooled metrics kept separate.** Pooled numbers must always
   carry dispersion.

## Blockers

| # | Blocker | Detail |
|---|---|---|
| B1 | **Engine is single-symbol by construction** | `BacktestEngine.run(symbol: str)` builds one `ParquetLoader`, holds one DataFrame, and processes one bar per timestamp (`vibe/backtester/core/engine.py:95,115,215`). There is no multi-symbol loop to extend. |
| B2 | **No buying-power check (hard blocker)** | `_position_size` (`engine.py:372-381`) returns `max(1, int(risk_dollars / stop_distance))` with no cash bound, and `Portfolio` moves cash without a floor (`portfolio.py:55-58`). A portfolio backtest on top of this silently levers to whatever the signals demand, so the result would be **meaningless, not merely inaccurate**. Must be fixed first (plan increment P2 / defect E3). |
| B3 | **Contention rule is a leakage surface** | Any ranking used to choose among competing signals must be causal. Ranking on same-bar or future information reintroduces look-ahead through the back door, and it is a *research parameter* that must be optimized inside the training split like any other. |
| B4 | **Survivorship bias in the available universe** | The ~25-symbol Parquet corpus was selected knowing which names survived. It supports `static_declared` studies only; genuine `point_in_time_screened` universes need delisted history that the repo does not have. |
| B5 | **No correlation/exposure model** | Nothing currently tracks simultaneous exposure or correlated risk. |

## Dependency order

P10b cannot start until **P2** (execution realism, including E3 buying power)
and **P5b** (universe declaration and cross-sectional aggregation) have landed.

## Guardrails while deferred

- Every run stamps `universe_type` (`single_symbol` | `static_declared` |
  `point_in_time_screened`) and `survivorship_bias`.
- `point_in_time_screened` is rejected by the planner until delisted history
  exists.
- `static_declared` results carry a permanent bias badge in both dashboards.
- Pooled cross-sectional metrics are never reported without dispersion.
