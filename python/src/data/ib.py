from .base import DataLoader, register_loader
import pandas as pd

@register_loader("ib")
class InteractiveBrokersDataLoader(DataLoader):
    def __init__(self, client=None):
        self.client = client  # could be ib_insync.IB() connection

    def fetch(self, symbol: str, start: str, end: str):
        # For demo, just return dummy data
        # Replace with IB API logic
        data = {
            "datetime": pd.date_range(start=start, end=end, freq="1D"),
            "open": [100] * 10,
            "high": [105] * 10,
            "low": [95] * 10,
            "close": [102] * 10,
            "volume": [1000] * 10
        }
        return pd.DataFrame(data)
