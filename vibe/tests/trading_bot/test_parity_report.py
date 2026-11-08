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
    FILL_LATE_PLACEMENT,
    FILL_MARKET_GAP,
    FILL_NORMAL,
    MISMATCH_KINDS,
    LiveTrade,
    backtest_session,
    build_report,
    compare_symbol,
    dedupe_executions,
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
def replay():
    return backtest_session(_ruleset(), "AAA", session_minutes(), E0, DAY)


@pytest.fixture(scope="module")
def backtest_trade(replay):
    (trade,) = replay.trades
    return trade


# Live sizes at its 101.00 trigger: the 20% symbol cap gives floor(20000 / 101.00)
# = 198 shares. The backtest sizes at its 101.02 fill: floor(20000 / 101.02) = 197.
LIVE_QTY = 198


def _live_like(trade) -> LiveTrade:
    """A live round trip doing what the backtest did, sized as live sizes."""
    stop = 99.0
    return LiveTrade(
        symbol="AAA",
        side=trade.side,
        entry_time=trade.entry_time + timedelta(seconds=12),
        entry_price=trade.entry_price,
        quantity=LIVE_QTY,
        stop_price=stop,
        exit_time=trade.exit_time,
        exit_price=trade.exit_price,
        exit_reason="EOD",
        trigger_price=101.0,
        gap_fill=False,
        placement_latency_s=3.0,
        capped_by="max_symbol_exposure",
    )


def _report(live, replays, symbols=("AAA",), errors=None):
    return build_report(
        DAY, list(symbols), live, replays, errors, ruleset=_ruleset(), session_equity=E0
    )


def test_synthetic_day_matches(replay, backtest_trade):
    assert backtest_trade.side == "buy"
    assert backtest_trade.entry_price == pytest.approx(101.02)
    assert backtest_trade.exit_reason == "EOD"
    assert backtest_trade.quantity == 197
    assert replay.sizing_caps == {"max_symbol_exposure": 1}
    assert (replay.or_high, replay.or_low) == (101.0, 99.0)

    report = _report([_live_like(backtest_trade)], {"AAA": replay})

    assert report.matched == 1 and report.mismatched == []
    details = report.symbols[0].details
    assert details["expected_live_qty"] == LIVE_QTY and details["backtest_qty"] == 197
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
def test_each_mismatch_kind_is_reported(replay, backtest_trade, kind, change):
    live = change(_live_like(backtest_trade))

    result = compare_symbol(
        "AAA", live, backtest_trade, backtest_level=101.0,
        backtest_sizing_caps=replay.sizing_caps, ruleset=_ruleset(), session_equity=E0,
    )

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


def test_report_headline_counts_normal_fills_only(replay, backtest_trade):
    fast = _live_like(backtest_trade)
    # BBB filled as a gap live while the backtest filled at the level: the
    # order was not resting at the breakout, so it is a late placement.
    late = replace(fast, symbol="BBB", entry_price=101.30, gap_fill=True)
    report = _report(
        [fast, late], {"AAA": replay, "BBB": replay}, symbols=("AAA", "BBB", "CCC"),
        errors={"CCC": "no bars"},
    )

    assert report.entry_slippage_ticks == [pytest.approx(2.0)]
    assert report.median_slippage_ticks == pytest.approx(2.0)
    assert report.gap_fills == 0 and report.late_placements == 1
    assert report.late_placement_slippage_ticks == [pytest.approx(30.0)]
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
        config=SimpleNamespace(
            notifications=SimpleNamespace(discord_webhook_url="https://x"),
            database_path=str(tmp_path / "trades.db"),
        ),
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
    assert sent[0].normal_fills == 1 and sent[0].late_placements == 0
    saved = tmp_path / "parity_reports" / "bars" / "AAA_2026-07-20.csv"
    assert saved.is_file() and len(pd.read_csv(saved)) == len(session_minutes())


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
    report = build_report(DAY, ["AAA", "BBB"], [_live_like(backtest_trade)], {"AAA": [backtest_trade]})

    orchestrator.record_parity_report(report)

    saved = json.loads((tmp_path / "parity_reports" / "2026-07-20.json").read_text(encoding="utf-8"))
    assert saved["matched"] == 1
    names = {m["metric_name"]: m for m in metrics}
    assert names["parity_matched"]["metric_value"] == 1.0
    assert names["parity_errors"]["metric_value"] == 0.0
    assert names["parity_entry_slippage_ticks_mean"]["metric_value"] == pytest.approx(2.0)
    symbol_rows = [m for m in metrics if m["metric_name"] == "parity_symbol_matched"]
    # BBB traded on neither side: no per-symbol row.
    assert [m["dimensions"]["symbol"] for m in symbol_rows] == ["AAA"]
    assert len({m["idempotency_key"] for m in metrics}) == len(metrics)

    metrics.clear()
    errored = build_report(DAY, ["AAA"], [], {}, errors={"AAA": "no bars"})
    orchestrator.record_parity_report(errored)
    names = {m["metric_name"]: m for m in metrics}
    assert names["parity_errors"]["metric_value"] == 1.0
    assert "parity_symbol_matched" not in names
    orchestrator.trade_store.close()


def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "live_backtest_parity",
        Path(__file__).resolve().parents[3] / "scripts" / "live_backtest_parity.py",
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def _journal(tmp_path, backtest_trade, symbol="QQQ", extra=()):
    from vibe.trading_bot.storage.ib_execution_store import IBExecutionStore

    live = _live_like(backtest_trade)
    store = IBExecutionStore(str(tmp_path / "ib.db"))
    rows = [
        {
            "execution_id": "e1", "broker_order_id": "1", "symbol": symbol, "side": "buy",
            "quantity": live.quantity, "price": live.entry_price, "account_id": "DU1",
            "filled_at": live.entry_time.astimezone(timezone.utc).isoformat(),
            "order_metadata": {
                "order_role": "entry", "benchmark_price": 101.0, "strategy_stop_price": 99.0,
            },
        },
        {
            "execution_id": "x1", "broker_order_id": "2", "symbol": symbol, "side": "sell",
            "quantity": live.quantity, "price": live.exit_price, "account_id": "DU1",
            "filled_at": live.exit_time.astimezone(timezone.utc).isoformat(),
            "order_metadata": {"order_role": "eod", "exit_reason": "time_exit"},
        },
        *extra,
    ]
    for row in rows:
        store.upsert_execution(row)
    store.close()
    return tmp_path / "ib.db"


def _args(db, *extra):
    return [
        "--start", "2026-07-20", "--end", "2026-07-20",
        "--ruleset", "orb_resting_stop_paper",
        "--executions-db", str(db), "--session-equity", str(E0), *extra,
    ]


def test_offline_script_recomputes_reports_from_parquet(tmp_path, backtest_trade, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    frame = session_minutes()
    frame.index.name = "ts_event"
    frame.to_parquet(data_dir / "QQQ.parquet")
    output = tmp_path / "parity.json"

    code = _script().main(
        _args(_journal(tmp_path, backtest_trade), "--data-dir", str(data_dir),
              "--output", str(output))
    )

    assert code == 0
    (report,) = json.loads(output.read_text(encoding="utf-8"))
    assert report["matched"] == 1 and report["mismatched"] == 0
    assert report["bars_source"] == "parquet"
    assert "not comparable to in-bot report" in report["label"]
    out = capsys.readouterr().out
    assert "matched=1" in out and "not comparable to in-bot report" in out
    assert "normal_fills=1" in out


def test_offline_script_replays_saved_ibkr_bars(tmp_path, backtest_trade, capsys):
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    frame = session_minutes()
    frame.index.name = "timestamp"
    frame.to_csv(bars_dir / "QQQ_2026-07-20.csv")
    output = tmp_path / "parity.json"

    code = _script().main(
        _args(_journal(tmp_path, backtest_trade), "--ib-bars-dir", str(bars_dir),
              "--output", str(output))
    )

    assert code == 0
    (report,) = json.loads(output.read_text(encoding="utf-8"))
    assert report["matched"] == 1 and report["bars_source"] == "ibkr"
    assert "label" not in report
    assert "not comparable" not in capsys.readouterr().out


def test_offline_script_requires_a_ruleset(tmp_path, backtest_trade):
    args = [a for a in _args(_journal(tmp_path, backtest_trade)) if a not in (
        "--ruleset", "orb_resting_stop_paper")]
    with pytest.raises(SystemExit):
        _script().main(args)


def test_offline_script_rejects_symbols_outside_the_ruleset(tmp_path, backtest_trade, capsys):
    foreign = {
        "execution_id": "f1", "broker_order_id": "9", "symbol": "TSLA", "side": "buy",
        "quantity": 1, "price": 250.0, "account_id": "DU1",
        "filled_at": _ts("10:00").astimezone(timezone.utc).isoformat(),
        "order_metadata": {"order_role": "entry"},
    }
    with pytest.raises(SystemExit):
        _script().main(_args(_journal(tmp_path, backtest_trade, extra=[foreign])))
    assert "TSLA" in capsys.readouterr().err


def test_offline_script_filters_by_account(tmp_path, backtest_trade, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    frame = session_minutes()
    frame.index.name = "ts_event"
    frame.to_parquet(data_dir / "QQQ.parquet")
    other = {
        "execution_id": "o1", "broker_order_id": "8", "symbol": "QQQ", "side": "buy",
        "quantity": 7, "price": 101.05, "account_id": "DU2",
        "filled_at": _ts("09:41").astimezone(timezone.utc).isoformat(),
        "order_metadata": {"order_role": "entry", "benchmark_price": 101.0},
    }
    db = _journal(tmp_path, backtest_trade, extra=[other])

    with pytest.raises(SystemExit):
        _script().main(_args(db, "--data-dir", str(data_dir)))
    assert "--account" in capsys.readouterr().err

    output = tmp_path / "parity.json"
    assert _script().main(
        _args(db, "--data-dir", str(data_dir), "--account", "DU1", "--output", str(output))
    ) == 0
    (report,) = json.loads(output.read_text(encoding="utf-8"))
    assert report["matched"] == 1


# ---------------------------------------------------------------------------
# P1: fill classes
# ---------------------------------------------------------------------------

def gapped_session() -> pd.DataFrame:
    """The 09:40 minute opens through the 101.00 OR high at 101.30."""
    df = session_minutes()
    df.loc[_ts("09:40"), ["open", "high", "low", "close"]] = [101.30, 101.5, 101.25, 101.4]
    return df


def test_market_gap_is_measured_against_the_backtest_base_and_kept_out_of_the_headline():
    replay = backtest_session(_ruleset(), "AAA", gapped_session(), E0, DAY)
    (trade,) = replay.trades
    assert trade.entry_price == pytest.approx(101.32)  # gapped open + 2 ticks
    live = replace(_live_like(trade), entry_price=101.31, gap_fill=True, quantity=197)

    report = _report([live], {"AAA": replay})

    assert report.fills == [{
        "symbol": "AAA", "fill_class": FILL_MARKET_GAP,
        "slippage_ticks": pytest.approx(1.0), "reference": "backtest_base",
    }]
    assert report.entry_slippage_ticks == [] and report.mean_slippage_ticks is None
    assert report.gap_fills == 1 and report.mean_gap_slippage_ticks == pytest.approx(1.0)


def test_late_placement_is_its_own_class(backtest_trade, replay):
    slow = replace(_live_like(backtest_trade), placement_latency_s=40.0)

    report = _report([slow], {"AAA": replay})

    assert report.fills[0]["fill_class"] == FILL_LATE_PLACEMENT
    assert report.late_placements == 1 and report.entry_slippage_ticks == []


def test_normal_fill_is_the_headline(backtest_trade, replay):
    report = _report([_live_like(backtest_trade)], {"AAA": replay})

    assert report.fills[0]["fill_class"] == FILL_NORMAL
    assert report.entry_slippage_ticks == [pytest.approx(2.0)]


# ---------------------------------------------------------------------------
# P2: quantity
# ---------------------------------------------------------------------------

def test_live_qty_is_checked_against_the_sizing_rule_at_its_trigger(backtest_trade, replay):
    copied = replace(_live_like(backtest_trade), quantity=backtest_trade.quantity)  # 197

    result = _report([copied], {"AAA": replay}).symbols[0]

    assert "qty" in result.mismatches  # live should have sized 198 at 101.00
    assert result.details["expected_live_qty"] == LIVE_QTY


def test_book_dependent_live_caps_skip_the_qty_check(backtest_trade, replay):
    squeezed = replace(_live_like(backtest_trade), quantity=120, capped_by="equity_headroom")

    result = _report([squeezed], {"AAA": replay}).symbols[0]

    assert "qty" not in result.mismatches
    assert result.details["live_capped_by"] == "equity_headroom"
    assert result.details["backtest_sizing_caps"] == {"max_symbol_exposure": 1}


def test_raw_qty_tolerates_one_share_under_the_symbol_cap(backtest_trade):
    live = _live_like(backtest_trade)  # 198 vs backtest 197

    capped = compare_symbol(
        "AAA", live, backtest_trade, backtest_sizing_caps={"max_symbol_exposure": 1}
    )
    uncapped = compare_symbol("AAA", replace(live, capped_by=None), backtest_trade)

    assert "qty" not in capped.mismatches
    assert "qty" in uncapped.mismatches


# ---------------------------------------------------------------------------
# P3: bar validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bars, message",
    [
        (session_minutes().iloc[:0], "no bars"),
        (session_minutes(day=date(2026, 7, 17)), "expected 2026-07-20"),
        (session_minutes().iloc[:300], "of 390 RTH minutes"),
        (session_minutes().drop(_ts("09:32")), "opening-range minutes missing: 09:32"),
    ],
)
def test_bad_bars_raise_instead_of_replaying_as_no_trade(bars, message):
    with pytest.raises(ValueError, match=message):
        backtest_session(_ruleset(), "AAA", bars, E0, DAY)


def test_half_day_sessions_validate_against_the_exchange_calendar():
    half_day = date(2026, 11, 27)  # day after Thanksgiving: 13:00 close
    bars = session_minutes(day=half_day)
    bars = bars[bars.index < _ts("13:00", half_day)]

    replay = backtest_session(_ruleset(), "AAA", bars, E0, half_day)

    assert len(replay.trades) == 1


# ---------------------------------------------------------------------------
# P4 / P5: OCA overfill and execution corrections
# ---------------------------------------------------------------------------

def test_oca_double_fill_is_reported_not_merged(backtest_trade, replay):
    entry_meta = {"order_role": "entry", "benchmark_price": 101.0, "strategy_stop_price": 99.0}
    short_meta = {"order_role": "entry", "benchmark_price": 99.0, "strategy_stop_price": 101.0}
    executions = [
        _execution("e1", "buy", LIVE_QTY, 101.02, "09:40", entry_meta),
        _execution("e2", "sell", 50, 98.99, "09:41", short_meta),  # sibling not cancelled in time
        _execution("x1", "sell", LIVE_QTY - 50, 102.0, "15:55",
                   {"order_role": "eod", "exit_reason": "time_exit"}),
    ]

    (trade,) = live_trades_from_executions(executions, DAY)

    assert trade.side == "buy" and trade.quantity == LIVE_QTY
    assert trade.entry_price == pytest.approx(101.02)
    assert trade.oca_overfill_qty == 50
    assert trade.exit_price == pytest.approx(102.0)  # the short entry is not an exit
    assert "oca_overfill" in _report([trade], {"AAA": replay}).symbols[0].mismatches
    assert "oca_overfill" in MISMATCH_KINDS


def test_execution_corrections_count_once():
    meta = {"order_role": "entry", "benchmark_price": 101.0, "strategy_stop_price": 99.0}
    executions = [
        _execution("0000e0d5.6577b2c1.01.01", "buy", 100, 101.05, "09:40", meta),
        _execution("0000e0d5.6577b2c1.01.02", "buy", 100, 101.03, "09:40", meta),
        _execution("0000e0d5.6577b2c1.02.01", "buy", 50, 101.04, "09:40", meta),
    ]

    deduped = dedupe_executions(executions)
    (trade,) = live_trades_from_executions(executions, DAY)

    assert [e["execution_id"] for e in deduped] == [
        "0000e0d5.6577b2c1.01.02", "0000e0d5.6577b2c1.02.01",
    ]
    assert trade.quantity == 150
    assert trade.entry_price == pytest.approx((100 * 101.03 + 50 * 101.04) / 150)
