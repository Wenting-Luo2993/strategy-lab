"""
Regime Research CLI.

Runs the full pipeline:
  Feature Engine → Trade Attribution → Day Regime Labeler
  → Filter Evaluator → Report Generator

Usage::

    python scripts/analyze_regimes.py \\
      --trades-csv  reports/our_trades.csv \\
      --data-dir    data/parquet \\
      --symbol      QQQ \\
      --features    atr_pctile,gap_pct,slope_20d,adx_14 \\
      --filter      "regime == 'trending_up'" \\
      --output      reports/orb_regime_analysis

    # Compute all features
    python scripts/analyze_regimes.py \\
      --trades-csv reports/our_trades.csv --data-dir data/parquet \\
      --symbol QQQ --features all --output reports/regime_full

    # Analysis only (no filter comparison)
    python scripts/analyze_regimes.py \\
      --trades-csv reports/our_trades.csv --data-dir data/parquet \\
      --symbol QQQ --output reports/regime_analysis_only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

# Ensure repo root is on sys.path when run directly
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from vibe.backtester.analysis.regime_research.attribution import TradeAttributor
from vibe.backtester.analysis.regime_research.features import FeatureEngine
from vibe.backtester.analysis.regime_research.filter_evaluator import FilterEvaluator
from vibe.backtester.analysis.regime_research.labeler import DayRegimeLabeler, LabelerConfig
from vibe.backtester.analysis.regime_research.reporting import ReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("analyze_regimes")


def _load_ohlcv(data_dir: Path, symbol: str) -> pd.DataFrame:
    """Load OHLCV parquet for symbol.  Tries <symbol>.parquet and <symbol>_1min.parquet."""
    candidates = [
        data_dir / f"{symbol}.parquet",
        data_dir / f"{symbol}_1min.parquet",
        data_dir / f"{symbol.upper()}.parquet",
    ]
    for path in candidates:
        if path.exists():
            logger.info("Loading OHLCV from %s", path)
            df = pd.read_parquet(path)
            df.columns = [c.lower() for c in df.columns]
            return df
    raise FileNotFoundError(
        f"No parquet file found for symbol '{symbol}' in {data_dir}. "
        f"Tried: {[str(p) for p in candidates]}"
    )


def _load_trades(path: Path) -> pd.DataFrame:
    trades = pd.read_csv(path)
    trades.columns = [c.lower().strip() for c in trades.columns]
    if "entry_time" not in trades.columns:
        raise ValueError(
            f"Trades CSV must have an 'entry_time' column. Found: {trades.columns.tolist()}"
        )
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True).dt.tz_localize(None)
    if "pnl_r" not in trades.columns:
        raise ValueError(
            "Trades CSV must have a 'pnl_r' column (R-multiple per trade)."
        )
    logger.info("Loaded %d trades from %s", len(trades), path)
    return trades


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run regime research pipeline on backtester trade output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--trades-csv", required=True, help="Path to trades CSV (output of run_backtest.py)")
    parser.add_argument("--data-dir",   required=True, help="Directory containing OHLCV parquet files")
    parser.add_argument("--symbol",     default="QQQ", help="Symbol to load (default: QQQ)")
    parser.add_argument(
        "--features",
        default="atr_pctile,gap_pct,slope_20d,adx_14",
        help="Comma-separated feature names, or 'all'",
    )
    parser.add_argument(
        "--filter",
        default="",
        dest="filter_expr",
        help="Optional pandas query filter expression, e.g. \"regime == 'trending_up'\"",
    )
    parser.add_argument("--output", required=True, help="Output directory for reports")
    parser.add_argument(
        "--adx-threshold", type=float, default=22.0,
        help="ADX trend threshold for day regime labeler (default: 22.0)"
    )
    parser.add_argument(
        "--slope-flat-band", type=float, default=0.001,
        help="Slope flat-band for day regime labeler — normalized units (default: 0.001)"
    )

    args = parser.parse_args(argv)

    # ------------------------------------------------------------------ #
    # 1. Load data
    # ------------------------------------------------------------------ #
    ohlcv = _load_ohlcv(Path(args.data_dir), args.symbol)
    trades = _load_trades(Path(args.trades_csv))

    # ------------------------------------------------------------------ #
    # 2. Feature Engine
    # ------------------------------------------------------------------ #
    feature_list = "all" if args.features == "all" else args.features.split(",")
    logger.info("Computing features: %s", feature_list)
    engine = FeatureEngine()
    features = engine.compute(ohlcv, feature_list)
    logger.info("Feature table: %d rows × %d columns", len(features), len(features.columns))

    # ------------------------------------------------------------------ #
    # 3. Trade Attribution
    # ------------------------------------------------------------------ #
    logger.info("Attributing features to trades …")
    attributor = TradeAttributor()
    enriched = attributor.enrich(trades, features)
    logger.info("Enriched trade table: %d rows", len(enriched))

    # ------------------------------------------------------------------ #
    # 4. Day Regime Labeler  (only if required features are present)
    # ------------------------------------------------------------------ #
    labeler_features = {"adx_14", "slope_20d", "atr_pctile"}
    if labeler_features.issubset(set(features.columns)):
        logger.info("Running Day Regime Labeler …")
        labeler = DayRegimeLabeler()
        cfg = LabelerConfig(adx_trend_threshold=args.adx_threshold, slope_flat_band=args.slope_flat_band)
        regime_labels = labeler.label(features, cfg)

        # Attach labels to enriched trades via entry_date join
        regime_df = regime_labels.to_frame("regime")
        regime_df.index.name = "date"
        regime_df = regime_df.reset_index()
        regime_df["date"] = pd.to_datetime(regime_df["date"]).dt.normalize()

        enriched["_entry_date"] = pd.to_datetime(enriched["entry_time"]).dt.normalize()
        enriched = enriched.merge(regime_df, left_on="_entry_date", right_on="date", how="left")
        enriched = enriched.drop(columns=["_entry_date", "date"], errors="ignore")

        label_counts = enriched["regime"].value_counts(dropna=False)
        logger.info("Regime label distribution:\n%s", label_counts.to_string())
    else:
        missing = labeler_features - set(features.columns)
        logger.warning(
            "Skipping Day Regime Labeler — missing features: %s. "
            "Add these features with --features to enable labeling.",
            sorted(missing),
        )

    # ------------------------------------------------------------------ #
    # 5. Filter Evaluator
    # ------------------------------------------------------------------ #
    filter_reports = []
    if args.filter_expr:
        logger.info("Evaluating filter: %s", args.filter_expr)
        evaluator = FilterEvaluator()
        report = evaluator.evaluate(enriched, args.filter_expr)
        filter_reports.append(report)
        if report.warnings:
            for w in report.warnings:
                logger.warning("[%s] %s", w.code, w.message)
        logger.info(
            "Filter results: baseline=%d trades → filtered=%d trades",
            report.baseline.trade_count,
            report.filtered.trade_count,
        )
    else:
        logger.info("No filter specified — running analysis only")

    # ------------------------------------------------------------------ #
    # 6. Report Generator
    # ------------------------------------------------------------------ #
    output_dir = Path(args.output)
    logger.info("Generating reports in %s …", output_dir)
    reporter = ReportGenerator()
    reporter.generate(enriched, filter_reports, output_dir)
    logger.info(
        "Done. Reports written to:\n  %s/regime_analysis.md\n  %s/filter_comparison.md\n  %s/summary.json",
        output_dir, output_dir, output_dir,
    )


if __name__ == "__main__":
    main()
