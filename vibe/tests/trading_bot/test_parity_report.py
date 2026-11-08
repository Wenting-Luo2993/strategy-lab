"""Stage 4: daily live-vs-backtest parity and slippage report."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from vibe.common.ruleset.loader import RuleSetLoader
from vibe.common.ruleset.models import StrategyRuleSet
from vibe.trading_bot.core.market_schedulers import MockMarketScheduler
from vibe.trading_bot.core.phases.cooldown import CooldownPhaseManager
from vibe.trading_bot.notifications.formatter import DiscordNotificationFormatter
from vibe.trading_bot.notifications.payloads import ParityReportPayload
from vibe.trading_bot.parity.report import (
    MISMATCH_KINDS,
    LiveTrade,
    backtest_session,
    build_report,
    compare_symbol,
    live_trades_from_executions,
)

ET = ZoneInfo("America/New_York")
DAY = date(2026, 7, 20)
E0 = 100_000.0


def _ruleset(symbols=("AAA",)) -> StrategyRuleSet:
    data = RuleSetLoader.from_name("orb_resting_stop_paper").model_dump()
    data["instruments"]["symbols"] = list(symbols)
    data["portfolio"]["symbol_priority"] = list(symbols)
    return StrategyRuleSet.model_validate(data)


def _ts(hhmm: str, day: date = DAY) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime(day.year, day.month, day.day, h, m, tzinfo=ET))


def session_minutes(day: date = DAY, park: float = 102.0) -> pd.DataFrame:
    """OR 99-101, a long breakout in the 09:40 minute, then parked."""
    idx = pd.date_range(_ts("09:30", day), _ts("15:59", day), freq="1min")
    df = pd.DataFrame(
        {"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 10_000.0},
        index=idx,
    )
    df.loc[_ts("09:31", day), "high"] = 101.0
    df.loc[_ts("09:33", day), "low"] = 99.0
    df.loc[_ts("09:40", day), ["open", "high", "low", "close"]] = [100.9, 101.5, 100.9, 101.4]
    after = df.index > _ts("09:40", day)
    df.loc[after, ["open", "close"]] = park
    df.loc[after, "high"] = park + 0.05
    df.loc[after, "low"] = park - 0.05
    return df


@pytest.fixture(scope="module")
def backtest_trade():
    (trade,) = backtest_session(_ruleset(), "AAA", session_minutes(), E0)
    return trade


def _live_like(trade) -> LiveTrade:
    """A live round trip that is exactly what the backtest did."""
    stop = 99.0
    return LiveTrade(
        symbol="AAA",
        side=trade.side,
        entry_time=trade.entry_time + timedelta(seconds=12),
        entry_price=trade.entry_price,
        quantity=trade.quantity,
        stop_price=stop,
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        exit_reason="EOD",
        trigger_price=101.0,
        gap_fill=False,
    )


def test_synthetic_day_matches(backtest_trade):
    assert backtest_trade.side == "buy"
    assert backtest_trade.entry_price == pytest.approx(101.02)
    assert backtest_trade.exit_reason == "EOD"

    report = build_report(DAY, ["AAA"], [_live_like(backtest_trade)], {"AAA": [backtest_trade]})

    assert report.matched == 1 and report.mismatched == []
    assert report.entry_slippage_ticks == [pytest.approx(2.0)]
    assert report.mean_slippage_ticks == pytest.approx(2.0)


@pytest.mark.parametrize(
    "kind, change",
    [
        ("side", lambda t: replace(t, side="sell")),
        ("entry_minute", lambda t: replace(t, entry_time=t.entry_time + timedelta(minutes=3))),
        ("entry_price", lambda t: replace(t, entry_price=t.entry_price + 0.25)),
        ("qty", lambda t: replace(t, quantity=t.quantity - 5)),
        ("exit_reason", lambda t: replace(t, exit_reason="STOP")),
        ("r", lambda t: replace(t, exit_price=t.exit_price - 1.0)),
    ],
)
def test_each_mismatch_kind_is_reported(backtest_trade, kind, change):
    live = change(_live_like(backtest_trade))

    result = compare_symbol("AAA", live, backtest_trade)

    assert not result.matched
    assert kind in result.mismatches


def test_taken_on_one_side_only_is_reported(backtest_trade):
    live_only = compare_symbol("AAA", _live_like(backtest_trade), None)
    backtest_only = compare_symbol("AAA", None, backtest_trade)
    neither = compare_symbol("AAA", None, None)

    assert live_only.mismatches == ["taken_live_only"]
    assert backtest_only.mismatches == ["taken_backtest_only"]
    assert neither.matched and neither.details == {"traded": False}
    assert set(MISMATCH_KINDS) >= {"taken_live_only", "taken_backtest_only"}


def _execution(eid, side, qty, price, hhmm, metadata, symbol="AAA"):
    return {
        "execution_id": eid,
        "broker_order_id": eid,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": price,
        "filled_at": _ts(hhmm).astimezone(timezone.utc).isoformat(),
        "order_metadata": metadata,
    }


def test_live_trades_rebuilt_from_executions():
    entry_meta = {
        "order_role": "entry", "benchmark_price": 101.0, "strategy_stop_price": 99.0,
        "gap_fill": 1, "oca_group": "ORB-AAA-1",
    }
    stop_meta = {"order_role": "protective_stop", "benchmark_price": 99.0}
    executions = [
        _execution("e1", "buy", 30, 101.03, "09:40", entry_meta),
        _execution("e2", "buy", 20, 101.05, "09:40", entry_meta),
        _execution("x1", "sell", 50, 98.97, "11:02", stop_meta),
        _execution("o1", "buy", 5, 50.0, "10:00", {}, symbol="ZZZ"),  # not a resting entry
    ]
    yesterday = dict(executions[0], execution_id="old", filled_at="2026-07-17T14:00:00+00:00")

    (trade,) = live_trades_from_executions(executions + [yesterday], DAY)

    assert trade.symbol == "AAA" and trade.side == "buy" and trade.quantity == 50
    assert trade.entry_price == pytest.approx((30 * 101.03 + 20 * 101.05) / 50)
    assert trade.entry_slippage_ticks == pytest.approx(3.8)
    assert trade.gap_fill is True
    assert trade.exit_reason == "STOP"
    assert trade.r_multiple == pytest.approx((98.97 - trade.entry_price) / (trade.entry_price - 99.0))


def test_report_summarises_slippage_and_gap_fills(backtest_trade):
    fast = _live_like(backtest_trade)
    gap = replace(fast, symbol="BBB", entry_price=101.30, gap_fill=True)
    report = build_report(
        DAY, ["AAA", "BBB", "CCC"], [fast, gap], {"AAA": [backtest_trade], "BBB": [backtest_trade]},
        errors={"CCC": "no bars"},
    )

    assert report.entry_slippage_ticks == [pytest.approx(2.0), pytest.approx(30.0)]
    assert report.median_slippage_ticks == pytest.approx(16.0)
    assert report.gap_fills == 1
    assert report.errors == {"CCC": "no bars"}
    assert report.to_dict()["mismatch_counts"] == {"entry_price": 1}


def test_parity_embed_follows_notification_standard():
    payload = ParityReportPayload(
        event_type="PARITY_REPORT",
        timestamp=datetime(2026, 7, 20, 16, 5, tzinfo=ET),
        session_date="2026-07-20",
        matched=4, mismatched=1, mismatch_counts={"qty": 1},
        mismatch_lines=["TSLA: qty (live buy 40 @ 250.1, backtest buy 44 @ 250.08)"],
        entry_fills=5, mean_slippage_ticks=2.6, median_slippage_ticks=2.0,
        modelled_slippage_ticks=2, gap_fills=1, version="v9.9.9",
    )
    embed = DiscordNotificationFormatter().format_parity_report(payload)["embeds"][0]

    assert embed["color"] == 0xF39C12
    assert embed["footer"]["text"] == "Trading Bot v9.9.9"
    assert any("modelled +2" in f["value"] for f in embed["fields"])
    with pytest.raises(ValueError):
        ParityReportPayload(**{**payload.__dict__, "event_type": "NOPE"})


@pytest.mark.asyncio
async def test_cooldown_runs_records_and_sends_the_report(backtest_trade, monkeypatch, tmp_path):
    import vibe.trading_bot.core.phases.cooldown as cooldown_module

    sent = []

    class FakeNotifier:
        async def send_parity_report(self, payload):
            sent.append(payload)
            return True

    @asynccontextmanager
    async def fake_context(_url):
        yield FakeNotifier()

    monkeypatch.setattr(cooldown_module, "discord_notification_context", fake_context)

    live = _live_like(backtest_trade)
    executions = [
        {
            "execution_id": "e1", "broker_order_id": "1", "symbol": "AAA", "side": "buy",
            "quantity": live.quantity, "price": live.entry_price,
            "filled_at": live.entry_time.astimezone(timezone.utc).isoformat(),
            "order_metadata": {
                "order_role": "entry", "benchmark_price": 101.0,
                "strategy_stop_price": 99.0, "gap_fill": 0,
            },
        },
        {
            "execution_id": "x1", "broker_order_id": "2", "symbol": "AAA", "side": "sell",
            "quantity": live.quantity, "price": live.exit_price,
            "filled_at": live.exit_time.astimezone(timezone.utc).isoformat(),
            "order_metadata": {"order_role": "eod", "exit_reason": "time_exit"},
        },
    ]

    class Exchange:
        def list_durable_executions(self):
            return executions

        async def fetch_session_minute_bars(self, symbol, session_date):
            if symbol == "BBB":
                raise TimeoutError("historical data timed out")
            return session_minutes()

    recorded = []
    orchestrator = SimpleNamespace(
        _testing_mode=True,
        config=SimpleNamespace(notifications=SimpleNamespace(discord_webhook_url="https://x")),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 16, 5), timezone="America/New_York"
        ),
        ruleset=_ruleset(("AAA", "BBB")),
        exchange=Exchange(),
        portfolio_risk_manager=SimpleNamespace(session_equity=E0),
        active_symbols=["AAA", "BBB"],
        record_parity_report=recorded.append,
    )

    report = await CooldownPhaseManager(orchestrator)._run_parity_report()

    assert report.matched == 1 and report.mismatched == []
    assert report.errors == {"BBB": "historical data timed out"}
    assert recorded == [report]
    assert len(sent) == 1 and sent[0].matched == 1 and sent[0].entry_fills == 1


@pytest.mark.asyncio
async def test_cooldown_skips_rulesets_without_resting_stops():
    orchestrator = SimpleNamespace(
        _testing_mode=True,
        ruleset=RuleSetLoader.from_name("orb_production"),
        exchange=SimpleNamespace(),
    )
    assert await CooldownPhaseManager(orchestrator)._run_parity_report() is None


def test_orchestrator_persists_report_file_and_metrics(tmp_path, backtest_trade):
    from vibe.trading_bot.config.settings import AppSettings
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator

    orchestrator = TradingOrchestrator(
        config=AppSettings(
            environment="test",
            database_path=str(tmp_path / "trades.db"),
            broker={"broker_type": "mock", "mode": "paper"},
            trading={"symbols": ["AAA"]},
            dashboard={"enabled": False},
            operational_metrics={"enabled": False},
        ),
        ruleset=_ruleset(),
        market_scheduler=MockMarketScheduler(
            initial_date=datetime(2026, 7, 20, 16, 5), timezone="America/New_York"
        ),
        testing_mode=True,
    )
    metrics = []
    orchestrator.operational_metrics_store = SimpleNamespace(
        record_metric=lambda **kwargs: metrics.append(kwargs)
    )
    report = build_report(DAY, ["AAA"], [_live_like(backtest_trade)], {"AAA": [backtest_trade]})

    orchestrator.record_parity_report(report)

    saved = json.loads((tmp_path / "parity_reports" / "2026-07-20.json").read_text(encoding="utf-8"))
    assert saved["matched"] == 1
    names = {m["metric_name"]: m for m in metrics}
    assert names["parity_matched"]["metric_value"] == 1.0
    assert names["parity_entry_slippage_ticks_mean"]["metric_value"] == pytest.approx(2.0)
    assert names["parity_symbol_matched"]["dimensions"]["symbol"] == "AAA"
    assert len({m["idempotency_key"] for m in metrics}) == len(metrics)
    orchestrator.trade_store.close()


def test_offline_script_recomputes_reports(tmp_path, backtest_trade, capsys):
    import importlib.util

    from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore

    spec = importlib.util.spec_from_file_location(
        "live_backtest_parity",
        Path(__file__).resolve().parents[3] / "scripts" / "live_backtest_parity.py",
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    frame = session_minutes()
    frame.index.name = "ts_event"
    frame.to_parquet(data_dir / "QQQ.parquet")
    live = _live_like(backtest_trade)
    store = IBExecutionStore(str(tmp_path / "ib.db"))
    store.upsert_execution({
        "execution_id": "e1", "broker_order_id": "1", "symbol": "QQQ", "side": "buy",
        "quantity": live.quantity, "price": live.entry_price,
        "filled_at": live.entry_time.astimezone(timezone.utc).isoformat(),
        "order_metadata": {
            "order_role": "entry", "benchmark_price": 101.0, "strategy_stop_price": 99.0,
        },
    })
    store.upsert_execution({
        "execution_id": "x1", "broker_order_id": "2", "symbol": "QQQ", "side": "sell",
        "quantity": live.quantity, "price": live.exit_price,
        "filled_at": live.exit_time.astimezone(timezone.utc).isoformat(),
        "order_metadata": {"order_role": "eod", "exit_reason": "time_exit"},
    })
    store.close()
    output = tmp_path / "parity.json"

    code = script.main([
        "--start", "2026-07-20", "--end", "2026-07-20",
        "--executions-db", str(tmp_path / "ib.db"),
        "--session-equity", str(E0),
        "--data-dir", str(data_dir),
        "--output", str(output),
    ])

    assert code == 0
    (report,) = json.loads(output.read_text(encoding="utf-8"))
    assert report["matched"] == 1 and report["mismatched"] == 0
    assert "matched=1" in capsys.readouterr().out
