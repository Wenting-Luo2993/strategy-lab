"""
Trade management module for handling position sizing, risk management, and trade state.
This module is shared between live trading and backtesting to ensure consistency.
"""

from typing import Optional, Tuple, Dict
import pandas as pd

from ..risk_management.base import RiskManagement
from ..config.columns import TradeColumns
from ..config.Enums import Regime

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
        self.current_position = None

    def calculate_position_size(self, price: float, risk_per_trade: float) -> float:
        """
        Calculate position size based on risk parameters.
        
        Args:
            price: Current price for the asset
            risk_per_trade: Percentage of account to risk per trade
            
        Returns:
            float: Position size in base currency
        """
        return (risk_per_trade * self.current_balance)

    def create_entry_position(self, 
                            price: float, 
                            signal: int, 
                            time: pd.Timestamp, 
                            market_data: pd.DataFrame,
                            current_idx: int,
                            initial_stop: float) -> dict:
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
        
        self.current_position = position
        return position

    def check_exit_conditions(self, 
                            current_price: float,
                            high: float, 
                            low: float,
                            time: pd.Timestamp,
                            current_idx: int) -> Tuple[bool, Optional[Dict]]:
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
        if not self.current_position:
            return False, None
            
        stop_loss = self.current_position[TradeColumns.STOP_LOSS.value]
        take_profit = self.current_position[TradeColumns.TAKE_PROFIT.value]
        direction = self.current_position[TradeColumns.DIRECTION.value]
        
        # First check if we should update trailing stop
        self.update_trailing_stop(pd.DataFrame({'close': [current_price]}, index=[0]), 0)
        
        # After potential trailing stop update, get the current stop loss
        stop_loss = self.current_position[TradeColumns.STOP_LOSS.value]
        
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
            trade = self.close_position(exit_price, time, current_idx, exit_reason)
            return True, trade
            
        return False, None

    def close_position(self, 
                      exit_price: float, 
                      time: pd.Timestamp,
                      current_idx: int,
                      exit_reason: str) -> dict:
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
        if not self.current_position:
            return None
            
        direction = self.current_position[TradeColumns.DIRECTION.value]
        entry_price = self.current_position[TradeColumns.ENTRY_PRICE.value]
        position_size = self.current_position[TradeColumns.SIZE.value]
        
        # Calculate PnL
        pnl = direction * (exit_price - entry_price) * position_size
        self.current_balance += pnl
        
        # Get trailing stop info if available
        trailing_info = {}
        ts_data = self.current_position.get(TradeColumns.TRAILING_STOP_DATA.value)
        if ts_data and ts_data.get('trailing_active', False):
            trailing_info = {
                TradeColumns.TRAILING_ACTIVE.value: True,
                TradeColumns.HIGHEST_PROFIT_ATR.value: ts_data.get('highest_profit_atr', 0.0),
                TradeColumns.INITIAL_STOP.value: ts_data.get('initial_stop', None)
            }
        
        # Create trade record
        trade = {
            **self.current_position,
            TradeColumns.EXIT_IDX.value: current_idx,
            TradeColumns.EXIT_TIME.value: time,
            TradeColumns.EXIT_PRICE.value: exit_price,
            TradeColumns.EXIT_REASON.value: exit_reason,
            TradeColumns.PNL.value: pnl,
            **trailing_info
        }
        
        self.current_position = None
        return trade

    def update_trailing_stop(self, market_data: pd.DataFrame, current_idx: int) -> None:
        """
        Update trailing stop if applicable.
        
        Args:
            market_data: DataFrame with market data
            current_idx: Current index in the data
        """
        if not self.current_position or not self.risk_manager or not hasattr(self.risk_manager, 'update_trailing_stop'):
            return
            
        # Get the current price
        close_price = market_data["close"].iloc[current_idx]
        
        # Get current position details
        direction = self.current_position[TradeColumns.DIRECTION.value]
        
        # Update trailing stop
        updated_position = self.risk_manager.update_trailing_stop(
            self.current_position.copy(), 
            close_price
        )
        
        if updated_position:
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