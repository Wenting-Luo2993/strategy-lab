"""
FixedATRStop: Risk management strategy using fixed and trailing ATR-based stops and targets.
"""
import pandas as pd
from typing import Dict, Optional
from .base import RiskManagement
from ..config.parameters import RiskConfig, TrailingStopConfig

class FixedATRStop(RiskManagement):
    """
    Risk management strategy that sets stop loss and take profit based on ATR multiples.
    Supports both fixed and trailing stops.
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
        
        # If trailing stop config is not provided, create a default one
        # Moved to base class, but we can still add ATR-specific defaults here if needed
        if not self.config.trailing_stop:
            # Use dynamic trailing stop by default
            self.config.trailing_stop = TrailingStopConfig(
                enabled=True,
                dynamic_mode=True,
                base_trail_r=0.5,
                breakpoints=[
                    [2.0, 1.0],   # At 2R profit, trail by 1R
                    [3.0, 1.5],   # At 3R profit, trail by 1.5R
                    [5.0, 2.0],   # At 5R profit, trail by 2R
                    [10.0, 3.0],  # At 10R profit, trail by 3R
                ],
                # Also provide static levels as fallback
                levels={
                    2.0: 0.5,  # At 2 ATR profit, trail by 0.5 ATR
                    2.5: 1.0,  # At 2.5 ATR profit, trail by 1 ATR
                    3.0: 2.0,  # At 3 ATR profit, trail by 2 ATR
                    4.0: 3.0,  # At 4 ATR profit, trail by 3 ATR
                    5.0: 4.0   # At 5 ATR profit, trail by 4 ATR
                }
            )

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
        
        # Get the ATR value
        atr = data.iloc[idx][self.atr_col]
        
        # Store ATR value for later use
        self.current_atr = atr
        
        # Calculate stop loss and take profit using the template method
        risk_values = self._calculate_stop_and_target(
            entry_price=entry_price,
            direction=direction,
            risk_value=self.config.stop_loss_value,
            reward_value=self.config.take_profit_value,
            risk_type=self.config.stop_loss_type,
            reward_type=self.config.take_profit_type
        )
        
        # Extract calculated values
        atr_stop = risk_values['stop_loss']
        tp = risk_values['take_profit']
        
        # Consider strategy-provided stop if available (use the stricter one)
        if strategy_stop is not None:
            if direction == 1:  # Long position - higher stop is stricter
                stop = max(strategy_stop, atr_stop)
            else:  # Short position - lower stop is stricter
                stop = min(strategy_stop, atr_stop)
        else:
            stop = atr_stop
        
        # Initialize trailing stop data using the base class method
        # and then extend it with ATR-specific data
        trailing_stop_data = self.initialize_trailing_stop_data(
            entry_price=entry_price,
            initial_stop=stop,
            direction=direction
        )
        
        # Add ATR-specific data if trailing stops are enabled
        if trailing_stop_data.get('enabled', False):
            trailing_stop_data['atr'] = atr
            trailing_stop_data['highest_profit_atr'] = 0.0
        
        from ..config.columns import TradeColumns
        
        return {
            "entry": entry_price, 
            "stop_loss": stop, 
            "take_profit": tp, 
            TradeColumns.TRAILING_STOP_DATA.value: trailing_stop_data
        }

    def _calculate_risk_values(self, entry_price: float, direction: int,
                              risk_value: float, reward_value: float,
                              risk_type: str, reward_type: str) -> Dict[str, float]:
        """
        ATR-specific implementation to calculate stop loss and take profit values.
        
        Args:
            entry_price (float): Entry price of the position
            direction (int): Position direction (1 for long, -1 for short)
            risk_value (float): ATR multiplier for stop loss
            reward_value (float): ATR multiplier for take profit
            risk_type (str): Type of risk calculation ('atr', 'percent', 'fixed')
            reward_type (str): Type of reward calculation ('atr', 'percent', 'fixed', 'r_multiple')
            
        Returns:
            dict: Dictionary with stop_loss and take_profit keys
        """
        # Use current_atr which was set in apply() method
        atr = getattr(self, 'current_atr', 1.0)
        
        # Calculate stop loss distance based on type
        if risk_type == 'atr':
            stop_distance = risk_value * atr
        elif risk_type == 'percent':
            stop_distance = entry_price * (risk_value / 100)
        elif risk_type == 'fixed':
            stop_distance = risk_value
        else:
            stop_distance = risk_value * atr  # Default to ATR
            
        # Apply stop distance based on direction
        stop_loss = entry_price - (direction * stop_distance)
        
        # Calculate take profit distance based on type
        if reward_type == 'atr':
            tp_distance = reward_value * atr
        elif reward_type == 'percent':
            tp_distance = entry_price * (reward_value / 100)
        elif reward_type == 'fixed':
            tp_distance = reward_value
        elif reward_type == 'r_multiple' and stop_distance > 0:
            # R-multiple is based on the risk distance
            tp_distance = stop_distance * reward_value
        else:
            tp_distance = reward_value * atr  # Default to ATR
            
        # Apply take profit distance based on direction
        take_profit = entry_price + (direction * tp_distance)
        
        return {
            'stop_loss': stop_loss,
            'take_profit': take_profit
        }
        
    def _calculate_trailing_stop(self, position: Dict, current_price: float, 
                                profit_r: float, ts_data: Dict) -> Optional[float]:
        """
        ATR-specific implementation for calculating trailing stops.
        
        Args:
            position: Current position information
            current_price: Current price to calculate from
            profit_r: Current profit in R-multiples (risk units)
            ts_data: Trailing stop data dictionary
            
        Returns:
            Optional[float]: New stop price or None if no update needed
        """
        from ..config.columns import TradeColumns
        
        direction = ts_data['direction']
        atr = ts_data.get('atr', 1.0)
        trail_distance = None
        
        # Check if we're using dynamic mode or static levels
        if ts_data.get('dynamic_mode', False) and ts_data.get('breakpoints'):
            # Dynamic trailing stop based on R-multiples
            trail_r = ts_data.get('base_trail_r', 0.5)  # Start with base trailing amount
            
            # Sort breakpoints by profit threshold (ascending)
            breakpoints = sorted(ts_data['breakpoints'], key=lambda x: x[0])
            
            # Find appropriate trailing distance based on current profit
            for threshold_r, trailing_r in breakpoints:
                if profit_r >= threshold_r:
                    trail_r = trailing_r
                else:
                    break
            
            # Convert R-multiple to ATR units
            initial_risk = abs(ts_data['entry_price'] - ts_data['initial_stop'])
            trail_distance = trail_r * (initial_risk / atr) if atr != 0 else 0
        else:
            # Traditional approach with discrete levels
            # Convert profit_r to profit_atr for compatibility with levels
            profit_atr = profit_r * (atr / initial_risk) if initial_risk != 0 else 0
            ts_data['highest_profit_atr'] = profit_atr  # Update for historical tracking
            
            levels = sorted(ts_data.get('levels', {}).items(), reverse=True)
            for threshold, level_trail_distance in levels:
                if profit_atr >= threshold:
                    trail_distance = level_trail_distance
                    break
        
        # Calculate new stop price if a trail distance was determined
        if trail_distance is not None and trail_distance > 0:
            if direction == 1:  # Long position
                new_stop = current_price - (trail_distance * atr)
                return new_stop
            else:  # Short position
                new_stop = current_price + (trail_distance * atr)
                return new_stop
                
        return None
        
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
