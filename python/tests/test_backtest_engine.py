import pandas as pd
from types import SimpleNamespace
from datetime import datetime, timedelta

from src.back_test.engine import BacktestEngine
from src.strategies.base import StrategyBase
from src.config.columns import TradeColumns


class ScenarioStrategy(StrategyBase):
    """Minimal incremental strategy for testing BacktestEngine.

    Supports two scenarios:
      - scenario='exit': single long entry then explicit strategy_exit via exit_flag.
      - scenario='reversal': long entry then reversal to short without explicit exit_flag.
    """
    def __init__(self, scenario: str):
        super().__init__(strategy_config=None)
        self.scenario = scenario
        self._in_position = 0

    def initial_stop_value(self, entry_price, is_long, row=None):  # simple fixed stop
        return entry_price - 1.0 if is_long else entry_price + 1.0

    def take_profit_value(self, entry_price, is_long, row=None):  # simple fixed TP
        return entry_price + 2.0 if is_long else entry_price - 2.0

    def generate_signal_incremental(self, df: pd.DataFrame):
        idx = len(df) - 1
        # Scenario: explicit exit via exit_flag
        if self.scenario == 'exit':
            if idx == 2 and self._in_position == 0:
                self._in_position = 1
                return 1, False  # entry long
            if idx == 5 and self._in_position == 1:
                # trigger exit flag
                self._in_position = 0
                return 0, True
            return 0, False
        # Scenario: reversal from long to short
        if self.scenario == 'reversal':
            if idx == 2 and self._in_position == 0:
                self._in_position = 1
                return 1, False
            if idx == 4 and self._in_position == 1:
                # produce opposite entry signal; let engine close + reopen
                return -1, False
            if idx == 7 and self._in_position == -1:  # later explicit strategy exit of short
                self._in_position = 0
                return 0, True
            return 0, False
        return 0, False


class MockRiskManager:
    def __init__(self):
        self.config = SimpleNamespace(position_allocation_cap_percent=1.0)

    def apply(self, signal_series, market_data):
        entry_price = signal_series['entry_price']
        direction = signal_series['signal']
        stop_loss = entry_price - 1.0 * direction  # crude directional stop
        take_profit = entry_price + 2.0 * direction
        return {
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'trailing_stop_data': {'trailing_active': False}
        }

    def calculate_position_size(self, current_balance, price, initial_stop):
        return 10  # fixed size for determinism

    def update_trailing_stop(self, position, close_price):
        return position  # no-op


def make_ohlcv(n=10, base_price=100.0):
    start = datetime(2024, 1, 1, 9, 30)
    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        close = base_price + i * 0.5
        high = close + 0.2
        low = close - 0.2
        open_p = close - 0.1
        rows.append((ts, open_p, high, low, close, 1000))
    df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).set_index('timestamp')
    return df


def test_backtest_engine_incremental_exit_flag():
    df = make_ohlcv(8)
    strategy = ScenarioStrategy(scenario='exit')
    engine = BacktestEngine(strategy=strategy, risk_manager=MockRiskManager(), data=df, initial_capital=10000)
    engine.run()
    trades = engine.get_trades()
    assert len(trades) == 1, 'Should have exactly one completed trade'
    trade = trades[0]
    assert trade[TradeColumns.EXIT_REASON.value] == 'strategy_exit'
    result = engine.get_result_dataframe()
    assert 'signal' in result.columns and 'exit_flag' in result.columns
    # Entry at index 2
    assert result['signal'].iloc[2] == 1
    # Exit flag at index 5
    assert result['exit_flag'].iloc[5] == 1
    # Ensure no other exit flags
    assert result['exit_flag'].sum() == 1


def test_backtest_engine_incremental_reversal_and_exit():
    # Custom price path to avoid triggering stop_loss prematurely on short position:
    # Rising into long entry then modest decline after reversal so stop (entry+1) not hit.
    start = datetime(2024, 1, 1, 9, 30)
    rows = []
    base_price = 100.0
    for i in range(10):
        ts = start + timedelta(minutes=i)
        if i < 4:
            close = base_price + i * 0.5  # rise until reversal signal
        else:
            # After short entry (i>=4) drift lower slowly (0.2 steps) so high stays below stop
            close = base_price + 4 * 0.5 - (i - 4) * 0.2
        high = close + 0.15
        low = close - 0.15
        open_p = close - 0.05
        rows.append((ts, open_p, high, low, close, 1000))
    df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']).set_index('timestamp')
    strategy = ScenarioStrategy(scenario='reversal')
    engine = BacktestEngine(strategy=strategy, risk_manager=MockRiskManager(), data=df, initial_capital=10000)
    engine.run()
    trades = engine.get_trades()
    # Expect two trades: first long closed by reversal, second short closed by explicit exit_flag later
    assert len(trades) == 2, f'Expected 2 trades, got {len(trades)}'
    assert trades[0][TradeColumns.EXIT_REASON.value] == 'reversal_signal'
    assert trades[1][TradeColumns.EXIT_REASON.value] == 'strategy_exit'
    result = engine.get_result_dataframe()
    # Signals: long at 2, short at 4
    assert result['signal'].iloc[2] == 1
    assert result['signal'].iloc[4] == -1
    # Exit flag for short at index 7
    assert result['exit_flag'].iloc[7] == 1
    # Equity series length matches data length
    assert len(result['equity']) == len(df)
