"""Durable checksums over a run's trade ledger and equity curve.

Section 7 of the backtest pipeline plan requires every run to persist a
``run_evidence`` row under *every* retention profile, including ``summary``,
where trades and equity points are discarded. Two of the required fields are
``trade_ledger_sha256`` and ``equity_curve_sha256``.

Those checksums are what make the reproducibility claim testable. Without
them, "a run must be re-executable from stored configuration alone" is an
assertion nothing can check: the evidence needed to verify it is exactly the
evidence the retention policy throws away. With them, a run can be replayed
from its fingerprint and the recomputed digest compared against the stored
one, so the ``diagnostic`` profile is needed only to *diagnose* a mismatch,
never to detect it.

The digests deliberately cover the **simulation**, not the metrics. Entry and
exit prices, quantities, timestamps, realized P&L, and costs are included;
derived statistics such as win rate or Sharpe are not. A change that only
redefines how a metric is computed must leave both digests untouched, while
any change to what the simulator actually did must alter them. That split is
what lets a metric-normalization change be reviewed as metric-only rather
than taken on trust.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from vibe.research_pipeline.hashing import hash_object

__all__ = [
    "LEDGER_DIGEST_VERSION",
    "TRADE_DIGEST_FIELDS",
    "equity_curve_digest",
    "trade_ledger_digest",
]

# Bump when the field list or ordering below changes, so that digests computed
# under different rules are recognizable as incomparable rather than as a
# behavioural regression.
LEDGER_DIGEST_VERSION = 1

# Ordered, explicit, and closed on purpose. A digest over ``model_dump()``
# would silently change meaning the moment a field was added to ``Trade``,
# turning an unrelated model edit into an apparent simulation change.
#
# ``trade_id`` is excluded: the backtester never assigns one, and a future
# identifier (uuid, row number) would be incidental to the simulation rather
# than part of it.
TRADE_DIGEST_FIELDS: tuple[str, ...] = (
    "symbol",
    "side",
    "quantity",
    "entry_price",
    "exit_price",
    "entry_time",
    "exit_time",
    "pnl",
    "commission",
    "initial_risk",
    "exit_reason",
)


def trade_ledger_digest(
    trades: Sequence[Any], *, fields: Sequence[str] = TRADE_DIGEST_FIELDS
) -> str:
    """Return a SHA-256 over the ordered trade ledger.

    Order is significant and preserved as given. Two runs producing the same
    trades in a different sequence are not the same run: ordering reflects the
    event stream, and reordering usually means a scheduling or merge defect.

    Args:
        trades: Completed trades, in execution order.
        fields: Attribute names to include. Defaults to
            :data:`TRADE_DIGEST_FIELDS`.

    Returns:
        Hex-encoded SHA-256 digest. An empty ledger hashes the empty list
        rather than returning a sentinel, so "ran and produced no trades"
        stays distinguishable from "did not run".
    """
    payload = {
        "digest_version": LEDGER_DIGEST_VERSION,
        "fields": list(fields),
        "rows": [
            [getattr(trade, name, None) for name in fields] for trade in trades
        ],
    }
    return hash_object(payload)


def equity_curve_digest(points: Iterable[Any]) -> str:
    """Return a SHA-256 over an equity curve.

    Accepts either a pandas Series indexed by timestamp or an iterable of
    ``(timestamp, value)`` pairs, because the engine carries the curve in both
    shapes at different stages and a digest that disagreed between them would
    report a phantom change.

    Args:
        points: Equity observations in chronological order.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    if hasattr(points, "items") and hasattr(points, "index"):
        pairs: list[tuple[Any, Any]] = list(points.items())
    else:
        pairs = [tuple(pair) for pair in points]  # type: ignore[misc]

    payload = {
        "digest_version": LEDGER_DIGEST_VERSION,
        "rows": [[timestamp, float(value)] for timestamp, value in pairs],
    }
    return hash_object(payload)
