import pandas as pd

from ..risk_management.base import RiskManagement
from ..strategies.base import StrategyBase
from ..config.columns import TradeColumns
from ..config.Enums import Regime
from ..core import TradeManager


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
        self.result_df = None  # Will store the result dataframe after running
        
        # Initialize trade manager
        self.trade_manager = TradeManager(risk_manager, initial_capital)
        
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
        self.trade_manager.reset()
        self.trades = []
        
        # Generate signals or use provided signals
        signals_to_use = signals if signals is not None else self.strategy.generate_signals(data_to_use)
        
        for i in range(len(data_to_use)):
            signal = signals_to_use.iloc[i]  # Get signal for current bar
            price = data_to_use["close"].iloc[i]
            
            # Update equity at each step with trade manager's current balance
            self.equity.append(self.trade_manager.get_current_balance())

            # Entry
            if position is None and signal != 0:
                # Get the initial stop from strategy if available
                initial_stop = None
                if self.strategy:
                    is_long = signal == 1  # True for long signal, False for short or no signal
                    initial_stop = self.strategy.initial_stop_value(price, is_long, data_to_use.iloc[i])
                
                # Create position using trade manager
                position = self.trade_manager.create_entry_position(
                    price=price,
                    signal=signal,
                    time=data_to_use.index[i],
                    market_data=data_to_use,
                    current_idx=i,
                    initial_stop=initial_stop
                )

            # Exit on signal
            elif position is not None and signal != 0:
                if (position.get(TradeColumns.DIRECTION.value) == 1 and signal == -1) or \
                   (position.get(TradeColumns.DIRECTION.value) == -1 and signal == 1):
                    trade = self.trade_manager.close_position(
                        exit_price=price,
                        time=data_to_use.index[i],
                        current_idx=i,
                        exit_reason="signal"
                    )
                    if trade:
                        self.trades.append(trade)
                        position = None

            # Check stop loss/take profit if in position
            elif position is not None:
                # Update trailing stop
                self.trade_manager.update_trailing_stop(data_to_use, i)
                
                # Check exit conditions
                low = data_to_use["low"].iloc[i]
                high = data_to_use["high"].iloc[i]
                price = data_to_use["close"].iloc[i]
                
                exited, trade = self.trade_manager.check_exit_conditions(
                    current_price=price,
                    high=high,
                    low=low,
                    time=data_to_use.index[i],
                    current_idx=i
                )
                
                if exited and trade:
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
