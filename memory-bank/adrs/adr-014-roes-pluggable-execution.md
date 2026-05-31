# ADR-014: Realistic Order Execution Simulator (ROES) - Pluggable Architecture

**Date**: 2026-05-31

**Status**: ✅ Accepted (Phase 1 Complete)

## Context

Current FillSimulator has monolithic slippage/volume/impact logic. Hard to extend with new models. Needs paper trading preparation with realistic execution constraints.

## Decision

Implement Realistic Order Execution Simulator (ROES) with Protocol-based pluggable models:
- **Slippage**: Fixed ticks (legacy) or sqrt-volume (realistic)
- **Volume**: Unlimited (legacy) or participation-rate-based (realistic)
- **Impact**: No impact (legacy) or sqrt-impact based on ADV (realistic)

Backward compatible: `ExecutionConfig.legacy()` replicates old behavior exactly.

## Alternatives Considered

- **Monolithic extension**: Add parameters to FillSimulator (rejected - harder to reason about, test, extend)
- **Inheritance-based**: Subclass FillSimulator (rejected - violates composition-over-inheritance)
- **External library**: Use existing backtester models (rejected - not tailored to trading research)

## Reasoning

- Protocol-based design enables zero-cost abstraction (structural typing)
- Factory methods (legacy/realistic) make configuration intent explicit
- Edge cases handled uniformly (zero volume, zero ADV → ValueError)
- 114 comprehensive tests ensure correctness before Phase 2 integration
- Full backward compatibility prevents breaking existing analysis

## Consequences

- ✅ Ready for Phase 2 (pending orders, latency, ADV computation)
- ✅ Supports realistic paper trading execution quality monitoring
- ✅ Easy to add new slippage/impact models without refactoring
- ✅ 100% test coverage of core execution logic
- ⚠️ Requires Phase 2/3 before full integration with backtester
- ⚠️ ADV window adds 20-bar memory (Phase 2)

## Phase Breakdown

- **Phase 1** (✅ Complete): Core models, ExecutionConfig, ExecutionSimulator (6 tasks, 114 tests)
- **Phase 2** (Next): ADV computation, pending orders, latency (4 tasks)
- **Phase 3**: Validation, determinism tests, degradation checks (4 tasks)
- **Phase 4**: Documentation, migration guides (4 tasks)

## Related Files

- Implementation: `vibe/backtester/core/execution/`
- Tests: `vibe/tests/backtester/execution/` (114 tests)
- Session memory: `/memories/session/phase1-completion.md`
- Progress: `memory-bank/progress-log.md` (updated 2026-05-31)
