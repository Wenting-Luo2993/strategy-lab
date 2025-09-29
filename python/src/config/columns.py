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