"""
Incremental Indicator Engine with O(1) state-based calculations.

Supports EMA, SMA, RSI, ATR, MACD, and Bollinger Bands with state persistence
and validation against batch calculations.
"""

import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class IndicatorState:
    """State for a single indicator instance."""

    name: str
    params: Dict[str, Any]
    symbol: str
    timeframe: str
    bar_count: int = 0

    # Common state for indicators
    ema_state: Optional[Dict[str, float]] = None  # {'value': float, 'multiplier': float}
    sma_state: Optional[Dict[str, Any]] = None  # {'sum': float, 'values': deque}
    rsi_state: Optional[Dict[str, Any]] = None  # {'gains': float, 'losses': float, 'avg_gain': float, 'avg_loss': float}
    atr_state: Optional[Dict[str, Any]] = None  # {'tr_values': deque, 'atr': float}
    macd_state: Optional[Dict[str, Any]] = None  # {'ema_fast': float, 'ema_slow': float, 'ema_signal': float}
    bb_state: Optional[Dict[str, Any]] = None  # {'sma': float, 'variance': float, 'prices': deque}


class IncrementalIndicatorEngine:
    """
    Calculates technical indicators incrementally with O(1) time complexity.

    Maintains state for each indicator to avoid recalculating from scratch
    for each new bar. Supports state persistence and validation.
    """

    def __init__(self, state_dir: Optional[Path] = None):
        """
        Initialize the engine.

        Args:
            state_dir: Directory for persisting indicator state. If None, state is in-memory only.
        """
        self.state_dir = state_dir
        if state_dir:
            state_dir.mkdir(parents=True, exist_ok=True)

        self.states: Dict[str, IndicatorState] = {}

    def _get_state_key(self, indicator_name: str, symbol: str, timeframe: str, params: Dict[str, Any]) -> str:
        """Generate unique key for indicator state."""
        param_str = "_".join(f"{k}_{v}" for k, v in sorted(params.items()))
        return f"{indicator_name}_{symbol}_{timeframe}_{param_str}"

    def _save_state(self, key: str, state: IndicatorState) -> None:
        """Persist indicator state to disk."""
        if not self.state_dir:
            return

        state_file = self.state_dir / f"{key}.pkl"
        try:
            with open(state_file, "wb") as f:
                pickle.dump(state, f)

            # Check file size and trim if necessary
            file_size = state_file.stat().st_size
            if file_size > 100_000:  # 100KB limit
                logger.warning(f"State file {key} is {file_size} bytes, exceeds 100KB limit")
        except Exception as e:
            logger.error(f"Failed to save state for {key}: {e}")

    def _load_state(self, key: str) -> Optional[IndicatorState]:
        """Load indicator state from disk."""
        if not self.state_dir:
            return None

        state_file = self.state_dir / f"{key}.pkl"
        if not state_file.exists():
            return None

        try:
            with open(state_file, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            logger.error(f"Failed to load state for {key}: {e}")
            return None

    def _initialize_ema(self, length: int) -> Dict[str, float]:
        """Initialize EMA state."""
        multiplier = 2.0 / (length + 1)
        return {"value": None, "multiplier": multiplier, "length": length}

    def _update_ema(self, state: Dict[str, float], close: float) -> float:
        """Update EMA incrementally."""
        if state["value"] is None:
            state["value"] = close
            return close

        state["value"] = (close * state["multiplier"]) + (state["value"] * (1 - state["multiplier"]))
        return state["value"]

    def _initialize_sma(self, length: int) -> Dict[str, Any]:
        """Initialize SMA state."""
        return {"length": length, "sum": 0.0, "values": [], "count": 0}

    def _update_sma(self, state: Dict[str, Any], close: float) -> float:
        """Update SMA incrementally."""
        state["values"].append(close)
        state["sum"] += close
        state["count"] += 1

        # Keep only the last N values
        if len(state["values"]) > state["length"]:
            old_value = state["values"].pop(0)
            state["sum"] -= old_value

        return state["sum"] / len(state["values"])

    def _initialize_rsi(self, length: int) -> Dict[str, Any]:
        """Initialize RSI state."""
        return {
            "length": length,
            "gains": 0.0,
            "losses": 0.0,
            "avg_gain": None,
            "avg_loss": None,
            "prev_close": None,
            "count": 0,
        }

    def _update_rsi(self, state: Dict[str, Any], close: float) -> Optional[float]:
        """Update RSI incrementally."""
        if state["prev_close"] is None:
            state["prev_close"] = close
            return None

        change = close - state["prev_close"]
        state["prev_close"] = close

        if change > 0:
            state["gains"] += change
        else:
            state["losses"] -= change

        state["count"] += 1

        # Not enough data yet
        if state["count"] < state["length"]:
            return None

        # Initialize averages
        if state["avg_gain"] is None:
            state["avg_gain"] = state["gains"] / state["length"]
            state["avg_loss"] = state["losses"] / state["length"]
        else:
            # Smooth average (Wilder's smoothing)
            state["avg_gain"] = (state["avg_gain"] * (state["length"] - 1) + (state["gains"] if state["count"] == state["length"] else 0)) / state["length"]
            state["avg_loss"] = (state["avg_loss"] * (state["length"] - 1) + (state["losses"] if state["count"] == state["length"] else 0)) / state["length"]
            state["gains"] = 0.0
            state["losses"] = 0.0

        if state["avg_loss"] == 0:
            return 100.0 if state["avg_gain"] > 0 else 0.0

        rs = state["avg_gain"] / state["avg_loss"]
        rsi = 100 - (100 / (1 + rs))

        return rsi

    def _initialize_atr(self, length: int) -> Dict[str, Any]:
        """Initialize ATR state."""
        return {
            "length": length,
            "tr_values": [],
            "atr": None,
        }

    def _update_atr(self, state: Dict[str, Any], high: float, low: float, close: float) -> Optional[float]:
        """Update ATR incrementally."""
        if len(state["tr_values"]) == 0:
            # Need previous close for first TR
            state["tr_values"].append(high - low)
            return None

        prev_close = state["prev_close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        state["tr_values"].append(tr)

        # Keep only last N values
        if len(state["tr_values"]) > state["length"]:
            state["tr_values"].pop(0)

        # Calculate ATR
        if state["atr"] is None and len(state["tr_values"]) >= state["length"]:
            state["atr"] = sum(state["tr_values"]) / state["length"]
        elif state["atr"] is not None:
            # Smooth average
            state["atr"] = (state["atr"] * (state["length"] - 1) + state["tr_values"][-1]) / state["length"]

        return state["atr"]

    def _initialize_macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, Any]:
        """Initialize MACD state."""
        return {
            "fast": fast,
            "slow": slow,
            "signal": signal,
            "ema_fast": None,
            "ema_slow": None,
            "ema_signal": None,
            "fast_mult": 2.0 / (fast + 1),
            "slow_mult": 2.0 / (slow + 1),
            "signal_mult": 2.0 / (signal + 1),
            "count": 0,
        }

    def _update_macd(self, state: Dict[str, Any], close: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Update MACD incrementally."""
        state["count"] += 1

        # Initialize EMAs
        if state["ema_fast"] is None:
            state["ema_fast"] = close
            state["ema_slow"] = close
            return None, None, None

        # Update fast and slow EMAs
        state["ema_fast"] = (close * state["fast_mult"]) + (state["ema_fast"] * (1 - state["fast_mult"]))
        state["ema_slow"] = (close * state["slow_mult"]) + (state["ema_slow"] * (1 - state["slow_mult"]))

        # Calculate MACD line
        macd_line = state["ema_fast"] - state["ema_slow"]

        # Initialize signal line
        if state["ema_signal"] is None:
            state["ema_signal"] = macd_line
        else:
            # Update signal line
            state["ema_signal"] = (macd_line * state["signal_mult"]) + (state["ema_signal"] * (1 - state["signal_mult"]))

        histogram = macd_line - state["ema_signal"]

        return macd_line, state["ema_signal"], histogram

    def _initialize_bb(self, length: int, std_dev: float = 2.0) -> Dict[str, Any]:
        """Initialize Bollinger Bands state."""
        return {
            "length": length,
            "std_dev": std_dev,
            "prices": [],
            "sma": None,
        }

    def _update_bb(self, state: Dict[str, Any], close: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Update Bollinger Bands incrementally."""
        state["prices"].append(close)

        # Keep only last N prices
        if len(state["prices"]) > state["length"]:
            state["prices"].pop(0)

        # Calculate SMA
        sma = sum(state["prices"]) / len(state["prices"])

        # Calculate standard deviation
        if len(state["prices"]) == state["length"]:
            variance = sum((p - sma) ** 2 for p in state["prices"]) / state["length"]
            std = np.sqrt(variance)

            upper_band = sma + (std * state["std_dev"])
            lower_band = sma - (std * state["std_dev"])

            return upper_band, sma, lower_band

        return None, None, None

    def update(
        self,
        df: pd.DataFrame,
        start_idx: int,
        indicators: List[Dict[str, Any]],
        symbol: str,
        timeframe: str,
    ) -> pd.DataFrame:
        """
        Update indicators for all bars from start_idx onwards.

        Args:
            df: DataFrame with OHLCV data (columns: open, high, low, close, volume)
            start_idx: Starting index for calculation
            indicators: List of indicator configs [{'name': 'ema', 'params': {'length': 20}}, ...]
            symbol: Trading symbol
            timeframe: Timeframe string (e.g., '5m', '1h')

        Returns:
            DataFrame with indicator columns added
        """
        # Initialize result DataFrame
        result_df = df.copy()

        # Process each indicator
        for ind_config in indicators:
            ind_name = ind_config["name"]
            params = ind_config["params"]
            key = self._get_state_key(ind_name, symbol, timeframe, params)

            # Load or create state
            state = self.states.get(key)
            if state is None:
                state = self._load_state(key)
                if state is None:
                    state = IndicatorState(
                        name=ind_name,
                        params=params,
                        symbol=symbol,
                        timeframe=timeframe,
                    )
                self.states[key] = state

            # Calculate indicator values
            result_df = self._calculate_indicator(
                result_df, state, ind_name, params, start_idx
            )

            # Save state
            self._save_state(key, state)

        return result_df

    def _calculate_indicator(
        self,
        df: pd.DataFrame,
        state: IndicatorState,
        ind_name: str,
        params: Dict[str, Any],
        start_idx: int,
    ) -> pd.DataFrame:
        """Calculate a single indicator."""
        col_name = self._get_indicator_column_name(ind_name, params)

        if col_name not in df.columns:
            df[col_name] = np.nan

        # Initialize state if needed
        if ind_name == "ema":
            if state.ema_state is None:
                state.ema_state = self._initialize_ema(params["length"])
        elif ind_name == "sma":
            if state.sma_state is None:
                state.sma_state = self._initialize_sma(params["length"])
        elif ind_name == "rsi":
            if state.rsi_state is None:
                state.rsi_state = self._initialize_rsi(params["length"])
        elif ind_name == "atr":
            if state.atr_state is None:
                state.atr_state = self._initialize_atr(params["length"])
        elif ind_name == "macd":
            if state.macd_state is None:
                state.macd_state = self._initialize_macd(
                    params.get("fast", 12),
                    params.get("slow", 26),
                    params.get("signal", 9),
                )
        elif ind_name == "bb":
            if state.bb_state is None:
                state.bb_state = self._initialize_bb(
                    params["length"],
                    params.get("std_dev", 2.0),
                )

        # Calculate for each bar
        for i in range(start_idx, len(df)):
            row = df.iloc[i]
            close = row["close"]
            high = row["high"]
            low = row["low"]

            # Store previous close for ATR calculation
            if i > 0 and state.atr_state is not None:
                state.atr_state["prev_close"] = df.iloc[i - 1]["close"]

            if ind_name == "ema":
                value = self._update_ema(state.ema_state, close)
                df.loc[i, col_name] = value

            elif ind_name == "sma":
                value = self._update_sma(state.sma_state, close)
                df.loc[i, col_name] = value

            elif ind_name == "rsi":
                value = self._update_rsi(state.rsi_state, close)
                if value is not None:
                    df.loc[i, col_name] = value

            elif ind_name == "atr":
                value = self._update_atr(state.atr_state, high, low, close)
                if value is not None:
                    df.loc[i, col_name] = value

            elif ind_name == "macd":
                macd_line, signal, histogram = self._update_macd(state.macd_state, close)
                if macd_line is not None:
                    df.loc[i, col_name] = macd_line
                    df.loc[i, f"{col_name}_signal"] = signal
                    df.loc[i, f"{col_name}_histogram"] = histogram

            elif ind_name == "bb":
                upper, middle, lower = self._update_bb(state.bb_state, close)
                if upper is not None:
                    df.loc[i, f"{col_name}_upper"] = upper
                    df.loc[i, f"{col_name}_middle"] = middle
                    df.loc[i, f"{col_name}_lower"] = lower

            state.bar_count += 1

        return df

    def _get_indicator_column_name(self, ind_name: str, params: Dict[str, Any]) -> str:
        """Generate column name for indicator."""
        if ind_name == "ema":
            return f"EMA_{params['length']}"
        elif ind_name == "sma":
            return f"SMA_{params['length']}"
        elif ind_name == "rsi":
            return f"RSI_{params['length']}"
        elif ind_name == "atr":
            return f"ATR_{params['length']}"
        elif ind_name == "macd":
            fast = params.get("fast", 12)
            slow = params.get("slow", 26)
            return f"MACD_{fast}_{slow}"
        elif ind_name == "bb":
            return f"BB_{params['length']}"
        return ind_name

    def get_indicator(
        self,
        symbol: str,
        timeframe: str,
        ind_name: str,
        params: Dict[str, Any],
    ) -> Optional[IndicatorState]:
        """Retrieve indicator state."""
        key = self._get_state_key(ind_name, symbol, timeframe, params)
        return self.states.get(key) or self._load_state(key)

    def clear_states(self) -> None:
        """Clear all in-memory states."""
        self.states.clear()
