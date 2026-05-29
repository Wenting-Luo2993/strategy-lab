"""Deterministically compare right-tail preservation between baseline and variant backtests.

This script runs baseline and variant ORB configs over the same date range,
then measures whether the top X% baseline winners are preserved, cut short,
or exited earlier under the variant.
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from vibe.backtester.analysis.parameter_sweep import ParameterDefinition, ParameterSweep
from vibe.backtester.core.engine import BacktestEngine
from vibe.common.ruleset.models import StrategyRuleSet


ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class VariantConfig:
    trigger_r: float
    plus_ticks: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare top-percent baseline winner preservation for a trailing-stop variant.",
    )
    parser.add_argument("--symbol", default="QQQ", help="Symbol to backtest")
    parser.add_argument("--start", default="2018-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2024-12-31", help="End date YYYY-MM-DD")
    parser.add_argument(
        "--top-pcts",
        default="20,10,5",
        help="Comma-separated top winner percentages to evaluate",
    )
    parser.add_argument("--trigger-r", type=float, required=True, help="Variant trigger R")
    parser.add_argument("--plus-ticks", type=int, required=True, help="Variant plus ticks")
    parser.add_argument(
        "--ruleset",
        default="vibe/rulesets/orb_production.yaml",
        help="Base ruleset YAML path",
    )
    parser.add_argument(
        "--data-dir",
        default="vibe/data/parquet",
        help="Backtest parquet data directory",
    )
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--slippage-ticks", type=int, default=5)
    parser.add_argument(
        "--output-dir",
        default="reports/optimization/orb_trailing_breakeven_hyp004/tail_preservation",
        help="Directory to save summary and detailed comparison files",
    )
    parser.add_argument(
        "--quiet-loggers",
        action="store_true",
        help="Suppress verbose strategy/indicator logs",
    )
    parser.add_argument(
        "--cache-dir",
        default="cache/optimization",
        help="Directory containing cached backtest result pickles",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache loading and force fresh backtests",
    )
    return parser.parse_args()


def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ET)


def parse_top_pcts(raw: str) -> list[float]:
    pcts = [float(x.strip()) for x in raw.split(",") if x.strip()]
    if not pcts:
        raise ValueError("--top-pcts must contain at least one value")
    for p in pcts:
        if p <= 0 or p > 100:
            raise ValueError(f"top pct must be in (0,100], got {p}")
    return sorted(set(pcts), reverse=True)


def set_quiet_loggers() -> None:
    for name in [
        "vibe.common.strategies",
        "vibe.common.strategies.orb",
        "vibe.common.indicators",
        "vibe.common.indicators.orb_levels",
    ]:
        logging.getLogger(name).setLevel(logging.WARNING)


def load_ruleset_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_ruleset_config(base_cfg: dict[str, Any], variant: VariantConfig | None) -> dict[str, Any]:
    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("exit", {})
    cfg["exit"]["take_profit"] = {"method": "orb_range_multiple", "multiplier": 0}

    if variant is None:
        cfg["exit"]["trailing_stop"] = None
    else:
        cfg["exit"]["trailing_stop"] = {
            "method": "breakeven_plus_ticks",
            "trigger_r": variant.trigger_r,
            "plus_ticks": variant.plus_ticks,
        }
    return cfg


def run_backtest(
    cfg: dict[str, Any],
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    data_dir: Path,
    initial_capital: float,
    slippage_ticks: int,
    features: pd.DataFrame,
):
    ruleset = StrategyRuleSet(**cfg)
    engine = BacktestEngine(
        ruleset,
        data_dir=data_dir,
        initial_capital=initial_capital,
        slippage_ticks=slippage_ticks,
    )
    return engine.run(
        symbol=symbol,
        start_date=start,
        end_date=end,
        precomputed_features=features,
    )


def trades_to_df(result) -> pd.DataFrame:
    rows = []
    for t in result.trades:
        r_value = None
        if t.initial_risk:
            r_value = t.pnl / t.initial_risk
        rows.append(
            {
                "entry_time": t.entry_time,
                "side": t.side,
                "symbol": t.symbol,
                "pnl": t.pnl,
                "r": r_value,
                "exit_reason": t.exit_reason,
                "exit_time": t.exit_time,
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["entry_time", "side", "symbol"]).reset_index(drop=True)
    return df


def compare_top_pct(
    baseline_df: pd.DataFrame,
    variant_df: pd.DataFrame,
    top_pct: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    winners = baseline_df[baseline_df["pnl"] > 0].copy()
    winners = winners.sort_values(["pnl", "entry_time", "side", "symbol"], ascending=[False, True, True, True])

    if winners.empty:
        empty = pd.DataFrame()
        summary = {
            "top_pct": top_pct,
            "n_baseline_winners": 0,
            "n_top": 0,
        }
        return empty, summary

    n_top = max(1, int((len(winners) * (top_pct / 100.0)) + 0.999999))
    top = winners.head(n_top).copy()

    overlap = top.merge(
        variant_df,
        on=["entry_time", "side", "symbol"],
        how="left",
        suffixes=("_b", "_v"),
    )

    overlap["still_win_v"] = overlap["pnl_v"] > 0
    overlap["retain_90pct_pnl"] = overlap["pnl_v"] >= (0.9 * overlap["pnl_b"])
    overlap["retain_90pct_r"] = overlap["r_v"] >= (0.9 * overlap["r_b"])
    overlap["cut_short"] = overlap["pnl_v"] < overlap["pnl_b"]
    overlap["exit_earlier"] = overlap["exit_time_v"] < overlap["exit_time_b"]
    overlap["cut_by_stop"] = overlap["cut_short"] & (overlap["exit_reason_v"] == "STOP")
    overlap["pnl_capture_ratio"] = overlap["pnl_v"] / overlap["pnl_b"]

    n = len(overlap)
    summary = {
        "top_pct": top_pct,
        "n_baseline_winners": int(len(winners)),
        "n_top": int(n),
        "found_in_variant": int(overlap["pnl_v"].notna().sum()),
        "still_win_count": int(overlap["still_win_v"].sum()),
        "still_win_ratio": float(overlap["still_win_v"].mean()),
        "retain_90pct_pnl_count": int(overlap["retain_90pct_pnl"].sum()),
        "retain_90pct_pnl_ratio": float(overlap["retain_90pct_pnl"].mean()),
        "retain_90pct_r_count": int(overlap["retain_90pct_r"].sum()),
        "retain_90pct_r_ratio": float(overlap["retain_90pct_r"].mean()),
        "cut_short_count": int(overlap["cut_short"].sum()),
        "cut_short_ratio": float(overlap["cut_short"].mean()),
        "exit_earlier_count": int(overlap["exit_earlier"].sum()),
        "exit_earlier_ratio": float(overlap["exit_earlier"].mean()),
        "cut_by_stop_count": int(overlap["cut_by_stop"].sum()),
        "cut_by_stop_ratio": float(overlap["cut_by_stop"].mean()),
        "median_pnl_capture_ratio": float(overlap["pnl_capture_ratio"].median()),
    }
    return overlap, summary


def main() -> None:
    args = parse_args()
    if args.quiet_loggers:
        set_quiet_loggers()

    top_pcts = parse_top_pcts(args.top_pcts)
    start = parse_date(args.start)
    end = parse_date(args.end)

    ruleset_path = Path(args.ruleset)
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)

    base_cfg = load_ruleset_yaml(ruleset_path)
    variant = VariantConfig(trigger_r=args.trigger_r, plus_ticks=args.plus_ticks)

    # ParameterSweep provides deterministic cache key + pkl load/save utilities.
    pre = ParameterSweep(
        base_ruleset_path=ruleset_path,
        data_dir=data_dir,
        parameters=[
            ParameterDefinition(
                path="exit.take_profit.multiplier",
                values=[0],
                name="tp_multiplier",
            )
        ],
        initial_capital=args.initial_capital,
        slippage_ticks=args.slippage_ticks,
        sweep_mode="grid",
    )
    features: Optional[pd.DataFrame] = None

    def get_or_run_result(params: dict[str, Any], cfg: dict[str, Any], label: str):
        nonlocal features

        cache_key = pre._cache_key(params, args.symbol, start, end)
        if not args.no_cache:
            cached = pre._get_cached_result(cache_dir, cache_key)
            if cached is not None:
                print(f"{label}: loaded from cache ({cache_key})")
                return cached

        if features is None:
            # Only compute indicators if at least one result must be simulated.
            features = pre._precompute_features(args.symbol, start, end, timeframe="5m")

        result = run_backtest(
            cfg,
            symbol=args.symbol,
            start=start,
            end=end,
            data_dir=data_dir,
            initial_capital=args.initial_capital,
            slippage_ticks=args.slippage_ticks,
            features=features,
        )

        if not args.no_cache:
            pre._save_cached_result(cache_dir, cache_key, result)
            print(f"{label}: computed and cached ({cache_key})")
        else:
            print(f"{label}: computed (cache disabled)")
        return result

    baseline_result = get_or_run_result(
        {"tp_multiplier": 0},
        build_ruleset_config(base_cfg, variant=None),
        label="baseline",
    )
    variant_result = get_or_run_result(
        {
            "tp_multiplier": 0,
            "trailing_method": "breakeven_plus_ticks",
            "trigger_r": args.trigger_r,
            "plus_ticks": args.plus_ticks,
        },
        build_ruleset_config(base_cfg, variant=variant),
        label="variant",
    )

    baseline_df = trades_to_df(baseline_result)
    variant_df = trades_to_df(variant_result)

    run_id = f"tr{args.trigger_r:g}_pt{args.plus_ticks}"
    baseline_df.to_csv(output_dir / f"baseline_trades_{run_id}.csv", index=False)
    variant_df.to_csv(output_dir / f"variant_trades_{run_id}.csv", index=False)

    summaries = []
    for top_pct in top_pcts:
        overlap, summary = compare_top_pct(baseline_df, variant_df, top_pct)
        summaries.append(summary)
        overlap.to_csv(output_dir / f"tail_overlap_top{int(top_pct)}_{run_id}.csv", index=False)

    payload = {
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "variant": {"trigger_r": args.trigger_r, "plus_ticks": args.plus_ticks},
        "baseline_expectancy_r": float(baseline_result.overall.expectancy_r),
        "variant_expectancy_r": float(variant_result.overall.expectancy_r),
        "baseline_max_win_r": float(baseline_result.overall.max_win_r),
        "variant_max_win_r": float(variant_result.overall.max_win_r),
        "summaries": summaries,
    }

    summary_path = output_dir / f"tail_preservation_summary_{run_id}.json"
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    print(f"saved: {summary_path}")


if __name__ == "__main__":
    main()
