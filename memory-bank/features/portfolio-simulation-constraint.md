# Portfolio Simulation Contract

**Status**: ✅ Implemented in P10b
**Date recorded**: 2026-09-09
**Implemented**: 2026-10-12
**Decision**: Use `PortfolioBacktestEngine` when results need one shared
capital pool. Keep P5b cross-sectional evidence distinct; it remains useful for
per-symbol dispersion but must not be described as a portfolio return.
**Design doc**: `docs/backtest-research-summaries/2026-09-09-backtest-pipeline-implementation-plan.md`
(increments P5b and P10b)

---

## What it is

Two different multi-symbol capabilities are often confused. Only the first is
in scope.

| Capability | Meaning | Status |
|---|---|---|
| **Cross-sectional** (P5b) | Run the same strategy independently per symbol, then aggregate. Each symbol gets its own notional capital. No interaction between symbols. | ✅ In scope |
| **Portfolio simulation** (P10b) | One shared capital pool across N symbols. Symbols compete for capital, and taking one trade can prevent another. | ✅ Implemented |

The distinction matters because cross-sectional results **overstate** what a
real portfolio would earn. If ten symbols each fire a signal at 09:35 and
capital only supports three, the cross-sectional result books all ten.

## What it is for

- Answering "what would I actually have made trading this universe?" rather than
  "what was the average per-symbol edge?"
- Measuring realized concentration and correlated drawdown, which is the risk
  that actually ends accounts.
- Producing an equity curve that can be compared to a real brokerage statement.

Only a `PortfolioBacktestResult.aggregate` return may be described as a
portfolio return. Cross-sectional output remains per-symbol evidence with
dispersion.

## Implemented contract

1. **Merged event stream.** Timezone-aware per-symbol frames are heap-merged
   into a stable union timeline. Missing bars produce no event for that symbol.
   XNYS calendar opens/closes, including early closes, define session
   boundaries. Incomplete sessions fail by default; an explicit
   `liquidate_last_observed` fallback closes at the final observed print and is
   counted in result evidence.
2. **Finite shared capital.** Portfolio runs require
   `ExecutionRealismConfig.enforce_buying_power`; sizing and final fills share
   one `PortfolioManager` buying-power pool.
3. **Causal contention.** Simultaneous eligible signals are ranked by declared
   symbol priority, then lexical symbol order. The ranker receives only symbol,
   signal timestamp, and side; it cannot inspect prices, same-bar outcomes, or
   future bars.
4. **Concentration caps.** `max_symbol_exposure_pct` is required and
   `max_open_positions` is optional. Both are enforced before fills.
5. **Evidence and identity.** Results report realized gross leverage, funded
   entry leverage, single-name concentration, simultaneous positions, capital
   rejections, and cap counts. Aggregate portfolio metrics and per-symbol trade
   metrics are separate. Validation identity binds policy, execution model,
   ruleset, capital, symbols, and a deterministic OHLCV input checksum.

## Resolved blockers

| # | Blocker | Detail |
|---|---|---|
| B1 | **Engine is single-symbol by construction** — **RESOLVED 2026-10-12** | `PortfolioBacktestEngine` is a separate shared-capital engine. `BacktestEngine.run(symbol=...)` is unchanged for compatibility. |
| B2 | **No buying-power check** — **RESOLVED 2026-09-14** | Position sizing now gates on available buying power in both the backtester and the live bot, via the shared `PositionSizer`. Under `ExecutionRealismConfig.realistic()` a single-symbol ORB run holds `min_cash` at +$0.12 and peak leverage at 1.01x, versus -$26.7M and 10.82x before. Absolute P&L fell 19x ($4.42M → $233k) while expectancy held at ~0.326R. See [active-context](../active-context.md). **Caveat**: this bounds a *single-symbol* run. A portfolio backtest must still apportion one buying-power pool across concurrent symbols, which `BacktestEngine.run(symbol: str)` cannot express — that remains blocker B1. |
| B3 | **Contention rule is a leakage surface** — **RESOLVED 2026-10-12** | The default/only built-in ranker has a deliberately narrow immutable input and deterministic declared-priority/lexical ordering. Priority changes remain research parameters and must stay inside the normal train/validation discipline. |
| B4 | **Survivorship bias in the available universe** | The ~25-symbol Parquet corpus was selected knowing which names survived. It supports `static_declared` studies only; genuine `point_in_time_screened` universes need delisted history that the repo does not have. |
| B5 | **No correlation/exposure model** — **PARTIAL 2026-10-12** | Single-name concentration, position count, gross exposure, and leverage are enforced/reported. Sector/factor/covariance limits remain deliberately outside P10b. |

## Deliberate limitations

- The checked-in universe remains `static_declared` and survivorship-biased;
  P10b does not create point-in-time membership or delisted history.
- Concentration is enforced by single-name notional and optional position count,
  not sector, factor, beta, or covariance risk.
- The built-in ranking rule is intentionally simple. Strategies needing another
  causal score require a separately reviewed ranker contract; arbitrary
  callbacks do not receive market frames.
- The optional `liquidate_last_observed` fallback does not synthesize bars or
  liquidity during a halt; its early liquidation is visible as incomplete
  session evidence.

## Dependency order

P10b cannot start until **P2** (execution realism, including E3 buying power)
and **P5b** (universe declaration and cross-sectional aggregation) have landed.

## Guardrails

- Every run stamps `universe_type` (`single_symbol` | `static_declared` |
  `point_in_time_screened`) and `survivorship_bias`.
- `point_in_time_screened` is rejected by the planner until delisted history
  exists.
- `static_declared` results carry a permanent bias badge in both dashboards.
- Pooled cross-sectional metrics are never reported without dispersion.
