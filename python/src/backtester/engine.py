import pandas as pd

from ..risk_management.base import RiskManagement
from ..strategies.base import StrategyBase
from ..config.columns import TradeColumns
from ..config.Enums import Regime


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

    def __init__(self, strategy: StrategyBase =None, risk_manager: RiskManagement =None, data=None, config=None, initial_capital: float = 10000):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.data = data
        self.config = config
        self.trades = []
        self.initial_capital = initial_capital
        self.equity = [initial_capital]
        self.current_balance = initial_capital
        self.result_df = None  # Will store the result dataframe after running
        
    def _determine_ticker_regime(self, df, index) -> str:
        """
        Determine ticker regime based on RSI value.
        
        Args:
            df: DataFrame with market data
            index: Current row index
            
        Returns:
            str: Ticker regime ('bull', 'bear', or 'sideways')
        """
        # Find any column that starts with 'RSI'
        rsi_cols = [col for col in df.columns if col.startswith('RSI')]
        
        # If no RSI column found, return 'sideways' as default
        if not rsi_cols:
            return Regime.SIDEWAYS.value
            
        # Use the first RSI column found
        rsi_col = rsi_cols[0]
        rsi_value = df[rsi_col].iloc[index]
        
        # Check RSI thresholds
        if rsi_value >= 60:
            return Regime.BULL.value
        elif rsi_value <= 40:
            return Regime.BEAR.value
        else:
            return Regime.SIDEWAYS.value

    def run(self, df=None, signals=None) -> None:
        """
        Runs the backtest loop over the data and stores results internally.
        Use get_trades() or get_result_dataframe() to retrieve results.

        Args:
            df (pd.DataFrame, optional): DataFrame to use instead of self.data
            signals (pd.Series, optional): Pre-generated signals to use

        Returns:
            None
        """
        # Use provided data or fall back to instance data
        data_to_use: pd.DataFrame = df if df is not None else self.data
        
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
                # Get the initial stop from strategy if available
                initial_stop = None
                if self.strategy:
                    is_long = True  # For long signal
                    initial_stop = self.strategy.initial_stop_value(price, is_long, data_to_use.iloc[i])
                
                # Create a signal Series with the required structure for risk_manager.apply()
                signal_series = pd.Series({
                    'entry_price': price,
                    'signal': signal,
                    'index': i,
                    'initial_stop': initial_stop  # Include initial stop from strategy
                })
                
                # Apply risk management if available, otherwise use default stop/take profit
                if self.risk_manager:
                    risk_result = self.risk_manager.apply(signal_series, data_to_use)
                else:
                    # Default risk management (10% below/above entry)
                    risk_result = {
                        'stop_loss': initial_stop if initial_stop is not None else price * 0.9,
                        'take_profit': price * 1.1
                    }
                
                # Calculate position size based on current balance
                risk_per_trade = 0.01  # Default 1% risk
                if self.config and hasattr(self.config, 'risk') and hasattr(self.config.risk, 'risk_per_trade'):
                    risk_per_trade = self.config.risk.risk_per_trade
                position_size = risk_per_trade * self.current_balance
                
                # Determine ticker regime at entry
                ticker_regime = self._determine_ticker_regime(data_to_use, i)
                
                # Create position dictionary with trade information
                position = {
                    TradeColumns.ENTRY_IDX.value: i,
                    TradeColumns.ENTRY_TIME.value: data_to_use.index[i],
                    TradeColumns.ENTRY_PRICE.value: price,
                    TradeColumns.SIZE.value: position_size,
                    TradeColumns.STOP_LOSS.value: risk_result.get('stop_loss'),
                    TradeColumns.TAKE_PROFIT.value: risk_result.get('take_profit'),
                    TradeColumns.ACCOUNT_BALANCE.value: self.current_balance,
                    TradeColumns.DIRECTION.value: signal,  # Store position direction (1 for long, -1 for short)
                    # Store ticker regime information
                    TradeColumns.TICKER_REGIME.value: ticker_regime,
                    # Store trailing stop data if provided
                    TradeColumns.TRAILING_STOP_DATA.value: risk_result.get('trailing_stop_data', None)
                }

            # Entry for short signal
            elif signal == -1 and position is None:
                # Get the initial stop from strategy if available
                initial_stop = None
                if self.strategy:
                    is_long = False  # For short signal
                    initial_stop = self.strategy.initial_stop_value(price, is_long, data_to_use.iloc[i])

                # Create a signal Series with the required structure for risk_manager.apply()
                signal_series = pd.Series({
                    'entry_price': price,
                    'signal': signal,
                    'index': i,
                    'initial_stop': initial_stop  # Include initial stop from strategy
                })

                # Apply risk management if available, otherwise use default stop/take profit
                if self.risk_manager:
                    risk_result = self.risk_manager.apply(signal_series, data_to_use)
                else:
                    # Default risk management (10% above/below entry for short)
                    risk_result = {
                        'stop_loss': initial_stop if initial_stop is not None else price * 1.1,
                        'take_profit': price * 0.9
                    }

                # Calculate position size based on current balance
                risk_per_trade = 0.01  # Default 1% risk
                if self.config and hasattr(self.config, 'risk') and hasattr(self.config.risk, 'risk_per_trade'):
                    risk_per_trade = self.config.risk.risk_per_trade
                position_size = risk_per_trade * self.current_balance

                # Determine ticker regime at entry
                ticker_regime = self._determine_ticker_regime(data_to_use, i)

                # Create position dictionary with trade information
                position = {
                    TradeColumns.ENTRY_IDX.value: i,
                    TradeColumns.ENTRY_TIME.value: data_to_use.index[i],
                    TradeColumns.ENTRY_PRICE.value: price,
                    TradeColumns.SIZE.value: position_size,
                    TradeColumns.STOP_LOSS.value: risk_result.get('stop_loss'),
                    TradeColumns.TAKE_PROFIT.value: risk_result.get('take_profit'),
                    TradeColumns.ACCOUNT_BALANCE.value: self.current_balance,
                    TradeColumns.DIRECTION.value: signal,  # Store position direction (-1 for short)
                    # Store ticker regime information
                    TradeColumns.TICKER_REGIME.value: ticker_regime,
                    # Store trailing stop data if provided
                    TradeColumns.TRAILING_STOP_DATA.value: risk_result.get('trailing_stop_data', None)
                }

            # Exit
            elif signal == -1 and position is not None:
                exit_price = price
                direction = position.get(TradeColumns.DIRECTION.value, 1)  # Default to long if not specified
                entry_price = position[TradeColumns.ENTRY_PRICE.value]
                position_size = position[TradeColumns.SIZE.value]
                
                # Calculate PnL based on position direction
                pnl = direction * (exit_price - entry_price) * position_size
                
                # Update account balance with trade profit/loss
                self.current_balance += pnl
                
                # Get trailing stop info if available
                trailing_info = {}
                ts_data = position.get(TradeColumns.TRAILING_STOP_DATA.value, None)
                if ts_data and ts_data.get('trailing_active', False):
                    trailing_info = {
                        TradeColumns.TRAILING_ACTIVE.value: True,
                        TradeColumns.HIGHEST_PROFIT_ATR.value: ts_data.get('highest_profit_atr', 0.0),
                        TradeColumns.INITIAL_STOP.value: ts_data.get('initial_stop', None)
                    }
                
                trade = {
                    **position,
                    TradeColumns.EXIT_IDX.value: i,
                    TradeColumns.EXIT_TIME.value: data_to_use.index[i],
                    TradeColumns.EXIT_PRICE.value: exit_price,
                    TradeColumns.PNL.value: pnl,
                    TradeColumns.EXIT_REASON.value: "signal",
                    **trailing_info
                }
                self.trades.append(trade)
                position = None

            # Check stop loss/take profit if in position
            elif position is not None:
                # Update trailing stop if applicable
                if self.risk_manager and hasattr(self.risk_manager, 'update_trailing_stop'):
                    close_price = data_to_use["close"].iloc[i]
                    position = self.risk_manager.update_trailing_stop(position, close_price)
                
                stop_loss = position.get(TradeColumns.STOP_LOSS.value)
                take_profit = position.get(TradeColumns.TAKE_PROFIT.value)
                direction = position.get(TradeColumns.DIRECTION.value, 1)  # Default to long if direction not specified
                low = data_to_use["low"].iloc[i]
                high = data_to_use["high"].iloc[i]
                exit_reason = None
                exit_price = None

                # Unified stop loss/take profit logic using direction
                # For stop loss: Check if price moved against position direction
                # For take profit: Check if price moved in position direction
                if stop_loss is not None:
                    # For long positions (direction=1): Check if price went below stop
                    # For short positions (direction=-1): Check if price went above stop
                    if (direction == 1 and low <= stop_loss) or (direction == -1 and high >= stop_loss):
                        exit_price = stop_loss
                        exit_reason = "stop_loss"
                
                # Only check take profit if stop loss wasn't triggered
                if exit_price is None and take_profit is not None:
                    # For long positions (direction=1): Check if price went above target
                    # For short positions (direction=-1): Check if price went below target
                    if (direction == 1 and high >= take_profit) or (direction == -1 and low <= take_profit):
                        exit_price = take_profit
                        exit_reason = "take_profit"

                if exit_price is not None:
                    direction = position.get(TradeColumns.DIRECTION.value, 1)  # Default to long if not specified
                    entry_price = position[TradeColumns.ENTRY_PRICE.value]
                    position_size = position[TradeColumns.SIZE.value]
                    
                    # Calculate PnL based on position direction
                    # For long (1): (exit - entry) * size
                    # For short (-1): (entry - exit) * size = direction * (exit - entry) * size
                    pnl = direction * (exit_price - entry_price) * position_size
                    
                    # Update account balance with trade profit/loss
                    self.current_balance += pnl
                    
                    # Get trailing stop info if available
                    trailing_info = {}
                    ts_data = position.get(TradeColumns.TRAILING_STOP_DATA.value, None)
                    if ts_data and ts_data.get('trailing_active', False):
                        trailing_info = {
                            TradeColumns.TRAILING_ACTIVE.value: True,
                            TradeColumns.HIGHEST_PROFIT_ATR.value: ts_data.get('highest_profit_atr', 0.0),
                            TradeColumns.INITIAL_STOP.value: ts_data.get('initial_stop', None)
                        }
                    
                    trade = {
                        **position,
                        TradeColumns.EXIT_IDX.value: i,
                        TradeColumns.EXIT_TIME.value: data_to_use.index[i],
                        TradeColumns.EXIT_PRICE.value: exit_price,
                        TradeColumns.EXIT_REASON.value: exit_reason,
                        TradeColumns.PNL.value: pnl,
                        **trailing_info
                    }
                    self.trades.append(trade)
                    position = None

            # Check stop loss/take profit for short positions
            elif position is not None and position.get(TradeColumns.DIRECTION.value) == -1:
                stop_loss = position.get(TradeColumns.STOP_LOSS.value)
                take_profit = position.get(TradeColumns.TAKE_PROFIT.value)
                direction = position.get(TradeColumns.DIRECTION.value, -1)  # Default to short if direction not specified
                low = data_to_use["low"].iloc[i]
                high = data_to_use["high"].iloc[i]
                exit_reason = None
                exit_price = None

                # Unified stop loss/take profit logic for short positions
                # For stop loss: Check if price moved against position direction
                # For take profit: Check if price moved in position direction
                if stop_loss is not None:
                    # For short positions (direction=-1): Check if price went above stop
                    if high >= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "stop_loss"

                # Only check take profit if stop loss wasn't triggered
                if exit_price is None and take_profit is not None:
                    # For short positions (direction=-1): Check if price went below target
                    if low <= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"

                if exit_price is not None:
                    entry_price = position[TradeColumns.ENTRY_PRICE.value]
                    position_size = position[TradeColumns.SIZE.value]

                    # Calculate PnL based on position direction
                    # For short (-1): (entry - exit) * size = direction * (exit - entry) * size
                    pnl = direction * (exit_price - entry_price) * position_size

                    # Update account balance with trade profit/loss
                    self.current_balance += pnl

                    # Get trailing stop info if available
                    trailing_info = {}
                    ts_data = position.get(TradeColumns.TRAILING_STOP_DATA.value, None)
                    if ts_data and ts_data.get('trailing_active', False):
                        trailing_info = {
                            TradeColumns.TRAILING_ACTIVE.value: True,
                            TradeColumns.HIGHEST_PROFIT_ATR.value: ts_data.get('highest_profit_atr', 0.0),
                            TradeColumns.INITIAL_STOP.value: ts_data.get('initial_stop', None)
                        }

                    trade = {
                        **position,
                        TradeColumns.EXIT_IDX.value: i,
                        TradeColumns.EXIT_TIME.value: data_to_use.index[i],
                        TradeColumns.EXIT_PRICE.value: exit_price,
                        TradeColumns.EXIT_REASON.value: exit_reason,
                        TradeColumns.PNL.value: pnl,
                        **trailing_info
                    }
                    self.trades.append(trade)
                    position = None

        # If still in position at end, close at last price
        if position is not None:
            exit_price = data_to_use["close"].iloc[-1]
            direction = position.get(TradeColumns.DIRECTION.value, 1)  # Default to long if not specified
            entry_price = position[TradeColumns.ENTRY_PRICE.value]
            position_size = position[TradeColumns.SIZE.value]
            
            # Calculate PnL based on position direction
            pnl = direction * (exit_price - entry_price) * position_size
            
            # Update account balance with trade profit/loss
            self.current_balance += pnl
            
            # Get trailing stop info if available
            trailing_info = {}
            ts_data = position.get(TradeColumns.TRAILING_STOP_DATA.value, None)
            if ts_data and ts_data.get('trailing_active', False):
                trailing_info = {
                    TradeColumns.TRAILING_ACTIVE.value: True,
                    TradeColumns.HIGHEST_PROFIT_ATR.value: ts_data.get('highest_profit_atr', 0.0),
                    TradeColumns.INITIAL_STOP.value: ts_data.get('initial_stop', None)
                }
            
            trade = {
                **position,
                TradeColumns.EXIT_IDX.value: len(data_to_use) - 1,
                TradeColumns.EXIT_TIME.value: data_to_use.index[-1],
                TradeColumns.EXIT_PRICE.value: exit_price,
                TradeColumns.EXIT_REASON.value: "end_of_data",
                TradeColumns.PNL.value: pnl,
                **trailing_info
            }
            self.trades.append(trade)
        
        # Create result dataframe with equity curve
        # Handle case where equity array is longer than data index (common at end of backtest)
        equity_len = min(len(self.equity), len(data_to_use))
        equity_series = pd.Series(self.equity[:equity_len], index=data_to_use.index[:equity_len])
        
        # Store DataFrame with equity column
        self.result_df = data_to_use.copy()
        self.result_df['equity'] = equity_series
        
        # No return value - results are stored in self.trades and self.result_df
        
    def get_trades(self):
        """
        Get the list of trades from the last backtest run.
        
        Returns:
            list: List of trade dictionaries with entry/exit information
        """
        return self.trades
    
    def get_result_dataframe(self):
        """
        Get the result dataframe with market data and equity curve from the last backtest run.
        
        Returns:
            pd.DataFrame: DataFrame with market data and equity column
        """
        return self.result_df
