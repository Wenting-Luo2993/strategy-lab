# Strategy Lab

Strategy Lab is a toolkit for developing, backtesting, and executing trading strategies using both Python and Pine Script. It is designed for:

- **Backtesting**: Simulate and evaluate trading strategies on historical data using Python.
- **Paper/Live Trading**: Run strategies in real-time or simulated environments.
- **Rapid Prototyping & Screening**: Quickly prototype, screen, and set up alerts using Pine Script on TradingView.

## Project Structure

- `python/` — Python package for data loading, indicator calculation, strategy logic, backtesting engine, and visualization.

  - `src/backtester/` — Backtest engine, parameter management, and data fetching.
  - `src/data/` — Data loaders for Yahoo, Polygon, IB, and caching.
  - `src/indicators/` — Technical indicators and ORB (Opening Range Breakout) logic.
  - `src/strategies/` — Strategy base classes and ORB strategy implementation.
  - `src/visualization/` — Charting and result visualization.
  - `main.py` — Example: run a backtest from end-to-end.

- `pine/` — Pine Script libraries and strategies for TradingView.
  - `libraries/` — Modular Pine Script libraries for display, entry, range, and risk management.
  - `strategies/` — Example strategies (e.g., ORB strategy) for rapid prototyping and alerts.

## Key Features

- Modular Python and Pine Script codebases for flexibility and rapid iteration.
- Opening Range Breakout (ORB) strategy templates in both Python and Pine Script.
- Data loading from Yahoo Finance and other sources (Python).
- Backtesting engine with parameter grid search (Python).
- Visualization of results and equity curves (Python).
- Pine Script libraries for advanced entry, risk, and display logic.

## Getting Started

### Python

1. Install dependencies (see requirements.txt, e.g. `yfinance`, `pandas`, `matplotlib`, `mplfinance`).
2. Run `python/main.py` for a basic backtest example.
3. Modify or extend strategies in `python/src/strategies/` and parameters in `python/src/backtester/parameters.py`.

### Pine Script

1. Use scripts in `pine/strategies/` directly on TradingView.
2. Import libraries from `pine/libraries/` for custom strategies.
3. Adjust parameters and logic for rapid prototyping and screening.

## Example Usage

**Python:**

```python
from src.data import DataLoaderFactory, DataSource, Timeframe
from src.strategies.orb import ORBStrategy
from src.backtester.engine import Backtester

loader = DataLoaderFactory.create(DataSource.YAHOO, interval=Timeframe.MIN_5.value)
df = loader.fetch("AAPL", timeframe=Timeframe.MIN_5.value, start="2025-08-01", end="2025-08-05")
strategy = ORBStrategy()
signals = strategy.generate_signals(df)
backtester = Backtester(initial_capital=10000)
result = backtester.run(df, signals)
```

**Pine Script:**
Use `pine/strategies/orb-strategy.pine` in TradingView for rapid prototyping, screening, and alerts.

## License

MIT License
