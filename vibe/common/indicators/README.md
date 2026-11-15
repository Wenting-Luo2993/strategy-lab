# Shared indicator implementation contract

Indicators in this folder are **strategy-independent, decision-time causal
measurements**. Strategies own thresholds, directional interpretations, entry
decisions and orders. Do not put ORB, experiment IDs, portfolio state or
broker/data-provider calls into indicator calculations.

## Before adding an indicator

1. Reuse existing primitives; do not copy a formula into a strategy or research
   script. Add only the smallest reusable function needed, not a new framework.
2. Define inputs, units, output/index alignment, price source, seeding, warm-up,
   session reset and missing-data behavior. Parameterize periods and relevant
   exchange/session boundaries; never hardcode research thresholds.
3. Values at bar `t` may use only completed bars through `t`. For start-stamped
   bars the value is available at **bar close**, not at its start timestamp.
   Full-day aggregates must be shifted to the next session for intraday use.
4. Keep symbols isolated. Batch functions accept one symbol at one resolution.
   Stateful implementations must key all state by symbol, timeframe and params.
5. Preserve unavailable values as NaN/None and reject malformed inputs
   explicitly. Never forward-fill or silently shorten a required history.
6. Test hand-calculated values and **prefix invariance**: appending or changing
   future data must not change earlier outputs. Test session boundaries,
   timezone/DST, missing history and parameter changes. If both batch and
   incremental implementations exist, test equivalent warm-up/seed behavior.
7. Opt strategy consumers into new features. Preserve existing defaults and
   reject unsupported execution paths rather than silently ignoring gates.
8. Precompute causal batch columns once before a backtest loop; consume only
   the decision-time row. An incremental strategy API can consume these columns,
   but that is not evidence of O(1) streaming support.

## Implementations and conventions

| Function | Contract |
| --- | --- |
| `batch.ema_series(series, length)` | First-observation seed, alpha `2/(length+1)`, output after `length` observations; missing observations invalidate later outputs |
| `session.session_vwap_series` | Cumulative bar-price x volume / cumulative volume; resets at configured local regular-session open; HLC3 or close source; excludes extended hours |
| `session.same_time_relative_volume_series` | Current bar volume / mean of matching clock-time bars in prior `lookback_sessions` observed sessions; current session excluded; requires every slot |
| `daily_atr.add_daily_atr` | Prior-session ATR mapped to intraday rows; default simple TR mean remains unchanged; explicit `smoothing="wilder"` uses `batch.atr_series` and a distinct column |
| `efficiency.session_efficiency_ratio` | Absolute net close change / absolute close-path length over a parameterized number of contiguous same-session changes; flat path is zero, missing/early windows NaN; available at bar close |
| `engine.IncrementalIndicatorEngine` | Existing streaming EMA/SMA/RSI/ATR/MACD/Bollinger implementation |

Session functions require sorted, unique, timezone-aware timestamps. Session
boundaries are inclusive at open and exclusive at close. Missing an entire
session cannot be detected from OHLCV alone; calendar/data-quality validation
belongs to ingestion. Missing clock slots in observed sessions are not skipped.
VWAP from OHLCV is an approximation, not exchange tick-exact VWAP. Volumes
retain the input feed's coverage; an indicator does not turn venue-only data
into consolidated market volume.

New shared functions have no production warm-up integration by implication.
The exploratory entry-confirmation consumers currently require batch-prepared
session history; live rulesets enabling them are refused until that lifecycle
is implemented and independently verified.
The completed-bar backtest consumer uses the standard execution-realism policy;
custom execution configurations are refused. Next-open entries are booked at
the following bar, including checks for stops reached on that entry bar.

Repository guardrail: [CLAUDE.md](../../../CLAUDE.md#shared-indicator-architecture).
Agent-scoped instructions: `.github/instructions/indicators.instructions.md`.
