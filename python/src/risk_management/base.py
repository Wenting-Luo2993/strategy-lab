"""
Base class for risk management strategies.
Defines the interface and common logic for all risk management modules.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import pandas as pd

# Import RiskConfig from parameters.py
from ..config.parameters import RiskConfig

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
    def apply(self, signal: pd.Series, data: pd.DataFrame) -> Dict:
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
        
    def _calculate_stop_and_target(self, entry_price: float, direction: int, 
                                  risk_value: float, reward_value: float,
                                  risk_type: str = None, reward_type: str = None) -> Dict[str, float]:
        """
        Template method to calculate stop loss and take profit values based on risk parameters.
        Base implementation handles common parameter validation. Specific calculations
        should be implemented by subclasses.
        
        Args:
            entry_price (float): Entry price of the position
            direction (int): Position direction (1 for long, -1 for short)
            risk_value (float): Value for stop loss calculation (meaning depends on risk_type)
            reward_value (float): Value for take profit calculation (meaning depends on reward_type)
            risk_type (str): Type of risk calculation (implementation-dependent)
            reward_type (str): Type of reward calculation (implementation-dependent)
            
        Returns:
            dict: Dictionary with calculated stop loss and take profit values
        """
        # Validate input parameters
        if entry_price <= 0:
            raise ValueError("Entry price must be positive")
        
        if direction not in [1, -1]:
            raise ValueError("Direction must be either 1 (long) or -1 (short)")
            
        # Default to config types if not specified
        if risk_type is None:
            risk_type = self.config.stop_loss_type
        if reward_type is None:
            reward_type = self.config.take_profit_type
            
        # Calculate stop loss and take profit using implementation-specific method
        return self._calculate_risk_values(
            entry_price=entry_price,
            direction=direction,
            risk_value=risk_value,
            reward_value=reward_value,
            risk_type=risk_type,
            reward_type=reward_type
        )
        
    def _calculate_risk_values(self, entry_price: float, direction: int,
                              risk_value: float, reward_value: float,
                              risk_type: str, reward_type: str) -> Dict[str, float]:
        """
        Implementation-specific method to calculate stop loss and take profit values.
        This should be overridden by subclasses.
        
        Args:
            entry_price (float): Entry price of the position
            direction (int): Position direction (1 for long, -1 for short)
            risk_value (float): Value for stop loss calculation
            reward_value (float): Value for take profit calculation
            risk_type (str): Type of risk calculation 
            reward_type (str): Type of reward calculation
            
        Returns:
            dict: Dictionary with stop_loss and take_profit keys
        """
        # Base implementation uses simple percentage-based calculation
        stop_distance = entry_price * (risk_value / 100)
        tp_distance = entry_price * (reward_value / 100)
        
        stop_loss = entry_price - (direction * stop_distance)
        take_profit = entry_price + (direction * tp_distance)
        
        return {
            'stop_loss': stop_loss,
            'take_profit': take_profit
        }

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
        risk_per_trade = getattr(self.config, 'risk_per_trade', 0.01)  # Default 1%
        risk_amount = account_balance * risk_per_trade
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            raise ValueError("Stop loss must not be equal to entry price.")
        position_size = risk_amount / risk_per_unit

        maximum_percent_of_account = getattr(self.config, 'max_position_size_percent', 1.0)  # Default 100%
        max_position_size = account_balance * maximum_percent_of_account / entry_price

        return min(position_size, max_position_size)

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
        
    def initialize_trailing_stop_data(self, 
                                     entry_price: float, 
                                     initial_stop: float, 
                                     direction: int) -> Dict:
        """
        Initialize trailing stop data for a new position.
        
        Args:
            entry_price (float): Entry price of the position
            initial_stop (float): Initial stop loss price
            direction (int): Position direction (1 for long, -1 for short)
            
        Returns:
            Dict: Trailing stop data dictionary
        """
        trailing_config = self.config.trailing_stop
        
        # If trailing stop is not enabled or configured, return minimal data
        if not trailing_config or not trailing_config.enabled:
            return {
                "enabled": False
            }
        
        # Initialize trailing stop data with common fields
        trailing_stop_data = {
            "enabled": True,
            "dynamic_mode": trailing_config.dynamic_mode,
            "levels": trailing_config.levels if trailing_config.levels else {},
            "breakpoints": trailing_config.breakpoints if trailing_config.breakpoints else [],
            "base_trail_r": trailing_config.base_trail_r if hasattr(trailing_config, 'base_trail_r') else 0.5,
            "entry_price": entry_price,
            "direction": direction,
            "current_stop": initial_stop,
            "initial_stop": initial_stop,
            "highest_profit_r": 0.0,  # Track profit in R-multiples (risk units)
            "trailing_active": False
        }
        
        return trailing_stop_data
    
    def update_trailing_stop(self, position: Dict, current_price: float) -> Dict:
        """
        Base implementation for updating trailing stops. This method handles the 
        common logic but delegates the specific trailing stop distance calculation
        to the _calculate_trailing_distance method that should be implemented by subclasses.
        
        Args:
            position (Dict): Current position data with trailing_stop_data
            current_price (float): Current price for comparison (typically close price)
            
        Returns:
            Dict: Updated position with potentially modified stop_loss
        """
        from ..config.columns import TradeColumns
        
        # If trailing stops aren't enabled or no trailing_stop_data, return position unchanged
        if not position.get(TradeColumns.TRAILING_STOP_DATA.value, {}).get('enabled', False):
            return position
        
        # Make a copy to avoid modifying the original
        position = position.copy()
        ts_data = position[TradeColumns.TRAILING_STOP_DATA.value]
        entry_price = ts_data['entry_price']
        direction = ts_data['direction']
        initial_stop = ts_data.get('initial_stop')
        
        # Calculate current profit
        if direction == 1:  # Long position
            current_profit = current_price - entry_price
        else:  # Short position
            current_profit = entry_price - current_price
        
        # Calculate profit in R-multiples
        initial_risk = abs(entry_price - initial_stop) if initial_stop is not None else 1.0
        current_profit_r = current_profit / initial_risk if initial_risk != 0 else 0
        
        # Track highest profit in R-multiples
        if current_profit_r > ts_data.get('highest_profit_r', 0):
            ts_data['highest_profit_r'] = current_profit_r
            
            # Calculate trailing distance using the specific implementation
            # This method should be overridden by subclasses
            new_stop = self._calculate_trailing_stop(
                position=position,
                current_price=current_price,
                profit_r=current_profit_r,
                ts_data=ts_data
            )
            
            # Only update stop if it's valid and better than current stop
            if new_stop is not None:
                if (direction == 1 and new_stop > position.get('stop_loss', float('-inf'))) or \
                   (direction == -1 and new_stop < position.get('stop_loss', float('inf'))):
                    position['stop_loss'] = new_stop
                    ts_data['current_stop'] = new_stop
                    ts_data['trailing_active'] = True
        
        # Update the trailing_stop_data in the position
        position[TradeColumns.TRAILING_STOP_DATA.value] = ts_data
        return position
        
    def _calculate_trailing_stop(self, position: Dict, current_price: float, 
                                profit_r: float, ts_data: Dict) -> Optional[float]:
        """
        Calculate new trailing stop price. This is a placeholder that should be 
        overridden by subclasses with their specific implementation.
        
        Args:
            position: Current position information
            current_price: Current price to calculate from
            profit_r: Current profit in R-multiples (risk units)
            ts_data: Trailing stop data dictionary
            
        Returns:
            Optional[float]: New stop price or None if no update needed
        """
        # Base class doesn't know how to calculate trailing stops
        # Subclasses must implement this method
        return None
