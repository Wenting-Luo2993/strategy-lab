# ADR-001: Event-Driven Backtester Architecture

**Date**: 2025-11-15

**Status**: ✅ Accepted

## Context

Need a backtest engine that accurately simulates real-time trading with realistic execution, intrabar stops, and bar flush timing.

## Decision

Implement event-driven backtester with discrete events (bar open, bar close, order fill, stop hit).

## Alternatives Considered

- **Vectorized backtester** (Pandas/NumPy) - Fast but less accurate (no intrabar stops, assumes perfect timing)
- **Tick-by-tick replay** - Most accurate but slow and data-intensive
- **External framework** (Backtrader, Zipline) - Feature-complete but harder to customize for our needs

## Reasoning

- Event-driven allows intrabar stop detection (critical for ORB strategy with tight stops)
- Bar flush timing can be modeled accurately (end-of-bar vs intrabar execution)
- Easier to add realistic slippage and execution delays
- Extensible for future strategies and order types

## Consequences

- ✅ Accurate simulation of real-time trading
- ✅ Easy to debug with event logging
- ⚠️ Slower than vectorized approach (acceptable for daily/weekly backtests)
- ⚠️ More complex code than simple Pandas operations

## Related Code

- `vibe/backtester/backtester.py`
- `vibe/backtester/execution/`
