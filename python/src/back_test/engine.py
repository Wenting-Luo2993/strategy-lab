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

        # Prepare containers for incremental signals & exits
        incremental_signals = []  # entry signals only (1, -1, 0)
        exit_flags = []          # 1 when strategy requested exit on that bar else 0

        use_incremental = hasattr(self.strategy, 'generate_signal_incremental') and signals is None
        if not use_incremental:
            # Fallback: use provided signals or batch generation (legacy path)
            signals_to_use = signals if signals is not None else self.strategy.generate_signals(data_to_use)

        for i in range(len(data_to_use)):
            row_time = data_to_use.index[i]
            price = data_to_use["close"].iloc[i]

            # Obtain per-bar entry signal & exit flag
            if use_incremental:
                latest_slice = data_to_use.iloc[:i+1]
                entry_signal, exit_flag = self.strategy.generate_signal_incremental(latest_slice)
                signal = entry_signal  # for backwards-compatible variable name usage below
                incremental_signals.append(signal)
                exit_flags.append(1 if exit_flag else 0)
            else:
                signal = signals_to_use.iloc[i]
                incremental_signals.append(signal)
                exit_flags.append(0)  # no explicit exit flag info in legacy path

            # Update equity curve tracking
            self.equity.append(self.trade_manager.get_current_balance())

            # ENTRY LOGIC
            if position is None and signal != 0:
                initial_stop = None
                if self.strategy:
                    is_long = signal == 1
                    try:
                        initial_stop = self.strategy.initial_stop_value(price, is_long, data_to_use.iloc[i])
                    except Exception:
                        initial_stop = None
                position = self.trade_manager.create_entry_position(
                    price=price,
                    signal=signal,
                    time=row_time,
                    market_data=data_to_use,
                    current_idx=i,
                    initial_stop=initial_stop
                )
                continue  # move to next bar

            # EXIT due to explicit strategy exit flag (incremental path only)
            if position is not None and use_incremental and exit_flags[-1] == 1:
                trade = self.trade_manager.close_position(
                    exit_price=price,
                    time=row_time,
                    current_idx=i,
                    exit_reason="strategy_exit"
                )
                if trade:
                    self.trades.append(trade)
                    position = None
                # Ensure strategy internal state reset mirrors trade closure (if risk manager closed earlier)
                if hasattr(self.strategy, '_in_position'):
                    self.strategy._in_position = 0
                    self.strategy._entry_price = None
                    self.strategy._take_profit = None
                    self.strategy._initial_stop = None
                continue

            # REVERSAL: opposite entry signal while in position
            if position is not None and signal != 0:
                current_dir = position.get(TradeColumns.DIRECTION.value)
                if (current_dir == 1 and signal == -1) or (current_dir == -1 and signal == 1):
                    # Close existing
                    trade = self.trade_manager.close_position(
                        exit_price=price,
                        time=row_time,
                        current_idx=i,
                        exit_reason="reversal_signal"
                    )
                    if trade:
                        self.trades.append(trade)
                    position = None
                    # Reset strategy state if incremental path
                    if use_incremental and hasattr(self.strategy, '_in_position'):
                        self.strategy._in_position = 0
                        self.strategy._entry_price = None
                        self.strategy._take_profit = None
                        self.strategy._initial_stop = None
                    # Open new position for reversal
                    initial_stop = None
                    if self.strategy:
                        is_long = signal == 1
                        try:
                            initial_stop = self.strategy.initial_stop_value(price, is_long, data_to_use.iloc[i])
                        except Exception:
                            initial_stop = None
                    position = self.trade_manager.create_entry_position(
                        price=price,
                        signal=signal,
                        time=row_time,
                        market_data=data_to_use,
                        current_idx=i,
                        initial_stop=initial_stop
                    )
                    # Sync strategy internal state for reversal entry (strategy did not set it itself)
                    if use_incremental and hasattr(self.strategy, '_in_position') and position is not None:
                        self.strategy._in_position = signal
                        self.strategy._entry_price = price
                        is_long = signal == 1
                        try:
                            self.strategy._take_profit = self.strategy.take_profit_value(price, is_long=is_long, row=data_to_use.iloc[i])
                        except Exception:
                            self.strategy._take_profit = None
                        self.strategy._initial_stop = initial_stop
                continue

            # RISK MANAGEMENT EXITS (stop loss / take profit / trailing)
            if position is not None:
                self.trade_manager.update_trailing_stop(data_to_use, i)
                low = data_to_use["low"].iloc[i]
                high = data_to_use["high"].iloc[i]
                exited, exit_data = self.trade_manager.check_exit_conditions(
                    current_price=price,
                    high=high,
                    low=low,
                    time=row_time,
                    current_idx=i
                )
                if exited and exit_data:
                    trade = self.trade_manager.close_position(
                        exit_price=exit_data['exit_price'],
                        time=exit_data['exit_time'],
                        current_idx=exit_data['exit_idx'],
                        exit_reason=exit_data['exit_reason'],
                        ticker=exit_data['ticker']
                    )
                    if trade:
                        self.trades.append(trade)
                        position = None
                    # Keep strategy state consistent if incremental
                    if use_incremental and hasattr(self.strategy, '_in_position'):
                        self.strategy._in_position = 0
                        self.strategy._entry_price = None
                        self.strategy._take_profit = None
                        self.strategy._initial_stop = None

        # If still in position at end, close via trade manager
        if position is not None:
            exit_price = data_to_use["close"].iloc[-1]
            trade = self.trade_manager.close_position(
                exit_price=exit_price,
                time=data_to_use.index[-1],
                current_idx=len(data_to_use) - 1,
                exit_reason="end_of_data"
            )
            if trade:
                self.trades.append(trade)

        # Create result dataframe with equity curve and signal metadata
        equity_len = min(len(self.equity), len(data_to_use))
        equity_series = pd.Series(self.equity[:equity_len], index=data_to_use.index[:equity_len])
        self.result_df = data_to_use.copy()
        self.result_df['equity'] = equity_series
        # Attach incremental signals & exit flags
        self.result_df['signal'] = pd.Series(incremental_signals, index=data_to_use.index)
        self.result_df['exit_flag'] = pd.Series(exit_flags, index=data_to_use.index)

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
