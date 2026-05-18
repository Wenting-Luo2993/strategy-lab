# Strategy Optimization Framework

A comprehensive system for optimizing trading strategy parameters with robustness testing, walk-forward validation, and surface analysis.

## Quick Start

```bash
# Basic optimization
python scripts/optimize_strategy.py --strategy orb

# With robustness and walk-forward validation
python scripts/optimize_strategy.py --strategy orb \
    --robustness --walk-forward --surface
```

## Documentation

- **[PRD](prd.md)** — Original product requirements
- **[Architecture Review](architecture-review.md)** — System design and fit analysis
- **[Implementation Summary](implementation-summary.md)** — Detailed implementation guide
- **[Entry Confidence Analysis](entry-confidence-analysis.md)** — Adding entry signal confidence validation
- **[Adding Custom Indicators](adding-custom-indicators.md)** — How to add overnight gap, SMA/EMA, or custom indicators

## Features

### Core Capabilities

1. **Parameter Sweeping** — Grid search and one-at-a-time sensitivity testing
2. **Pre-computed Indicators** — 50-90% performance gain by computing indicators once
3. **Result Caching** — Avoid re-running identical parameter combinations
4. **Composite Scoring** — Multi-metric ranking with tail risk adjustment
5. **Robustness Analysis** — Noise injection to test execution sensitivity
6. **Walk-Forward Validation** — Out-of-sample testing to detect overfitting
7. **Surface Analysis** — 2D parameter heatmaps with cliff/plateau detection

### Key Metrics

**Composite Score Formula:**
```
score = 
    0.30 * sharpe_normalized +
    0.20 * expectancy_r_normalized +
    0.10 * tail_ratio_normalized +      # Captures convexity
    0.20 * win_rate_normalized +
    0.20 * profit_factor_normalized
```

**Tail Ratio:** `95th percentile return / 5th percentile return`  
→ Captures convex payoff structures (trend-following, tail-dependent strategies)

## Example Usage

### Command Line

```bash
# Quick sensitivity test (7 combinations)
python scripts/optimize_strategy.py --strategy orb --mode quick

# Full grid search with validation (27 combinations)
python scripts/optimize_strategy.py --strategy orb --mode full \
    --robustness --walk-forward --surface

# Custom date range
python scripts/optimize_strategy.py --strategy orb \
    --start 2020-01-01 --end 2024-12-31 \
    --output reports/my_optimization
```

### Programmatic

```python
from datetime import datetime
from pathlib import Path
from vibe.backtester.optimization import OptimizationPipeline
from vibe.backtester.analysis.parameter_sweep import ParameterDefinition

# Define parameters
params = [
    ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15]),
    ParameterDefinition("exit.take_profit.multiplier", [1.5, 2.0, 3.0]),
]

# Run optimization
pipeline = OptimizationPipeline(
    base_ruleset_path="vibe/rulesets/orb_production.yaml",
    data_dir=Path("vibe/data/parquet"),
)

result = pipeline.optimize(
    symbol="QQQ",
    start_date=datetime(2020, 1, 1),
    end_date=datetime(2024, 12, 31),
    parameters=params,
    sweep_mode="grid",
    run_robustness=True,
    run_walk_forward=True,
    run_surface=True,
)

# Results
print(f"Best params: {result.best_params}")
print(f"Composite score: {result.best_score:.3f}")
print(f"Robustness: {result.robustness_analysis.robustness_score:.2f}")
```

## Module Reference

### Core Modules

- **`parameter_sweep`** — Parameter combination generation and execution
- **`scoring`** — Composite score calculation with tail metrics
- **`robustness`** — Noise injection and stability testing
- **`walk_forward`** — Rolling train/test validation
- **`surface`** — 2D parameter landscape analysis
- **`pipeline`** — Unified optimization orchestration

### Key Classes

```python
# Parameter sweep
from vibe.backtester.analysis.parameter_sweep import ParameterSweep, ParameterDefinition

# Scoring
from vibe.backtester.analysis.scoring import composite_score, calculate_tail_ratio

# Robustness
from vibe.backtester.analysis.robustness import RobustnessAnalyzer

# Walk-forward
from vibe.backtester.analysis.walk_forward import WalkForwardEngine

# Surface
from vibe.backtester.analysis.surface import SurfaceAnalyzer

# Pipeline
from vibe.backtester.optimization import OptimizationPipeline
```

## Performance

### Speed Improvements

| Scenario | Time (Before) | Time (After) | Speedup |
|----------|---------------|--------------|---------|
| 10-param sweep | 120s | 15s | **8.0x** |
| 100-param grid + cache | 1,200s | 75s | **16.0x** |

**Key Optimizations:**
1. Pre-computed indicators (compute once, reuse for all params)
2. Result caching (avoid re-running identical combinations)

### Memory Usage

- Pre-computed features: ~10 MB per symbol
- Cached results: ~50 KB per result
- Total overhead (100-param grid): ~15 MB

## Architecture

### Data Flow

```
1. Load OHLCV data
   ↓
2. Pre-compute indicators (ATR, ADX, etc.) ← Compute ONCE
   ↓
3. Generate parameter combinations
   ↓
4. For each combination:
   - Check cache (skip if exists)
   - Run backtest with pre-computed features
   - Calculate composite score
   - Save to cache
   ↓
5. Rank by composite score
   ↓
6. Best candidate → Robustness analysis
   ↓
7. Best candidate → Walk-forward validation
   ↓
8. Surface analysis (2D parameter pairs)
```

### Module Dependencies

```
OptimizationPipeline
├── ParameterSweep
│   ├── FeatureEngine (from regime_research)
│   ├── BacktestEngine
│   └── Scoring
├── RobustnessAnalyzer
│   └── BacktestEngine
├── WalkForwardEngine
│   └── BacktestEngine
└── SurfaceAnalyzer
```

## Output Files

Running the optimization pipeline creates:

```
reports/optimization/
├── parameter_sweep.csv                      # All parameter combinations + scores
├── surface_param1_vs_param2.png             # Heatmap visualization
└── optimization_summary.txt                 # Human-readable summary
```

### Example Summary

```
================================================================================
OPTIMIZATION RESULT SUMMARY
================================================================================

Best Parameters:
  orb_duration_minutes: 10
  tp_multiplier: 2.0

Composite Score: 0.723

Robustness Score: 0.85
  Expectancy Std: ±0.042R

Walk-Forward Score: 0.78
  Avg Test Expectancy: 0.114R
  Avg Degradation: 15.3%

Surface Analysis:
  orb_duration_minutes_vs_tp_multiplier:
    Cliffs detected: 2
    Plateaus detected: 5
================================================================================
```

## How to Add New Parameters

### Step 1: Identify Available Parameters

All parameters in the strategy YAML are available for optimization using **dot-notation**:

```yaml
# Example: vibe/rulesets/orb_production.yaml
strategy:
  orb_duration_minutes: 5        # → "strategy.orb_duration_minutes"
  entry_cutoff_time: "15:00"     # → "strategy.entry_cutoff_time"

exit:
  take_profit:
    multiplier: 2.0               # → "exit.take_profit.multiplier"
  
position_size:
  value: 0.01                     # → "position_size.value"
```

### Step 2: Add Parameter to Sensitivity Runner

Edit `vibe/backtester/analysis/sensitivity_runner.py`:

```python
def get_orb_parameters(mode: str = "quick") -> list:
    if mode == "quick":
        return [
            # Existing parameters
            ParameterDefinition(
                path="strategy.orb_duration_minutes",
                values=[5, 10, 15],
                base_value=5,
                name="ORB_Duration",
            ),
            
            # NEW: Add your parameter
            ParameterDefinition(
                path="strategy.orb_body_pct_filter",  # YAML path
                values=[0.0, 0.3, 0.5, 0.7],           # Test values
                base_value=0.0,                         # Default/baseline
                name="Body_Filter",                     # Display name
            ),
        ]
```

### Step 3: Run Optimization

```bash
python scripts/optimize_strategy.py --strategy orb --mode quick
```

The framework will:
1. Load base ruleset (`orb_production.yaml`)
2. Generate parameter combinations
3. For each combination, modify the YAML value at the specified path
4. Run backtest and rank by composite score

### Step 4: Validate Parameter Exists in YAML

If the parameter isn't in your base ruleset, add it:

```yaml
# vibe/rulesets/orb_production.yaml
strategy:
  type: orb
  orb_duration_minutes: 5
  orb_body_pct_filter: 0.0  # ← Add this line
```

### Example: Adding Multiple Parameters

```python
# Test 3 parameters simultaneously (grid search = 3×3×3 = 27 combinations)
ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15], 5, "ORB_Duration"),
ParameterDefinition("strategy.orb_body_pct_filter", [0.0, 0.5, 0.7], 0.0, "Body_Filter"),
ParameterDefinition("exit.eod_time", ["15:50", "15:55", "16:00"], "15:55", "EOD_Time"),
```

### Parameter Types Supported

| Type | Example Values | Notes |
|------|----------------|-------|
| Integer | `[5, 10, 15, 20]` | ORB duration, max shares |
| Float | `[0.005, 0.01, 0.02]` | Risk %, thresholds |
| String (time) | `["14:00", "15:00"]` | Times (HH:MM format) |
| Boolean | `[True, False]` | Flags (use sparingly) |

### Best Practices

1. **Start with quick mode** (one-at-a-time) to identify influential parameters
2. **Use mechanically grounded values** (e.g., ORB duration aligned with market microstructure)
3. **Avoid over-parameterization** (>5 params = high overfitting risk)
4. **Validate with walk-forward** to detect overfitting

---

## Future Enhancements

### Planned (Not Yet Implemented)

- [ ] Parallelization (`ProcessPoolExecutor` for parameter sweep)
- [ ] Parameter perturbation (wiggle params by ±10%)
- [ ] Random sub-sampling (test on random date subsets)
- [ ] Bayesian optimization (smart parameter search)
- [ ] Multi-symbol optimization (find universal params)

### Completed

- [x] Pre-computed indicators
- [x] Result caching
- [x] Composite scoring with tail metrics
- [x] Robustness analysis (noise injection)
- [x] Walk-forward validation
- [x] Surface analysis (cliff/plateau detection)
- [x] Unified pipeline
- [x] CLI tool

## Credits

**Design:** Based on PRD and architecture review  
**Implementation:** 2026-05-18  
**Version:** 1.0.0
