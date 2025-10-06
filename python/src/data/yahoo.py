import yfinance as yf
import pandas as pd
from .base import DataLoader, register_loader

@register_loader("yahoo")
class YahooDataLoader(DataLoader):
    def __init__(self, interval="1d", timezone="US/Eastern"):
        self.interval = interval
        self.timezone = timezone

    def fetch(self, symbol: str, start: str, end: str):
        df = yf.download(symbol, start=start, end=end, interval=self.interval, auto_adjust=True)
        # df.reset_index(inplace=True)
        if df.empty:
            return df
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
        df.columns = [c.lower() for c in df.columns]
        # Convert index to specified timezone if possible
        if hasattr(df.index, 'tz_convert'):
            # If index is tz-aware, convert
            try:
                df.index = df.index.tz_convert(self.timezone)
            except TypeError:
                # If index is tz-naive, localize then convert
                df.index = df.index.tz_localize('UTC').tz_convert(self.timezone)
        return df
