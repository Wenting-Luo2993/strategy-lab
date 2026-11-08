"""Tests for run evidence checksums."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from vibe.common.models.trade import Trade
from vibe.research_pipeline.evidence import (
    LEDGER_DIGEST_VERSION,
    TRADE_DIGEST_FIELDS,
    equity_curve_digest,
    trade_ledger_digest,
)


def _trade(**overrides) -> Trade:
    base = dict(
        symbol="QQQ",
        side="buy",
        quantity=10.0,
        entry_price=100.0,
        exit_price=101.0,
        entry_time=datetime(2022, 3, 1, 14, 30, tzinfo=timezone.utc),
        exit_time=datetime(2022, 3, 1, 20, 0, tzinfo=timezone.utc),
        pnl=10.0,
        initial_risk=5.0,
        exit_reason="STOP",
    )
    base.update(overrides)
    return Trade(**base)


class TestTradeLedgerDigest:
    def test_is_deterministic(self):
        trades = [_trade(), _trade(entry_price=102.0)]
        assert trade_ledger_digest(trades) == trade_ledger_digest(trades)

    def test_distinguishes_a_changed_price(self):
        a = trade_ledger_digest([_trade()])
        b = trade_ledger_digest([_trade(entry_price=100.01)])
        assert a != b

    def test_distinguishes_a_changed_pnl(self):
        """P&L is derived from prices, so drive it through the exit price."""
        a = trade_ledger_digest([_trade(exit_price=101.0)])
        b = trade_ledger_digest([_trade(exit_price=101.5)])
        assert a != b

    def test_pnl_is_derived_not_supplied(self):
        """Pins a Trade invariant the digest depends on.

        Trade recomputes pnl from entry/exit price and quantity, discarding
        any supplied value. A digest field list that assumed pnl was
        independently settable would be testing nothing.
        """
        supplied = _trade(exit_price=101.0, pnl=999.0)
        assert supplied.pnl == 10.0

    def test_commission_participates(self):
        """E4 adds costs; that is a simulation change and must show up."""
        a = trade_ledger_digest([_trade(commission=0.0)])
        b = trade_ledger_digest([_trade(commission=1.25)])
        assert a != b

    def test_order_is_significant(self):
        first, second = _trade(), _trade(entry_price=102.0)
        assert (
            trade_ledger_digest([first, second])
            != trade_ledger_digest([second, first])
        )

    def test_empty_ledger_hashes_rather_than_sentinel(self):
        """'Ran, no trades' must stay distinct from 'did not run'."""
        digest = trade_ledger_digest([])
        assert isinstance(digest, str) and len(digest) == 64
        assert digest != trade_ledger_digest([_trade()])

    def test_trade_id_is_excluded(self):
        """An incidental identifier is not part of the simulation."""
        a = trade_ledger_digest([_trade(trade_id=None)])
        b = trade_ledger_digest([_trade(trade_id="abc-123")])
        assert a == b

    def test_ignores_fields_outside_the_declared_set(self):
        """A new Trade field must not silently alter historical digests."""
        a = trade_ledger_digest([_trade(strategy=None)])
        b = trade_ledger_digest([_trade(strategy="orb")])
        assert a == b
        assert "strategy" not in TRADE_DIGEST_FIELDS

    def test_missing_attribute_does_not_raise(self):
        class Bare:
            symbol = "QQQ"

        assert isinstance(trade_ledger_digest([Bare()]), str)

    def test_version_is_part_of_the_payload(self):
        assert LEDGER_DIGEST_VERSION == 1


class TestEquityCurveDigest:
    def test_series_and_pairs_agree(self):
        index = pd.DatetimeIndex(
            [datetime(2022, 3, 1, 14, 30, tzinfo=timezone.utc),
             datetime(2022, 3, 1, 14, 35, tzinfo=timezone.utc)]
        )
        series = pd.Series([100.0, 101.0], index=index)
        pairs = list(zip(index.to_pydatetime(), [100.0, 101.0]))
        assert equity_curve_digest(series) == equity_curve_digest(pairs)

    def test_detects_a_changed_value(self):
        idx = pd.DatetimeIndex([datetime(2022, 3, 1, tzinfo=timezone.utc)])
        a = equity_curve_digest(pd.Series([100.0], index=idx))
        b = equity_curve_digest(pd.Series([100.5], index=idx))
        assert a != b

    def test_detects_a_changed_timestamp(self):
        a = equity_curve_digest(
            pd.Series([100.0], index=pd.DatetimeIndex(
                [datetime(2022, 3, 1, tzinfo=timezone.utc)]))
        )
        b = equity_curve_digest(
            pd.Series([100.0], index=pd.DatetimeIndex(
                [datetime(2022, 3, 2, tzinfo=timezone.utc)]))
        )
        assert a != b

    def test_empty_curve(self):
        assert len(equity_curve_digest([])) == 64

    def test_integer_values_compare_equal_to_floats(self):
        idx = pd.DatetimeIndex([datetime(2022, 3, 1, tzinfo=timezone.utc)])
        assert (
            equity_curve_digest(pd.Series([100], index=idx))
            == equity_curve_digest(pd.Series([100.0], index=idx))
        )
