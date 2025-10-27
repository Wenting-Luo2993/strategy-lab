# Canonical column names used across the project

from enum import Enum

OHLCV = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume"
}

class TradeColumns(Enum):
    # Base trade information
    ENTRY_IDX = "entry_idx"
    ENTRY_TIME = "entry_time"
    ENTRY_PRICE = "entry_price"
    SIZE = "size"
    EXIT_IDX = "exit_idx"
    EXIT_TIME = "exit_time"
    EXIT_PRICE = "exit_price"
    EXIT_REASON = "exit_reason"
    PNL = "pnl"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    
    # Account and position information
    ACCOUNT_BALANCE = "account_balance"
    DIRECTION = "direction"  # 1 for long, -1 for short
    
    # Market condition information
    TICKER_REGIME = "ticker_regime"
    TICKER = "ticker"
    
    # Trailing stop information
    TRAILING_STOP_DATA = "trailing_stop_data"
    TRAILING_ACTIVE = "trailing_active"
    HIGHEST_PROFIT_ATR = "highest_profit_atr"
    HIGHEST_PROFIT_R = "highest_profit_r"
    INITIAL_STOP = "initial_stop"