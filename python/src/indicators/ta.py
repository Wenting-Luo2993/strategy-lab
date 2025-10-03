from src.utils.logger import get_logger
import pandas as pd
import pandas_ta as ta
from typing import Dict, Callable, List, Any, Optional, Union, Tuple
import numpy as np
from datetime import timedelta

logger = get_logger("IndicatorFactory")

class IndicatorFactory:
    """
    Factory class for managing and applying technical indicators.
    Uses a registry pattern to store indicator calculation functions.
    
    Indicators can use either bar count or calendar days as the length unit.
    When using days as the length unit, the dataframe must have a DatetimeIndex.
    """
    # Registry to store indicator calculation functions
    _registry: Dict[str, Callable] = {}
    
    @classmethod
    def register(cls, name: str):
        """
        Decorator to register an indicator function in the registry.
        
        Args:
            name: Name to register the indicator function under
        """
        def decorator(func: Callable):
            cls._registry[name] = func
            return func
        return decorator
    
    @classmethod
    def resample_to_daily(cls, df: pd.DataFrame) -> pd.DataFrame:
        """
        Resample intraday data to daily OHLCV.
        
        Args:
            df: DataFrame with a DatetimeIndex and OHLCV columns
            
        Returns:
            pd.DataFrame: Daily resampled data with the following rules:
                - open: first value of the day
                - high: maximum value of the day
                - low: minimum value of the day
                - close: last value of the day
                - volume: sum of the day
        
        Raises:
            ValueError: If DataFrame index is not a DatetimeIndex
        """
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame index must be a DatetimeIndex to resample to daily data")
            
        # Make sure column names are lowercase for consistency
        df_lower = df.copy()
        df_lower.columns = [col.lower() for col in df_lower.columns]
        
        # Resample to daily data
        daily_df = df_lower.resample('D').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        return daily_df
    
    @classmethod
    def map_daily_to_original(cls, original_df: pd.DataFrame, daily_df: pd.DataFrame, indicator_cols: List[str]) -> pd.DataFrame:
        """
        Maps daily indicator values back to the original higher frequency DataFrame.
        
        Args:
            original_df: Original DataFrame with original frequency (e.g. 5min)
            daily_df: Daily resampled DataFrame with calculated indicators
            indicator_cols: List of column names of indicators to map back
            
        Returns:
            pd.DataFrame: Copy of original DataFrame with added indicator columns
        """
        if not isinstance(original_df.index, pd.DatetimeIndex) or not isinstance(daily_df.index, pd.DatetimeIndex):
            raise ValueError("Both DataFrames must have DatetimeIndex")
            
        result_df = original_df.copy()
        
        # For each indicator column in daily_df
        for col in indicator_cols:
            if col in daily_df.columns:
                # Get the daily values
                daily_values = daily_df[col].copy()
                
                # Create a complete daily range that spans the original data
                reindexed = daily_values.reindex(
                    pd.date_range(start=daily_df.index.min(), end=original_df.index.max(), freq='D')
                )
                
                # Forward fill daily values to ensure each bar in a day has the same value
                filled = reindexed.fillna(method='ffill')
                
                # Map to original DataFrame's index
                result_df[col] = filled.reindex(original_df.index, method='ffill')
                
                logger.info(f"Mapped daily indicator {col} back to original frequency")
                
        return result_df
    
    @classmethod
    def get_indicator(cls, name: str):
        """
        Get an indicator function by name.
        
        Args:
            name: Name of the indicator
            
        Returns:
            The indicator calculation function
        
        Raises:
            ValueError: If indicator name is not in registry
        """
        if name not in cls._registry:
            raise ValueError(f"Indicator '{name}' not found in registry")
        return cls._registry[name]
    
    @classmethod
    def list_indicators(cls):
        """
        List all registered indicators.
        
        Returns:
            List of indicator names
        """
        return list(cls._registry.keys())
    
    @classmethod
    def ensure_indicators(cls, df: pd.DataFrame, indicators: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Ensures specified indicator columns exist in the DataFrame, adding only missing ones.
        
        Args:
            df: Input DataFrame with OHLCV data
            indicators: List of indicator configurations, each a dict with:
                        'name': Indicator type (e.g., 'sma', 'atr')
                        'params': Optional parameters for the indicator
                        'column': Expected column name in the DataFrame
                        Example: [{'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'}]
                        
        Returns:
            DataFrame with any missing indicators added
        """
        result_df = df.copy()
        indicators_to_apply = []
        
        for ind_config in indicators:
            name = ind_config['name']
            params = ind_config.get('params', {})
            column = ind_config['column']
            
            # Check if column already exists
            if column not in result_df.columns:
                logger.info(f"Indicator column '{column}' not found. Generating using indicator factory...")
                indicators_to_apply.append({'name': name, 'params': params})
            else:
                logger.info(f"Indicator column '{column}' already exists. Skipping generation.")
            
        # Only apply indicators that don't already exist
        if indicators_to_apply:
            result_df = cls.apply(result_df, indicators_to_apply)
            
        return result_df
    
    @classmethod
    def apply(cls, df: pd.DataFrame, indicators: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        Apply multiple indicators to a DataFrame.
        
        Args:
            df: Input DataFrame with OHLCV data
            indicators: List of indicator configurations, each a dict with 'name' and optional 'params'
                        Example: [{'name': 'sma', 'params': {'length': 20}}]
                        
        Returns:
            DataFrame with added indicators
        """
        result_df = df.copy()
        
        for ind_config in indicators:
            name = ind_config['name']
            params = ind_config.get('params', {})
            
            indicator_func = cls.get_indicator(name)
            result_df = indicator_func(result_df, **params)
            
        return result_df


# Register SMA indicator
@IndicatorFactory.register('sma')
def calculate_sma(df: pd.DataFrame, length: int = 20, use_days: bool = False) -> pd.DataFrame:
    """
    Calculate Simple Moving Average.
    
    Args:
        df: DataFrame with OHLCV data
        length: Number of periods for calculation (days or bars)
        use_days: If True, length is in calendar days; if False, length is in bars
        
    Returns:
        DataFrame with added SMA column
    """
    df_copy = df.copy()
    
    if use_days and isinstance(df.index, pd.DatetimeIndex):
        # Resample to daily data
        daily_df = IndicatorFactory.resample_to_daily(df)
        
        # Calculate SMA on daily data
        daily_df.ta.sma(length=length, append=True)
        
        # Get the column name of the generated SMA
        sma_cols = [col for col in daily_df.columns if 'SMA_' in col]
        if sma_cols:
            # Map daily indicator values back to original frequency
            df_copy = IndicatorFactory.map_daily_to_original(df, daily_df, sma_cols)
            logger.info(f"Calculated SMA using {length} days as length unit")
    else:
        if use_days and not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("DataFrame index is not DatetimeIndex. Falling back to bar-based calculation.")
        df_copy.ta.sma(length=length, append=True)
        logger.info(f"Calculated SMA using {length} bars as length unit")
        
    return df_copy


# Register EMA indicator
@IndicatorFactory.register('ema')
def calculate_ema(df: pd.DataFrame, length: int = 20, use_days: bool = False) -> pd.DataFrame:
    """
    Calculate Exponential Moving Average.
    
    Args:
        df: DataFrame with OHLCV data
        length: Number of periods for calculation (days or bars)
        use_days: If True, length is in calendar days; if False, length is in bars
        
    Returns:
        DataFrame with added EMA column
    """
    df_copy = df.copy()
    
    if use_days and isinstance(df.index, pd.DatetimeIndex):
        # Resample to daily data
        daily_df = IndicatorFactory.resample_to_daily(df)
        
        # Calculate EMA on daily data
        daily_df.ta.ema(length=length, append=True)
        
        # Get the column name of the generated EMA
        ema_cols = [col for col in daily_df.columns if 'EMA_' in col]
        if ema_cols:
            # Map daily indicator values back to original frequency
            df_copy = IndicatorFactory.map_daily_to_original(df, daily_df, ema_cols)
            logger.info(f"Calculated EMA using {length} days as length unit")
    else:
        if use_days and not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("DataFrame index is not DatetimeIndex. Falling back to bar-based calculation.")
        df_copy.ta.ema(length=length, append=True)
        logger.info(f"Calculated EMA using {length} bars as length unit")
        
    return df_copy


# Register RSI indicator
@IndicatorFactory.register('rsi')
def calculate_rsi(df: pd.DataFrame, length: int = 14, use_days: bool = False) -> pd.DataFrame:
    """
    Calculate Relative Strength Index.
    
    Args:
        df: DataFrame with OHLCV data
        length: Number of periods for calculation (days or bars)
        use_days: If True, length is in calendar days; if False, length is in bars
        
    Returns:
        DataFrame with added RSI column
    """
    df_copy = df.copy()
    
    if use_days and isinstance(df.index, pd.DatetimeIndex):
        # Resample to daily data
        daily_df = IndicatorFactory.resample_to_daily(df)
        
        # Calculate RSI on daily data
        daily_df.ta.rsi(length=length, append=True)
        
        # Get the column name of the generated RSI
        rsi_cols = [col for col in daily_df.columns if 'RSI_' in col]
        if rsi_cols:
            # Map daily indicator values back to original frequency
            df_copy = IndicatorFactory.map_daily_to_original(df, daily_df, rsi_cols)
            logger.info(f"Calculated RSI using {length} days as length unit")
    else:
        if use_days and not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("DataFrame index is not DatetimeIndex. Falling back to bar-based calculation.")
        df_copy.ta.rsi(length=length, append=True)
        logger.info(f"Calculated RSI using {length} bars as length unit")
        
    return df_copy


# Register ATR indicator
@IndicatorFactory.register('atr')
def calculate_atr(df: pd.DataFrame, length: int = 14, use_days: bool = False) -> pd.DataFrame:
    """
    Calculate Average True Range.
    
    Args:
        df: DataFrame with OHLCV data
        length: Number of periods for calculation (days or bars)
        use_days: If True, length is in calendar days; if False, length is in bars
        
    Returns:
        DataFrame with added ATR column
    """
    df_copy = df.copy()
    
    if use_days and isinstance(df.index, pd.DatetimeIndex):
        # Resample to daily data
        daily_df = IndicatorFactory.resample_to_daily(df)
        
        # Calculate ATR on daily data
        daily_df.ta.atr(length=length, append=True)
        
        # Get the column name of the generated ATR
        atr_cols = [col for col in daily_df.columns if 'ATR' in col and 'ATRP' not in col]
        if atr_cols:
            # Map daily indicator values back to original frequency
            df_copy = IndicatorFactory.map_daily_to_original(df, daily_df, atr_cols)
            logger.info(f"Calculated ATR using {length} days as length unit")
    else:
        if use_days and not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("DataFrame index is not DatetimeIndex. Falling back to bar-based calculation.")
        df_copy.ta.atr(length=length, append=True)
        logger.info(f"Calculated ATR using {length} bars as length unit")
        
    return df_copy


# Register MACD indicator
@IndicatorFactory.register('macd')
def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, use_days: bool = False) -> pd.DataFrame:
    """
    Calculate MACD (Moving Average Convergence Divergence).
    
    Args:
        df: DataFrame with OHLCV data
        fast: Number of periods for fast EMA calculation (days or bars)
        slow: Number of periods for slow EMA calculation (days or bars)
        signal: Number of periods for signal line calculation (days or bars)
        use_days: If True, periods are in calendar days; if False, periods are in bars
        
    Returns:
        DataFrame with added MACD columns
    """
    df_copy = df.copy()
    
    if use_days and isinstance(df.index, pd.DatetimeIndex):
        # Resample to daily data
        daily_df = IndicatorFactory.resample_to_daily(df)
        
        # Calculate MACD on daily data
        daily_df.ta.macd(fast=fast, slow=slow, signal=signal, append=True)
        
        # Get the column names of the generated MACD
        macd_cols = [col for col in daily_df.columns if 'MACD' in col]
        if macd_cols:
            # Map daily indicator values back to original frequency
            df_copy = IndicatorFactory.map_daily_to_original(df, daily_df, macd_cols)
            logger.info(f"Calculated MACD using {fast}/{slow}/{signal} days as parameters")
    else:
        if use_days and not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("DataFrame index is not DatetimeIndex. Falling back to bar-based calculation.")
        df_copy.ta.macd(fast=fast, slow=slow, signal=signal, append=True)
        logger.info(f"Calculated MACD using {fast}/{slow}/{signal} bars as parameters")
        
    return df_copy


# Register Bollinger Bands
@IndicatorFactory.register('bbands')
def calculate_bbands(df: pd.DataFrame, length: int = 20, std: float = 2.0, use_days: bool = False) -> pd.DataFrame:
    """
    Calculate Bollinger Bands.
    
    Args:
        df: DataFrame with OHLCV data
        length: Number of periods for calculation (days or bars)
        std: Number of standard deviations for the bands
        use_days: If True, length is in calendar days; if False, length is in bars
        
    Returns:
        DataFrame with added Bollinger Bands columns
    """
    df_copy = df.copy()
    
    if use_days and isinstance(df.index, pd.DatetimeIndex):
        # Resample to daily data
        daily_df = IndicatorFactory.resample_to_daily(df)
        
        # Calculate Bollinger Bands on daily data
        daily_df.ta.bbands(length=length, std=std, append=True)
        
        # Get the column names of the generated Bollinger Bands
        bb_cols = [col for col in daily_df.columns if 'BB' in col]
        if bb_cols:
            # Map daily indicator values back to original frequency
            df_copy = IndicatorFactory.map_daily_to_original(df, daily_df, bb_cols)
            logger.info(f"Calculated Bollinger Bands using {length} days as length unit")
    else:
        if use_days and not isinstance(df.index, pd.DatetimeIndex):
            logger.warning("DataFrame index is not DatetimeIndex. Falling back to bar-based calculation.")
        df_copy.ta.bbands(length=length, std=std, append=True)
        logger.info(f"Calculated Bollinger Bands using {length} bars as length unit")
        
    return df_copy


def add_basic_indicators(df: pd.DataFrame, use_days: bool = False) -> pd.DataFrame:
    """
    Add common technical indicators using the IndicatorFactory.
    
    Args:
        df: DataFrame with OHLCV data
        use_days: If True, indicator periods are in calendar days; if False, periods are in bars
        
    Returns:
        DataFrame with added indicators
    """
    indicators = [
        {'name': 'sma', 'params': {'length': 20, 'use_days': use_days}},
        {'name': 'sma', 'params': {'length': 30, 'use_days': use_days}},
        {'name': 'sma', 'params': {'length': 50, 'use_days': use_days}},
        {'name': 'sma', 'params': {'length': 200, 'use_days': use_days}},
        {'name': 'rsi', 'params': {'length': 14, 'use_days': use_days}},
        {'name': 'atr', 'params': {'length': 14, 'use_days': use_days}},
    ]
    
    return IndicatorFactory.apply(df, indicators)
