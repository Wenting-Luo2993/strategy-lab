"""Daily aggregates preserve source evidence and refuse partial retention."""

import json
import sqlite3

import pytest

from vibe.research_pipeline.daily_series import build_daily_series
from vibe.research_pipeline.publishing import (
    ResearchHttpResponse, ResearchSupabaseSettings, SupabaseResearchDestination,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "research.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE research_experiments(experiment_id TEXT, state TEXT);
            CREATE TABLE research_experiment_runs(experiment_id TEXT, run_id TEXT, fold_index INTEGER, candidate_id TEXT, segment_role TEXT);
            CREATE TABLE runs(run_id TEXT, display_alias TEXT);
            CREATE TABLE run_evidence(id INTEGER, run_id TEXT, trade_count INTEGER, equity_points INTEGER, payload_json TEXT, trade_ledger_sha256 TEXT, equity_curve_sha256 TEXT);
            CREATE TABLE run_trades(run_id TEXT, sequence INTEGER, symbol TEXT, entry_time TEXT, pnl REAL, commission REAL, initial_risk REAL);
            CREATE TABLE run_equity_points(run_id TEXT, sequence INTEGER, observed_at TEXT, equity REAL);
            CREATE TABLE run_metrics(run_id TEXT, id INTEGER, metric_key TEXT, metric_value REAL);
            INSERT INTO research_experiments VALUES ('study', 'completed');
            INSERT INTO research_experiment_runs VALUES ('study', 'run', 0, 'rule', 'train');
            INSERT INTO runs VALUES ('run', 'RUN-1');
            INSERT INTO run_trades VALUES ('run', 0, 'QQQ', '2026-01-03T01:00:00+00:00', 11, 1, 10);
            INSERT INTO run_trades VALUES ('run', 1, 'QQQ', '2026-01-05T15:00:00+00:00', -9, 1, 10);
            INSERT INTO run_equity_points VALUES ('run', 0, '2026-01-02T15:00:00+00:00', 99);
            INSERT INTO run_equity_points VALUES ('run', 1, '2026-01-02T21:00:00+00:00', 110);
        """)
        db.execute("INSERT INTO run_evidence VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (1, "run", 2, 2, json.dumps({"starting_equity": 100}), "a" * 64, "b" * 64))
    return path


def test_daily_net_r_timezone_equity_last_observation_and_immutable_source(db_path):
    before = db_path.read_bytes()
    row = build_daily_series(db_path, "study")
    run = row["payload"]["runs"][0]
    assert run["dailyR"] == [{"date": "2026-01-02", "value": 1}, {"date": "2026-01-05", "value": -1}]
    assert run["dailyEquity"] == [{"date": "2026-01-02", "value": 110}]
    assert run["profitFactor"] == 1
    assert row == build_daily_series(db_path, "study")
    assert db_path.read_bytes() == before
    assert set(run).isdisjoint({"trades", "entry_price", "quantity", "fills"})


def test_missing_equity_is_explicit_and_partial_trades_refused(db_path):
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM run_equity_points")
    run = build_daily_series(db_path, "study")["payload"]["runs"][0]
    assert run["equityAvailable"] is False
    assert run["dailyEquity"] == []
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM run_trades WHERE sequence = 1")
    with pytest.raises(ValueError, match="Incomplete retained trades"):
        build_daily_series(db_path, "study")

def test_closed_equity_uses_exits_net_costs_and_complete_daily_price_calendar(db_path, tmp_path):
    import pandas as pd
    with sqlite3.connect(db_path) as db:
        db.execute("ALTER TABLE run_trades ADD COLUMN exit_time TEXT")
        db.execute("UPDATE run_trades SET exit_time = '2026-01-05T20:00:00+00:00' WHERE sequence = 0")
        db.execute("UPDATE run_trades SET exit_time = '2026-01-06T20:00:00+00:00' WHERE sequence = 1")
    index = pd.DatetimeIndex(["2026-01-02T20:59:00Z", "2026-01-05T20:59:00Z",
                             "2026-01-06T20:59:00Z", "2026-01-07T20:59:00Z"], name="ts_event")
    pd.DataFrame({"close": [100, 102, 105, 999]}, index=index).to_parquet(tmp_path / "QQQ.parquet")
    before = db_path.read_bytes()
    run = build_daily_series(db_path, "study", tmp_path)["payload"]["runs"][0]
    assert run["dailyPrices"] == [{"date": "2026-01-02", "value": 100},
                                   {"date": "2026-01-05", "value": 102},
                                   {"date": "2026-01-06", "value": 105}]
    assert run["dailyClosedEquity"] == [{"date": "2026-01-02", "value": 100},
                                         {"date": "2026-01-05", "value": 110},
                                         {"date": "2026-01-06", "value": 100}]
    assert len(run["priceChecksum"]) == 64
    assert db_path.read_bytes() == before


@pytest.mark.asyncio
async def test_daily_publication_requires_matching_acknowledgement(db_path):
    row = build_daily_series(db_path, "study")

    class Transport:
        async def request(self, method, url, **kwargs):
            assert method == "POST"
            assert url.endswith("/research_experiment_daily_series")
            assert kwargs["params"] == {"on_conflict": "experiment_id"}
            return ResearchHttpResponse(200, [kwargs["json_body"]])

    destination = SupabaseResearchDestination(
        ResearchSupabaseSettings(url="https://example.supabase.co", service_role_key="test-only"),
        transport=Transport(),
    )
    await destination.publish_daily_series(row)

    class WrongAcknowledgement(Transport):
        async def request(self, *args, **kwargs):
            return ResearchHttpResponse(200, [])

    destination.transport = WrongAcknowledgement()
    with pytest.raises(RuntimeError, match="acknowledgement"):
        await destination.publish_daily_series(row)
