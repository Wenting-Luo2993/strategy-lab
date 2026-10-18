# Research optimization framework

> Updated 2026-10-18. The former
> `vibe.backtester.optimization.OptimizationPipeline`,
> `vibe.backtester.analysis.walk_forward`, and
> `scripts/optimize_strategy.py` interfaces were deleted. They implemented a
> fixed-parameter procedure that could be mistaken for genuine nested
> out-of-sample research.

Research orchestration now lives under `vibe.research_pipeline`; the
`vibe.backtester` package contains only simulation and performance primitives.

## Supported entry points

Run a genuine nested walk-forward study:

```powershell
python scripts\run_nested_walk_forward.py --help
```

Run parameter sensitivity:

```powershell
python scripts\run_parameter_sensitivity.py --help
```

Manage methodology freeze and the authorized final holdout:

```powershell
python scripts\manage_research_study.py freeze --help
python scripts\manage_research_study.py authorize-final-holdout --help
python scripts\manage_research_study.py final-holdout --help
```

Authorization is durable and one-use. The final runner atomically claims it
before reading the ruleset, lock, manifests, or bars; verifies every frozen
runtime identity; records the run through the normal lifecycle with full trade
and equity detail; and atomically links the terminal verdict to that protected
`FINAL_OOS` run.

Manage explicit local-detail retention:

```powershell
python scripts\manage_research_retention.py --help
```

## Current modules

- `vibe.research_pipeline.optimization` — candidate grids, selectors, and
  single-symbol/shared-capital portfolio segment executors.
- `vibe.research_pipeline.walk_forward` — genuine nested walk-forward and
  stitched held-out evidence.
- `vibe.research_pipeline.robustness` — deterministic cost, neighborhood,
  fold, regime, and seeded bootstrap evidence.
- `vibe.research_pipeline.reliability` — immutable Reliability Policy v1 and
  deterministic study verdict.
- `vibe.research_pipeline.methodology` — frozen study identity and Bloom
  final-holdout authorization.
- `vibe.research_journal.integration` — optional journal trackers formerly
  located under the execution kernel; no compatibility import remains under
  `vibe.backtester`.
- `vibe.research_pipeline.parameter_sweep` and `sensitivity_runner` —
  research-specific exploratory analysis.

Every new research execution must provide a passing immutable
`EngineIntegrityReport`. Procedure completion and research reliability are
reported separately; neither is a promise of future profitability.

The current architecture and policy rationale are documented in
`docs/backtest-research-summaries/2026-10-17-backtest-decision-boundaries-and-cleanup-plan.md`.
