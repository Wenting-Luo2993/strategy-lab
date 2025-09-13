import pandas as pd

class Strategy:
    """Base strategy interface."""
    
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        Generate trading signals for given OHLCV data.
        
        Args:
            df (pd.DataFrame): DataFrame with OHLCV + indicators.
        
        Returns:
            pd.Series: +1 = Buy, -1 = Sell, 0 = Hold
        """
        raise NotImplementedError