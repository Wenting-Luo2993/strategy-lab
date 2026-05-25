# Research Journal Integration Guide

**Date:** 2026-05-24  
**Status:** ✅ Complete  
**Integrations:** Backtester + Parameter Sweep

---

## Quick Start

### 1. Backtest with Automatic Experiment Tracking

```python
from datetime import datetime
from pathlib import Path
from vibe.backtester.core.engine import BacktestEngine
from vibe.backtester.integration import wrap_backtest_engine
from vibe.research_journal.registry import ResearchRegistry
from vibe.common.ruleset.models import StrategyRuleSet

# Setup
registry = ResearchRegistry()
hyp = registry.create_hypothesis(
    title="Test ORB on QQQ",
    rationale="Volume-based breakout edge",
    tags=["orb"]
)
exp = registry.create_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={"orb_minutes": 5},
    dataset_config={"symbols": ["QQQ"]},
    hypothesis_id=hyp.id
)

# Setup engine
ruleset = StrategyRuleSet.from_yaml(Path("config.yaml"))
engine = BacktestEngine(ruleset, Path("data/parquet"))

# Wrap with tracking
tracked_engine = wrap_backtest_engine(engine, registry)

# Run with tracking
result = tracked_engine.run(
    symbol="QQQ",
    start_date=datetime(2024, 1, 1),
    end_date=datetime(2024, 12, 31),
    experiment_id=exp.id  # Triggers auto-completion
)

# Experiment automatically completed with results ✓
```

### 2. Parameter Sweep with Lineage Tracking

```python
from vibe.backtester.analysis.parameter_sweep import ParameterSweep, ParameterDefinition
from vibe.backtester.integration import ParameterSweepExperimentTracker

# Setup tracking
registry = ResearchRegistry()
tracker = ParameterSweepExperimentTracker(
    registry=registry,
    hypothesis_id="HYP-001"
)

# Create parent experiment
parent_id = tracker.create_parent_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    base_parameters={"orb_minutes": 5},
    dataset_config={...},
    tags=["optimization"]
)

# Run sweep
sweep = ParameterSweep(...)
results = sweep.run(symbol="QQQ", start_date=..., end_date=...)

# Create child experiments for each variation
for idx, row in results.iterrows():
    var_id = tracker.create_variation_experiment(
        variation_number=idx + 1,
        strategy_name="ORBStrategy",
        strategy_version="1.4.2",
        variation_parameters={...},
        dataset_config={...}
    )
    
    tracker.complete_variation_experiment(
        experiment_id=var_id,
        trades=row['trades'],
        metrics={...},
        rank=row['rank'],
        conclusion=f"Variation {idx+1}"
    )

# Parent → Child1 → Child2 → Child3 (visible as lineage)
```

---

## Architecture

### Integration Points

```
Research Journal Framework
├── Backtester Integration
│   ├── wrap_backtest_engine() → adds experiment_id parameter
│   ├── BacktestExperimentTracker → tracks results
│   └── Automatic completion on backtest finish
│
└── Parameter Sweep Integration
    ├── ParameterSweepExperimentTracker → creates parent
    ├── Creates child experiments for variations
    ├── Tracks parameter lineage
    └── SweepResultExperimentLinker → links all results
```

### Experiment Hierarchy

```
HYP-001: "Test ORB Strategy"
└── EXP-001: Base ORB (5m, TP 2.0)
    ├── EXP-002: Variation 1 (5m, TP 1.5) [sweep parent]
    │   ├── EXP-003: Child (ORB 5m, TP 1.5)
    │   ├── EXP-004: Child (ORB 10m, TP 1.5)
    │   └── EXP-005: Child (ORB 15m, TP 1.5)
    └── EXP-006: Variation 2 (10m, TP 2.0)
        ├── EXP-007: Child (...)
        └── ...
```

---

## Feature Comparison

| Feature | Before | After |
|---------|--------|-------|
| **Experiment tracking** | Manual | Automatic |
| **Git metadata capture** | N/A | ✅ Auto |
| **Backtest results storage** | CSV/JSON files | YAML + metrics |
| **Parameter lineage** | Not tracked | Full parent-child tree |
| **Optimization history** | Lost after sweep | Searchable experiments |
| **Result reproducibility** | No metadata | Git commit included |
| **Artifact management** | Manual | Automatic checksums |
| **Discovery** | File search | Chainable queries |

---

## Usage Patterns

### Pattern 1: Single Backtest with Journal

```python
# Create experiment BEFORE running backtest
exp = registry.create_experiment(...)

# Run backtest with tracking
result = engine.run(..., experiment_id=exp.id)

# Experiment auto-completed ✓
```

### Pattern 2: Parameter Sweep with Full Lineage

```python
# Create parent for sweep
parent_id = tracker.create_parent_experiment(...)

# For each variation
for params in variations:
    child_id = tracker.create_variation_experiment(...)
    tracker.complete_variation_experiment(...)

# Query results
query = ExperimentQuery(registry)
completed = query.by_status(COMPLETED).by_hypothesis(hyp_id).execute()
```

### Pattern 3: Optimization Chain

```
Base Experiment (EXP-001)
  ↓
Sweep 1 [Parent: EXP-002]
  ├── Variation 1 (EXP-003)
  ├── Variation 2 (EXP-004)
  └── Best: Variation 2
    ↓
Sweep 2 [Parent: EXP-005, based on Var 2]
  ├── Variation 1 (EXP-006)
  ├── Variation 2 (EXP-007)
  └── Best: Variation 1
    ↓
Final Experiment (EXP-008)
```

---

## Integration Examples

See examples folder:
- `examples/backtest_experiment_tracking.py` — Single backtest
- `examples/parameter_sweep_experiment_tracking.py` — Full optimization

Run examples:
```bash
cd d:\development\strategy-lab

# Backtest example
python examples/backtest_experiment_tracking.py

# Parameter sweep example
python examples/parameter_sweep_experiment_tracking.py
```

---

## Enabling Integration

### Method 1: Automatic (Default)

If `registry` is available, just pass it:

```python
tracked_engine = wrap_backtest_engine(engine, registry)
result = tracked_engine.run(..., experiment_id=exp.id)
```

### Method 2: Manual

Use tracker classes directly:

```python
from vibe.backtester.integration import BacktestExperimentTracker

tracker = BacktestExperimentTracker(registry)
tracker.track_backtest_result(
    backtest_result=result,
    experiment_id=exp.id,
    ...
)
```

### Method 3: Optional (No Breaking Changes)

If `registry=None`, tracking is disabled:

```python
tracked_engine = wrap_backtest_engine(engine, registry=None)
result = tracked_engine.run(...)  # Works normally, no tracking
```

---

## What Gets Tracked

### Backtest Integration

✅ **Automatically Captured:**
- Backtest trades (entry/exit prices, P&L)
- Performance metrics (Sharpe, expectancy, win rate)
- Git metadata (commit, branch, Python version)
- Dataset configuration (symbols, dates)
- Strategy parameters

✅ **User Provides:**
- Hypothesis ID
- Backtest conclusion

### Parameter Sweep Integration

✅ **Automatically Created:**
- Parent experiment (represents sweep run)
- Child experiments (one per variation)
- Parameter lineage (parent-child links)
- Experiment metrics (from each backtest)
- Rank information (best to worst)

✅ **User Provides:**
- Hypothesis ID
- Base parameters
- Parameter variations to test

---

## Querying Integrated Results

### Find All Completed Backtests

```python
from vibe.research_journal.query import ExperimentQuery

query = ExperimentQuery(registry)
results = query.by_status(ExperimentStatus.COMPLETED).execute()
print(f"Found {len(results)} completed experiments")
```

### Find Best Optimization Result

```python
query = ExperimentQuery(registry)
best_exps = (query
    .by_hypothesis("HYP-001")
    .by_result_quality("sharpe_ratio", 1.5, 2.5)
    .execute())
```

### View Optimization Lineage

```python
lineage = registry.get_lineage_graph()

# Find all children of base experiment
children = lineage.get_descendants("EXP-001")
print(f"Generated {len(children)} variations")

# Find root of optimization
root = lineage.find_root("EXP-050")
print(f"Root experiment: {root}")
```

---

## Best Practices

✅ **DO:**
- Create experiment BEFORE running backtest
- Use meaningful hypothesis titles
- Tag experiments for easy discovery
- Track parameter variations through lineage
- Query results after optimization complete

❌ **DON'T:**
- Forget to pass `experiment_id` parameter
- Ignore git_dirty warnings
- Abandon tracking mid-optimization
- Lose reference to parent experiment ID
- Modify completed experiments

---

## Troubleshooting

### Q: Experiment not being completed
**A:** Ensure `experiment_id` is passed to `engine.run()`. Check that registry is not None.

### Q: No lineage between parent and child
**A:** Pass `parent_experiment_id` when creating variation experiments. Verify parent exists.

### Q: Tracker returning None
**A:** Check if `tracker.can_track()` returns False (registry might be None).

### Q: Git metadata showing dirty
**A:** Commit changes before running backtest: `git add . && git commit -m "msg"`

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-05-24 | 1.0 | Released backtest + sweep integration |
| 2026-05-16 | 0.5 | Core framework complete (no integration) |

---

## Related Documentation

- [Research Journal Guide](memory-bank/features/research-journal-guide.md)
- [Framework Implementation](docs/backtester-mvp/research-journal-framework/IMPLEMENTATION_SUMMARY.md)
- [Examples](examples/)
