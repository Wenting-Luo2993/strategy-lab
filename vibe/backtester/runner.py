from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from vibe.common.ruleset.models import StrategyRuleSet
from vibe.common.strategies.orb import ORBStrategy, ORBStrategyConfig


class RuleSetRunner:
    """
    Adapts StrategyRuleSet (config) to ORBStrategy (executor) for the backtester.

    The backtester calls generate_signal() per bar and track_position()/close_position()
    to keep the strategy's internal state in sync with the portfolio.
    """

    def __init__(self, ruleset: StrategyRuleSet) -> None:
        self.ruleset = ruleset
        self.strategy = self._build_strategy(ruleset)

    def _build_strategy(self, ruleset: StrategyRuleSet) -> ORBStrategy:
        s = ruleset.strategy
        exit_ = ruleset.exit

        tp_multiplier = 0.0
        if exit_.take_profit is not None and hasattr(exit_.take_profit, "multiplier"):
            tp_multiplier = exit_.take_profit.multiplier

        config = ORBStrategyConfig(
            name=ruleset.name,
            orb_start_time=s.orb_start_time,
            orb_duration_minutes=s.orb_duration_minutes,
            orb_body_pct_filter=getattr(s, "orb_body_pct_filter", 0.0),
            entry_cutoff_time=s.entry_cutoff_time,
            take_profit_multiplier=tp_multiplier,
            stop_loss_at_level=(exit_.stop_loss.method == "orb_level"),
            use_volume_filter=ruleset.trade_filter.volume_confirmation,
            volume_threshold=ruleset.trade_filter.volume_threshold,
            market_close_time=exit_.eod_time if exit_.eod else "16:00",
        )
        return ORBStrategy(config=config)

    def generate_signal(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        df_context: pd.DataFrame,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Delegate to strategy.generate_signal_incremental().
        current_bar must include 'timestamp' key (datetime).
        df_context must include 'ATR_14' column.
        """
        return self.strategy.generate_signal_incremental(
            symbol=symbol,
            current_bar=current_bar,
            df_context=df_context,
        )

    def track_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        take_profit: Optional[float],
        stop_loss: float,
        timestamp: datetime,
    ) -> None:
        self.strategy.track_position(
            symbol=symbol, side=side,
            entry_price=entry_price, take_profit=take_profit,
            stop_loss=stop_loss, timestamp=timestamp,
        )

    def close_position(self, symbol: str) -> None:
        self.strategy.close_position(symbol)

    def reset_daily_state(self, symbol: str) -> None:
        """Reset per-day tracking (one-trade-per-day guard). Call at start of each new day."""
        if hasattr(self.strategy, "_traded_today"):
            self.strategy._traded_today.pop(symbol, None)
