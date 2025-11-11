# Strategy Verification Guide

This guide explains how to run the incremental multi‑day ORB strategy verification script: `strategy_verification.py`.

The script:

- Replays cached historical 5‑minute OHLCV data for one or more tickers
- Applies `ORBStrategy` incrementally (single entry per trading day logic)
- Records entry signals (`strategy_signal`) and exit flags (`strategy_exit_flag`)
- Writes enriched per‑day CSVs to `python/results/strategy-verification/`
- Optionally produces candlestick + signal plot images (if plotting dependencies present)

---

## 1. Prerequisites

1. Python virtual environment created & dependencies installed (per `python/requirements.txt`).
2. Cached market data present under a cache root (default: `data_cache/`). Structure examples:

```
data_cache/
  AAPL/
    AAPL_5m_2025-09.parquet
    ...
  MSFT/
    MSFT_5m_2025-09.parquet
```

3. 5‑minute timeframe data (script currently restricts to 5m / 5min).

> If no tickers are provided, the script will scan `data_cache/` and infer tickers whose subdirectories contain files with the timeframe string in the filename.

---

## 2. Activate the Environment (Windows PowerShell)

```powershell
cd c:\dev\strategy-lab\python
. .\/.venv312\Scripts\Activate.ps1   # or: .\.venv312\Scripts\activate
cd ../
```

(Adjust the venv path/name if yours differs.)

---

## 3. Basic Usage

Run from the `python/` directory (recommended) or project root:

```powershell
python strategy_verification.py --tickers AAPL MSFT --timeframe 5m --run-id verify01
```

If you omit `--tickers`, the script auto-discovers from the cache.

---

## 4. Command Line Arguments

| Argument             | Default                | Description                                                                         |
| -------------------- | ---------------------- | ----------------------------------------------------------------------------------- |
| `--tickers`          | (inferred)             | Space-separated list of tickers to process.                                         |
| `--timeframe`        | `5m`                   | Must be 5m/5min. Enforced.                                                          |
| `--run-id`           | `strategy_verify`      | Base identifier used in output filenames (date suffix appended).                    |
| `--orb-window`       | `5`                    | Opening range duration in minutes. Passed to `ORBStrategy`.                         |
| `--initial-stop-pct` | `0.25`                 | Fraction (0-1) of OR range to place initial stop inside range (if config supports). |
| `--verbose`          | (off)                  | Enable per-bar logging of entries/exits & replay progress.                          |
| `--days`             | `10`                   | Number of most recent trading days to process (ignored if date range supplied).     |
| `--date-start`       | (none)                 | Inclusive start date `YYYY-MM-DD`. Requires cached data intersecting this date.     |
| `--date-end`         | (today if start given) | Inclusive end date. If omitted but start provided, defaults to last available day.  |

---

## 5. Output Artifacts

For each processed day `YYYYMMDD` and ticker `TICK` you get:

```
python/results/strategy-verification/
  TICK_<run-id>_YYYYMMDD.csv
  (optional) plot images (filenames depend on plotting function)
```

CSV columns (superset):

- `open, high, low, close, volume` (source)
- `ORB_High, ORB_Low, ORB_Range, ORB_Breakout` (added lazily by indicator factory)
- `strategy_signal` (1 or -1 only on the bar where entry occurs, else 0)
- `strategy_exit_flag` (1 on the bar where an explicit exit condition triggers, else 0)

The script logs summary lines like:

```
replay.complete entries=1 exits=1
verification.multi_day.complete days_processed=10
```

---

## 6. Example Runs

1. Last 5 days for inferred tickers (quiet):

```powershell
python strategy_verification.py --days 5
```

2. Specific tickers with verbose bar logging and custom run id:

```powershell
python strategy_verification.py --tickers AAPL NVDA --run-id orbTest --verbose --days 3
```

3. Specific date range:

```powershell
python strategy_verification.py --tickers AAPL --date-start 2025-09-15 --date-end 2025-09-26 --run-id septRange
```

---

## 7. Interpreting Signals

- `strategy_signal`: Captures the initial breakout entry (long = 1, short = -1). Only one entry per trading day is permitted (the strategy code blocks same-day re-entry after exit).
- `strategy_exit_flag`: Indicates bars where exit logic (EOD, stop, or take-profit) fired. Price-level details (stop / TP values) are internal to the strategy instance; extend the script if you want those serialized.

---

## 8. Plotting

If all required plotting libs are available, plots are written to the same directory. Each plot overlays:

- Candlesticks
- ORB High/Low levels
- Entry and exit markers

If plotting fails (e.g., missing dependencies), the script logs a warning and continues; CSV output is still produced.

---

## 9. Common Issues & Troubleshooting

| Symptom                                            | Cause                                                    | Fix                                                                                    |
| -------------------------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `No tickers provided and none inferred from cache` | Empty / unexpected cache structure                       | Confirm `data_cache/<TICKER>/` folders with 5m files.                                  |
| Immediate exit or multiple entries                 | Old strategy version cached / missing single-entry logic | Ensure repository updated; reinstall package if using editable install.                |
| No ORB columns in CSV                              | Indicator factory not applied (rare)                     | Ensure timeframe is 5m; confirm columns `open/high/low/close` exist and are lowercase. |
| All zeros in signals                               | No breakout condition met                                | Adjust `--orb-window` or `body_breakout_percentage` in config factory (code change).   |
| Missing ATR for take profit                        | Not enough bars early in day                             | This is handled with fallback; first breakout very early may produce conservative TP.  |

---

## 10. Extending the Script

Ideas:

- Persist stop / take-profit prices per bar for post-trade analytics.
- Add PnL reconstruction from signals (requires position sizing & fills model).
- Parameter sweep harness (vary `initial_stop_pct`, body breakout pct) and aggregate metrics.
- Export charts to a dashboard (e.g., Panel / Dash) for interactive review.

---

## 11. Programmatic Use

You can import and call key helpers:

```python
from strategy_verification import incremental_apply, verify_replay
```

Ensure `PYTHONPATH` includes the `python/` directory or run from within it.

---

## 12. Support

Open an issue or check logs in `python/logs/` (if configured) when something behaves unexpectedly. Include:

- Command used
- Relevant log lines
- Snippet of resulting CSV (first ~20 rows)

---

Happy verifying!
