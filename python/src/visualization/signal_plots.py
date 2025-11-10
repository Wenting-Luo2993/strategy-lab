"""Visualization utilities for plotting OHLCV data with strategy signals.

Generates one figure per ticker combining candlesticks (or OHLC bars) and the latest
signal series (long/short indications) overlayed, then saves to results/images.

Usage (example):

    from src.visualization.signal_plots import plot_signals_for_tickers
    plot_signals_for_tickers(
        data_map={ticker: df},
        signal_map={ticker: signal_series},
        output_dir="results/images",
        style="candlestick"
    )

Assumptions:
- data_map[ticker] is a pandas DataFrame with columns: open, high, low, close, volume
  and a DateTimeIndex.
- signal_map[ticker] is a pandas Series aligned (same index) or broadcastable. Non-zero
  values indicate trade direction: positive -> long, negative -> short.

If signal_map is omitted, the function will attempt to infer a 'signal' column from the
DataFrame (if present).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

# Try to use mplfinance if available for candlesticks; fallback to manual bars.
try:
    import mplfinance as mpf  # type: ignore
    _HAS_MPLFIN = True
except Exception:
    _HAS_MPLFIN = False

DEFAULT_FIGSIZE = (12, 6)


def _ensure_output_dir(output_dir: str | Path) -> Path:
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    return out_path


def _prepare_signals(df: pd.DataFrame, signal_series: Optional[pd.Series]) -> pd.Series:
    if signal_series is not None:
        # Align index if needed
        if not signal_series.index.equals(df.index):
            # Reindex with forward fill (assumption: signals held until change)
            signal_series = signal_series.reindex(df.index).fillna(0)
        return signal_series.astype(int)
    # Infer from df
    inferred = None
    for col in ["signal", "Signal", "signals"]:
        if col in df.columns:
            inferred = df[col]
            break
    if inferred is None:
        inferred = pd.Series([0]*len(df), index=df.index)
    return inferred.astype(int)


def _plot_single_ticker(
    ticker: str,
    df: pd.DataFrame,
    signals: pd.Series,
    output_dir: Path,
    style: str = "candlestick",
    show: bool = False,
    dpi: int = 110,
    exit_flags: Optional[pd.Series] = None
) -> Path:
    """Plot OHLCV with signals for a single ticker and save image.

    Signals are shown as markers: green upward triangles for longs (1), red downward for shorts (-1).
    """
    if df.empty:
        raise ValueError(f"DataFrame for {ticker} is empty; cannot plot.")
    if not {"open", "high", "low", "close"}.issubset(df.columns):
        raise ValueError(f"DataFrame for {ticker} missing OHLC columns.")

    img_path = output_dir / f"{ticker}_signals.png"

    # Candlestick style via mplfinance if available
    if style == "candlestick" and _HAS_MPLFIN:
        # Build offset markers similar to main_secondary example (low-0.2/high+0.2)
        # Use close price fallback if low/high missing
        lows = df.get('low', df['close'])
        highs = df.get('high', df['close'])
        buy_markers = lows.where(signals > 0) - 0.2
        sell_markers = highs.where(signals < 0) + 0.2
        apds = []
        if (signals > 0).any():
            apds.append(
                mpf.make_addplot(
                    buy_markers,
                    type='scatter',
                    markersize=120,
                    marker='^',
                    color='green'
                )
            )
        if (signals < 0).any():
            apds.append(
                mpf.make_addplot(
                    sell_markers,
                    type='scatter',
                    markersize=120,
                    marker='v',
                    color='red'
                )
            )
        # Exit flag markers
        if exit_flags is not None and (exit_flags == 1).any():
            exit_markers = df['close'].where(exit_flags == 1)
            apds.append(
                mpf.make_addplot(
                    exit_markers,
                    type='scatter',
                    markersize=160,
                    marker='D',
                    color='orange'
                )
            )
        mpf.plot(
            df,
            type='candle',
            style='yahoo',
            addplot=apds if apds else None,
            volume=True,
            figratio=(12, 6),
            figscale=1.0,
            title=f"{ticker} OHLCV + Signals + Exits",
            savefig=dict(fname=str(img_path), dpi=dpi, bbox_inches='tight')
        )
        if show:
            plt.show()
        plt.close('all')
        return img_path

    # Fallback manual plot
    fig, (ax_price, ax_vol) = plt.subplots(2, 1, sharex=True, figsize=DEFAULT_FIGSIZE, height_ratios=[4, 1])
    ax_price.set_title(f"{ticker} OHLCV + Signals")

    # Plot OHLC as line segments (simplified) or use close line if bars too dense
    if len(df) <= 2000:  # rough threshold for clarity
        ax_price.plot(df.index, df['close'], color='black', linewidth=1, label='Close')
        # Optionally draw high-low wicks
        ax_price.vlines(df.index, df['low'], df['high'], color='gray', linewidth=0.5, alpha=0.6)
    else:
        ax_price.plot(df.index, df['close'], color='black', linewidth=0.8, label='Close')

    # Signal markers
    # Offset markers for line plot fallback
    lows = df.get('low', df['close'])
    highs = df.get('high', df['close'])
    long_points = (lows.where(signals > 0) - 0.2).dropna()
    short_points = (highs.where(signals < 0) + 0.2).dropna()
    if not long_points.empty:
        ax_price.scatter(long_points.index, long_points.values, marker='^', color='green', s=50, label='Long Signal')
    if not short_points.empty:
        ax_price.scatter(short_points.index, short_points.values, marker='v', color='red', s=50, label='Short Signal')
    if exit_flags is not None and (exit_flags == 1).any():
        exit_points = df['close'].where(exit_flags == 1).dropna()
        if not exit_points.empty:
            ax_price.scatter(exit_points.index, exit_points.values, marker='D', color='orange', s=60, label='Exit')

    ax_price.legend(loc='upper left')
    ax_price.grid(alpha=0.3)

    # Volume
    if 'volume' in df.columns:
        ax_vol.bar(df.index, df['volume'], width=0.8, color='steelblue')
        ax_vol.set_ylabel('Volume')
        ax_vol.grid(alpha=0.2)

    # Formatting x-axis
    ax_vol.xaxis.set_major_formatter(DateFormatter('%Y-%m-%d %H:%M'))
    fig.autofmt_xdate()

    fig.tight_layout()
    fig.savefig(img_path, dpi=dpi)
    if show:
        plt.show()
    plt.close(fig)
    return img_path


def plot_signals_for_run(
    run_id: str,
    results_dir: str | Path = "results",
    output_dir: str | Path = "results/images",
    style: str = "candlestick",
    show: bool = False,
    dpi: int = 110,
    infer_missing_volume: bool = True
) -> Dict[str, Path]:
    """Load signal diagnostics CSV for a run_id and plot OHLCV + signals per ticker.

    Reads: results/signal_diagnostics_{run_id}.csv which is expected to contain columns:
        run_id, bar_time, ticker, open, high, low, close, volume, signal, direction,
        size_float, size_int, skip_reason, stop_loss, exit_flag, available_funds, account_cash, account_equity

    The function reconstructs per-ticker DataFrames with OHLCV and a 'signal' column.

    Args:
        run_id: Identifier of the run (matches diagnostics file suffix).
        results_dir: Directory where the diagnostics CSV lives.
        output_dir: Directory to write image files.
        style: 'candlestick' (mplfinance) or 'line'.
        show: Display figures interactively.
        dpi: Resolution for saved images.
        infer_missing_volume: If True, replace missing/NaN volume with zeros.

    Returns:
        dict: ticker -> saved image path.
    """
    diag_path = Path(results_dir) / f"signal_diagnostics_{run_id}.csv"
    if not diag_path.exists():
        raise FileNotFoundError(f"Diagnostics file not found: {diag_path}")

    df = pd.read_csv(diag_path)
    if 'bar_time' not in df.columns or 'ticker' not in df.columns:
        raise ValueError("Diagnostics CSV missing required columns 'bar_time' or 'ticker'.")

    df['bar_time'] = pd.to_datetime(df['bar_time'], errors='coerce')
    df = df.dropna(subset=['bar_time'])
    df = df.sort_values('bar_time')

    out_path = _ensure_output_dir(output_dir)
    saved: Dict[str, Path] = {}

    # Build per-ticker frames
    for ticker, tdf in df.groupby('ticker'):
        try:
            # Construct OHLCV DataFrame
            ohlc_cols = ['open', 'high', 'low', 'close']
            base = tdf[['bar_time'] + ohlc_cols].copy()
            base = base.set_index('bar_time')
            # Volume handling
            if 'volume' in tdf.columns:
                base['volume'] = tdf['volume'].values
            else:
                base['volume'] = 0.0
            if infer_missing_volume:
                base['volume'] = base['volume'].fillna(0.0)
            # Attach signal / exit_flag columns
            signal_series = tdf['signal'].astype(int).values if 'signal' in tdf.columns else [0]*len(base)
            base['signal'] = signal_series
            if 'exit_flag' in tdf.columns:
                base['exit_flag'] = tdf['exit_flag'].astype(int).values
            else:
                base['exit_flag'] = 0
            # Optional: we could incorporate skip_reason or stop_loss overlays later.
            saved[ticker] = _plot_single_ticker(
                ticker=ticker,
                df=base,
                signals=base['signal'],
                output_dir=out_path,
                style=style,
                show=show,
                dpi=dpi,
                exit_flags=base.get('exit_flag')
            )
        except Exception as e:
            print(f"[signal_plots] Failed to plot {ticker}: {e}")

    return saved


__all__ = ["plot_signals_for_run"]
