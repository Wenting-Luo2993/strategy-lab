"""Run the registered, exploration-only ORB frozen-trade condition study.

The original v1 JSON mistakenly describes the shared ATR seed as EWM.
Calculations use the unchanged shared helper's mean-of-first-20-TR seed;
the protocol-description discrepancy is preserved in the living results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vibe.research_pipeline.hashing import hash_object
from vibe.research_pipeline.holdout import load_lock
from vibe.research_pipeline.movement_noise import (
    decision_features, freeze_labels, join_features, mean_estimate, summarize_diagnostics,
)
from vibe.research_pipeline.provenance import study_definition_hash
from vibe.research_pipeline.publishing import forbidden_payload_path
from vibe.research_pipeline.storage.sqlite_store import SqliteResearchStore
from vibe.research_pipeline.trials import TrialTag


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_exploration_bars(path: Path, start: str, end: str) -> pd.DataFrame:
    """Predicate-filter parquet before materializing any OHLCV rows."""
    schema = pq.ParquetFile(path).schema_arrow
    metadata = json.loads(schema.metadata[b"pandas"])
    columns = metadata["index_columns"]
    if len(columns) != 1 or not isinstance(columns[0], str):
        raise ValueError(f"{path.name}: expected a stored timestamp index")
    key = columns[0]
    timestamp_type = schema.field(key).type
    if not pa.types.is_timestamp(timestamp_type) or timestamp_type.tz is None:
        raise ValueError(f"{path.name}: expected an aware Arrow timestamp index")
    lo = pd.Timestamp(start, tz="America/New_York").tz_convert("UTC")
    hi = (pd.Timestamp(end, tz="America/New_York") + pd.Timedelta(days=1)).tz_convert("UTC")
    bars = pd.read_parquet(
        path, columns=["open", "high", "low", "close", "volume"],
        filters=[
            (key, ">=", pa.scalar(lo, type=timestamp_type)),
            (key, "<", pa.scalar(hi, type=timestamp_type)),
        ],
    )
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None:
        raise ValueError(f"{path.name}: expected aware intraday timestamps")
    if bars.empty or bars.index.min() < lo or bars.index.max() >= hi:
        raise ValueError(f"{path.name}: empty data or exploration bound violated")
    if not bars.index.is_monotonic_increasing or not bars.index.is_unique:
        raise ValueError(f"{path.name}: unsorted or duplicate timestamps")
    prices = bars[["open", "high", "low", "close"]].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError(f"{path.name}: missing/nonpositive source prices")
    return bars


def baseline_context(rows: pd.DataFrame, daily: dict[str, pd.DataFrame]) -> dict:
    """Realized daily baseline context; never a reconstructed filtered strategy."""
    sessions = sorted({str(day) for frame in daily.values() for day in frame.index})
    net = rows.groupby("session")["net_dollars"].sum().reindex(sessions, fill_value=0)
    rs = rows.groupby("session")["net_r"].sum().reindex(sessions, fill_value=0)
    capital = 100_000 * len(daily)
    equity = capital + net.cumsum()
    peaks = equity.cummax().clip(lower=capital)
    cumulative_r = rs.cumsum()
    r_peaks = cumulative_r.cummax().clip(lower=0)
    strategy_returns = net / equity.shift(1, fill_value=capital)
    closes = pd.DataFrame({s: d["close"] for s, d in daily.items()}).reindex(sessions)
    price_returns = closes.pct_change(fill_method=None)
    # Fixed equal initial allocations held in each source-price series.
    benchmark_equity = (100_000 * closes / closes.iloc[0]).sum(axis=1, min_count=len(daily))
    benchmark_returns = benchmark_equity.pct_change(fill_method=None)
    paired = pd.concat(
        [strategy_returns.rename("strategy"), benchmark_returns.rename("benchmark")],
        axis=1,
    ).dropna()
    downside = paired.loc[paired["benchmark"] < 0]
    symbol_sessions = sum(len(d) for d in daily.values())
    overnight = (
        rows["entry_time"].dt.tz_convert("America/New_York").dt.date
        != rows["exit_time"].dt.tz_convert("America/New_York").dt.date
    )
    if overnight.any():
        raise ValueError("Same-session baseline attribution found an overnight trade")

    def correlation(frame: pd.DataFrame) -> float | None:
        if len(frame) < 3 or (frame.std() == 0).any():
            return None
        return float(frame["strategy"].corr(frame["benchmark"]))

    return {
        "label": "unchanged_baseline_only_independent_accounts",
        "symbol_sessions": symbol_sessions,
        "sessions_by_symbol": {s: len(d) for s, d in daily.items()},
        "n_trades": len(rows),
        "trades_per_symbol_session": len(rows) / symbol_sessions,
        "net_dollars": float(net.sum()),
        "net_dollars_per_all_symbol_sessions": float(net.sum() / symbol_sessions),
        "net_r": float(rs.sum()),
        "daily_closed_trade_dollar_drawdown": float((equity - peaks).min()),
        "daily_closed_trade_drawdown_fraction": float((equity / peaks - 1).min()),
        "daily_closed_trade_r_drawdown": float((cumulative_r - r_peaks).min()),
        "overnight_trades": int(overnight.sum()),
        "daily_pearson_correlation": correlation(paired),
        "benchmark_down_days": len(downside),
        "benchmark_down_day_strategy_mean_return": float(downside["strategy"].mean()),
        "benchmark_down_day_correlation": correlation(downside),
        "benchmark_up_days": int((paired["benchmark"] > 0).sum()),
        "benchmark_up_day_strategy_mean_return": float(
            paired.loc[paired["benchmark"] > 0, "strategy"].mean()
        ),
        "by_symbol_correlation": {
            s: correlation(pd.concat([
                (rows.loc[rows["symbol"] == s].groupby("session")["net_dollars"].sum()
                 .reindex(d.index, fill_value=0)
                 / (100_000 + rows.loc[rows["symbol"] == s]
                    .groupby("session")["net_dollars"].sum()
                    .reindex(d.index, fill_value=0).cumsum().shift(1, fill_value=0))
                 ).rename("strategy"),
                price_returns[s].rename("benchmark"),
            ], axis=1).dropna())
            for s, d in daily.items()
        },
        "limitations": (
            "Daily realized equity, not intraday MTM; five independent $100k accounts, "
            "not a shared-capital portfolio. Buy-and-hold uses fixed equal initial "
            "allocations, source-price returns, no explicit dividends/fees; cash yield "
            "zero. Correlation is descriptive with no portfolio optimization or "
            "risk-matched claim. No new gated equity or improved drawdown is implied."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-db", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    registration = ROOT / "config" / "research" / "orb_movement_noise_diagnostic_v1.json"
    protocol = json.loads(registration.read_text(encoding="utf-8"))
    baseline_registration = ROOT / Path(protocol["source_registration"])
    if sha256(baseline_registration) != protocol["source_registration_sha256"]:
        raise ValueError("Frozen baseline registration has changed")
    baseline = json.loads(baseline_registration.read_text(encoding="utf-8"))
    for key in ("symbols", "start", "end", "lock_hash"):
        if protocol[key] != baseline[key]:
            raise ValueError(f"Protocol {key} differs from frozen exploration baseline")
    lock = load_lock()
    if lock is None or lock.lock_hash != protocol["lock_hash"]:
        raise ValueError("Missing or changed final-holdout lock")
    for symbol in protocol["symbols"]:
        lock.assert_within_dev(
            symbol=symbol, start=date.fromisoformat(protocol["start"]),
            end=date.fromisoformat(protocol["end"]),
        )
        if sha256(args.data_dir / f"{symbol}.parquet") != baseline["data_file_sha256"][symbol]:
            raise ValueError(f"{symbol}: source parquet has changed")
    if not args.research_db.is_file():
        raise FileNotFoundError(args.research_db)
    with sqlite3.connect(args.research_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        source = db.execute(
            "SELECT state,definition_json,result_json FROM research_experiments "
            "WHERE experiment_id=?", (protocol["source_experiment_id"],),
        ).fetchone()
        if source is None or source[0] != "completed":
            raise ValueError("Source experiment absent or incomplete")
        definition, source_result = json.loads(source[1]), json.loads(source[2])
        if definition.get("registration_sha256") != protocol["source_registration_sha256"]:
            raise ValueError("Source experiment does not match frozen baseline registration")
        members = db.execute(
            "SELECT run_id FROM research_experiment_runs WHERE experiment_id=?",
            (protocol["source_experiment_id"],),
        ).fetchall()
        if len(members) != len(protocol["symbols"]) or len(set(members)) != len(members):
            raise ValueError("Source experiment has unexpected run membership")
        trades = pd.read_sql_query(
            "SELECT t.* FROM run_trades t JOIN research_experiment_runs m "
            "ON m.run_id=t.run_id WHERE m.experiment_id=? ORDER BY t.run_id,t.sequence",
            db, params=(protocol["source_experiment_id"],),
        )
    if len(trades) != protocol["source_trade_count"] or set(trades["symbol"]) != set(protocol["symbols"]):
        raise ValueError("Incomplete source trade retention or unexpected symbols")
    source_trade_hash = hash_object(trades.drop(columns=["id", "recorded_at"]).to_dict("records"))
    from vibe.backtester.core.engine import _resample

    features, daily, loaded = {}, {}, {}
    for symbol in protocol["symbols"]:
        raw = load_exploration_bars(
            args.data_dir / f"{symbol}.parquet", protocol["start"], protocol["end"]
        )
        five = _resample(raw, "5min")
        # A partial source bar must not become an apparently contiguous ER path.
        counts = raw["close"].resample("5min").count().reindex(five.index)
        features[symbol], daily[symbol] = decision_features(
            five, protocol, complete_bars=(counts == 5)
        )
        loaded[symbol] = {
            "rows": len(raw), "first": raw.index[0].isoformat(),
            "last": raw.index[-1].isoformat(),
            "partial_five_minute_bars": int((counts != 5).sum()),
        }
    rows = join_features(trades, features)
    if not rows["session"].between(protocol["start"], protocol["end"]).all():
        raise ValueError("Source trade outside exploration interval")
    pooled = mean_estimate(rows)
    if not np.isclose(pooled["mean_r"], source_result["pooled"]["mean_r"], atol=1e-12, rtol=0):
        raise ValueError("Source ledger expectancy fails reconciliation")
    rows, thresholds = freeze_labels(rows, protocol)
    analysis = summarize_diagnostics(rows, protocol)
    context = baseline_context(rows, daily)
    summary = {
        "schema_version": 1, "kind": "condition_diagnostic",
        "evidence_role": "exploration",
        "source_experiment_id": protocol["source_experiment_id"],
        "not_a_new_strategy_backtest": True,
        "pooled": pooled, "outcome": "diagnostic_only", "errors": {},
        "thresholds": thresholds,
        "coverage": {
            "n_trades": len(rows), "measurable": int(rows["measurable"].sum()),
            "missing_opportunity": int(rows["opportunity"].isna().sum()),
            "missing_persistence": int(rows["persistence"].isna().sum()),
            "missing_both_features": int(rows[["opportunity", "persistence"]].isna().all(axis=1).sum()),
        },
        "baseline_context": context, **analysis,
    }
    for block in summary["slices"].values():
        for cell in block["cells"].values():
            cell["attributed_dollars_per_all_symbol_sessions"] = (
                cell["net_dollars"] / context["symbol_sessions"]
            )
    trial = TrialTag(
        programme=protocol["programme"], phase="exploration", family="condition",
        label="movement_noise_diagnostic_v1", n_trials=protocol["n_trials"],
    )
    source_files = [
        "scripts/run_movement_noise_study.py",
        "vibe/research_pipeline/movement_noise.py",
        "vibe/common/indicators/efficiency.py",
        "vibe/common/indicators/daily_atr.py",
        "vibe/common/indicators/batch.py",
        "vibe/common/indicators/session.py",
        "vibe/research_pipeline/inference.py",
    ]
    study = {
        "schema_version": 1, "kind": "condition_diagnostic", "evidence_role": "exploration",
        "trial": trial.model_dump(mode="json"), "protocol": protocol,
        "registration_sha256": sha256(registration),
        "source_definition_sha256": study_definition_hash(definition),
        "source_trade_sha256": source_trade_hash, "source_run_ids": [m[0] for m in members],
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_file_sha256": {p: sha256(ROOT / p) for p in source_files},
        "source_data_sha256": baseline["data_file_sha256"],
        "loaded_exploration": loaded, "lock_hash": lock.lock_hash,
        "start": protocol["start"], "end": protocol["end"],
        "symbols": protocol["symbols"], "execution_identity": definition["execution_identity"],
    }
    study["definition_sha256"] = study_definition_hash(study)
    forbidden = forbidden_payload_path(summary)
    if forbidden is not None:
        raise ValueError(f"Diagnostic aggregate violates publication boundary: {forbidden}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Persist reproducible private diagnostics first; no raw-trade publication.
    rows.to_csv(args.output_dir / "movement-noise-trades.csv", index=False)
    summary_path = args.output_dir / "movement-noise-results.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    store = SqliteResearchStore(args.research_db)
    try:
        experiment_id = store.start_experiment(protocol["title"], study)
        store.finish_experiment(experiment_id, "completed", summary)
    finally:
        store.close()
    manifest = {
        "experiment_id": experiment_id, "definition": study,
        "results_sha256": sha256(summary_path),
        "trade_csv_sha256": sha256(args.output_dir / "movement-noise-trades.csv"),
    }
    (args.output_dir / "movement-noise-manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "experiment_id": experiment_id, "registration_sha256": sha256(registration),
        "coverage": summary["coverage"],
        "contrasts": summary["slices"]["all"]["contrasts"],
        "triage": summary["triage"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
