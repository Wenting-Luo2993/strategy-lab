"""
Trade management module for handling position sizing, risk management, and trade state.
This module is shared between live trading and backtesting to ensure consistency.
"""

from typing import Optional, Tuple, Dict
import pandas as pd

from src.utils.logger import get_logger

from ..risk_management.base import RiskManagement
from ..config.columns import TradeColumns
from ..config.Enums import Regime

# Get a configured logger for this module
logger = get_logger("TradeManager")

class TradeManager:
    """
    Shared trade management logic between live and backtest environments.
    Handles position sizing, risk management, and trade state.
    """
    def __init__(self, risk_manager: RiskManagement, initial_capital: float):
        """
        Initialize TradeManager with risk management and initial capital.

        Args:
            risk_manager: Risk management instance for stop loss/take profit
            initial_capital: Starting capital amount
        """
        self.risk_manager = risk_manager
        self.initial_capital = initial_capital
        self.current_balance = initial_capital
        # Keep a mapping of active positions by ticker (or an auto-generated key)
        self.current_positions = {}  # type: Dict[str, dict]
        # For backward compatibility we also keep a pointer to the most-recent position
        self.current_position = None
        # Cache of closed positions (trade records)
        self.closed_positions = []

    def create_entry_position(self,
                            price: float,
                            signal: int,
                            time: pd.Timestamp,
                            market_data: pd.DataFrame,
                            current_idx: int,
                            initial_stop: float,
                            ticker: str = None) -> dict:
        """
        Create a new position with consistent risk parameters.

        Args:
            price: Entry price
            signal: Direction (1 for long, -1 for short)
            time: Entry timestamp
            risk_per_trade: Risk per trade as decimal
            market_data: DataFrame with market data
            current_idx: Current index in the market data
            initial_stop: Optional initial stop loss price

        Returns:
            dict: Position information dictionary
        """
        try:
            # Create signal series for risk manager
            signal_series = pd.Series({
                'entry_price': price,
                'signal': signal,
                'index': current_idx,
                'initial_stop': initial_stop
            })

            # Get risk management parameters
            risk_result = self.risk_manager.apply(signal_series, market_data)

            is_long = signal == 1  # True for long signal, False for short or no signal
            risk_initial_stop = risk_result.get('stop_loss')
            initial_stop = max(initial_stop, risk_initial_stop) if is_long else min(initial_stop, risk_initial_stop)
            position_size = self.risk_manager.calculate_position_size(self.current_balance, price, initial_stop)

            # Determine regime
            regime = self.determine_ticker_regime(market_data, current_idx)

            position = {
                TradeColumns.ENTRY_IDX.value: current_idx,
                TradeColumns.ENTRY_TIME.value: time,
                TradeColumns.ENTRY_PRICE.value: price,
                TradeColumns.SIZE.value: position_size,
                TradeColumns.STOP_LOSS.value: risk_result.get('stop_loss'),
                TradeColumns.TAKE_PROFIT.value: risk_result.get('take_profit'),
                TradeColumns.ACCOUNT_BALANCE.value: self.current_balance,
                TradeColumns.DIRECTION.value: signal,
                TradeColumns.TICKER_REGIME.value: regime,
                TradeColumns.TRAILING_STOP_DATA.value: risk_result.get('trailing_stop_data')
            }
            # Attach ticker info if available. Preference order:
            # 1) explicit ticker arg, 2) DataFrame attrs 'ticker', 3) fallback generated id
            if ticker is None:
                ticker = getattr(market_data, 'name', None) or market_data.attrs.get('ticker', None)
            if ticker is None:
                ticker = f"pos_{len(self.current_positions) + 1}"

            position[TradeColumns.TICKER.value] = ticker

            # Store in mapping and preserve current_position pointer for compatibility
            self.current_positions[ticker] = position
            self.current_position = position
            return position
        except Exception as e:
            logger.error(f"Error creating entry position at {current_idx} for {ticker}")


    def check_exit_conditions(self,
                            current_price: float,
                            high: float,
                            low: float,
                            time: pd.Timestamp,
                            current_idx: int,
                            ticker: Optional[str] = None) -> Tuple[bool, Optional[Dict]]:
        """
        Check if position should be exited based on price action.

        Args:
            current_price: Current price
            high: High price of current period
            low: Low price of current period
            time: Current timestamp
            current_idx: Current index in the data

        Returns:
            tuple: (exit_triggered, trade_details)
        """
        # This method can operate on a specific ticker (when provided) or the current_position for
        # backwards compatibility. Multi-position checking across multiple tickers would require
        # per-ticker market_data and price inputs; to keep behavior minimal-impact we support
        # providing a `ticker` argument to target a single position.
        if ticker is None:
            # Back-compat: operate on the single most-recent position
            if not self.current_position:
                return False, None
            positions_to_check = [(self.current_position[TradeColumns.TICKER.value], self.current_position)]
        else:
            pos = self.current_positions.get(ticker)
            if not pos:
                return False, None
            positions_to_check = [(ticker, pos)]

        # We'll only check the specified position(s) and return the first trade found.
        for pos_ticker, pos in positions_to_check:
            stop_loss = pos[TradeColumns.STOP_LOSS.value]
            take_profit = pos[TradeColumns.TAKE_PROFIT.value]
            direction = pos[TradeColumns.DIRECTION.value]
            # First check if we should update trailing stop for this ticker
            self.update_trailing_stop(pd.DataFrame({'close': [current_price]}, index=[0]), 0, ticker=pos_ticker)
            # After potential trailing stop update, refresh stop_loss/take_profit
            stop_loss = pos[TradeColumns.STOP_LOSS.value]
            take_profit = pos[TradeColumns.TAKE_PROFIT.value]

            exit_price = None
            exit_reason = None

            # For long positions
            if direction == 1:
                # Check stop loss
                if stop_loss is not None and low <= stop_loss:
                    exit_price = stop_loss  # Assume we got filled at stop price
                    exit_reason = "stop_loss"
                # Check take profit only if stop loss wasn't hit
                elif take_profit is not None and high >= take_profit:
                    exit_price = take_profit  # Assume we got filled at take profit price
                    exit_reason = "take_profit"

            # For short positions
            elif direction == -1:
                # Check stop loss
                if stop_loss is not None and high >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                # Check take profit only if stop loss wasn't hit
                elif take_profit is not None and low <= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"

            if exit_price:
                # TODO: The position needs to be sent to the exchange and close after it is filled.
                # For backtesting, we assume immediate fill at stop or take profit price.
                # Need to remove close_position from inside this loop to delegate exchange interaction to caller.
                trade = self.close_position(exit_price, time, current_idx, exit_reason, ticker=pos_ticker)
                return True, trade

        return False, None

    def close_position(self,
                      exit_price: float,
                      time: pd.Timestamp,
                      current_idx: int,
                      exit_reason: str,
                      ticker: Optional[str] = None) -> dict:
        """
        Close current position and calculate PnL.

        Args:
            exit_price: Price at exit
            time: Exit timestamp
            current_idx: Current index in the data
            exit_reason: Reason for exit

        Returns:
            dict: Completed trade information
        """
        # Allow closing by ticker or by passing no ticker (back-compat uses self.current_position)
        if ticker is None:
            if not self.current_position:
                return None
            pos = self.current_position
            ticker = pos.get(TradeColumns.TICKER.value)
        else:
            pos = self.current_positions.get(ticker)
            if not pos:
                return None

        direction = pos[TradeColumns.DIRECTION.value]
        entry_price = pos[TradeColumns.ENTRY_PRICE.value]
        position_size = pos[TradeColumns.SIZE.value]

        # Calculate PnL
        pnl = direction * (exit_price - entry_price) * position_size
        self.current_balance += pnl

        # Get trailing stop info if available
        trailing_info = {}
        ts_data = pos.get(TradeColumns.TRAILING_STOP_DATA.value)
        if ts_data and ts_data.get('trailing_active', False):
            trailing_info = {
                TradeColumns.TRAILING_ACTIVE.value: True,
                TradeColumns.HIGHEST_PROFIT_ATR.value: ts_data.get('highest_profit_atr', 0.0),
                TradeColumns.INITIAL_STOP.value: ts_data.get('initial_stop', None)
            }

        # Create trade record
        trade = {
            **pos,
            TradeColumns.EXIT_IDX.value: current_idx,
            TradeColumns.EXIT_TIME.value: time,
            TradeColumns.EXIT_PRICE.value: exit_price,
            TradeColumns.EXIT_REASON.value: exit_reason,
            TradeColumns.PNL.value: pnl,
            **trailing_info
        }

        # Remove from active positions and cache the closed trade
        # Cache the trade record so callers can inspect recently closed positions
        self.closed_positions.append(trade)
        if ticker in self.current_positions:
            del self.current_positions[ticker]

        # If the removed position was the pointer-held current_position, clear it
        if self.current_position and self.current_position.get(TradeColumns.TICKER.value) == ticker:
            self.current_position = None

        return trade

    def update_trailing_stop(self, market_data: pd.DataFrame, current_idx: int, ticker: Optional[str] = None) -> None:
        """
        Update trailing stop if applicable.

        Args:
            market_data: DataFrame with market data
            current_idx: Current index in the data
        """
        # Update trailing stop for a specific ticker or the current_position if ticker is None
        if not self.risk_manager or not hasattr(self.risk_manager, 'update_trailing_stop'):
            return

        # Determine which position to update
        if ticker is None:
            pos = self.current_position
            if not pos:
                return
            ticker = pos.get(TradeColumns.TICKER.value)
        else:
            pos = self.current_positions.get(ticker)
            if not pos:
                return

        # Get the current price
        close_price = market_data["close"].iloc[current_idx]

        # Update trailing stop for this position
        updated_position = self.risk_manager.update_trailing_stop(
            pos.copy(),
            close_price
        )

        if updated_position:
            # Persist any updates back to the mapping and pointer
            updated_ticker = updated_position.get(TradeColumns.TICKER.value) or ticker
            self.current_positions[updated_ticker] = updated_position
            # If pointer pointed to this ticker, update it too
            if self.current_position and self.current_position.get(TradeColumns.TICKER.value) == updated_ticker:
                self.current_position = updated_position

    @staticmethod
    def determine_ticker_regime(df: pd.DataFrame, index: int) -> str:
        """
        Determine market regime based on RSI.

        Args:
            df: DataFrame with market data
            index: Current index in the data

        Returns:
            str: Market regime (BULL, BEAR, or SIDEWAYS)
        """
        rsi_cols = [col for col in df.columns if col.startswith('RSI')]

        if not rsi_cols:
            return Regime.SIDEWAYS.value

        rsi_col = rsi_cols[0]
        rsi_value = df[rsi_col].iloc[index]

        if rsi_value >= 60:
            return Regime.BULL.value
        elif rsi_value <= 40:
            return Regime.BEAR.value
        else:
            return Regime.SIDEWAYS.value

    def get_current_position(self) -> Optional[Dict]:
        """
        Get the current position information.

        Returns:
            dict or None: Current position information if exists
        """
        return self.current_position

    def get_closed_positions(self):
        """
        Return list of recently closed trade records cached by TradeManager.
        """
        return list(self.closed_positions)

    def get_current_balance(self) -> float:
        """
        Get the current account balance.

        Returns:
            float: Current balance
        """
        return self.current_balance

    def reset(self) -> None:
        """
        Reset the trade manager state.
        """
        self.current_balance = self.initial_capital
        self.current_position = None
        self.current_positions = {}
        self.closed_positions = []
