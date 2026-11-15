"""Derived, bounded daily research read model; never changes source evidence."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_RUNS = 200
MAX_POINTS = 50_000
MAX_BYTES = 2 * 1024 * 1024
MARKET_ZONE = ZoneInfo("America/New_York")


def _date(value: str) -> str:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Research series timestamps must be timezone-aware")
    return stamp.astimezone(MARKET_ZONE).date().isoformat()


def _number(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite research series value")
    return number


def _prices(directory: Path | None, symbol: str, start: str, end: str) -> tuple[list, str]:
    if directory is None:
        return [], ""
    if not symbol.isalnum():
        raise ValueError("Unsupported benchmark symbol")
    path = directory / f"{symbol}.parquet"
    if not path.is_file():
        return [], ""
    import pandas as pd
    first = datetime.fromisoformat(start).replace(tzinfo=MARKET_ZONE)
    last = datetime.fromisoformat(end).replace(tzinfo=MARKET_ZONE) + timedelta(days=1)
    frame = pd.read_parquet(path, columns=["close"], filters=[
        ("ts_event", ">=", first), ("ts_event", "<", last),
    ]).sort_index()
    if frame.index.tz is None:
        raise ValueError("Benchmark timestamps must be timezone-aware")
    clock = frame.index.tz_convert(MARKET_ZONE)
    minutes = clock.hour * 60 + clock.minute
    frame = frame.loc[(minutes >= 570) & (minutes < 960)]
    dates = frame.index.tz_convert(MARKET_ZONE).strftime("%Y-%m-%d")
    closes = frame.groupby(dates)["close"].last()
    points = [{"date": date, "value": _number(value)} for date, value in closes.items()]
    if any(p["value"] <= 0 for p in points):
        raise ValueError("Benchmark close must be positive")
    return points, hashlib.sha256(path.read_bytes()).hexdigest()


def build_daily_series(db_path: Path, experiment_id: str, price_dir: Path | None = None) -> dict:
    """Publishable daily aggregates keyed by explicit experiment membership.

    R is net of commission and attributed to the entry's market session, matching
    the local viewer. Equity uses the last retained observation of each session;
    it is not an intraday drawdown series. A separate closed-trade equity curve
    uses complete retained exit sessions and the recorded starting capital.
    """
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        experiment = db.execute(
            "SELECT state FROM research_experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        if not experiment or experiment["state"] == "running":
            raise ValueError("Daily series requires an existing finalized experiment")
        columns = {r[1] for r in db.execute("PRAGMA table_info(research_experiments)")}
        definition = json.loads(db.execute(
            "SELECT definition_json FROM research_experiments WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()[0]) if "definition_json" in columns else {}
        trade_columns = {r[1] for r in db.execute("PRAGMA table_info(run_trades)")}
        exit_column = "exit_time" if "exit_time" in trade_columns else "NULL AS exit_time"
        members = db.execute(
            """SELECT e.*, r.display_alias FROM research_experiment_runs e
               JOIN runs r USING (run_id) WHERE experiment_id = ?
               ORDER BY fold_index, segment_role, candidate_id""",
            (experiment_id,),
        ).fetchall()
        if len(members) > MAX_RUNS:
            raise ValueError("Experiment exceeds daily-series member limit")
        runs = []
        total_points = 0
        for member in members:
            run_id = member["run_id"]
            evidence = db.execute(
                "SELECT * FROM run_evidence WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not evidence:
                raise ValueError(f"Missing retained evidence for run {run_id}")
            daily = defaultdict(float)
            realized = defaultdict(float)
            exits_complete = True
            wins = losses = 0.0
            symbols = set()
            trade_count = 0
            for trade in db.execute(
                f"""SELECT symbol, entry_time, pnl, commission, initial_risk, {exit_column}
                   FROM run_trades WHERE run_id = ? ORDER BY sequence""", (run_id,),
            ):
                pnl = _number(trade["pnl"]) - _number(trade["commission"])
                risk = _number(trade["initial_risk"])
                if risk <= 0:
                    raise ValueError(f"Invalid initial risk in run {run_id}")
                daily[_date(trade["entry_time"])] += pnl / risk
                if trade["exit_time"] is None:
                    exits_complete = False
                else:
                    realized[_date(trade["exit_time"])] += pnl
                wins += max(pnl, 0)
                losses += max(-pnl, 0)
                symbols.add(trade["symbol"])
                trade_count += 1
            if trade_count and trade_count != evidence["trade_count"]:
                raise ValueError(f"Incomplete retained trades in run {run_id}")
            equity = {}
            equity_count = 0
            for point in db.execute(
                """SELECT observed_at, equity FROM run_equity_points
                   WHERE run_id = ? ORDER BY observed_at, sequence""", (run_id,),
            ):
                value = _number(point["equity"])
                if value <= 0:
                    raise ValueError(f"Non-positive equity in run {run_id}")
                equity[_date(point["observed_at"])] = value
                equity_count += 1
            if equity_count and equity_count != evidence["equity_points"]:
                raise ValueError(f"Incomplete retained equity in run {run_id}")
            metadata = json.loads(evidence["payload_json"])
            capital = metadata.get("starting_equity")
            prices, price_checksum = [], ""
            dates = sorted(set(daily) | set(realized))
            if len(symbols) == 1 and dates:
                prices, price_checksum = _prices(price_dir, next(iter(symbols)),
                    definition.get("start", dates[0]), definition.get("end", dates[-1]))
            closed = []
            if exits_complete and trade_count == evidence["trade_count"] and capital is not None and _number(capital) > 0:
                balance = _number(capital)
                for date in sorted(set(realized) | {p["date"] for p in prices}):
                    balance += realized[date]
                    closed.append({"date": date, "value": balance})
            total_points += len(set(daily) | {p["date"] for p in prices}) + len(equity) + len(prices) + len(closed)
            if total_points > MAX_POINTS:
                raise ValueError("Experiment exceeds daily-series point limit")
            runs.append({
                "runId": run_id,
                "label": ", ".join(sorted(symbols)) or member["display_alias"],
                "candidateId": member["candidate_id"],
                "foldIndex": member["fold_index"],
                "segmentRole": member["segment_role"],
                "tradeCount": evidence["trade_count"],
                "profitFactor": wins / losses if losses else None,
                "netExpectancyR": sum(daily.values()) / trade_count if trade_count else None,
                "metrics": dict(db.execute(
                    """SELECT metric_key, metric_value FROM run_metrics
                       WHERE run_id = ? ORDER BY id""", (run_id,),
                ).fetchall()),
                "startingEquity": metadata.get("starting_equity"),
                "tradeChecksum": evidence["trade_ledger_sha256"],
                "equityChecksum": evidence["equity_curve_sha256"],
                "dailyR": [{"date": date, "value": daily.get(date, 0.0)}
                           for date in sorted(set(daily) | {p["date"] for p in prices})],
                "dailyEquity": [{"date": date, "value": value} for date, value in sorted(equity.items())],
                "dailyClosedEquity": closed,
                "dailyPrices": prices,
                "priceChecksum": price_checksum,
                "priceBasis": "Regular-session source closes; price-ratio benchmark, no explicit dividends or benchmark fees.",
                "closedEquityBasis": "Starting capital plus retained net P&L at exit sessions; not intraday mark-to-market.",
                "rAvailable": trade_count == evidence["trade_count"],
                "equityAvailable": equity_count > 0,
            })
    payload = {"version": 1, "experimentId": experiment_id,
               "basis": "entry-session net R; last retained session equity", "runs": runs}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_BYTES:
        raise ValueError("Experiment exceeds daily-series byte limit")
    return {"experiment_id": experiment_id, "schema_version": 1, "payload": payload,
            "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = build_daily_series(args.db, args.experiment, args.prices)["payload"]
    encoded = json.dumps(payload, allow_nan=False)
    if args.output:
        from tempfile import NamedTemporaryFile
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent, delete=False) as handle:
            handle.write(encoded)
            temporary = Path(handle.name)
        try:
            temporary.replace(args.output)
        finally:
            if temporary.exists():
                temporary.unlink()
        print(f"Exported {args.experiment}: {len(payload['runs'])} members")
    else:
        print(encoded)
