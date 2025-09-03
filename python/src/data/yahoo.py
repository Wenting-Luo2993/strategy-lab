import yfinance as yf
import pandas as pd
from .base import DataLoader, register_loader

@register_loader("yahoo")
class YahooDataLoader(DataLoader):
    def __init__(self, interval="1d"):
        self.interval = interval

    def fetch(self, symbol: str, start: str, end: str):
        df = yf.download(symbol, start=start, end=end, interval=self.interval)
        # df.reset_index(inplace=True)
        df.rename(columns={
            "Date": "datetime",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume"
        }, inplace=True)
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        return df
