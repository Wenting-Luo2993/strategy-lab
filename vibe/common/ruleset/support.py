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
_BACKTEST_TRAILING = {"breakeven_plus_ticks", "stepped_r_multiple"}
_LIVE_TRAILING = {"breakeven_plus_ticks"}


class UnsupportedRulesetSettingsError(ValueError):
    """Raised when a ruleset sets something the engine would silently ignore."""


def unsupported_settings(ruleset: StrategyRuleSet, context: Context) -> List[str]:
    """Return a description of every set-but-ignored setting for ``context``."""
    problems: List[str] = []
    strategy = ruleset.strategy
    exit_ = ruleset.exit

    if strategy.entry_mode != _DEFAULT_ENTRY_MODE:
        problems.append(f"strategy.entry_mode={strategy.entry_mode!r} is not implemented")
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
