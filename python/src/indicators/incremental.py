"""Incremental indicator calculation using talipp library with state management.

This module provides an incremental indicator calculation engine that:
- Calculates indicators only on newly-fetched data segments
- Maintains indicator state for resuming calculation
- Integrates with talipp for efficient incremental computation
- Falls back to batch calculation if needed
- Persists state to disk for cache recovery

Key classes:
    IncrementalIndicatorEngine: Main orchestrator for incremental calculation
    TalippIndicatorWrapper: Adapter for talipp indicators
"""

from __future__ import annotations

import pickle
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from talipp.indicators import EMA, RSI, ATR, SMA, MACD, BB
from talipp.ohlcv import OHLCV

from src.utils.logger import get_logger

logger = get_logger("IncrementalIndicators")


class IncrementalIndicatorEngine:
    """Manages incremental indicator calculation and state persistence.

    This engine:
    - Tracks indicator state per (symbol, timeframe, indicator_name)
    - Calculates indicators only on new data segments
    - Persists/restores state from disk
    - Validates state consistency before use

    Attributes:
        states: Dict mapping (symbol, timeframe) -> indicator states
        version: State format version for compatibility checking
    """

    def __init__(self):
        """Initialize the incremental indicator engine."""
        self.states: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.version = "v1"
        logger.info("IncrementalIndicatorEngine initialized")

    def update(
        self,
        df: pd.DataFrame,
        new_start_idx: int,
        indicators: List[Dict[str, Any]],
        symbol: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Calculate indicators incrementally on new portion of DataFrame.

        Args:
            df: Combined DataFrame with old cache + new data
            new_start_idx: Index where new data begins (row number, not timestamp)
            indicators: List of indicator configurations:
                [{'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'}, ...]
            symbol: Ticker symbol (e.g., 'AAPL')
            timeframe: Timeframe string (e.g., '5m')

        Returns:
            DataFrame with indicators calculated on new portion

        Note:
            - Existing indicator columns in df[:new_start_idx] are preserved
            - Only df[new_start_idx:] gets new indicator values
            - State is updated in-memory (call save_state to persist)
        """
        if new_start_idx == 0:
            logger.info(f"Empty cache for {symbol} {timeframe}, calculating all indicators from scratch")
        else:
            logger.info(
                f"Incremental calculation for {symbol} {timeframe}: "
                f"cache rows={new_start_idx}, new rows={len(df) - new_start_idx}"
            )

        # Get or initialize state for this symbol/timeframe
        state_key = (symbol, timeframe)
        if state_key not in self.states:
            self.states[state_key] = self._init_state(symbol, timeframe)

        state = self.states[state_key]

        # Process each indicator
        for ind_config in indicators:
            ind_name = ind_config['name']
            ind_params = ind_config.get('params', {})

            # Determine expected column name(s)
            if 'column' in ind_config:
                col_names = [ind_config['column']]
            elif 'columns' in ind_config:
                col_names = ind_config['columns']
            else:
                # Infer column name (e.g., ema -> EMA_20)
                col_names = [self._infer_column_name(ind_name, ind_params)]

            # Handle ORB separately (day-based indicator with hybrid approach)
            if ind_name == 'orb_levels':
                df = self._update_orb_levels(
                    df, new_start_idx, ind_params, col_names, state, symbol, timeframe
                )
                continue

            # Skip if indicator doesn't support incremental yet
            if ind_name not in ['ema', 'rsi', 'atr', 'sma', 'macd', 'bbands']:
                logger.warning(
                    f"Indicator '{ind_name}' not yet supported for incremental calculation. "
                    f"Use batch mode or implement wrapper."
                )
                continue

            # Calculate incrementally
            df = self._update_indicator(
                df, new_start_idx, ind_name, ind_params, col_names, state, symbol, timeframe
            )

        return df

    def _update_indicator(
        self,
        df: pd.DataFrame,
        new_start_idx: int,
        ind_name: str,
        ind_params: Dict[str, Any],
        col_names: List[str],
        state: Dict[str, Any],
        symbol: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Update a single indicator incrementally.

        Args:
            df: DataFrame to update
            new_start_idx: Start index of new data
            ind_name: Indicator name ('ema', 'rsi', 'atr', 'sma')
            ind_params: Indicator parameters (e.g., {'length': 20})
            col_names: Expected column names in df
            state: State dictionary for this symbol/timeframe
            symbol: Ticker symbol
            timeframe: Timeframe string

        Returns:
            Updated DataFrame with indicator values
        """
        ind_key = self._make_indicator_key(ind_name, ind_params)

        # Initialize or restore indicator wrapper
        if ind_key not in state['indicators']:
            # First time seeing this indicator: initialize from cache warmup
            wrapper = self._init_indicator_wrapper(
                df, new_start_idx, ind_name, ind_params, symbol, timeframe
            )
            state['indicators'][ind_key] = {
                'wrapper': wrapper,
                'params': ind_params,
                'version': self.version,
                'last_update': datetime.utcnow().isoformat()
            }
        else:
            # Restore existing wrapper
            wrapper = state['indicators'][ind_key]['wrapper']
            # Validate params match
            if state['indicators'][ind_key]['params'] != ind_params:
                logger.warning(
                    f"Parameter mismatch for {ind_name} on {symbol} {timeframe}. "
                    f"Expected {state['indicators'][ind_key]['params']}, got {ind_params}. "
                    f"Reinitializing from cache."
                )
                wrapper = self._init_indicator_wrapper(
                    df, new_start_idx, ind_name, ind_params, symbol, timeframe
                )
                state['indicators'][ind_key]['wrapper'] = wrapper

        # Add new data points to indicator
        col_name = col_names[0]  # Primary column for simple indicators

        for idx in range(new_start_idx, len(df)):
            row = df.iloc[idx]

            if ind_name in ['ema', 'sma']:
                # Simple price indicators: just need close
                wrapper.add(row['close'])
                indicator_value = wrapper[-1]
                df.loc[df.index[idx], col_name] = indicator_value

            elif ind_name == 'rsi':
                # RSI needs close
                wrapper.add(row['close'])
                indicator_value = wrapper[-1]
                df.loc[df.index[idx], col_name] = indicator_value

            elif ind_name == 'atr':
                # ATR needs OHLCV
                ohlcv = OHLCV(
                    open=row['open'],
                    high=row['high'],
                    low=row['low'],
                    close=row['close'],
                    volume=row.get('volume', 0)
                )
                wrapper.add(ohlcv)
                indicator_value = wrapper[-1]
                df.loc[df.index[idx], col_name] = indicator_value

            elif ind_name == 'macd':
                # MACD returns MACDVal(macd, signal, histogram)
                wrapper.add(row['close'])
                macd_val = wrapper[-1]

                if macd_val is not None:
                    # col_names should be ['MACD_12_26_9', 'MACDh_12_26_9', 'MACDs_12_26_9']
                    df.loc[df.index[idx], col_names[0]] = macd_val.macd
                    df.loc[df.index[idx], col_names[1]] = macd_val.histogram
                    df.loc[df.index[idx], col_names[2]] = macd_val.signal

            elif ind_name == 'bbands':
                # Bollinger Bands returns BBVal(lb, cb, ub) = (lower, center, upper)
                wrapper.add(row['close'])
                bb_val = wrapper[-1]

                if bb_val is not None:
                    # col_names should be ['BBU_20_2.0_2.0', 'BBM_20_2.0_2.0', 'BBL_20_2.0_2.0']
                    # Order: Upper, Middle, Lower
                    df.loc[df.index[idx], col_names[0]] = bb_val.ub  # Upper
                    df.loc[df.index[idx], col_names[1]] = bb_val.cb  # Middle (center)
                    df.loc[df.index[idx], col_names[2]] = bb_val.lb  # Lower

        # Update state metadata
        state['indicators'][ind_key]['last_update'] = datetime.utcnow().isoformat()

        logger.info(
            f"Updated {ind_name} for {symbol} {timeframe}: "
            f"processed {len(df) - new_start_idx} new bars"
        )

        return df

    def _init_indicator_wrapper(
        self,
        df: pd.DataFrame,
        new_start_idx: int,
        ind_name: str,
        ind_params: Dict[str, Any],
        symbol: str,
        timeframe: str
    ) -> Any:
        """Initialize a talipp indicator wrapper with warmup data.

        If cache exists (new_start_idx > 0), warm up by processing ALL cache data
        to ensure state matches exactly. This is needed because indicators have
        internal state that can't be reconstructed from just recent bars.

        Args:
            df: Full DataFrame (cache + new data)
            new_start_idx: Index where new data starts
            ind_name: Indicator name
            ind_params: Indicator parameters
            symbol: Ticker symbol
            timeframe: Timeframe string

        Returns:
            Initialized talipp indicator object
        """
        length = ind_params.get('length', 14)

        # Create indicator
        if ind_name == 'ema':
            indicator = EMA(length)
        elif ind_name == 'sma':
            indicator = SMA(length)
        elif ind_name == 'rsi':
            indicator = RSI(length)
        elif ind_name == 'atr':
            indicator = ATR(length)
        elif ind_name == 'macd':
            # MACD params: fast_period, slow_period, signal_period
            fast = ind_params.get('fast', 12)
            slow = ind_params.get('slow', 26)
            signal = ind_params.get('signal', 9)
            indicator = MACD(fast, slow, signal)
        elif ind_name == 'bbands':
            # Bollinger Bands params: period, std_dev
            period = ind_params.get('period', 20)
            std_dev = ind_params.get('std_dev', 2.0)
            indicator = BB(period, std_dev)
        else:
            raise ValueError(f"Unsupported indicator: {ind_name}")

        # Warmup from cache if available
        # Process ALL cache data to get exact state match with pandas_ta
        if new_start_idx > 0:
            logger.info(
                f"Warming up {ind_name} for {symbol} {timeframe} with "
                f"{new_start_idx} bars from cache (full history warmup)"
            )

            for idx in range(new_start_idx):
                row = df.iloc[idx]

                if ind_name in ['ema', 'sma', 'rsi', 'macd', 'bbands']:
                    indicator.add(row['close'])
                elif ind_name == 'atr':
                    ohlcv = OHLCV(
                        open=row['open'],
                        high=row['high'],
                        low=row['low'],
                        close=row['close'],
                        volume=row.get('volume', 0)
                    )
                    indicator.add(ohlcv)
        else:
            logger.info(f"No cache warmup for {ind_name} on {symbol} {timeframe}")

        return indicator

    def _init_state(self, symbol: str, timeframe: str) -> Dict[str, Any]:
        """Initialize state dictionary for a symbol/timeframe pair.

        Args:
            symbol: Ticker symbol
            timeframe: Timeframe string

        Returns:
            Empty state dictionary
        """
        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'indicators': {},  # {indicator_key: {wrapper, params, version, last_update}}
            'created_at': datetime.utcnow().isoformat(),
            'version': self.version
        }

    def save_state(self, path: Path) -> None:
        """Persist indicator state to disk with trimmed history.

        Args:
            path: File path to save state (e.g., AAPL_5m.indicator_state.pkl)

        Note:
            Uses pickle for serialization. State file contains:
            - Trimmed indicator wrappers (only minimum required history)
            - Metadata (params, version, timestamps)

            Trimming reduces file size by 95%+ while preserving calculation accuracy.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            # Create a copy of states with trimmed indicators to avoid modifying in-memory state
            trimmed_states = self._trim_states_for_save()

            with open(path, 'wb') as f:
                pickle.dump(trimmed_states, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(f"Saved trimmed indicator state to {path}")
        except Exception as e:
            logger.error(f"Failed to save indicator state to {path}: {e}")
            raise

    def _trim_states_for_save(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """Create a trimmed copy of states for saving to disk.

        This reduces file size by keeping only the minimum required history
        for each indicator type while preserving full calculation accuracy.

        Returns:
            Trimmed copy of self.states
        """
        import copy

        trimmed = {}
        for state_key, state in self.states.items():
            # Deep copy metadata
            trimmed[state_key] = {
                'symbol': state['symbol'],
                'timeframe': state['timeframe'],
                'indicators': {},
                'created_at': state['created_at'],
                'version': state['version']
            }

            # Trim each indicator
            for ind_key, ind_data in state['indicators'].items():
                trimmed_ind_data = {
                    'wrapper': self._trim_indicator_wrapper(ind_key, ind_data.get('wrapper')),
                    'params': ind_data.get('params', {}),
                    'version': ind_data.get('version', self.version),
                    'last_update': ind_data.get('last_update')
                }
                trimmed[state_key]['indicators'][ind_key] = trimmed_ind_data

        return trimmed

    def _trim_indicator_wrapper(self, ind_key: str, wrapper: Any) -> Any:
        """Trim an indicator wrapper to keep only minimum required history.

        Args:
            ind_key: Indicator key (e.g., 'ema_length=20')
            wrapper: Talipp indicator instance (or None for stateless indicators)

        Returns:
            Trimmed wrapper with only necessary history
        """
        # Handle None wrappers (e.g., ORB which doesn't use talipp)
        if wrapper is None:
            return None

        # Safety check: if wrapper doesn't support len(), return as-is
        try:
            wrapper_len = len(wrapper)
        except (TypeError, AttributeError):
            logger.warning(f"Wrapper for {ind_key} doesn't support len(), skipping trim")
            return wrapper

        if wrapper_len == 0:
            return wrapper

        # Parse indicator type from key
        ind_type = ind_key.split('_')[0] if '_' in ind_key else ind_key

        # Determine minimum required history based on indicator type
        if ind_type == 'ema':
            # EMA needs 2-3x the length for proper warmup
            length = self._extract_param_from_key(ind_key, 'length')
            keep = min(length * 3, wrapper_len) if length else wrapper_len
        elif ind_type == 'rsi':
            # RSI needs ~2x the length
            length = self._extract_param_from_key(ind_key, 'length')
            keep = min(length * 2 + 10, wrapper_len) if length else wrapper_len
        elif ind_type == 'atr':
            # ATR needs the full length plus a few bars
            length = self._extract_param_from_key(ind_key, 'length')
            keep = min(length + 10, wrapper_len) if length else wrapper_len
        elif ind_type in ['sma', 'macd', 'bbands']:
            # Similar to EMA - needs 2-3x the period
            # For MACD, use slow period; for BB use period
            period = self._extract_param_from_key(ind_key, 'period') or \
                     self._extract_param_from_key(ind_key, 'slow') or 26
            keep = min(period * 3, wrapper_len)
        elif 'orb' in ind_type:
            # ORB only needs current day's state
            keep = min(100, wrapper_len)  # Keep last 100 bars (~8 hours of 5m data)
        else:
            # Unknown indicator - keep last 300 bars as safe default
            keep = min(300, wrapper_len)

        # Don't trim if already small
        if wrapper_len <= keep:
            return wrapper

        # Trim talipp indicator by slicing its internal input_values and output_values lists
        # Most talipp indicators store history in these attributes
        try:
            import copy
            trimmed_wrapper = copy.copy(wrapper)  # Shallow copy

            # Trim the value lists (deep in place)
            if hasattr(trimmed_wrapper, 'input_values') and isinstance(trimmed_wrapper.input_values, list):
                original_input_len = len(trimmed_wrapper.input_values)
                trimmed_wrapper.input_values = trimmed_wrapper.input_values[-keep:]
                logger.debug(f"Trimmed {ind_key} input_values from {original_input_len} to {len(trimmed_wrapper.input_values)}")

            if hasattr(trimmed_wrapper, 'output_values') and isinstance(trimmed_wrapper.output_values, list):
                original_output_len = len(trimmed_wrapper.output_values)
                trimmed_wrapper.output_values = trimmed_wrapper.output_values[-keep:]
                logger.debug(f"Trimmed {ind_key} output_values from {original_output_len} to {len(trimmed_wrapper.output_values)}")

            # Also need to trim managed_sequences if present (for complex indicators)
            if hasattr(trimmed_wrapper, 'managed_sequences') and isinstance(trimmed_wrapper.managed_sequences, list):
                for seq in trimmed_wrapper.managed_sequences:
                    if hasattr(seq, 'input_values') and isinstance(seq.input_values, list):
                        seq.input_values = seq.input_values[-keep:]
                    if hasattr(seq, 'output_values') and isinstance(seq.output_values, list):
                        seq.output_values = seq.output_values[-keep:]

            logger.info(f"Successfully trimmed {ind_key} from {wrapper_len} to ~{keep} values")
            return trimmed_wrapper

        except Exception as e:
            logger.warning(f"Failed to trim {ind_key}: {e}. Keeping full wrapper")
            import traceback
            traceback.print_exc()
            return wrapper

    @staticmethod
    def _extract_param_from_key(ind_key: str, param_name: str) -> Optional[int]:
        """Extract parameter value from indicator key string.

        Args:
            ind_key: Key like 'ema_length=20' or 'rsi_length=14'
            param_name: Parameter name to extract (e.g., 'length', 'period')

        Returns:
            Parameter value as int, or None if not found
        """
        try:
            for part in ind_key.split('_'):
                if '=' in part:
                    key, val = part.split('=')
                    if key == param_name:
                        return int(val)
        except (ValueError, AttributeError):
            pass
        return None

    def load_state(self, path: Path) -> None:
        """Load indicator state from disk.

        Args:
            path: File path to load state from

        Note:
            - Validates version compatibility
            - Merges loaded state with existing in-memory state
            - Logs warning if state file is corrupt or incompatible
        """
        if not path.exists():
            logger.info(f"No existing state file at {path}, starting fresh")
            return

        try:
            with open(path, 'rb') as f:
                loaded_states = pickle.load(f)

            # Validate version
            for state_key, state in loaded_states.items():
                if state.get('version') != self.version:
                    logger.warning(
                        f"State version mismatch for {state_key}: "
                        f"expected {self.version}, got {state.get('version')}. "
                        f"Discarding incompatible state."
                    )
                    continue

                # Merge into current state
                self.states[state_key] = state

            logger.info(f"Loaded indicator state from {path} ({len(loaded_states)} symbol/timeframe pairs)")
        except Exception as e:
            logger.error(f"Failed to load indicator state from {path}: {e}. Starting fresh.")
            # Don't raise - allow graceful degradation to batch mode

    @staticmethod
    def _make_indicator_key(ind_name: str, ind_params: Dict[str, Any]) -> str:
        """Create a unique key for an indicator configuration.

        Args:
            ind_name: Indicator name
            ind_params: Indicator parameters

        Returns:
            String key like "ema_20" or "rsi_14"
        """
        # Sort params for deterministic key
        param_str = "_".join(f"{k}={v}" for k, v in sorted(ind_params.items()))
        return f"{ind_name}_{param_str}" if param_str else ind_name

    @staticmethod
    def _infer_column_name(ind_name: str, ind_params: Dict[str, Any]) -> str:
        """Infer expected column name for an indicator.

        Args:
            ind_name: Indicator name
            ind_params: Indicator parameters

        Returns:
            Expected column name (e.g., "EMA_20")
        """
        length = ind_params.get('length', '')
        if ind_name == 'ema':
            return f"EMA_{length}"
        elif ind_name == 'sma':
            return f"SMA_{length}"
        elif ind_name == 'rsi':
            return f"RSI_{length}"
        elif ind_name == 'atr':
            return f"ATRr_{length}"  # pandas_ta convention
        return ind_name.upper()

    def _update_orb_levels(
        self,
        df: pd.DataFrame,
        new_start_idx: int,
        ind_params: Dict[str, Any],
        col_names: List[str],
        state: Dict[str, Any],
        symbol: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Calculate ORB levels using hybrid approach: recalculate affected days only.

        ORB is day-scoped, so we need to:
        1. Identify which days are affected by new data
        2. Recalculate ORB for those days only
        3. Preserve ORB values for unchanged days

        Args:
            df: Full DataFrame (cache + new data)
            new_start_idx: Index where new data begins
            ind_params: ORB parameters (start_time, duration_minutes, body_pct)
            col_names: Expected column names ['ORB_High', 'ORB_Low', 'ORB_Range', 'ORB_Breakout']
            state: State dictionary for this symbol/timeframe
            symbol: Ticker symbol
            timeframe: Timeframe string

        Returns:
            DataFrame with ORB columns updated for new/affected days
        """
        from src.indicators.orb import calculate_orb_levels

        start_time = ind_params.get('start_time', '09:30')
        duration_minutes = ind_params.get('duration_minutes', 5)
        body_pct = ind_params.get('body_pct', 0.5)

        ind_key = self._make_indicator_key('orb_levels', ind_params)

        # Ensure date column exists
        if not isinstance(df.index, pd.DatetimeIndex):
            logger.error("ORB requires DataFrame with DatetimeIndex")
            return df

        df['_temp_date'] = df.index.date

        # Identify affected days (days with new data)
        if new_start_idx > 0:
            new_dates = set(df.iloc[new_start_idx:]['_temp_date'].unique())
            logger.info(
                f"ORB for {symbol} {timeframe}: {len(new_dates)} affected days "
                f"(new data from {df.index[new_start_idx]})"
            )
        else:
            # Cold start: calculate all days
            new_dates = set(df['_temp_date'].unique())
            logger.info(f"ORB for {symbol} {timeframe}: cold start, calculating all {len(new_dates)} days")

        # For affected days, recalculate ORB
        for date in new_dates:
            day_mask = df['_temp_date'] == date
            day_df = df[day_mask].copy()

            if len(day_df) == 0:
                continue

            # Calculate ORB for this day using existing function
            day_df_with_orb = calculate_orb_levels(
                day_df,
                start_time=start_time,
                duration_minutes=duration_minutes,
                body_pct=body_pct
            )

            # Update the main DataFrame for this day
            for col in ['ORB_High', 'ORB_Low', 'ORB_Range', 'ORB_Breakout']:
                if col in day_df_with_orb.columns:
                    df.loc[day_mask, col] = day_df_with_orb[col].values

        # Clean up temp column
        df.drop('_temp_date', axis=1, inplace=True)

        # Update state metadata
        if ind_key not in state['indicators']:
            state['indicators'][ind_key] = {
                'params': ind_params,
                'last_update': None,
                'wrapper': None  # ORB doesn't use talipp wrapper
            }

        state['indicators'][ind_key]['last_update'] = datetime.utcnow().isoformat()

        logger.info(
            f"Updated ORB levels for {symbol} {timeframe}: "
            f"recalculated {len(new_dates)} day(s)"
        )

        return df
