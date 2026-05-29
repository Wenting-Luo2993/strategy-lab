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
from typing import List

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


def _parse_float_list(raw: str) -> List[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_int_list(raw: str) -> List[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def get_orb_parameters(
    mode: str = "quick",
    trailing_breakeven: bool = False,
    trailing_only: bool = False,
    trigger_rs: List[float] | None = None,
    plus_ticks: List[int] | None = None,
) -> list:
    """Get ORB strategy parameter definitions.

    tp_multiplier=0 means no take-profit (pure EOD exit).
    ORB is a convex structure — winners tend to run far, so capping profit
    with a TP multiplier kills positive skew. Include 0 to test the no-TP case.
    """
    if trigger_rs is None:
        trigger_rs = [1.0, 2.0, 2.5, 3.0]
    if plus_ticks is None:
        plus_ticks = [0, 1, 2, 3, 5]

    if trailing_breakeven:
        base = [
            ParameterDefinition(
                path="exit.take_profit.multiplier",
                values=[0],
                base_value=0,
                name="tp_multiplier",
            ),
            ParameterDefinition(
                path="exit.trailing_stop.method",
                values=["breakeven_plus_ticks"],
                base_value="breakeven_plus_ticks",
                name="trailing_method",
            ),
            ParameterDefinition(
                path="exit.trailing_stop.trigger_r",
                values=trigger_rs,
                base_value=trigger_rs[0],
                name="trigger_r",
            ),
            ParameterDefinition(
                path="exit.trailing_stop.plus_ticks",
                values=plus_ticks,
                base_value=plus_ticks[0],
                name="plus_ticks",
            ),
        ]
        if trailing_only:
            return base

    if mode == "quick":
        # One-at-a-time mode (8 tests)
        params = [
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
        if trailing_breakeven:
            params.extend(base[1:])
        return params
    else:  # "full" mode
        # Grid search (36 tests: 3 ORB x 4 TP x 3 risk)
        params = [
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
        if trailing_breakeven:
            params.extend(base[1:])
        return params


def get_parameters_for_strategy(
    strategy: str,
    mode: str = "quick",
    trailing_breakeven: bool = False,
    trailing_only: bool = False,
    trigger_rs: List[float] | None = None,
    plus_ticks: List[int] | None = None,
) -> list:
    """Get parameter definitions for a strategy."""
    if strategy == "orb":
        return get_orb_parameters(
            mode=mode,
            trailing_breakeven=trailing_breakeven,
            trailing_only=trailing_only,
            trigger_rs=trigger_rs,
            plus_ticks=plus_ticks,
        )
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

    parser.add_argument(
        "--trailing-breakeven",
        action="store_true",
        help="Enable break-even-plus-ticks trailing stop sweep",
    )

    parser.add_argument(
        "--trailing-only",
        action="store_true",
        help="When --trailing-breakeven is set, sweep only trailing params on fixed base config",
    )

    parser.add_argument(
        "--trigger-rs",
        type=str,
        default="1.0,2.0,2.5,3.0",
        help="Comma-separated trigger R values for break-even trailing stop",
    )

    parser.add_argument(
        "--plus-ticks",
        type=str,
        default="0,1,2,3,5",
        help="Comma-separated plus-ticks values for break-even trailing stop",
    )

    parser.add_argument(
        "--journal",
        action="store_true",
        help="Register parameter sweep rows as completed experiments in research journal",
    )

    parser.add_argument(
        "--research-root",
        type=str,
        default="research",
        help="Research journal root directory (default: research)",
    )

    parser.add_argument(
        "--hypothesis-id",
        type=str,
        default=None,
        help="Existing hypothesis ID to attach experiments to (e.g., HYP-004)",
    )

    parser.add_argument(
        "--hypothesis-title",
        type=str,
        default=None,
        help="Create a new hypothesis with this title when --journal is enabled",
    )

    parser.add_argument(
        "--hypothesis-rationale",
        type=str,
        default=None,
        help="Rationale for new hypothesis when --journal is enabled",
    )

    parser.add_argument(
        "--journal-tags",
        type=str,
        default="optimization,parameter-sweep",
        help="Comma-separated tags for created hypothesis",
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
    
    # Parse optional trailing-stop grids
    trigger_rs = _parse_float_list(args.trigger_rs)
    plus_ticks = _parse_int_list(args.plus_ticks)

    # Get strategy configuration
    base_ruleset = get_base_ruleset_for_strategy(args.strategy)
    parameters = get_parameters_for_strategy(
        args.strategy,
        args.mode,
        trailing_breakeven=args.trailing_breakeven,
        trailing_only=args.trailing_only,
        trigger_rs=trigger_rs,
        plus_ticks=plus_ticks,
    )
    
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
            register_in_research_journal=args.journal,
            research_root=Path(args.research_root),
            hypothesis_id=args.hypothesis_id,
            hypothesis_title=args.hypothesis_title,
            hypothesis_rationale=args.hypothesis_rationale,
            journal_tags=[t.strip() for t in args.journal_tags.split(",") if t.strip()],
            experiment_tags=["optimization", "parameter-sweep", args.strategy],
            strategy_name=f"{args.strategy.upper()}Strategy",
        )
        
        # Print summary
        print("\n" + result.summary())
        if result.hypothesis_id:
            print(f"Research Journal Hypothesis: {result.hypothesis_id}")
        
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
