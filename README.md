# Strategy Lab

A toolkit for algorithmic trading — strategy development, backtesting, and live trading execution (stocks).

## 📁 Project Structure

```
strategy-lab/
├── vibe/
│   ├── common/          # Shared strategies, indicators, models (ORB, etc.)
│   ├── backtester/      # Event-driven backtesting engine
│   └── trading_bot/     # Live/paper trading bot
├── scripts/             # CLI entry points (run_backtest.py, convert_databento.py, …)
├── data/                # Local data (gitignored) — Databento source + Parquet cache
├── pine/                # TradingView Pine Script strategies
└── docs/                # Design docs and implementation plans
```

---

## 🛠 Environment Setup (New Developer)

### Prerequisites

- **Python 3.12** — The project targets 3.12. Check with `python --version`.
- **Git** — for cloning.
- **Docker** (optional) — only needed for live bot deployment.

### 1. Create a virtual environment

From the repo root:

```bash
python -m venv .venv312
```

Activate it:

```bash
# macOS / Linux
source .venv312/bin/activate

# Windows (cmd / PowerShell)
.venv312\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r vibe/trading_bot/requirements.txt
```

This single file covers both the backtester and the live bot.

If you plan to run the **one-time Databento → Parquet conversion** for the backtester, also install:

```bash
pip install zstandard
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env — at minimum fill in API keys for data providers you want to use
```

For the **backtester only**, the two variables that matter are:

```bash
BACKTEST__DATABENTO_DIR=./data/databento    # raw .csv.zst source files
BACKTEST__DATA_DIR=./data/parquet           # processed Parquet output
```

### 4. (Backtester only) Convert raw data to Parquet

This step is required **once** before running any backtest. It converts the Databento
`.csv.zst` files in `data/databento/` into Parquet format.

```bash
# Convert all symbols
python scripts/convert_databento.py

# Or a single symbol
python scripts/convert_databento.py --symbol QQQ

# Validate without writing
python scripts/convert_databento.py --dry-run
```

---

## 🚀 Quick Start

### Backtester

```bash
python scripts/run_backtest.py \
    --ruleset orb_production \
    --symbol QQQ \
    --start 2023-01-01 \
    --end 2024-12-31 \
    --capital 100000 \
    --output reports/backtest.html \
    --trades-csv reports/our_trades.csv
```

The HTML report opens in any browser and includes an equity curve, trade log, and
convexity / regime breakdown.

### Live Trading Bot (Docker)

```bash
cd vibe/trading_bot
cp .env.example .env        # fill in API keys + EXCHANGE__PAPER_TRADING=true
docker-compose up -d
docker-compose logs -f      # stream logs
```

### Pine Script (TradingView)

Open `pine/strategies/orb-strategy.pine` in the TradingView Pine Editor for rapid
prototyping and alert configuration.

---

## 🧪 Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=vibe --cov-report=term-missing

# Backtester tests only
pytest vibe/tests/backtester/

# Live bot tests only
pytest vibe/trading_bot/tests/
```

Tests run automatically before each commit (pre-commit hooks):

```bash
pip install pre-commit
pre-commit install
```

---

## ✨ Key Features

### Backtester (`vibe/backtester/`)

- **Event-driven engine** — processes data bar-by-bar, matches live trading execution flow
- **Realistic fills** — stop-market model: fills at trigger price on bar high/low, not bar close
- **Configurable slippage** — `--slippage-ticks` CLI flag; defaults to 2 ticks
- **Performance analytics** — Sharpe ratio, max drawdown, win rate, expectancy, R-multiples
- **Market regime classification** — ADX-based trending vs ranging
- **HTML reports** — equity curve, regime breakdown, convexity dashboard (Plotly)
- **Parameter sensitivity** — `scripts/run_parameter_sensitivity.py`

### Trading Bot (`vibe/trading_bot/`)

- **Multi-provider data** — Polygon (primary), Finnhub (fallback), yfinance (historical)
- **Real-time ORB strategy** — shares identical signal logic with backtester via `vibe/common/`
- **Discord notifications** — ORB levels established, order fills, end-of-day summary
- **Smart caching** — 30-day TTL for historical bars
- **Docker deployment** — production-ready with health checks
- **Paper trading** — set `EXCHANGE__PAPER_TRADING=true` in `.env`

### Shared (`vibe/common/`)

- **ORB strategy** — Opening Range Breakout with intrabar detection and LEAN tie-break
- **Technical indicators** — ATR, incremental calculation
- **Bar model** — Pydantic-validated OHLCV with timezone-aware timestamps
- **Ruleset loader** — YAML-based strategy configuration

---

## 📊 Backtester Design

The backtester and live bot share the same strategy code (`vibe/common/`). A signal
that fires in a backtest fires from identical logic in production.

**Fill model:** entry at `OR_high + $0.01 + slippage` (long) / `OR_low - $0.01 - slippage`
(short). Stops fill at `stop_price` on an intrabar wick, not at bar close.

**Data:** Databento ITCH 1-minute bars. Raw (not dividend-adjusted) — buy-and-hold returns
will appear ~12–15% lower than published QQQ over 8 years. This is expected.

See [`docs/backtester-mvp/`](./docs/backtester-mvp/) for the full design and validation framework.

---

## 📖 Documentation

| Document | Purpose |
|---|---|
| [`docs/backtester-mvp/design.md`](./docs/backtester-mvp/design.md) | Backtester architecture |
| [`docs/backtester-mvp/validate-backtester-pipeline.md`](./docs/backtester-mvp/validate-backtester-pipeline.md) | Validation framework (Phases 0–5) |
| [`docs/backtester-mvp/qc-alignment-fixes.md`](./docs/backtester-mvp/qc-alignment-fixes.md) | QC alignment fixes: before/after + measured impact |
| [`vibe/trading_bot/README.md`](./vibe/trading_bot/README.md) | Live bot architecture |
| [`docs/trading-bot-mvp/DEPLOYMENT.md`](./docs/trading-bot-mvp/DEPLOYMENT.md) | Cloud deployment guide |
| [`CLAUDE.md`](./CLAUDE.md) | Code patterns for AI assistants |

---

## 🔧 Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Data processing | pandas, numpy |
| Visualization | Plotly |
| Validation | Pydantic v2 |
| Live data | Polygon.io, Finnhub, yfinance |
| Notifications | Discord (webhooks) |
| Deployment | Docker, docker-compose |
| REST/WS API | FastAPI, uvicorn |
| Dashboard | Streamlit |
| Storage | SQLite |
| Testing | pytest, pytest-cov |

---

## 🌐 Live Bot: Market Configuration

Switch markets via `.env`:

```bash
# Stocks (default)
MARKET_TYPE=stocks
EXCHANGE=NYSE
SYMBOLS=QQQ

# Forex (24/5)
MARKET_TYPE=forex
SYMBOLS=EURUSD,GBPUSD

# Crypto (24/7)
MARKET_TYPE=crypto
SYMBOLS=BTCUSD,ETHUSD
```

---

## 📈 Roadmap

- [x] Event-driven backtester with realistic fill model
- [x] ORB strategy with QC-validated signal logic
- [x] Backtester validation framework (Phases 0–5)
- [x] HTML reports with Plotly convexity dashboard
- [x] Multi-provider live data (Polygon + Finnhub fallback)
- [x] Discord notifications and end-of-day summaries
- [x] Docker deployment
- [ ] Real broker integrations (Alpaca, Interactive Brokers)
- [ ] Multi-symbol portfolio backtesting
- [ ] Walk-forward optimization
- [ ] Web dashboard with live charts

---

## 📄 License

MIT License
