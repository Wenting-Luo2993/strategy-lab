"""
PercentageStop: Risk management strategy using percentage-based stops and targets.
"""
import pandas as pd
from typing import Dict, Optional
from .base import RiskManagement
from ..backtester.parameters import RiskConfig, TrailingStopConfig

class PercentageStop(RiskManagement):
    """
    Risk management strategy that sets stop loss and take profit based on price percentages.
    Inherits trailing stop functionality from the RiskManagement base class.
    """
    def __init__(
        self,
        config: RiskConfig,
    ) -> None:
        """
        Initialize PercentageStop.
        Args:
            config (RiskConfig): Risk configuration object.
        """
        super().__init__(config)
        
        # If trailing stop config is not provided, create a default one
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
                ]
            )

    def apply(self, signal: pd.Series, data: pd.DataFrame) -> Dict[str, float]:
        """
        Apply percentage-based stop loss and take profit to a trade signal.
        
        Args:
            signal (pd.Series): Trade signal (must contain 'signal', 'entry_price', and 'index').
                               May contain 'initial_stop' from strategy.
            data (pd.DataFrame): Market data.
        Returns:
            dict: {'entry': entry_price, 'stop_loss': stop, 'take_profit': tp}
        """
        entry_price = signal['entry_price']
        idx = int(signal['index'])
        direction = signal['signal']
        strategy_stop = signal.get('initial_stop')
        
        # Calculate stop loss and take profit using the template method
        # This method supports different types including 'percent'
        risk_values = self._calculate_stop_and_target(
            entry_price=entry_price,
            direction=direction,
            risk_value=self.config.stop_loss_value,
            reward_value=self.config.take_profit_value,
            risk_type='percent',  # Override to use percentage-based stops
            reward_type='percent'  # Override to use percentage-based targets
        )
        
        # Extract calculated values
        percent_stop = risk_values['stop_loss']
        tp = risk_values['take_profit']
        
        # Consider strategy-provided stop if available (use the stricter one)
        if strategy_stop is not None:
            if direction == 1:  # Long position - higher stop is stricter
                stop = max(strategy_stop, percent_stop)
            else:  # Short position - lower stop is stricter
                stop = min(strategy_stop, percent_stop)
        else:
            stop = percent_stop
            
        # Store average volatility for later use in trailing stop calculations
        # This is the percentage volatility used instead of ATR
        percent_volatility = self._calculate_percent_volatility(data, idx)
        self.current_volatility = percent_volatility
        
        # Initialize trailing stop data using the base class method
        trailing_stop_data = self.initialize_trailing_stop_data(
            entry_price=entry_price,
            initial_stop=stop,
            direction=direction
        )
        
        # Add percentage-specific data if trailing stops are enabled
        if trailing_stop_data.get('enabled', False):
            trailing_stop_data['volatility'] = percent_volatility
        
        from ..config.columns import TradeColumns
        
        return {
            "entry": entry_price, 
            "stop_loss": stop, 
            "take_profit": tp, 
            TradeColumns.TRAILING_STOP_DATA.value: trailing_stop_data
        }
        
    def _calculate_percent_volatility(self, data: pd.DataFrame, idx: int) -> float:
        """
        Calculate percentage volatility based on recent price action.
        
        Args:
            data: DataFrame with market data
            idx: Current index in the dataframe
            
        Returns:
            float: Percentage volatility
        """
        # Use a simple method - average daily range as percentage of close
        # Look back up to 10 periods if available
        start_idx = max(0, idx - 10)
        recent_data = data.iloc[start_idx:idx+1]
        
        if len(recent_data) > 1:
            # Calculate average daily range as percentage
            daily_ranges = (recent_data['high'] - recent_data['low']) / recent_data['close']
            avg_range_pct = daily_ranges.mean() * 100  # Convert to percentage
            return max(0.5, avg_range_pct)  # Minimum 0.5% volatility
        else:
            # Default if not enough data
            return 1.0  # Default 1% volatility
            
    def _calculate_risk_values(self, entry_price: float, direction: int,
                              risk_value: float, reward_value: float,
                              risk_type: str, reward_type: str) -> Dict[str, float]:
        """
        Percentage-specific implementation to calculate stop loss and take profit values.
        
        Args:
            entry_price (float): Entry price of the position
            direction (int): Position direction (1 for long, -1 for short)
            risk_value (float): Percentage for stop loss
            reward_value (float): Percentage for take profit
            risk_type (str): Type of risk calculation (usually 'percent')
            reward_type (str): Type of reward calculation (usually 'percent')
            
        Returns:
            dict: Dictionary with stop_loss and take_profit keys
        """
        # Calculate stop loss distance based on percentage
        stop_distance = entry_price * (risk_value / 100)
        
        # Apply stop distance based on direction
        stop_loss = entry_price - (direction * stop_distance)
        
        # Calculate take profit distance based on percentage or R-multiple
        if reward_type == 'r_multiple' and stop_distance > 0:
            # R-multiple is based on the risk distance
            tp_distance = stop_distance * reward_value
        else:
            # Default to percentage
            tp_distance = entry_price * (reward_value / 100)
            
        # Apply take profit distance based on direction
        take_profit = entry_price + (direction * tp_distance)
        
        return {
            'stop_loss': stop_loss,
            'take_profit': take_profit
        }
        
    def _calculate_trailing_stop(self, position: Dict, current_price: float, 
                                profit_r: float, ts_data: Dict) -> Optional[float]:
        """
        Percentage-based implementation for calculating trailing stops.
        
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
        volatility = ts_data.get('volatility', 1.0)  # Default to 1% if not provided
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
            
            # Convert R-multiple to percentage of price
            initial_risk = abs(ts_data['entry_price'] - ts_data['initial_stop'])
            risk_percent = (initial_risk / ts_data['entry_price']) * 100
            trail_percent = trail_r * risk_percent
            
            # Calculate trail distance as percentage of current price
            trail_distance = current_price * (trail_percent / 100)
        else:
            # Traditional approach with discrete levels
            levels = sorted(ts_data.get('levels', {}).items(), reverse=True)
            for threshold, level_trail_percent in levels:
                if profit_r >= threshold:
                    # Use volatility as the unit (similar to how ATR is used)
                    trail_distance = current_price * (level_trail_percent * volatility / 100)
                    break
        
        # Calculate new stop price if a trail distance was determined
        if trail_distance is not None and trail_distance > 0:
            if direction == 1:  # Long position
                new_stop = current_price - trail_distance
                return new_stop
            else:  # Short position
                new_stop = current_price + trail_distance
                return new_stop
                
        return None