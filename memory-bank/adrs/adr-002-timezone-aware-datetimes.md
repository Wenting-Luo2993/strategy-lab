# ADR-002: Timezone-Aware DateTime Operations

**Date**: 2025-12-10

**Status**: ✅ Accepted

## Context

Backtester and trading bot had subtle bugs across DST boundaries. Date changes were detected in UTC, not market timezone (EST/EDT).

## Decision

All datetime operations use market-timezone-aware helpers (`get_market_now()`, `get_market_date()`).

## Alternatives Considered

- **UTC everywhere** - Convert to market timezone only for display
- **Naive datetimes** - Assume all times are in market timezone (no explicit timezone)
- **Mixed approach** - UTC in backend, market timezone in frontend

## Reasoning

- Market timezone (EST/EDT) is the source of truth for trading decisions
- DST transitions happen twice a year - explicit timezone prevents bugs
- Date changes should be detected at 4:00 PM EST (market close), not midnight UTC
- Simpler mental model: all datetimes match wall-clock time in New York

## Consequences

- ✅ No DST bugs (tested across multiple year boundaries)
- ✅ Clear semantics: `get_market_now()` always returns NY time
- ⚠️ Must use helper functions (can't use `datetime.now()` directly)
- ⚠️ Slight performance overhead (timezone conversions)

## Related Code

- `vibe/trading_bot/utils/datetime_utils.py`
- `docs/TIMEZONE_FIX_SUMMARY.md`
