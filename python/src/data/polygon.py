from .base import DataLoader, register_loader
import pandas as pd

@register_loader("polygon")
class PolygonDataLoader(DataLoader):
    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch(self, symbol: str, start: str, end: str):
        # Placeholder
        # You’d use polygon’s REST API here
        return pd.DataFrame()
