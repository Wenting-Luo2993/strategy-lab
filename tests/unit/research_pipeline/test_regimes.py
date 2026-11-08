from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from vibe.research_pipeline.contracts import SegmentRole
from vibe.research_pipeline.regimes import build_orb_regime_breakdown


def _segment(role, start, end):
    return SimpleNamespace(
        role=role,
        start_session=start,
        end_session=end,
    )


def test_regime_breakdown_uses_prior_information_and_training_threshold():
    sessions = pd.bdate_range("2024-01-02", periods=90, tz="America/New_York")
    closes = [100 + index * 0.2 for index in range(len(sessions))]
    bars = pd.DataFrame(
        {
            "high": [value + 1 + (index % 3) for index, value in enumerate(closes)],
            "low": [value - 1 - (index % 3) for index, value in enumerate(closes)],
            "close": closes,
        },
        index=[stamp.replace(hour=15, minute=55) for stamp in sessions],
    )
    train_start = sessions[20].date()
    train_end = sessions[69].date()
    test_dates = [sessions[75].date(), sessions[80].date()]
    trades = (
        SimpleNamespace(
            entry_time=datetime.combine(test_dates[0], time(10), tzinfo=ZoneInfo("America/New_York")),
            side="buy",
            pnl=200.0,
            commission=0.0,
            initial_risk=100.0,
        ),
        SimpleNamespace(
            entry_time=datetime.combine(test_dates[1], time(10), tzinfo=ZoneInfo("America/New_York")),
            side="sell",
            pnl=-100.0,
            commission=0.0,
            initial_risk=100.0,
        ),
    )
    study = SimpleNamespace(folds=(
        SimpleNamespace(
            test_result=SimpleNamespace(
                result=SimpleNamespace(trades=trades),
            ),
        ),
    ))
    plan = SimpleNamespace(folds=(
        SimpleNamespace(segments=(
            _segment(SegmentRole.TRAIN, train_start, train_end),
            _segment(SegmentRole.TEST, test_dates[0], test_dates[-1]),
        )),
    ))

    result = build_orb_regime_breakdown(
        study,
        plan,
        bars,
        trend_sessions=10,
        volatility_sessions=5,
    )

    assert sum(item["n_trades"] for item in result.observations) == 2
    assert sum(result.return_contributions_r.values()) == 1.0
    assert result.unclassified_trades == 0
    assert {item["dimension"] for item in result.definitions} == {
        "trend_alignment",
        "prior_volatility",
    }
