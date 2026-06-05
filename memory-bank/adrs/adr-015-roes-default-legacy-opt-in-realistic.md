# ADR-015: ROES Execution Mode Contract (Default Legacy, Explicit Realistic Opt-In)

**Date**: 2026-06-04

**Status**: ✅ Accepted

## Context

ROES introduced realistic execution mechanics (slippage, volume participation, impact, latency). Existing backtests and research flows rely on legacy instant-fill behavior and must not change silently.

A final integration rule is required to prevent behavior drift while still allowing realistic fills when requested.

## Decision

Adopt explicit execution mode contract in `BacktestEngine`:

- **Default mode**: legacy-compatible behavior when `execution_config is None`.
- **Realistic mode**: enabled only when `execution_config` is explicitly provided.
- In realistic mode, do not force ORB `price_override` for entries; use model-based pricing path.
- Keep pending order queue and latency in the execution loop for realistic order handling.

## Alternatives Considered

- Always use realistic execution by default.
  - Rejected: breaks historical comparability and existing workflows.
- Infer mode from `slippage_ticks` only.
  - Rejected: ambiguous and hides intent.
- Keep forced ORB override in all modes.
  - Rejected: bypasses realistic slippage/impact and invalidates model intent.

## Reasoning

- Preserves backward compatibility for all existing users and tests.
- Makes realistic execution a deliberate research choice.
- Avoids silent regression in historical research baselines.
- Keeps engine behavior deterministic and auditable across modes.

## Consequences

- ✅ Existing backtests continue to behave as before by default.
- ✅ Realistic fill experiments are explicit and reproducible.
- ✅ Cleaner A/B comparison between legacy and realistic modes.
- ⚠️ Developers must pass `execution_config` intentionally for realistic studies.

## Related Files

- `vibe/backtester/core/engine.py`
- `vibe/backtester/core/execution/simulator.py`
- `vibe/tests/backtester/execution/test_phase3_validation.py`
- `docs/backtester-mvp/realistic-fill/completion-and-usage-guide.md`
