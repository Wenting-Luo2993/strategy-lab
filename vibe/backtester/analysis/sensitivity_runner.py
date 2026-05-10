#!/usr/bin/env python3
"""
Parameter sensitivity testing for trading strategies.

Usage:
    # Test ORB strategy with default parameter ranges
    python -m vibe.backtester.analysis.sensitivity_runner --strategy orb
    
    # Test with custom date range and symbol
    python -m vibe.backtester.analysis.sensitivity_runner --strategy orb --symbol SPY \
        --start 2022-01-01 --end 2023-12-31
    
    # Test with custom output path
    python -m vibe.backtester.analysis.sensitivity_runner --strategy orb \
        --output reports/orb_sensitivity.csv
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    # Look for .env in project root (3 levels up from this file)
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

from vibe.backtester.analysis.parameter_sweep import ParameterDefinition, ParameterSweep

ET = ZoneInfo("America/New_York")

# Logging configuration will be set in main() based on --verbose flag


# ============================================================================
# Strategy-Specific Parameter Definitions
# ============================================================================

def get_orb_parameters(mode: str = "quick") -> list:
    """
    Get parameter definitions for ORB strategy.
    
    Args:
        mode: "quick" (one-at-a-time: 3+3+3=9 tests) or "full" (grid search)
        
    Returns:
        List of ParameterDefinition objects
    """
    if mode == "quick":
        return [
            ParameterDefinition(
                path="strategy.orb_duration_minutes",
                values=[5, 10, 15],
                base_value=5,
                name="ORB_Duration",
            ),
            ParameterDefinition(
                path="exit.take_profit.multiplier",
                values=[1.5, 2.0, 3.0],
                base_value=2.0,
                name="TP_Multiplier",
            ),
            ParameterDefinition(
                path="position_size.value",
                values=[0.005, 0.01, 0.02],
                base_value=0.01,
                name="Risk_Pct",
            ),
        ]
    elif mode == "full":
        return [
            ParameterDefinition(
                path="strategy.orb_duration_minutes",
                values=[5, 10, 15, 20, 30],
                base_value=5,
                name="ORB_Duration",
            ),
            ParameterDefinition(
                path="exit.take_profit.multiplier",
                values=[1.0, 1.5, 2.0, 2.5, 3.0],
                base_value=2.0,
                name="TP_Multiplier",
            ),
            ParameterDefinition(
                path="position_size.value",
                values=[0.005, 0.01, 0.015, 0.02],
                base_value=0.01,
                name="Risk_Pct",
            ),
            ParameterDefinition(
                path="strategy.entry_cutoff_time",
                values=["14:00", "15:00", "15:30"],
                base_value="15:00",
                name="Entry_Cutoff",
            ),
        ]
    else:
        raise ValueError(f"Unknown mode: {mode}")


def get_parameters_for_strategy(strategy: str, mode: str = "quick") -> list:
    """
    Get parameter definitions for a given strategy.
    
    Args:
        strategy: Strategy name ("orb", etc.)
        mode: Test mode ("quick" or "full")
        
    Returns:
        List of ParameterDefinition objects
    """
    strategy_params = {
        "orb": get_orb_parameters,
        # Add other strategies here:
        # "mean_reversion": get_mean_reversion_parameters,
        # "momentum": get_momentum_parameters,
    }
    
    if strategy not in strategy_params:
        raise ValueError(f"Unknown strategy: {strategy}. Available: {list(strategy_params.keys())}")
    
    return strategy_params[strategy](mode)


def get_base_ruleset_for_strategy(strategy: str) -> Path:
    """
    Get base ruleset path for a strategy.
    
    Args:
        strategy: Strategy name
        
    Returns:
        Path to base ruleset YAML file
    """
    strategy_rulesets = {
        "orb": "vibe/rulesets/orb_production.yaml",
        # Add other strategies here
    }
    
    if strategy not in strategy_rulesets:
        raise ValueError(f"Unknown strategy: {strategy}")
    
    return Path(strategy_rulesets[strategy])


# ============================================================================
# Main Execution
# ============================================================================

def progress_callback(current: int, total: int, params: dict) -> None:
    """Print progress updates during sweep."""
    pct = (current / total) * 100
    print(f"\n[{current}/{total} - {pct:.1f}%] Testing: {params}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parameter sensitivity testing for trading strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    # Strategy selection
    parser.add_argument(
        "--strategy",
        required=True,
        choices=["orb"],  # Add more as implemented
        help="Strategy to test",
    )
    
    # Test configuration
    parser.add_argument(
        "--mode",
        default="quick",
        choices=["quick", "full"],
        help="Test mode: 'quick' (one-at-a-time) or 'full' (grid search)",
    )
    
    # Backtest parameters
    parser.add_argument("--symbol", default="QQQ", help="Symbol to test")
    parser.add_argument("--start", default="2023-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2024-12-31", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", default=10_000.0, type=float, help="Initial capital")
    parser.add_argument("--slippage-ticks", default=5, type=int, help="Slippage in ticks")
    
    # Output
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path (default: reports/parameter-sensitivity/{strategy}-{mode}.csv)",
    )
    parser.add_argument(
        "--top-n",
        default=10,
        type=int,
        help="Number of top results to display",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (shows all INFO messages)",
    )
    
    args = parser.parse_args()
    
    # Configure logging based on verbosity
    if args.verbose:
        log_level = logging.INFO
    else:
        log_level = logging.WARNING
    
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,  # Override any existing config
    )
    
    # Always show progress from the parameter sweep module
    logging.getLogger("vibe.backtester.analysis.parameter_sweep").setLevel(logging.INFO)
    
    # Validate data directory
    data_dir = Path("vibe/data/parquet")
    if not data_dir.exists():
        print(f"ERROR: Data directory not found: {data_dir}", file=sys.stderr)
        print("Run: python scripts/convert_databento.py", file=sys.stderr)
        sys.exit(1)
    
    # Get strategy configuration
    base_ruleset = get_base_ruleset_for_strategy(args.strategy)
    parameters = get_parameters_for_strategy(args.strategy, args.mode)
    
    # Determine sweep mode based on test mode
    sweep_mode = "one_at_a_time" if args.mode == "quick" else "grid"
    
    # Create sweep
    sweep = ParameterSweep(
        base_ruleset_path=base_ruleset,
        data_dir=data_dir,
        parameters=parameters,
        initial_capital=args.capital,
        slippage_ticks=args.slippage_ticks,
        sweep_mode=sweep_mode,
    )
    
    # Parse dates
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=ET)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=ET)
    
    # Set output path if not specified
    if args.output is None:
        output_path = Path(f"reports/parameter-sensitivity/{args.strategy}-{args.mode}.csv")
    else:
        output_path = Path(args.output)
    
    # Create output directory
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Run sweep
    print(f"\nParameter Sensitivity Test: {args.strategy.upper()} Strategy")
    print(f"Symbol: {args.symbol}")
    print(f"Period: {args.start} to {args.end}")
    print(f"Mode: {args.mode}")
    print(f"Parameters to test: {[p.name for p in parameters]}")
    
    total_combinations = 1
    for p in parameters:
        total_combinations *= len(p.values)
        print(f"  - {p.name}: {p.values}")
    
    print(f"\nTotal combinations: {total_combinations}")
    print("\nStarting parameter sweep...\n")
    
    results_df = sweep.run(
        symbol=args.symbol,
        start_date=start,
        end_date=end,
        progress_callback=progress_callback,
    )
    
    # Save results
    sweep.save_results(results_df, output_path)
    
    # Print summary
    sweep.print_summary(results_df, top_n=args.top_n)
    
    # Print statistics
    print("SUMMARY STATISTICS")
    print("=" * 80)
    print(f"Total tests: {len(results_df)}")
    print(f"Best Total P&L: ${results_df['total_pnl'].max():,.2f}")
    print(f"Worst Total P&L: ${results_df['total_pnl'].min():,.2f}")
    print(f"Mean Total P&L: ${results_df['total_pnl'].mean():,.2f}")
    print(f"Median Total P&L: ${results_df['total_pnl'].median():,.2f}")
    print(f"\nBest Win Rate: {results_df['win_rate'].max():.1%}")
    print(f"Best Expectancy: {results_df['expectancy_r'].max():.2f}R")
    print(f"Best Profit Factor: {results_df['profit_factor'].max():.2f}")
    print("=" * 80)
    
    print(f"\nFull results saved to: {output_path}")


if __name__ == "__main__":
    main()
