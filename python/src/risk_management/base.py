"""
Base class for risk management strategies.
Defines the interface and common logic for all risk management modules.
"""
from abc import ABC, abstractmethod
from typing import Any
import pandas as pd

# Import RiskConfig from parameters.py
from ..backtester.parameters import RiskConfig

class RiskManagement(ABC):
    """
    Abstract base class for risk management strategies.
    
    Args:
        config (RiskConfig): Configuration parameters for risk management (e.g., stop loss type/value, take profit type/value).
    """
    def __init__(self, config: RiskConfig) -> None:
        """
        Initialize the risk management strategy with configuration.
        """
        self.config = config

    @abstractmethod
    def apply(self, signal: pd.Series, data: pd.DataFrame) -> pd.Series:
        """
        Apply stop loss and take profit rules to a trade signal.
        Must be implemented by subclasses.
        
        Args:
            signal (dict): Trade signal to modify. May contain 'initial_stop' from the strategy.
            data (pd.DataFrame): Market data relevant to the trade.
        Returns:
            dict: Modified trade signal with risk management applied.
        """
        raise NotImplementedError("Subclasses must implement the apply method.")

    def calculate_position_size(self, account_balance: float, entry_price: float, stop_loss: float) -> float:
        """
        Compute position size based on risk percentage of account.
        
        Args:
            account_balance (float): Total account balance.
            entry_price (float): Entry price of the trade.
            stop_loss (float): Stop loss price.
        Returns:
            float: Position size (number of units/contracts).
        """
        # Example: risk_per_trade could be added to RiskConfig if needed
        risk_per_trade = getattr(self.config, 'risk_per_trade', 0.01)  # Default 1%
        risk_amount = account_balance * risk_per_trade
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            raise ValueError("Stop loss must not be equal to entry price.")
        position_size = risk_amount / risk_per_unit
        return position_size

    def validate_trade(self, signal: pd.Series, account_balance: float) -> bool:
        """
        Ensure the trade respects risk limits (e.g., not risking more than X% of account).
        
        Args:
            signal (dict): Trade signal containing position size and stop loss.
            account_balance (float): Total account balance.
        Returns:
            bool: True if trade is valid, False otherwise.
        """
        risk_per_trade = getattr(self.config, 'risk_per_trade', 0.01)
        entry_price = signal.get('entry_price')
        stop_loss = signal.get('stop_loss')
        position_size = signal.get('position_size')
        if None in (entry_price, stop_loss, position_size):
            raise ValueError("Signal must contain entry_price, stop_loss, and position_size.")
        risk_per_unit = abs(entry_price - stop_loss)
        total_risk = risk_per_unit * position_size
        max_risk = account_balance * risk_per_trade
        return total_risk <= max_risk
