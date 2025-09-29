import pandas as pd
from ..config.columns import TradeColumns


class BacktestEngine:
    """
    Modular backtest engine for simulating trading strategies.

    Args:
        strategy: Object with generate_signal(df, i) -> int (+1=buy, -1=sell, 0=hold)
        risk_manager: Object with apply(trade, df, i, config) -> dict (sets stop loss/take profit)
        data (pd.DataFrame): Historical OHLCV data.
        config (dict): Configuration parameters.
    """

    def __init__(self, strategy, risk_manager, data: pd.DataFrame, config: dict):
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.data = data
        self.config = config
        self.trades = []

    def run(self) -> list:
        """
        Runs the backtest loop over the data.

        Returns:
            List of trade dictionaries with entry/exit info and PnL.
        """
        position = None  # None or dict with entry info
        for i in range(len(self.data)):
            signal = self.strategy.generate_signal(self.data, i)
            price = self.data["close"].iloc[i]

            # Entry
            if signal == 1 and position is None:
                position = {
                    TradeColumns.ENTRY_IDX.value: i,
                    TradeColumns.ENTRY_TIME.value: self.data.index[i],
                    TradeColumns.ENTRY_PRICE.value: price,
                    TradeColumns.SIZE.value: self.config.get("trade_size", 1)
                }
                position = self.risk_manager.apply(position, self.data, i, self.config)

            # Exit
            elif signal == -1 and position is not None:
                exit_price = price
                trade = {
                    **position,
                    TradeColumns.EXIT_IDX.value: i,
                    TradeColumns.EXIT_TIME.value: self.data.index[i],
                    TradeColumns.EXIT_PRICE.value: exit_price,
                    TradeColumns.PNL.value: (exit_price - position[TradeColumns.ENTRY_PRICE.value]) * position[TradeColumns.SIZE.value]
                }
                self.trades.append(trade)
                position = None

            # Check stop loss/take profit if in position
            elif position is not None:
                stop_loss = position.get(TradeColumns.STOP_LOSS.value)
                take_profit = position.get(TradeColumns.TAKE_PROFIT.value)
                low = self.data["low"].iloc[i]
                high = self.data["high"].iloc[i]
                exit_reason = None
                exit_price = None

                if stop_loss is not None and low <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif take_profit is not None and high >= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"

                if exit_price is not None:
                    trade = {
                        **position,
                        TradeColumns.EXIT_IDX.value: i,
                        TradeColumns.EXIT_TIME.value: self.data.index[i],
                        TradeColumns.EXIT_PRICE.value: exit_price,
                        TradeColumns.EXIT_REASON.value: exit_reason,
                        TradeColumns.PNL.value: (exit_price - position[TradeColumns.ENTRY_PRICE.value]) * position[TradeColumns.SIZE.value]
                    }
                    self.trades.append(trade)
                    position = None

        # If still in position at end, close at last price
        if position is not None:
            exit_price = self.data["close"].iloc[-1]
            trade = {
                **position,
                TradeColumns.EXIT_IDX.value: len(self.data) - 1,
                TradeColumns.EXIT_TIME.value: self.data.index[-1],
                TradeColumns.EXIT_PRICE.value: exit_price,
                TradeColumns.EXIT_REASON.value: "end_of_data",
                TradeColumns.PNL.value: (exit_price - position[TradeColumns.ENTRY_PRICE.value]) * position[TradeColumns.SIZE.value]
            }
            self.trades.append(trade)

        return self.trades
