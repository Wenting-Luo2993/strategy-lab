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

### Research registry (`vibe/research_journal/`, `vibe/research_pipeline/`)

- **SQLite authority** — hypotheses, experiments, notes, artifacts, lineage, and
  parameter-sweep journal records are written to `ResearchStore`; normal
  operation creates no YAML/Markdown registry records.
- **Legacy compatibility** — an existing YAML research tree is imported once
  into SQLite with canonical-hash parity, `legacy-uncontrolled` methodology
  stamping, terminal immutability, and pre-P2b execution results kept visibly
  incomparable. After import, reads come from SQLite only.
- **Execution identity** — new experiments retain the complete realistic
  execution-model identity and version.
- **Final-holdout enforcement** — promotion validation fails when the same
  research lineage touches the locked final holdout more than once.
- **Durable remote publication** — terminal summaries and registry identity
  records enter a same-file SQLite outbox atomically, then publish to Supabase
  in deterministic order with idempotent upserts, bounded retry, dead letters,
  restart recovery, and explicit reconciliation/repair.
- **Summary-only boundary** — remote payloads are built from allowlisted
  aggregate columns. Trades, fills, ledgers, equity points/curves, raw bars,
  and other event-level data are rejected recursively at storage and transport
  boundaries; only aggregate counts and evidence checksums leave the device.

The database path defaults outside the repository. Override it with
`RESEARCH__DB_PATH`; do not place an active SQLite file in Git or a synced
working tree.

Review the current device's schema v8 database with the dependency-light local
viewer (Node.js 22.13.0+):

```powershell
node tools\backtest-research-viewer\server.mjs
```

The command resolves the same `RESEARCH__DB_PATH` / `RESEARCH__DATA_DIR`
defaults as Python and serves `http://127.0.0.1:4178`. An explicit database can
be selected only at process startup with `--db C:\local\research.sqlite3`.
The server is read-only/query-only, rejects non-loopback binds and Host headers,
and exposes only allowlisted, parameterized, paginated views. See
`tools/backtest-research-viewer/README.md`.

To enable trusted local publication, apply
`docs/backtest-research-summaries/supabase-research-schema.sql`, then set
`RESEARCH_SUPABASE_URL` and `RESEARCH_SUPABASE_SERVICE_ROLE_KEY`. The research
service-role key must be distinct from live-trading credentials and must never
use a `NEXT_PUBLIC_*` name. Browser/dashboard code receives read-only anonymous
access through row-level security; it never receives the service-role key.

```bash
python scripts/publish_research.py status
python scripts/publish_research.py flush
python scripts/publish_research.py reconcile
# Repairs are never automatic and only requeue records missing remotely.
# Checksum-divergent immutable keys remain visible for administrative resolution.
python scripts/publish_research.py reconcile --repair --reason "operator-approved"
```

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
