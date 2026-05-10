# Parameter Sensitivity Testing Framework

A generic, reusable framework for testing parameter combinations across any trading strategy.

## Features

✅ **Generic Design** - Works with any strategy ruleset  
✅ **Two Sweep Modes** - One-at-a-time (quick) or grid search (comprehensive)  
✅ **Nested Parameters** - Supports dot-notation paths (e.g., `exit.take_profit.multiplier`)  
✅ **Automatic Combinations** - Generates parameter combinations intelligently  
✅ **Progress Tracking** - Real-time progress updates during sweep  
✅ **CSV Export** - Saves detailed results for further analysis  
✅ **Top Results** - Displays best-performing parameter combinations  

## Sweep Modes

### One-at-a-Time Mode (Quick)
- Varies **one parameter at a time** while keeping others at base values
- Example: 3 parameters × 3 values = **7 tests** (base + 2×3 variations)
- Fast and efficient for initial parameter exploration
- Ideal for understanding individual parameter impact

### Grid Search Mode (Full)
- Tests **all combinations** (Cartesian product)
- Example: 3 parameters × 3 values = **27 tests**
- Comprehensive but slower
- Reveals parameter interactions and optimal combinations

## Quick Start

### 1. Run Pre-configured Tests

Test ORB strategy with one-at-a-time sweep (7 tests):

```bash
python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --mode quick

# Or use the convenience wrapper
python scripts/run_parameter_sensitivity.py --strategy orb
```

Test with grid search (27 tests):

```bash
python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --mode full
```

Test with custom date range:

```bash
python -m vibe.backtester.analysis.sensitivity_runner --strategy orb \
    --symbol QQQ \
    --start 2023-01-01 \
    --end 2024-12-31
```

Enable verbose logging (see all INFO messages):

```bash
python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --verbose
```

**Note:** By default, only WARNING and ERROR messages are shown (plus progress updates). Use `--verbose` to see all INFO-level logs from strategy calculations.

### 2. Programmatic Usage

```python
from datetime import datetime
from pathlib import Path
from vibe.backtester.analysis import ParameterSweep, ParameterDefinition

# Define parameters to test
parameters = [
    ParameterDefinition(
        path="strategy.orb_duration_minutes",
        values=[5, 10, 15],
        base_value=5,  # Default value for one-at-a-time mode
        name="ORB_Duration",
    ),
    ParameterDefinition(
        path="exit.take_profit.multiplier",
        values=[1.5, 2.0, 3.0],
        base_value=2.0,
        name="TP_Multiplier",
    ),
]

# Create sweep (one-at-a-time mode)
sweep = ParameterSweep(
    base_ruleset_path="vibe/rulesets/orb_production.yaml",
    data_dir=Path("vibe/data/parquet"),
    parameters=parameters,
    initial_capital=10_000.0,
    slippage_ticks=5,
    sweep_mode="one_at_a_time",  # or "grid" for full search
)

# Run sweep
results = sweep.run(
    symbol="QQQ",
    start_date=datetime(2023, 1, 1),
    end_date=datetime(2024, 12, 31),
)

# Save and display results
sweep.save_results(results, "reports/sensitivity.csv")
sweep.print_summary(results, top_n=5)
```

## Adding New Strategies

To add parameter sensitivity testing for a new strategy:
[vibe/backtester/analysis/sensitivity_runner.py](vibe/backtester/analysis/sensitivity_runner.py):

```python
def get_momentum_parameters(mode: str = "quick") -> list:
    """Parameter definitions for momentum strategy."""
    if mode == "quick":
        return [
            ParameterDefinition(
                path="strategy.lookback_periods",
                values=[10, 20, 30],
                base_value=20,  # Default for one-at-a-time
                name="Lookback",
            ),
            ParameterDefinition(
                path="strategy.momentum_threshold",
                values=[0.02, 0.05, 0.10],
                base_value=0.05,
                name="Threshold",
            ),
        ]
    # ... add "full" mode with more combinations and grid search
            ),
        ]
    # ... add "full" mode with more combinations
```

### 2. Register Strategy

Add to the strategy registry:

```python
def get_parameters_for_strategy(strategy: str, mode: str = "quick") -> list:
    strategy_params = {
        "orb": get_orb_parameters,
        "momentum": get_momentum_parameters,  # Add new strategy
    }
    # ...

def get_base_ruleset_for_strategy(strategy: str) -> Path:
    strategy_rulesets = {
        "orb": "vibe/rulesets/orb_production.yaml",
        "momentum": "vibe/rulesets/momentum.yaml",  # Add new strategy
    }
    # ...
```

### 3. Update CLI

Add to choices in argparse:

```python
parser.add_argument(
    "--strategy",
    required=True,
    choices=["orb", "momentum"],  # Add new strategy
    help="Strategy to test",
)
```

### 4. Run Tests
-m vibe.backtester.analysis.sensitivity_runner
```bash
python scripts/parameter_sensitivity.py --strategy momentum --mode quick
```

## Parameter Path Syntax

Parameters use dot-notation to navigate nested YAML structures:

| Parameter Path | YAML Structure |
|---------------|----------------|
| `strategy.orb_duration_minutes` | `strategy: { orb_duration_minutes: 5 }` |
| `exit.take_profit.multiplier` | `exit: { take_profit: { multiplier: 2.0 } }` |
| `position_size.value` | `position_size: { value: 0.01 }` |
| `trade_filter.volume_threshold` | `trade_filter: { volume_threshold: 1.5 }` |

## Output Format

Results CSV contains:

| Column | Description |
|--------|-------------|
| Parameter columns | One column per tested parameter |
| `n_trades` | Total number of trades |
| `win_rate` | Percentage of winning trades |
| `expectancy_r` | Expected return per trade (in R) |
| `total_pnl` | Total profit/loss |
| `max_drawdown` | Maximum drawdown |
| `profit_factor` | Gross profit / gross loss |
| `avg_win` | Average winning trade |
| `avg_loss` | Average losing trade |
| `sharpe_ratio` | Risk-adjusted return |

## Example Output

### One-at-a-Time Mode (Quick)

```
================================================================================
PARAMETER SENSITIVITY ANALYSIS - TOP 5 RESULTS
================================================================================
 ORB_Duration  TP_Multiplier  Risk_Pct  n_trades  win_rate  expectancy_r  total_pnl  max_drawdown
            5            2.0      0.01        52     48.1%         1.85R    $12,450       -$2,100
           10            2.0      0.01        45     51.1%         2.12R    $13,780       -$1,850
           15            2.0      0.01        38     49.2%         1.95R    $11,230       -$2,340
            5            1.5      0.01        52     52.3%         1.68R    $10,890       -$1,920
            5            3.0      0.01        52     44.2%         2.18R    $14,120       -$2,680
            5            2.0     0.005        52     48.1%         1.82R     $6,225       -$1,050
            5            2.0      0.02        52     48.1%         1.87R    $24,900       -$4,200
================================================================================

Tests: 7 (base + 6 variations)
Mode: one_at_a_time
```

### Grid Search Mode (Full)

```
================================================================================
PARAMETER SENSITIVITY ANALYSIS - TOP 5 RESULTS
================================================================================
 ORB_Duration  TP_Multiplier  Risk_Pct  n_trades  win_rate  expectancy_r  total_pnl  max_drawdown
           10            3.0      0.02        45     51.1%         2.34R    $15,780       -$2,850
           15   One-at-a-Time: 7 tests)

Base configuration: ORB_Duration=5, TP_Multiplier=2.0, Risk_Pct=0.01

Tests variations of each parameter:
```python
ORB_Duration: [5 (base), 10, 15]      # Opening range duration (minutes)
TP_Multiplier: [1.5, 2.0 (base), 3.0] # Take-profit multiple of ORB range
Risk_Pct: [0.005, 0.01 (base), 0.02]  # Risk per trade (0.5%, 1%, 2%)
```

Tests performed:
1. Base: (5, 2.0, 0.01)
2. Vary ORB_Duration: (10, 2.0, 0.01)
3. Vary ORB_Duration: (15, 2.0, 0.01)
4. Vary TP_Multiplier: (5, 1.5, 0.01)
5. Vary TP_Multiplier: (5, 3.0, 0.01)
6. Vary Risk_Pct: (5, 2.0, 0.005)
7. Vary Risk_Pct: (5, 2.0, 0.02)
with One-at-a-Time** - Use quick mode first to understand individual parameter impact
2. **Identify Key Parameters** - Focus grid search on parameters that showed the most sensitivity
3. **Test One Symbol** - Parameter sensitivity is symbol-specific
4. **Use Sufficient Data** - At least 1-2 years for statistical significance
5. **Avoid Overfitting** - Don't optimize too many parameters at once
6. **Out-of-Sample Test** - Validate best parameters on held-out data
7. **Consider Regime** - Different parameters may work better in different market conditions
8. **Document Base Values** - Always specify base_value for one-at-a-time mode

## Choosing Sweep Mode

**Use One-at-a-Time when:**
- Initial parameter exploration
- Limited computational budget
- Understanding individual parameter impact
- Many parameters to test (>4)
- Quick iteration needed

**Use Grid Search when:**
- Finding optimal parameter combinations
- Testing parameter interactions
- Few parameters (<4)
- Final optimization phase
- Sufficient computational resource
Risk_Pct: [0.005, 0.01, 0.015, 0.02]
Entry_Cutoff: ["14:00", "15:00", "15:30"]

Total: 5 × 5 × 4 × 3 = 300 combinations
ORB_Duration: [5, 10, 15]           # Opening range duration (minutes)
TP_Multiplier: [1.5, 2.0, 3.0]      # Take-profit multiple of ORB range
Risk_Pct: [0.005, 0.01, 0.02]       # Risk per trade (0.5%, 1%, 2%)
```

### Full Mode (300 tests)
, or specify base_value

**Issue**: Too many tests in grid mode  
**Solution**: Use one-at-a-time mode first, then focus grid search on key parameters
```python
ORB_Duration: [5, 10, 15, 20, 30]
TP_Multiplier: [1.0, 1.5, 2.0, 2.5, 3.0]
Risk_Pct: [0.005, 0.01, 0.015, 0.02]
Entry_Cutoff: ["14:00", "15:00", "15:30"]
```    # Generic framework
│   ├── ParameterDefinition      # Defines parameter + values + base
│   ├── ParameterSweep           # Main sweep orchestrator
│   │   ├── sweep_mode           # "one_at_a_time" or "grid"
│   │   └── _generate_combinations()  # Smart combination generation
│   └── SweepResult              # Individual test result
│
└── sensitivity_runner.py        # CLI tool
    ├── get_orb_parameters()             # Strategy-specific param definitions
    ├── get_parameters_for_strategy()    # Strategy registry
    └── main()                           # CLI entry point

scripts/
└── run_parameter_sensitivity.py # Convenience wrapperk better in different market conditions

## Performance Tips

- **Parallel Execution**: Currently runs serially; could be parallelized with `multiprocessing`
- **Data Caching**: Parquet data is loaded once per test (consider pre-loading for multiple symbols)
- **Result Streaming**: For very large sweeps, consider streaming results to CSV incrementally

## Troubleshooting

**Issue**: `ModuleNotFoundError: No module named 'pandas'`  
**Solution**: Install dependencies: `pip install pandas pyyaml`

**Issue**: `FileNotFoundError: data directory not found`  
**Solution**: Run `python scripts/convert_databento.py` to create Parquet data

**Issue**: `KeyError: 'exit.take_profit'`  
**Solution**: Ensure base ruleset YAML has all required sections

## Architecture

```
vibe/backtester/analysis/
├── parameter_sweep.py       # Generic framework
│   ├── ParameterDefinition  # Defines parameter + values to test
│   ├── ParameterSweep       # Main sweep orchestrator
│   └── SweepResult          # Individual test result

scripts/
└── parameter_sensitivity.py # CLI tool
    ├── get_orb_parameters()       # Strategy-specific param definitions
    ├── get_parameters_for_strategy()  # Strategy registry
    └── main()                      # CLI entry point
```

## Future Enhancements

- [ ] Parallel execution (multiprocessing)
- [ ] Walk-forward optimization
- [ ] Genetic algorithm optimization
- [ ] Interactive visualization dashboard
- [ ] Monte Carlo simulation for parameter robustness
- [ ] Market regime-aware parameter selection
