"""Declared causality class for every feature the research path can compute.

Why this file exists
--------------------

``FeatureEngine`` will happily compute any registered feature and hand it to
``BacktestEngine.run(precomputed_features=...)``. Nothing in that path asks
whether a given column is safe to make decisions with. This registry is the
missing declaration, and :func:`assert_decision_features_are_causal` is the
enforcement point.

The classification below is not a guess. Each ``DIAGNOSTIC`` entry marked as
intraday-leaky was measured with a truncation-equivalence probe against real
QQQ 5-minute bars (2022-01-03..2022-06-30): the feature's value at a mid-session
bar differs depending on whether bars *after* that timestamp were present when
the column was computed. See
:func:`vibe.research_pipeline.features.leakage.check_truncation_equivalence`,
which is the same check run as a test.

The daily-resample trap
-----------------------

``FeatureEngine`` computes some features by resampling intraday bars to daily,
computing on the daily frame, then reindexing back onto the intraday index with
``method="ffill"``. The daily bar for session D summarizes the *whole* of
session D, including bars that have not happened yet at 10:00 on session D. The
forward-fill then stamps that whole-session value onto every intraday bar of
session D.

Features that apply ``.shift(1)`` on the daily frame before reindexing are safe:
session D carries session D-1's value, which is fully observable. Features that
do not are not safe. That single difference is what separates
``prev_day_range`` (causal) from ``adx_14`` (leaky), and it is invisible at the
call site.

``FeatureEngine``'s own module docstring claims "all features are
forward-observable". That claim holds at daily granularity and fails at intraday
granularity, which is the granularity the parameter sweep actually uses.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

from vibe.research_pipeline.contracts import FeatureDeclaration, FeatureKind
from vibe.research_pipeline.hashing import hash_object

# A 5-minute regular-hours session is 78 bars. A feature contaminated by a
# whole-session daily aggregate can, at the first bar of the session, consume up
# to the remaining 77 bars of that session.
_SESSION_BARS_5M = 78


class FeatureNotDeclaredError(KeyError):
    """Raised when a column reaches a decision path without a declaration.

    An undeclared feature is not assumed safe. Silence is not a classification.
    """


class LeakyFeatureInDecisionError(ValueError):
    """Raised when a diagnostic feature is used to make a trading decision."""


def _causal(name: str, lookback: int, description: str) -> FeatureDeclaration:
    return FeatureDeclaration(
        name=name,
        kind=FeatureKind.CAUSAL,
        lookback_bars=lookback,
        lookahead_bars=0,
        description=description,
    )


def _diagnostic(
    name: str, lookback: int, lookahead: int, description: str
) -> FeatureDeclaration:
    return FeatureDeclaration(
        name=name,
        kind=FeatureKind.DIAGNOSTIC,
        lookback_bars=lookback,
        lookahead_bars=lookahead,
        description=description,
    )


_DECLARATIONS: tuple[FeatureDeclaration, ...] = (
    # ---- Volatility -------------------------------------------------------
    _causal(
        "atr_14",
        14,
        "Wilder ATR over 14 bars of the traded timeframe. Computed directly on "
        "the intraday frame, so it never touches a daily aggregate.",
    ),
    _causal(
        "atr_pctile",
        252 + 14,
        "Rank of atr_14 within a trailing 252-bar window. Rolling, not "
        "full-sample: the window ends at the current bar.",
    ),
    _causal(
        "realized_vol",
        21,
        "Rolling 20-bar standard deviation of log returns.",
    ),
    _causal(
        "vol_pctile",
        252 + 21,
        "Rank of realized_vol within a trailing 252-bar window.",
    ),
    _causal(
        "gap_pct",
        1,
        "Open versus previous close. The open is known at the bar it labels.",
    ),
    # ---- Trend ------------------------------------------------------------
    _causal(
        "dist_ma20_pct",
        20,
        "Close versus a 20-bar SMA, computed on the intraday frame.",
    ),
    _causal(
        "dist_ma50_pct",
        50,
        "Close versus a 50-bar SMA, computed on the intraday frame.",
    ),
    _diagnostic(
        "slope_20d",
        20 * _SESSION_BARS_5M,
        _SESSION_BARS_5M - 1,
        "LEAKY INTRADAY. Rolling 20-day OLS slope of daily closes, forward-"
        "filled onto intraday bars with no shift. Session D's value uses "
        "session D's close, so at 10:00 it encodes the not-yet-known close. "
        "Measured: differs under truncation at a mid-session bar. Safe only if "
        "consumed on a daily index with an explicit shift(1).",
    ),
    _diagnostic(
        "slope_50d",
        50 * _SESSION_BARS_5M,
        _SESSION_BARS_5M - 1,
        "LEAKY INTRADAY. Same daily-resample-then-ffill construction as "
        "slope_20d. Measured: fails truncation equivalence.",
    ),
    _diagnostic(
        "adx_14",
        14 * _SESSION_BARS_5M,
        _SESSION_BARS_5M - 1,
        "LEAKY INTRADAY. Wilder ADX on daily bars, forward-filled onto "
        "intraday bars with no shift, so session D's high/low/close leak "
        "backwards into every intraday bar of session D. Measured: differs "
        "under truncation at a mid-session bar.",
    ),
    # ---- Opening behavior -------------------------------------------------
    _causal(
        "or_size_pct",
        1,
        "Opening-range size relative to previous close. Defined only once the "
        "opening range is complete; the ORB boundary fixture pins that the "
        "breakout bar does not contribute to the range it is tested against.",
    ),
    _causal(
        "open_vol_pctile",
        252,
        "Rank of bar volume within a trailing 252-bar window.",
    ),
    _causal(
        "or_expansion",
        252,
        "Opening-range size divided by the *previous* bar's ATR. The shift is "
        "what keeps it causal.",
    ),
    _causal(
        "gap_continuation",
        2,
        "Sign agreement between the gap and the previous bar's body.",
    ),
    # ---- Market context ---------------------------------------------------
    _causal(
        "prev_day_range",
        2 * _SESSION_BARS_5M,
        "Previous session's high-low range. Daily-resampled but shifted by one "
        "session before reindexing, so it only ever exposes completed data.",
    ),
    _causal(
        "prev_day_trend_pct",
        2 * _SESSION_BARS_5M,
        "Previous session's close-versus-open percentage. Shifted by one "
        "session before reindexing.",
    ),
    _causal(
        "prev_close_location",
        2 * _SESSION_BARS_5M,
        "Where the previous session closed within its range. Shifted by one "
        "session before reindexing.",
    ),
    _causal(
        "inside_day",
        2 * _SESSION_BARS_5M,
        "Whether the previous session was an inside day. Shifted by one "
        "session before reindexing.",
    ),
)

FEATURE_REGISTRY: Mapping[str, FeatureDeclaration] = MappingProxyType(
    {d.name: d for d in _DECLARATIONS}
)

#: The exact feature list ``ParameterSweep._precompute_features`` requests.
#: Three of these five are diagnostic, which is the finding P5 exists to surface.
SWEEP_PRECOMPUTED_FEATURES: tuple[str, ...] = (
    "atr_14",
    "atr_pctile",
    "adx_14",
    "slope_20d",
    "slope_50d",
)

#: Columns that are raw market data or bookkeeping rather than derived features.
#: They are exempt from the declaration requirement because they are inputs, not
#: transforms, and a transform is the only thing that can introduce lookahead.
_RAW_COLUMNS: frozenset[str] = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "timestamp",
        "symbol",
        # ORB levels are computed by the strategy under its own boundary rule,
        # which check_orb_boundary pins separately.
        "or_high",
        "or_low",
        # Backward-compatibility alias the sweep adds for the ORB strategy.
        "ATR_14",
    }
)


def declaration_for(name: str) -> FeatureDeclaration:
    """Return the declaration for ``name``, or raise if it was never declared."""
    try:
        return FEATURE_REGISTRY[name]
    except KeyError:
        raise FeatureNotDeclaredError(
            f"Feature {name!r} has no declaration. Every feature that can reach "
            f"a decision must be declared causal or diagnostic in "
            f"vibe/research_pipeline/features/registry.py. Declared features: "
            f"{sorted(FEATURE_REGISTRY)}"
        ) from None


def causal_feature_names() -> tuple[str, ...]:
    """Sorted names of every feature cleared for trading decisions."""
    return tuple(
        sorted(n for n, d in FEATURE_REGISTRY.items() if d.kind is FeatureKind.CAUSAL)
    )


def diagnostic_feature_names() -> tuple[str, ...]:
    """Sorted names of every feature barred from trading decisions."""
    return tuple(
        sorted(
            n for n, d in FEATURE_REGISTRY.items() if d.kind is FeatureKind.DIAGNOSTIC
        )
    )


def assert_decision_features_are_causal(
    columns: Iterable[str], *, context: str = "decision path"
) -> None:
    """Refuse a decision path that can see a diagnostic or undeclared feature.

    Args:
        columns: Column names visible to signal, filter, sizing, execution, or
            parameter-selection logic.
        context: Human-readable description used in the error message.

    Raises:
        FeatureNotDeclaredError: A column is neither raw market data nor
            declared. Undeclared is treated as unsafe, not as safe-by-default.
        LeakyFeatureInDecisionError: A declared diagnostic feature is visible.
    """
    names = [c for c in columns if c not in _RAW_COLUMNS]

    undeclared = sorted(n for n in names if n not in FEATURE_REGISTRY)
    if undeclared:
        raise FeatureNotDeclaredError(
            f"{context} exposes undeclared feature(s) {undeclared}. Declare them "
            f"in vibe/research_pipeline/features/registry.py before use. An "
            f"undeclared feature is not assumed safe."
        )

    leaky = sorted(n for n in names if FEATURE_REGISTRY[n].kind is FeatureKind.DIAGNOSTIC)
    if leaky:
        details = "; ".join(f"{n}: {FEATURE_REGISTRY[n].description}" for n in leaky)
        raise LeakyFeatureInDecisionError(
            f"{context} exposes diagnostic feature(s) {leaky}, which may not "
            f"enter signal, filter, sizing, execution, or parameter-selection "
            f"logic. {details}"
        )


def registry_hash() -> str:
    """Canonical hash of the declarations, for stamping on a run fingerprint.

    Reclassifying a feature changes what the run was allowed to see, so it must
    change the identity of the run.
    """
    payload = [
        {
            "name": d.name,
            "kind": d.kind.value,
            "lookback_bars": d.lookback_bars,
            "lookahead_bars": d.lookahead_bars,
        }
        for d in sorted(FEATURE_REGISTRY.values(), key=lambda d: d.name)
    ]
    return hash_object(payload)
