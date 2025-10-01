from src.utils.logger import get_logger
import pandas as pd
import pandas_ta as ta
from typing import Dict, Callable, List, Any, Optional

logger = get_logger("IndicatorFactory")

class IndicatorFactory:
    """
    Factory class for managing and applying technical indicators.
    Uses a registry pattern to store indicator calculation functions.
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
def calculate_sma(df: pd.DataFrame, length: int = 20) -> pd.DataFrame:
    """Calculate Simple Moving Average"""
    df_copy = df.copy()
    df_copy.ta.sma(length=length, append=True)
    return df_copy


# Register EMA indicator
@IndicatorFactory.register('ema')
def calculate_ema(df: pd.DataFrame, length: int = 20) -> pd.DataFrame:
    """Calculate Exponential Moving Average"""
    df_copy = df.copy()
    df_copy.ta.ema(length=length, append=True)
    return df_copy


# Register RSI indicator
@IndicatorFactory.register('rsi')
def calculate_rsi(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """Calculate Relative Strength Index"""
    df_copy = df.copy()
    df_copy.ta.rsi(length=length, append=True)
    return df_copy


# Register ATR indicator
@IndicatorFactory.register('atr')
def calculate_atr(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """Calculate Average True Range"""
    df_copy = df.copy()
    df_copy.ta.atr(length=length, append=True)
    return df_copy


# Register MACD indicator
@IndicatorFactory.register('macd')
def calculate_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Calculate MACD (Moving Average Convergence Divergence)"""
    df_copy = df.copy()
    df_copy.ta.macd(fast=fast, slow=slow, signal=signal, append=True)
    return df_copy


# Register Bollinger Bands
@IndicatorFactory.register('bbands')
def calculate_bbands(df: pd.DataFrame, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Calculate Bollinger Bands"""
    df_copy = df.copy()
    df_copy.ta.bbands(length=length, std=std, append=True)
    return df_copy


def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add common technical indicators using the IndicatorFactory.
    """
    indicators = [
        {'name': 'sma', 'params': {'length': 20}},
        {'name': 'sma', 'params': {'length': 30}},
        {'name': 'sma', 'params': {'length': 50}},
        {'name': 'sma', 'params': {'length': 200}},
        {'name': 'rsi', 'params': {'length': 14}},
        {'name': 'atr', 'params': {'length': 14}},
    ]
    
    return IndicatorFactory.apply(df, indicators)
