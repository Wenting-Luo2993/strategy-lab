# Realistic Fill - Feature Guide

**Status:** ✅ Complete (backward-compatible integration)

## What this feature provides

- Pluggable execution simulation (slippage, volume participation, impact, latency).
- Backward-compatible default behavior in backtester.
- Explicit realistic-fill opt-in for research runs.
- A/B comparison helper for legacy vs realistic results.

## Execution mode contract

- `BacktestEngine(..., execution_config=None)`:
  - Uses legacy-compatible behavior by default.
- `BacktestEngine(..., execution_config=ExecutionConfig.realistic(...))`:
  - Enables realistic execution path.

This contract is intentional and must be preserved for historical comparability.

## Primary APIs

- `ExecutionConfig.legacy(...)`
- `ExecutionConfig.realistic(...)`
- `ExecutionSimulator.execute_order(...)`
- `compare_execution_modes(legacy_result, realistic_result)`

## Canonical docs

- Implementation plan: `docs/backtester-mvp/realistic-fill/implementation.md`
- Completion + usage guide: `docs/backtester-mvp/realistic-fill/completion-and-usage-guide.md`
- ADRs:
  - `memory-bank/adrs/adr-014-roes-pluggable-execution.md`
  - `memory-bank/adrs/adr-015-roes-default-legacy-opt-in-realistic.md`

## Validation references

- `vibe/tests/backtester/execution/test_phase3_validation.py`
- `vibe/tests/backtester/execution/test_engine_integration.py`
- `vibe/tests/backtester/test_execution_comparison_report.py`
