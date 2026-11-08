# ADR-019: Declared Execution Semantics with Always-On Measurement

**Date**: 2026-09-09

**Status**: ✅ Accepted

## Context

A design review of the backtest pipeline found three defects that inflate results without appearing anywhere in the output:

- **E1 Intrabar exit ordering.** `PortfolioManager.check_exits` evaluated take-profit before stop-loss using `bar.high`/`bar.low`. Any bar touching both levels was booked a win. On 5-minute bars with a tight ORB stop this is common, not marginal. The method's docstring also claimed exits triggered on `bar.close`, which the code never did.
- **E2 Gap-through fills.** Exits filled at exactly the stop or target, so a bar that opened straight through the level cost nothing. Gap risk was free.
- **E3 Undeclared leverage.** `BacktestEngine._position_size` has no buying-power check and portfolio cash has no floor, so a strategy could take positions it could never fund. Unfunded size is indistinguishable from edge.

These are the assumptions a reader would most want disclosed, and were exactly the ones the artifacts omitted.

## Decision

Introduce `vibe/backtester/core/execution_realism.py` defining `ExecutionRealismConfig`, and separate two concerns:

1. **Behaviour is opt-in.** Following ADR-015, the default is `ExecutionRealismConfig.legacy()`, which reproduces prior results bit-for-bit. `realistic()` selects conservative intrabar resolution, fill-at-open on gaps, and buying-power enforcement.
2. **Measurement is mandatory.** `ambiguous_exit_bars`, `gap_through_exits`, `min_cash`, and `max_gross_exposure_ratio` are recorded in *every* mode. A legacy run still reports how much of its result depends on the optimistic assumptions.

`ExecutionRealismConfig.identity()` carries the settings, not just `EXECUTION_MODEL_VERSION`, so two runs with identical code but different execution assumptions cannot collide in the run fingerprint.

Under `AT_OPEN`, fills are clamped to `[bar.low, bar.high]`: the simulator may not invent a price nobody traded.

## Alternatives Considered

- **Fix the ordering outright and change the default.**
  - Rejected: silently invalidates every existing baseline and breaks comparability, the precise failure ADR-015 exists to prevent.
- **Only add the config, without the counters.**
  - Rejected: leaves the default path quietly optimistic. Opt-in realism that nobody opts into changes nothing. The counters make the assumption visible even when unaddressed.
- **Clamp fills to the bar range in legacy mode too.**
  - Rejected: clamping only differs when a gap occurred, so it would change legacy numbers. Gaps are counted in legacy mode instead, and repriced only under `AT_OPEN`.
- **Drop ambiguous trades entirely.**
  - Rejected as a default: silently changes the trade population. Offered as `WORST_CASE_UNRESOLVED` so exclusion is a deliberate, recorded choice.

## Reasoning

- Intrabar sequence is genuinely unknowable from OHLC. The honest response is to declare a convention and quantify its effect, not to pick the flattering one silently.
- The gap between `OPTIMISTIC` and `CONSERVATIVE` results *is* the size of the assumption, and is now directly measurable by re-running.
- Buying-power enforcement is the hard prerequisite for portfolio simulation (see `memory-bank/features/portfolio-simulation-constraint.md`, blocker B2).

## Consequences

- ✅ Existing backtests and stored results remain valid and comparable.
- ✅ Optimism is quantified rather than argued about.
- ✅ Unblocks B2 for future portfolio-level work.
- ⚠️ Legacy-mode runs still overstate performance by default; the counters disclose it but do not correct it.
- ⚠️ Researchers must pass `ExecutionRealismConfig.realistic()` deliberately for results meant to be believed.
- ⚠️ `BacktestEngine` does not yet thread the config through to `PortfolioManager`; that wiring lands with the engine increment.

## Related Files

- `vibe/backtester/core/execution_realism.py`
- `vibe/backtester/core/portfolio.py`
- `tests/unit/research_pipeline/test_execution_realism.py`
- `memory-bank/adrs/adr-015-roes-default-legacy-opt-in-realistic.md`
- `docs/backtest-research-summaries/2026-09-09-backtest-pipeline-implementation-plan.md` (section 6)
