
"""Incremental verification of ORB strategy with plotting of signals.

This script:
1. Loads cached intraday data for a ticker.
2. Applies prerequisite indicators (orb_levels).
3. Iteratively feeds bars to the ORBStrategy collecting entry/exit signals.
4. After completion, appends `strategy_signal` and `strategy_exit_flag` columns.
5. Uses `plot_signals_for_tickers` to generate a PNG chart under results/images.

The plotting utility normalizes columns automatically, so we retain original names
(`strategy_signal`, `strategy_exit_flag`) and it will alias.
"""

from datetime import datetime

from src.config import build_orb_atr_strategy_config_with_or_stop
from src.data.cache import CacheDataLoader
from src.strategies.orb import ORBStrategy
from src.indicators import IndicatorFactory
from src.visualization.signal_plots import plot_signals_for_tickers

ticker = "AAPL"

# Load your intraday OHLCV data (must have columns: open, high, low, close, volume)
cache_loader = CacheDataLoader()
df = cache_loader.fetch(ticker, "5m", start="2025-09-18", end="2025-09-19")

# Pre-calc indicators once (optional; strategy will calc if missing)
df = IndicatorFactory.apply(df, [
    {
        'name': 'orb_levels',
        'params': {
            'start_time': '09:30',
            'duration_minutes': 5,
            'body_pct': 0.5
        }
    }
])

strategy = ORBStrategy(breakout_window=5, strategy_config=build_orb_atr_strategy_config_with_or_stop())

# Accumulators for signals
entry_signals: list[int] = []
exit_flags: list[int] = []

for i in range(len(df)):
    window = df.iloc[: i + 1]
    entry_signal, exit_flag = strategy.generate_signal_incremental(window)
    entry_signals.append(int(entry_signal or 0))
    exit_flags.append(int(exit_flag or 0))
    latest = window.iloc[-1]
    print(
        f"[{ticker}][{latest.name}] close={latest['close']:.2f} breakout={latest.get('ORB_Breakout', None)} "
        f"entry_signal={entry_signal} exit_flag={exit_flag} "
        f"in_pos={getattr(strategy, '_in_position', 0)} TP={getattr(strategy, '_take_profit', None)} "
        f"STOP={getattr(strategy, '_initial_stop', None)}"
    )

# Append collected signals to DataFrame
df_enriched = df.copy()
df_enriched['strategy_signal'] = entry_signals
df_enriched['strategy_exit_flag'] = exit_flags

# Generate a run_id to prevent overwriting (date + short ticker + range suffix)
run_id = f"verify_{ticker}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

print(f"Plotting signals for run_id={run_id} ...")
saved = plot_signals_for_tickers(
    data_map={ticker: df_enriched},
    output_dir="results/images",
    style="candlestick",  # falls back gracefully if mplfinance not installed
    show=False,
    dpi=110,
    run_id=run_id,
)
for t, path in saved.items():
    print(f"Saved plot for {t} -> {path}")
