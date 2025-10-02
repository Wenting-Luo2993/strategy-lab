"""
FixedATRStop: Risk management strategy using fixed ATR-based stops and targets.
"""
import pandas as pd
from typing import Dict
from .base import RiskManagement
from ..backtester.parameters import RiskConfig

class FixedATRStop(RiskManagement):
    """
    Risk management strategy that sets stop loss and take profit based on ATR multiples.
    """
    def __init__(
        self,
        config: RiskConfig,
        atr_col: str = "ATRr_14",
    ) -> None:
        """
        Initialize FixedATRStop.
        Args:
            config (RiskConfig): Risk configuration object.
            atr_col (str): Column name for ATR in the DataFrame.
        """
        super().__init__(config)
        self.atr_col = atr_col

    def apply(self, signal: pd.Series, data: pd.DataFrame) -> Dict[str, float]:
        """
        Apply ATR-based stop loss and take profit to a trade signal.
        Respects strategy-provided initial stops when they are stricter.
        
        Args:
            signal (pd.Series): Trade signal (must contain 'signal', 'entry_price', and 'index').
                               May contain 'initial_stop' from strategy.
            data (pd.DataFrame): Market data with ATR column.
        Returns:
            dict: {'entry': entry_price, 'stop_loss': stop, 'take_profit': tp}
        """
        entry_price = signal['entry_price']
        idx = int(signal['index'])
        direction = signal['signal']
        strategy_stop = signal.get('initial_stop')
        
        # Calculate ATR-based stop and take profit
        atr = data.iloc[idx][self.atr_col]
        stop_multiple = self.config.stop_loss_value
        tp_multiple = self.config.take_profit_value
        
        if direction == 1:  # Long position
            atr_stop = entry_price - atr * stop_multiple
            tp = entry_price + atr * tp_multiple
            
            # Use the stricter (higher) stop between strategy stop and ATR stop
            if strategy_stop is not None:
                stop = max(strategy_stop, atr_stop)
            else:
                stop = atr_stop
                
        elif direction == -1:  # Short position
            atr_stop = entry_price + atr * stop_multiple
            tp = entry_price - atr * tp_multiple
            
            # Use the stricter (lower) stop between strategy stop and ATR stop
            if strategy_stop is not None:
                stop = min(strategy_stop, atr_stop)
            else:
                stop = atr_stop
                
        else:
            raise ValueError("Signal direction must be 1 (long) or -1 (short).")
            
        return {"entry": entry_price, "stop_loss": stop, "take_profit": tp}

    def calculate_position_size(self, account_balance: float, entry_price: float, stop_loss: float) -> float:
        """
        Calculate position size based on risk percentage and stop distance.
        Args:
            account_balance (float): Account balance.
            entry_price (float): Entry price.
            stop_loss (float): Stop loss price.
        Returns:
            float: Position size.
        """
        risk_pct = self.config.risk_per_trade
        risk_amount = account_balance * risk_pct
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            raise ValueError("Stop loss must not be equal to entry price.")
        position_size = risk_amount / risk_per_unit
        return position_size

    def validate_trade(self, signal: pd.Series, account_balance: float) -> bool:
        """
        Validate that the trade does not exceed max risk and position size is positive.
        Args:
            signal (pd.Series): Trade signal (must contain 'entry_price', 'stop_loss', 'position_size').
            account_balance (float): Account balance.
        Returns:
            bool: True if trade is valid, False otherwise.
        """
        max_risk_pct = getattr(self.config, 'max_risk_pct', 0.02)  # Default 2%
        entry_price = signal.get('entry_price')
        stop_loss = signal.get('stop_loss')
        position_size = signal.get('position_size')
        if None in (entry_price, stop_loss, position_size):
            raise ValueError("Signal must contain entry_price, stop_loss, and position_size.")
        risk_per_unit = abs(entry_price - stop_loss)
        total_risk = risk_per_unit * position_size
        max_risk = account_balance * max_risk_pct
        return position_size > 0 and total_risk <= max_risk
