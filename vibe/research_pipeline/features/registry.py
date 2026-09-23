"""Declared causality class for every feature the research path can compute.

Why this file exists
--------------------

``FeatureEngine`` will happily compute any registered feature and hand it to
``BacktestEngine.run(precomputed_features=...)``. Nothing in that path asks
whether a given column is safe to make decisions with. This registry is the
missing declaration, and :func:`assert_decision_features_are_causal` is the
enforcement point.

The classification below is not a guess. Every entry was measured with a
truncation-equivalence probe against real QQQ 5-minute bars
(2022-01-03..2022-06-30): a feature is leaky if its value at a mid-session bar
differs depending on whether bars *after* that timestamp were present when the
column was computed. See
:func:`vibe.research_pipeline.features.leakage.check_truncation_equivalence`,
which is the same check run as a test.

The daily-resample trap (found here, then fixed)
------------------------------------------------

``FeatureEngine`` computes some features by resampling intraday bars to daily,
computing on the daily frame, then reindexing back onto the intraday index with
``method="ffill"``. The daily bar for session D summarizes the *whole* of
session D, including bars that have not happened yet at 10:00 on session D. The
forward-fill then stamps that whole-session value onto every intraday bar of
session D.

``adx_14``, ``slope_20d``, and ``slope_50d`` were measured to do exactly this:
on real data the 09:30 bar of a session carried that session's *complete* daily
ADX. They are now fixed at the source — ``_reindex_causally`` lags the daily
series by one session when the input frame is intraday — and re-measured as
causal against all three checks. They are declared ``CAUSAL`` on that evidence,
not on the assumption that the fix worked.

The shift is deliberately conditional on intraday input, because
``DayRegimeLabeler`` applies its own ``shift(1)`` and shifting unconditionally
would double-lag the daily path. That regression was checked: daily-frame
feature checksums and regime label counts are bit-identical across the fix.

What still separates causal from leaky here is whether a daily-derived value is
lagged before being forward-filled. That difference is invisible at the call
site, which is why this registry exists rather than a convention.

``FeatureEngine``'s module docstring previously claimed "all features are
forward-observable". That claim held at daily granularity and failed at intraday
granularity — the granularity the parameter sweep actually uses — and has been
corrected.
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
    _causal(
        "slope_20d",
        21 * _SESSION_BARS_5M,
        "Rolling 20-day OLS slope of daily closes, mapped onto the caller's "
        "index by _reindex_causally. Was LEAKY INTRADAY until the daily series "
        "was lagged one session for intraday frames; session D now carries "
        "session D-1's slope. The lookback covers the extra session that lag "
        "consumes. Re-measured: passes truncation equivalence, prefix "
        "invariance, and future perturbation.",
    ),
    _causal(
        "slope_50d",
        51 * _SESSION_BARS_5M,
        "Same daily-resample construction as slope_20d, and fixed by the same "
        "one-session lag. Re-measured: passes all three checks.",
    ),
    _causal(
        "adx_14",
        15 * _SESSION_BARS_5M,
        "Wilder ADX on daily bars, mapped onto the caller's index by "
        "_reindex_causally. Was the headline leak: session D's high/low/close "
        "propagated backwards into every intraday bar of session D, so the "
        "09:30 bar carried that session's complete ADX. Now lagged one session "
        "on intraday frames. Verified on real QQQ bars: the 09:30 value equals "
        "the prior session's daily ADX exactly.",
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
    columns: Iterable[str],
    *,
    context: str = "decision path",
    registry: Mapping[str, FeatureDeclaration] | None = None,
) -> None:
    """Refuse a decision path that can see a diagnostic or undeclared feature.

    Args:
        columns: Column names visible to signal, filter, sizing, execution, or
            parameter-selection logic.
        context: Human-readable description used in the error message.
        registry: Declarations to enforce against. Defaults to
            :data:`FEATURE_REGISTRY`. Injectable so the guard stays testable
            when - as is currently the case - no shipped feature is diagnostic.
            A guard that cannot be made to fire is not evidence of anything.

    Raises:
        FeatureNotDeclaredError: A column is neither raw market data nor
            declared. Undeclared is treated as unsafe, not as safe-by-default.
        LeakyFeatureInDecisionError: A declared diagnostic feature is visible.
    """
    reg = FEATURE_REGISTRY if registry is None else registry
    names = [c for c in columns if c not in _RAW_COLUMNS]

    undeclared = sorted(n for n in names if n not in reg)
    if undeclared:
        raise FeatureNotDeclaredError(
            f"{context} exposes undeclared feature(s) {undeclared}. Declare them "
            f"in vibe/research_pipeline/features/registry.py before use. An "
            f"undeclared feature is not assumed safe."
        )

    leaky = sorted(n for n in names if reg[n].kind is FeatureKind.DIAGNOSTIC)
    if leaky:
        details = "; ".join(f"{n}: {reg[n].description}" for n in leaky)
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
