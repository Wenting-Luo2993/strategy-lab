import pandas as pd

from ..risk_management.base import RiskManagement
from .parameters import StrategyConfig
from ..strategies.base import StrategyBase
from ..config.columns import TradeColumns


class BacktestEngine:
    """
    Modular backtest engine for simulating trading strategies.

    Args:
        strategy: Object with generate_signals(df) -> pd.Series (+1=buy, -1=sell, 0=hold), can be None when using direct run(df, signals)
        risk_manager: Object with apply(trade, df, i, config) -> dict (sets stop loss/take profit), can be None when using direct run(df, signals)
        data (pd.DataFrame): Historical OHLCV data, can be None when using direct run(df, signals)
        config (dict): Configuration parameters, can be None when using direct run(df, signals)
        initial_capital (float): Starting capital amount
    """

    def __init__(self, strategy=None, risk_manager=None, data=None, config=None, initial_capital: float = 10000):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.data = data
        self.config = config
        self.trades = []
        self.initial_capital = initial_capital
        self.equity = [initial_capital]
        self.current_balance = initial_capital

    def run(self, df=None, signals=None) -> dict:
        """
        Runs the backtest loop over the data.

        Args:
            df (pd.DataFrame, optional): DataFrame to use instead of self.data
            signals (pd.Series, optional): Pre-generated signals to use

        Returns:
            dict: Dictionary with trade list and equity curve
        """
        # Use provided data or fall back to instance data
        data_to_use = df if df is not None else self.data
        
        position = None  # None or dict with entry info
        # Reset equity tracking and trades list
        self.equity = [self.initial_capital]
        self.current_balance = self.initial_capital
        self.trades = []
        
        # Generate signals or use provided signals
        signals_to_use = signals if signals is not None else self.strategy.generate_signals(data_to_use)
        
        for i in range(len(data_to_use)):
            signal = signals_to_use.iloc[i]  # Get signal for current bar
            price = data_to_use["close"].iloc[i]
            
            # Update equity at each step with current balance
            self.equity.append(self.current_balance)

            # Entry
            if signal == 1 and position is None:
                # Create a signal Series with the required structure for risk_manager.apply()
                signal_series = pd.Series({
                    'entry_price': price,
                    'signal': signal,
                    'index': i
                })
                # Apply risk management if available, otherwise use default stop/take profit
                if self.risk_manager:
                    risk_result = self.risk_manager.apply(signal_series, data_to_use)
                else:
                    # Default risk management (10% below/above entry)
                    risk_result = {
                        'stop_loss': price * 0.9,
                        'take_profit': price * 1.1
                    }
                
                # Calculate position size based on current balance
                risk_per_trade = 0.01  # Default 1% risk
                if self.config and hasattr(self.config, 'risk') and hasattr(self.config.risk, 'risk_per_trade'):
                    risk_per_trade = self.config.risk.risk_per_trade
                position_size = risk_per_trade * self.current_balance
                
                # Create position dictionary with trade information
                position = {
                    TradeColumns.ENTRY_IDX.value: i,
                    TradeColumns.ENTRY_TIME.value: data_to_use.index[i],
                    TradeColumns.ENTRY_PRICE.value: price,
                    TradeColumns.SIZE.value: position_size,
                    TradeColumns.STOP_LOSS.value: risk_result.get('stop_loss'),
                    TradeColumns.TAKE_PROFIT.value: risk_result.get('take_profit'),
                    'account_balance': self.current_balance
                }

            # Exit
            elif signal == -1 and position is not None:
                exit_price = price
                pnl = (exit_price - position[TradeColumns.ENTRY_PRICE.value]) * position[TradeColumns.SIZE.value]
                
                # Update account balance with trade profit/loss
                self.current_balance += pnl
                
                trade = {
                    **position,
                    TradeColumns.EXIT_IDX.value: i,
                    TradeColumns.EXIT_TIME.value: data_to_use.index[i],
                    TradeColumns.EXIT_PRICE.value: exit_price,
                    TradeColumns.PNL.value: pnl,
                    TradeColumns.EXIT_REASON.value: "signal"
                }
                self.trades.append(trade)
                position = None

            # Check stop loss/take profit if in position
            elif position is not None:
                stop_loss = position.get(TradeColumns.STOP_LOSS.value)
                take_profit = position.get(TradeColumns.TAKE_PROFIT.value)
                low = data_to_use["low"].iloc[i]
                high = data_to_use["high"].iloc[i]
                exit_reason = None
                exit_price = None

                if stop_loss is not None and low <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif take_profit is not None and high >= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"

                if exit_price is not None:
                    pnl = (exit_price - position[TradeColumns.ENTRY_PRICE.value]) * position[TradeColumns.SIZE.value]
                    
                    # Update account balance with trade profit/loss
                    self.current_balance += pnl
                    
                    trade = {
                        **position,
                        TradeColumns.EXIT_IDX.value: i,
                        TradeColumns.EXIT_TIME.value: data_to_use.index[i],
                        TradeColumns.EXIT_PRICE.value: exit_price,
                        TradeColumns.EXIT_REASON.value: exit_reason,
                        TradeColumns.PNL.value: pnl
                    }
                    self.trades.append(trade)
                    position = None

        # If still in position at end, close at last price
        if position is not None:
            exit_price = data_to_use["close"].iloc[-1]
            pnl = (exit_price - position[TradeColumns.ENTRY_PRICE.value]) * position[TradeColumns.SIZE.value]
            
            # Update account balance with trade profit/loss
            self.current_balance += pnl
            
            trade = {
                **position,
                TradeColumns.EXIT_IDX.value: len(data_to_use) - 1,
                TradeColumns.EXIT_TIME.value: data_to_use.index[-1],
                TradeColumns.EXIT_PRICE.value: exit_price,
                TradeColumns.EXIT_REASON.value: "end_of_data",
                TradeColumns.PNL.value: pnl
            }
            self.trades.append(trade)
        
        # Create result dictionary with equity curve
        # Handle case where equity array is longer than data index (common at end of backtest)
        equity_len = min(len(self.equity), len(data_to_use))
        equity_series = pd.Series(self.equity[:equity_len], index=data_to_use.index[:equity_len])
        
        # For main_test.py compatibility, return DataFrame with equity column
        result_df = data_to_use.copy()
        result_df['equity'] = equity_series
        
        # For backtest_orchestrator.py compatibility
        if len(self.trades) > 0 and self.config is not None:
            return self.trades
        
        # Default return format for direct API usage
        return result_df
