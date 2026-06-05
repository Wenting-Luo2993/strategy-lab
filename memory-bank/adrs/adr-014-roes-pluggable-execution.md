# ADR-014: Realistic Order Execution Simulator (ROES) - Pluggable Architecture

**Date**: 2026-05-31

**Status**: ✅ Accepted (Integrated)

## Context

Current FillSimulator had monolithic slippage/volume/impact logic and was hard to extend. The system needed realistic execution constraints while keeping legacy backtest behavior intact.

## Decision

Implement Realistic Order Execution Simulator (ROES) with Protocol-based pluggable models:
- **Slippage**: Fixed ticks (legacy) or sqrt-volume (realistic)
- **Volume**: Unlimited (legacy) or participation-rate-based (realistic)
- **Impact**: No impact (legacy) or sqrt-impact based on ADV (realistic)

Backward compatible foundation: `ExecutionConfig.legacy()` replicates old behavior. Final mode contract is documented in ADR-015.

## Alternatives Considered

- **Monolithic extension**: Add parameters to FillSimulator (rejected - harder to reason about, test, extend)
- **Inheritance-based**: Subclass FillSimulator (rejected - violates composition-over-inheritance)
- **External library**: Use existing backtester models (rejected - not tailored to trading research)

## Reasoning

- Protocol-based design enables zero-cost abstraction (structural typing)
- Factory methods (legacy/realistic) make configuration intent explicit
- Edge cases handled uniformly for unfillable bars and volume constraints
- Extensive tests across core, integration, and validation phases
- Full backward compatibility prevents breaking existing analysis

## Consequences

- ✅ Fully integrated with backtester execution flow
- ✅ Supports realistic paper trading execution quality monitoring
- ✅ Easy to add new slippage/impact models without refactoring
- ✅ Backward-compatible default behavior preserved (see ADR-015)
- ⚠️ Realistic mode requires explicit opt-in via `execution_config`

## Phase Breakdown

- **Phase 1** (✅ Complete): Core models, ExecutionConfig, ExecutionSimulator
- **Phase 2** (✅ Complete): ADV computation, pending orders, latency wiring
- **Phase 3** (✅ Complete): Validation, determinism, degradation, comparison tooling
- **Phase 4** (✅ Complete): Documentation and handoff guides

## Related Files

- Implementation: `vibe/backtester/core/execution/`
- Engine integration: `vibe/backtester/core/engine.py`
- Tests: `vibe/tests/backtester/execution/`
- Feature guide: `memory-bank/features/realistic-fill-guide.md`
- Completion guide: `docs/backtester-mvp/realistic-fill/completion-and-usage-guide.md`
