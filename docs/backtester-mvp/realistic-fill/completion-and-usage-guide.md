# Realistic Fill Feature Completion and Usage Guide

## Status

The realistic fill feature is complete for backtester integration and is backward compatible by default.

- Default behavior (no execution config): legacy instant-fill behavior is preserved.
- Opt-in behavior (execution config provided): realistic slippage, volume participation, impact, and latency flow are enabled.
- Legacy wrapper compatibility: FillSimulator delegates to ExecutionSimulator with legacy config.
- Comparison/reporting: A/B comparison helper is available for legacy vs realistic runs.

## What Was Implemented

### Core execution models

- Order and Fill data models.
- Slippage models:
  - FixedTickSlippage for legacy behavior.
  - SqrtVolumeSlippage for realistic behavior.
- Volume models:
  - UnlimitedVolume for legacy behavior.
  - ParticipationRateVolume for realistic behavior.
- Impact models:
  - NoImpact for legacy behavior.
  - SqrtImpact for realistic behavior.
- ExecutionConfig factory methods:
  - ExecutionConfig.legacy(...)
  - ExecutionConfig.realistic(...)
- ExecutionSimulator market/limit execution and unfillable handling.

### Engine integration

- BacktestEngine now supports execution_config as an explicit opt-in for realistic execution.
- BacktestEngine default path remains backward compatible (legacy semantics).
- Pending order queue and latency-based eligibility are integrated in the event loop.
- ADV is precomputed once before loop and looked up per day.
- ORB entry price_override is used only in legacy-like mode; realistic mode uses model pricing path.

### Reporting and validation

- compare_execution_modes(legacy_result, realistic_result) returns markdown A/B comparison.
- Determinism and compatibility regression tests were added for legacy and realistic modes.

## Backward Compatibility Contract

### Default behavior (safe for existing users)

If you create BacktestEngine without execution_config, behavior defaults to legacy instant-fill semantics.

```python
engine = BacktestEngine(
    ruleset=ruleset,
    data_dir=data_dir,
    initial_capital=10_000.0,
    slippage_ticks=5,
)
```

### Opt-in realistic behavior (for research)

To enable realistic execution, pass execution_config explicitly.

```python
config = ExecutionConfig.realistic(
    slippage_k=0.1,
    participation_rate=0.10,
    impact_k=0.1,
    latency_bars=0,
    adv_window=20,
)

engine = BacktestEngine(
    ruleset=ruleset,
    data_dir=data_dir,
    initial_capital=10_000.0,
    slippage_ticks=5,
    execution_config=config,
)
```

## Developer Quick Start

### 1. Baseline legacy run

```python
legacy_engine = BacktestEngine(ruleset, data_dir, execution_config=None)
legacy_result = legacy_engine.run("QQQ", start_date, end_date)
```

### 2. Realistic run

```python
realistic_engine = BacktestEngine(
    ruleset,
    data_dir,
    execution_config=ExecutionConfig.realistic(),
)
realistic_result = realistic_engine.run("QQQ", start_date, end_date)
```

### 3. Compare outputs

```python
from vibe.backtester.analysis.performance import compare_execution_modes

report_md = compare_execution_modes(legacy_result, realistic_result)
print(report_md)
```

## Recommended Validation Commands

Run these before merge or when touching execution logic:

```powershell
pytest -q vibe/tests/backtester/test_fill_simulator.py
pytest -q vibe/tests/backtester/execution/test_simulator.py
pytest -q vibe/tests/backtester/execution/test_engine_integration.py
pytest -q vibe/tests/backtester/execution/test_phase3_validation.py
pytest -q vibe/tests/backtester/test_execution_comparison_report.py
```

## Key Guardrails for Future Developers and Agents

- Do not change default behavior: execution_config=None must remain legacy-compatible.
- Do not force price_override in realistic mode, or slippage/impact will be bypassed.
- Keep deterministic behavior: same input/config must produce same result.
- Keep realistic behavior explicit and opt-in to avoid surprising existing backtests.
- Preserve legacy wrapper API for FillSimulator users.

## Primary Files

- vibe/backtester/core/engine.py
- vibe/backtester/core/execution/config.py
- vibe/backtester/core/execution/simulator.py
- vibe/backtester/core/fill_simulator.py
- vibe/backtester/analysis/performance.py
- vibe/tests/backtester/execution/test_phase3_validation.py
- vibe/tests/backtester/execution/test_engine_integration.py
- vibe/tests/backtester/test_execution_comparison_report.py

## Notes for Handoff

If a future change modifies entry pricing, pending queue behavior, or order execution semantics, re-run the Phase 3 validation suite and compare legacy vs realistic runs using compare_execution_modes.
