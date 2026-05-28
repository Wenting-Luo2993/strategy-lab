#!/usr/bin/env python3
"""
CLI tool for strategy optimization.

Runs comprehensive optimization pipeline including:
- Parameter sweeping with pre-computed indicators
- Composite scoring with tail risk metrics
- Robustness analysis
- Walk-forward validation
- Surface analysis

Usage:
    # Basic optimization
    python scripts/optimize_strategy.py --strategy orb

    # With robustness and walk-forward
    python scripts/optimize_strategy.py --strategy orb \
        --robustness --walk-forward
    
    # Custom date range
    python scripts/optimize_strategy.py --strategy orb \
        --start 2020-01-01 --end 2024-12-31
    
    # Grid search mode with surface analysis
    python scripts/optimize_strategy.py --strategy orb \
        --mode grid --surface
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from vibe.backtester.optimization.pipeline import OptimizationPipeline
from vibe.backtester.analysis.parameter_sweep import ParameterDefinition

ET = ZoneInfo("America/New_York")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_orb_parameters(mode: str = "quick") -> list:
    """Get ORB strategy parameter definitions.

    tp_multiplier=0 means no take-profit (pure EOD exit).
    ORB is a convex structure — winners tend to run far, so capping profit
    with a TP multiplier kills positive skew. Include 0 to test the no-TP case.
    """
    if mode == "quick":
        # One-at-a-time mode (8 tests)
        return [
            ParameterDefinition(
                path="strategy.orb_duration_minutes",
                values=[5, 10, 15],
                base_value=5,
                name="orb_duration_minutes",
            ),
            ParameterDefinition(
                path="exit.take_profit.multiplier",
                values=[0, 1.5, 2.0, 3.0],  # 0 = no TP (EOD-only exit)
                base_value=0,
                name="tp_multiplier",
            ),
        ]
    else:  # "full" mode
        # Grid search (36 tests: 3 ORB x 4 TP x 3 risk)
        return [
            ParameterDefinition(
                path="strategy.orb_duration_minutes",
                values=[5, 10, 15],
                name="orb_duration_minutes",
            ),
            ParameterDefinition(
                path="exit.take_profit.multiplier",
                values=[0, 1.5, 2.0, 3.0],  # 0 = no TP (EOD-only exit)
                name="tp_multiplier",
            ),
            ParameterDefinition(
                path="position_size.value",
                values=[0.01, 0.02, 0.03],
                name="risk_pct",
            ),
        ]


def get_parameters_for_strategy(strategy: str, mode: str = "quick") -> list:
    """Get parameter definitions for a strategy."""
    if strategy == "orb":
        return get_orb_parameters(mode)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def get_base_ruleset_for_strategy(strategy: str) -> Path:
    """Get base ruleset path for a strategy."""
    if strategy == "orb":
        return Path("vibe/rulesets/orb_production.yaml")
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def main():
    parser = argparse.ArgumentParser(
        description="Optimize trading strategy parameters",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    parser.add_argument(
        "--strategy",
        type=str,
        default="orb",
        choices=["orb"],
        help="Strategy to optimize (default: orb)",
    )
    
    parser.add_argument(
        "--symbol",
        type=str,
        default="QQQ",
        help="Trading symbol (default: QQQ)",
    )
    
    parser.add_argument(
        "--start",
        type=str,
        default="2023-01-01",
        help="Start date YYYY-MM-DD (default: 2023-01-01)",
    )
    
    parser.add_argument(
        "--end",
        type=str,
        default="2024-12-31",
        help="End date YYYY-MM-DD (default: 2024-12-31)",
    )
    
    parser.add_argument(
        "--mode",
        type=str,
        default="quick",
        choices=["quick", "full"],
        help="Sweep mode: quick (one-at-a-time) or full (grid search)",
    )
    
    parser.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Initial capital (default: 100,000)",
    )
    
    parser.add_argument(
        "--slippage-ticks",
        type=int,
        default=5,
        help="Slippage simulation in ticks (default: 5)",
    )
    
    parser.add_argument(
        "--robustness",
        action="store_true",
        help="Run robustness analysis on best candidate",
    )
    
    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation",
    )
    
    parser.add_argument(
        "--surface",
        action="store_true",
        help="Run surface analysis (parameter heatmaps)",
    )
    
    parser.add_argument(
        "--output",
        type=str,
        default="reports/optimization",
        help="Output directory for reports (default: reports/optimization)",
    )
    
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="cache/optimization",
        help="Cache directory for results (default: cache/optimization)",
    )
    
    args = parser.parse_args()
    
    # Parse dates
    try:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=ET)
        end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=ET)
    except ValueError as e:
        print(f"Error parsing dates: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Validate data directory
    data_dir = Path("vibe/data/parquet")
    if not data_dir.exists():
        print(f"ERROR: Data directory not found: {data_dir}", file=sys.stderr)
        print("Run: python scripts/convert_databento.py", file=sys.stderr)
        sys.exit(1)
    
    # Get strategy configuration
    base_ruleset = get_base_ruleset_for_strategy(args.strategy)
    parameters = get_parameters_for_strategy(args.strategy, args.mode)
    
    # Determine sweep mode
    sweep_mode = "one_at_a_time" if args.mode == "quick" else "grid"
    
    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create cache directory
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Create pipeline
    pipeline = OptimizationPipeline(
        base_ruleset_path=base_ruleset,
        data_dir=data_dir,
        initial_capital=args.capital,
        slippage_ticks=args.slippage_ticks,
    )
    
    # Run optimization
    logger.info("Starting optimization pipeline...")
    
    try:
        result = pipeline.optimize(
            symbol=args.symbol,
            start_date=start_date,
            end_date=end_date,
            parameters=parameters,
            sweep_mode=sweep_mode,
            cache_dir=cache_dir,
            run_robustness=args.robustness,
            run_walk_forward=args.walk_forward,
            run_surface=args.surface,
            output_dir=output_dir,
        )
        
        # Print summary
        print("\n" + result.summary())
        
        # Save summary to file
        summary_path = output_dir / "optimization_summary.txt"
        with open(summary_path, "w") as f:
            f.write(result.summary())
        
        logger.info(f"\nSummary saved to {summary_path}")
        logger.info(f"All results saved to {output_dir}/")
        
    except Exception as e:
        logger.error(f"Optimization failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
