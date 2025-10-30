import pandas as pd
import pytest
from datetime import datetime

from src.core.trade_manager import TradeManager
from src.config.columns import TradeColumns
from src.config.Enums import Regime

from .utils import MockRiskManager, make_market_data_one_day


def test_create_entry_position_basic():
    rm = MockRiskManager()
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    # Use a subset of generated data for simplicity in this test (first 10 bars)
    df_full = make_market_data_one_day(rsi_sequence=[65] + [50]*77)
    df = df_full.iloc[:10]
    pos = tm.create_entry_position(price=101.0, signal=1, time=df.index[0], market_data=df, current_idx=0, initial_stop=100.5)

    assert pos[TradeColumns.ENTRY_PRICE.value] == 101.0
    assert pos[TradeColumns.DIRECTION.value] == 1
    assert pos[TradeColumns.STOP_LOSS.value] == 100.0  # entry - offset
    assert pos[TradeColumns.TAKE_PROFIT.value] == 103.0
    assert pos[TradeColumns.SIZE.value] == 10
    assert pos[TradeColumns.TICKER_REGIME.value] == Regime.BULL.value
    assert tm.get_current_position() is pos
    assert pos[TradeColumns.TICKER.value] in tm.current_positions


def test_create_entry_position_stop_loss_adjustment_long():
    rm = MockRiskManager(stop_loss_offset=0.3)  # risk manager sets stop to 100.7
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df = make_market_data_one_day().iloc[:10]
    # Provide initial_stop less protective (100.9) -> for long should take max(initial, risk_manager)
    pos = tm.create_entry_position(price=101.0, signal=1, time=df.index[0], market_data=df, current_idx=0, initial_stop=100.9)
    assert pos[TradeColumns.STOP_LOSS.value] == pytest.approx(100.7)


def test_create_entry_position_stop_loss_adjustment_short():
    rm = MockRiskManager(stop_loss_offset=0.3)
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df = make_market_data_one_day().iloc[:10]
    # Short trade: initial_stop above entry maybe 101.2, risk mgr sets stop to 101.3 -> choose min for short
    pos = tm.create_entry_position(price=101.0, signal=-1, time=df.index[0], market_data=df, current_idx=0, initial_stop=101.2)
    assert pos[TradeColumns.STOP_LOSS.value] == pytest.approx(101.3)


def test_determine_ticker_regime_variants():
    rm = MockRiskManager()
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df_bull = make_market_data_one_day(rsi_sequence=[70] + [50]*77).iloc[:10]
    df_bear = make_market_data_one_day(rsi_sequence=[30] + [50]*77).iloc[:10]
    df_side = make_market_data_one_day(rsi_sequence=[50] * 78).iloc[:10]

    assert tm.determine_ticker_regime(df_bull, 0) == Regime.BULL.value
    assert tm.determine_ticker_regime(df_bear, 0) == Regime.BEAR.value
    assert tm.determine_ticker_regime(df_side, 0) == Regime.SIDEWAYS.value

    # No RSI column case
    df_no_rsi = df_side.drop(columns=['RSI_14'])
    assert tm.determine_ticker_regime(df_no_rsi, 0) == Regime.SIDEWAYS.value


def test_check_exit_conditions_long_stop_and_take_profit():
    rm = MockRiskManager(stop_loss_offset=1.0, take_profit_offset=2.0)
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df = make_market_data_one_day().iloc[:10]
    pos = tm.create_entry_position(price=101.0, signal=1, time=df.index[0], market_data=df, current_idx=0, initial_stop=100.5)

    # Simulate a bar hitting stop loss first
    exit_triggered, trade = tm.check_exit_conditions(current_price=100.0, high=100.2, low=99.8, time=df.index[1], current_idx=1)
    assert exit_triggered is True
    assert trade[TradeColumns.EXIT_REASON.value] == 'stop_loss'
    assert tm.get_current_position() is None

    # New long position for take profit test
    pos2 = tm.create_entry_position(price=102.0, signal=1, time=df.index[2], market_data=df, current_idx=2, initial_stop=101.0)
    exit_triggered, trade = tm.check_exit_conditions(current_price=104.0, high=104.2, low=103.5, time=df.index[3], current_idx=3)
    assert exit_triggered is True
    assert trade[TradeColumns.EXIT_REASON.value] == 'take_profit'


def test_check_exit_conditions_short_stop_and_take_profit():
    rm = MockRiskManager(stop_loss_offset=1.0, take_profit_offset=2.0)
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df = make_market_data_one_day().iloc[:10]
    pos = tm.create_entry_position(price=101.0, signal=-1, time=df.index[0], market_data=df, current_idx=0, initial_stop=101.5)

    # Simulate stop loss hit (price goes above stop)
    exit_triggered, trade = tm.check_exit_conditions(current_price=102.5, high=102.6, low=101.9, time=df.index[1], current_idx=1)
    assert exit_triggered is True
    assert trade[TradeColumns.EXIT_REASON.value] == 'stop_loss'

    # New short position for take profit
    pos2 = tm.create_entry_position(price=100.0, signal=-1, time=df.index[2], market_data=df, current_idx=2, initial_stop=100.5)
    exit_triggered, trade = tm.check_exit_conditions(current_price=97.5, high=98.0, low=97.4, time=df.index[3], current_idx=3)
    assert exit_triggered is True
    assert trade[TradeColumns.EXIT_REASON.value] == 'take_profit'


def test_close_position_updates_balance_and_storage():
    rm = MockRiskManager()
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df = make_market_data_one_day().iloc[:10]
    pos = tm.create_entry_position(price=101.0, signal=1, time=df.index[0], market_data=df, current_idx=0, initial_stop=100.5)
    balance_before = tm.get_current_balance()
    trade = tm.close_position(exit_price=103.0, time=df.index[1], current_idx=1, exit_reason='manual')
    assert trade[TradeColumns.PNL.value] > 0
    assert tm.get_current_balance() > balance_before
    assert tm.get_current_position() is None
    assert len(tm.get_closed_positions()) == 1


def test_update_trailing_stop_activation():
    rm = MockRiskManager(trailing=True)
    tm = TradeManager(risk_manager=rm, initial_capital=10000)
    df = make_market_data_one_day().iloc[:10]
    pos = tm.create_entry_position(price=101.0, signal=1, time=df.index[0], market_data=df, current_idx=0, initial_stop=100.0)

    # Price move small -> no activation
    tm.update_trailing_stop(pd.DataFrame({'close': [101.4]}), 0)
    assert pos[TradeColumns.TRAILING_STOP_DATA.value]['trailing_active'] is False

    # Larger profit -> activation
    tm.update_trailing_stop(pd.DataFrame({'close': [102.2]}), 0)
    updated_pos = tm.get_current_position()
    assert updated_pos[TradeColumns.TRAILING_STOP_DATA.value]['trailing_active'] is True
    assert updated_pos['stop_loss'] >= pos['stop_loss']


def test_reset_clears_state():
    rm = MockRiskManager()
    tm = TradeManager(risk_manager=rm, initial_capital=5000)
    df = make_market_data_one_day(base_price=50.0).iloc[:10]
    pos = tm.create_entry_position(price=50.0, signal=1, time=df.index[0], market_data=df, current_idx=0, initial_stop=49.0)
    tm.close_position(exit_price=52.0, time=df.index[1], current_idx=1, exit_reason='manual')
    tm.reset()
    assert tm.get_current_balance() == 5000
    assert tm.get_current_position() is None
    assert tm.current_positions == {}
    assert tm.get_closed_positions() == []


def test_multi_ticker_position_management(market_data_sets, risk_manager):
    """Verify managing multiple simultaneous positions for different tickers/regimes."""
    tm = TradeManager(risk_manager=risk_manager, initial_capital=20000)

    bull_df = market_data_sets['bull']
    bear_df = market_data_sets['bear']
    side_df = market_data_sets['side']

    # Create three positions with explicit tickers
    bull_pos = tm.create_entry_position(price=bull_df['open'].iloc[0], signal=1, time=bull_df.index[0], market_data=bull_df, current_idx=0, initial_stop=bull_df['open'].iloc[0]-0.5, ticker='BULL')
    bear_pos = tm.create_entry_position(price=bear_df['open'].iloc[0], signal=-1, time=bear_df.index[0], market_data=bear_df, current_idx=0, initial_stop=bear_df['open'].iloc[0]+0.5, ticker='BEAR')
    side_pos = tm.create_entry_position(price=side_df['open'].iloc[0], signal=1, time=side_df.index[0], market_data=side_df, current_idx=0, initial_stop=side_df['open'].iloc[0]-0.5, ticker='SIDE')

    assert set(tm.current_positions.keys()) == {'BULL', 'BEAR', 'SIDE'}
    assert bull_pos[TradeColumns.TICKER_REGIME.value] == 'bull'
    assert bear_pos[TradeColumns.TICKER_REGIME.value] == 'bear'
    assert side_pos[TradeColumns.TICKER_REGIME.value] == 'sideways'

    # Trigger exit for one ticker (simulate take profit on BULL)
    bull_tp = bull_pos[TradeColumns.TAKE_PROFIT.value]
    triggered, trade = tm.check_exit_conditions(current_price=bull_tp + 0.1, high=bull_tp + 0.1, low=bull_tp - 0.2, time=bull_df.index[1], current_idx=1, ticker='BULL')
    assert triggered is True
    assert trade[TradeColumns.EXIT_REASON.value] == 'take_profit'
    assert 'BULL' not in tm.current_positions

    # Ensure other positions remain
    assert 'BEAR' in tm.current_positions and 'SIDE' in tm.current_positions

    # Trigger stop loss for BEAR (short position stop above)
    bear_stop = bear_pos[TradeColumns.STOP_LOSS.value]
    triggered2, trade2 = tm.check_exit_conditions(current_price=bear_stop + 0.1, high=bear_stop + 0.1, low=bear_stop - 0.2, time=bear_df.index[1], current_idx=1, ticker='BEAR')
    assert triggered2 is True
    assert trade2[TradeColumns.EXIT_REASON.value] == 'stop_loss'
    assert 'BEAR' not in tm.current_positions

    # Close remaining SIDE manually
    side_trade = tm.close_position(exit_price=side_pos[TradeColumns.ENTRY_PRICE.value] + 1.0, time=side_df.index[2], current_idx=2, exit_reason='manual', ticker='SIDE')
    assert side_trade[TradeColumns.PNL.value] != 0
    assert tm.current_positions == {}
    assert len(tm.get_closed_positions()) == 3
