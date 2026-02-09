"""
Abstract base class for trading strategies.

Defines the interface that all strategies must implement and provides
common functionality for signal generation, configuration, and logging.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import time, datetime as dt
import pandas as pd
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class StrategyConfig(BaseModel):
    """Base configuration model for strategies."""

    name: str = Field(..., description="Strategy name")
    enabled: bool = Field(default=True, description="Whether strategy is enabled")
    take_profit_type: str = Field(
        default="atr_multiple",
        description="Take-profit calculation type: 'atr_multiple', 'fixed_pips', 'percentage'",
    )
    stop_loss_type: str = Field(
        default="atr_multiple",
        description="Stop-loss calculation type: 'atr_multiple', 'fixed_pips', 'fixed_price'",
    )
    take_profit_value: float = Field(default=2.0, description="Take-profit value")
    stop_loss_value: float = Field(default=1.0, description="Stop-loss value")

    class Config:
        """Pydantic config."""

        validate_assignment = True


@dataclass
class ExitSignal:
    """Exit signal details."""

    exit_type: str  # 'take_profit', 'stop_loss', 'time_exit'
    reason: str
    level: float


class StrategyBase(ABC):
    """
    Abstract base class for all trading strategies.

    Subclasses must implement generate_signals() and can optionally override
    generate_signal_incremental() for real-time signal generation.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        """
        Initialize strategy.

        Args:
            config: Strategy configuration (Pydantic model)
        """
        self.config = config or StrategyConfig(name=self.__class__.__name__)
        self.logger = logging.getLogger(f"vibe.strategies.{self.__class__.__name__}")

        # Position tracking state
        self.positions: Dict[str, Dict[str, Any]] = {}  # symbol -> position_data

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate trading signals for entire DataFrame.

        Batch signal generation - processes complete OHLCV data and returns
        a Series of signals (-1 for short, 0 for neutral, 1 for long).

        Args:
            df: DataFrame with OHLCV data and any pre-calculated indicators
               (columns: timestamp, open, high, low, close, volume)

        Returns:
            Series of signals aligned with df index
        """
        pass

    def generate_signal_incremental(
        self,
        symbol: str,
        current_bar: Dict[str, float],
        df_context: pd.DataFrame,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Generate signal for current bar incrementally.

        Real-time signal generation - processes one bar at a time with historical
        context. Override this for efficient incremental calculation.

        Args:
            symbol: Trading symbol
            current_bar: Current OHLCV bar {'open': float, 'high': float, ...}
            df_context: Historical DataFrame for context (last N bars)

        Returns:
            Tuple of (signal, metadata)
            - signal: -1, 0, or 1
            - metadata: Dict with signal details (reason, confidence, etc.)
        """
        # Default: not implemented, requires full batch
        return 0, {"reason": "incremental not implemented"}

    def calculate_take_profit(
        self,
        entry_price: float,
        side: str,
        atr: Optional[float] = None,
        **kwargs,
    ) -> float:
        """
        Calculate take-profit level.

        Args:
            entry_price: Entry price
            side: 'buy' or 'sell'
            atr: Average True Range (required for atr_multiple type)
            **kwargs: Additional parameters

        Returns:
            Take-profit price level
        """
        if self.config.take_profit_type == "atr_multiple":
            if atr is None:
                raise ValueError("ATR required for atr_multiple take-profit")
            tp_distance = atr * self.config.take_profit_value
        elif self.config.take_profit_type == "fixed_pips":
            tp_distance = self.config.take_profit_value
        elif self.config.take_profit_type == "percentage":
            tp_distance = entry_price * (self.config.take_profit_value / 100)
        else:
            raise ValueError(f"Unknown take-profit type: {self.config.take_profit_type}")

        if side == "buy":
            return entry_price + tp_distance
        elif side == "sell":
            return entry_price - tp_distance
        else:
            raise ValueError(f"Invalid side: {side}")

    def calculate_stop_loss(
        self,
        entry_price: float,
        side: str,
        atr: Optional[float] = None,
        **kwargs,
    ) -> float:
        """
        Calculate stop-loss level.

        Args:
            entry_price: Entry price
            side: 'buy' or 'sell'
            atr: Average True Range (required for atr_multiple type)
            **kwargs: Additional parameters

        Returns:
            Stop-loss price level
        """
        if self.config.stop_loss_type == "atr_multiple":
            if atr is None:
                raise ValueError("ATR required for atr_multiple stop-loss")
            sl_distance = atr * self.config.stop_loss_value
        elif self.config.stop_loss_type == "fixed_pips":
            sl_distance = self.config.stop_loss_value
        elif self.config.stop_loss_type == "fixed_price":
            return self.config.stop_loss_value
        else:
            raise ValueError(f"Unknown stop-loss type: {self.config.stop_loss_type}")

        if side == "buy":
            return entry_price - sl_distance
        elif side == "sell":
            return entry_price + sl_distance
        else:
            raise ValueError(f"Invalid side: {side}")

    def check_exit_conditions(
        self,
        symbol: str,
        current_price: float,
        current_time: Any,
        market_close: str = "16:00",
    ) -> Optional[ExitSignal]:
        """
        Check if open position should be exited.

        Args:
            symbol: Trading symbol
            current_price: Current price
            current_time: Current time
            market_close: Market close time (HH:MM format)

        Returns:
            ExitSignal if position should be exited, None otherwise
        """
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]

        # Check take-profit
        if pos["side"] == "buy" and current_price >= pos["take_profit"]:
            return ExitSignal(
                exit_type="take_profit",
                reason=f"Take-profit reached at {current_price:.2f}",
                level=pos["take_profit"],
            )

        if pos["side"] == "sell" and current_price <= pos["take_profit"]:
            return ExitSignal(
                exit_type="take_profit",
                reason=f"Take-profit reached at {current_price:.2f}",
                level=pos["take_profit"],
            )

        # Check stop-loss
        if pos["side"] == "buy" and current_price <= pos["stop_loss"]:
            return ExitSignal(
                exit_type="stop_loss",
                reason=f"Stop-loss triggered at {current_price:.2f}",
                level=pos["stop_loss"],
            )

        if pos["side"] == "sell" and current_price >= pos["stop_loss"]:
            return ExitSignal(
                exit_type="stop_loss",
                reason=f"Stop-loss triggered at {current_price:.2f}",
                level=pos["stop_loss"],
            )

        # Check time-based exit (end of day)
        # Parse current_time to time object
        if isinstance(current_time, str):
            # Parse string to time
            try:
                current_time_obj = dt.strptime(current_time, "%H:%M").time()
            except ValueError:
                try:
                    current_time_obj = dt.strptime(current_time, "%H:%M:%S").time()
                except ValueError:
                    self.logger.warning(f"Could not parse time string: {current_time}")
                    return None
        elif hasattr(current_time, "time"):
            current_time_obj = current_time.time()
        elif hasattr(current_time, "hour"):
            current_time_obj = current_time
        else:
            self.logger.warning(f"Unknown time format: {type(current_time)}")
            return None

        # Parse market_close to time object
        if isinstance(market_close, str):
            hour, minute = map(int, market_close.split(":"))
            market_close_obj = time(hour, minute)
        else:
            market_close_obj = market_close

        if current_time_obj >= market_close_obj:
            return ExitSignal(
                exit_type="time_exit",
                reason="Market close approaching",
                level=current_price,
            )

        return None

    def track_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        timestamp: Any,
    ) -> None:
        """
        Track an open position.

        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            entry_price: Entry price
            take_profit: Take-profit level
            stop_loss: Stop-loss level
            timestamp: Entry timestamp
        """
        self.positions[symbol] = {
            "side": side,
            "entry_price": entry_price,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "timestamp": timestamp,
        }

    def close_position(self, symbol: str) -> None:
        """Close tracked position."""
        if symbol in self.positions:
            del self.positions[symbol]

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get tracked position for symbol."""
        return self.positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        """Check if symbol has open position."""
        return symbol in self.positions
