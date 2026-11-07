"""Which ruleset settings each execution path actually honours.

The ruleset model accepts more settings than the ORB strategy implements.
A setting that is accepted but ignored makes a backtest or a live session
silently run a different strategy from the one described, so callers check
``unsupported_settings`` before running: the backtester refuses to start,
live trading logs each one as an error.

Only non-default values are reported. A default either matches the hard-coded
behaviour (for example one trade per day) or turns the feature off.
"""

from __future__ import annotations

from typing import List, Literal

from vibe.common.ruleset.models import StrategyRuleSet

Context = Literal["backtest", "live"]

_DEFAULT_ENTRY_MODE = "no_validation_simple"
FIRST_CANDLE_DIRECTION = "first_candle_direction"
_BACKTEST_ENTRY_MODES = {_DEFAULT_ENTRY_MODE, FIRST_CANDLE_DIRECTION}
_LIVE_ENTRY_MODES = {_DEFAULT_ENTRY_MODE}
_BACKTEST_TRAILING = {"breakeven_plus_ticks", "stepped_r_multiple"}
_LIVE_TRAILING = {"breakeven_plus_ticks"}


class UnsupportedRulesetSettingsError(ValueError):
    """Raised when a ruleset sets something the engine would silently ignore."""


def unsupported_settings(ruleset: StrategyRuleSet, context: Context) -> List[str]:
    """Return a description of every set-but-ignored setting for ``context``."""
    problems: List[str] = []
    strategy = ruleset.strategy
    exit_ = ruleset.exit

    supported_entry_modes = (
        _BACKTEST_ENTRY_MODES if context == "backtest" else _LIVE_ENTRY_MODES
    )
    if strategy.entry_mode not in supported_entry_modes:
        problems.append(
            f"strategy.entry_mode={strategy.entry_mode!r} is not implemented for {context}"
        )
    if strategy.entry_mode == FIRST_CANDLE_DIRECTION:
        # No breakout bar exists in this mode, so breakout-bar filters would be
        # silently meaningless.
        if strategy.orb_body_pct_filter > 0:
            problems.append(
                "strategy.orb_body_pct_filter>0 has no breakout bar to filter under "
                "entry_mode='first_candle_direction'"
            )
        if strategy.breakout_evaluation != "wick":
            problems.append(
                "strategy.breakout_evaluation='body' has no meaning under "
                "entry_mode='first_candle_direction'"
            )
        if ruleset.trade_filter.volume_confirmation:
            problems.append(
                "trade_filter.volume_confirmation=true is not implemented for "
                "entry_mode='first_candle_direction'"
            )
    if not strategy.one_trade_per_day:
        problems.append("strategy.one_trade_per_day=false is not implemented (always one trade)")
    if not strategy.cancel_other_side:
        problems.append("strategy.cancel_other_side=false is not implemented")
    if strategy.allow_reentry:
        problems.append("strategy.allow_reentry=true is not implemented")

    if ruleset.position_size.method != "max_loss_pct":
        problems.append(
            f"position_size.method={ruleset.position_size.method!r} is not implemented "
            "(sizing is always max_loss_pct)"
        )

    if exit_.stop_loss.method != "orb_level":
        problems.append(
            f"exit.stop_loss.method={exit_.stop_loss.method!r} is not implemented "
            "(only orb_level is honoured)"
        )
    if exit_.take_profit is not None and exit_.take_profit.method == "fixed_pct":
        problems.append("exit.take_profit.method='fixed_pct' is not implemented")
    if (
        context == "live"
        and exit_.take_profit is not None
        and exit_.take_profit.method == "r_multiple"
    ):
        problems.append(
            "exit.take_profit.method='r_multiple' is not implemented for live "
            "(the target is set from the entry fill, which live does not do)"
        )

    trailing = exit_.trailing_stop
    supported_trailing = _BACKTEST_TRAILING if context == "backtest" else _LIVE_TRAILING
    if trailing is not None and trailing.method not in supported_trailing:
        problems.append(
            f"exit.trailing_stop.method={trailing.method!r} is not implemented for {context}"
        )

    if ruleset.trade_filter.vix_max is not None:
        problems.append("trade_filter.vix_max is not implemented")
    if ruleset.mtf_validation.enabled:
        problems.append("mtf_validation.enabled=true is not implemented")

    if context == "backtest":
        default_entry_order = type(strategy.entry_order)()
        if strategy.entry_order != default_entry_order:
            problems.append(
                "strategy.entry_order is live-only; the backtester prices entries "
                "with ExecutionRealismConfig"
            )

    return problems


def require_supported(ruleset: StrategyRuleSet, context: Context) -> None:
    """Raise ``UnsupportedRulesetSettingsError`` listing every ignored setting."""
    problems = unsupported_settings(ruleset, context)
    if problems:
        raise UnsupportedRulesetSettingsError(
            f"Ruleset {ruleset.name!r} sets options the {context} path would ignore: "
            + "; ".join(problems)
        )


def resting_stop_conflicts(ruleset: StrategyRuleSet) -> List[str]:
    """Settings a backtest with a resting stop entry order cannot honour.

    A resting stop fills the instant price reaches the level, so its entry
    decision may only use information known at that moment. Any filter that
    reads the *completed* breakout bar is lookahead under that order model.

    ``orb_body_pct_filter`` is deliberately not listed: dropping the
    completed-bar body filter is part of the policy's definition (the engine
    reports the ignored value in its diagnostics), whereas the settings below
    have no such declared meaning and would otherwise be silently ignored.
    """
    problems: List[str] = []
    if ruleset.strategy.entry_mode == FIRST_CANDLE_DIRECTION:
        problems.append(
            "strategy.entry_mode='first_candle_direction' enters at market once the "
            "opening candle has closed; there is no breakout level for a stop order to rest at"
        )
    if ruleset.trade_filter.volume_confirmation:
        problems.append(
            "trade_filter.volume_confirmation=true reads the completed breakout bar's volume"
        )
    if ruleset.strategy.breakout_evaluation != "wick":
        problems.append(
            f"strategy.breakout_evaluation={ruleset.strategy.breakout_evaluation!r} "
            "needs the completed breakout bar's body"
        )
    return problems


def require_resting_stop_compatible(ruleset: StrategyRuleSet) -> None:
    """Raise when a ruleset enables a completed-bar filter under a resting stop."""
    problems = resting_stop_conflicts(ruleset)
    if problems:
        raise UnsupportedRulesetSettingsError(
            f"Ruleset {ruleset.name!r} enables completed-bar entry filters that a "
            "resting stop entry (EntryFillPolicy.RESTING_STOP) cannot know at fill "
            "time: " + "; ".join(problems)
        )
