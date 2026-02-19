# Strategy Lab

A comprehensive toolkit for algorithmic trading - from strategy development and backtesting to live trading execution across multiple markets (stocks, forex, crypto).

## 🚀 Quick Start

**Live Trading Bot (Production-Ready):**
```bash
cd vibe/trading_bot
docker-compose up -d
```

**Backtesting:**
```bash
cd vibe/backtester
python main.py --symbol AAPL --start 2024-01-01 --end 2024-12-31
```

**Pine Script (TradingView):**
Open `pine/strategies/orb-strategy.pine` in TradingView for rapid prototyping and alerts.

## 📁 Project Structure

### `vibe/` — Production Trading System ⭐

Modern, production-ready trading infrastructure with comprehensive features:

- **`vibe/trading_bot/`** — Live/paper trading bot with multi-market support
  - Supports stocks, forex, and crypto markets
  - Smart event-based logging and monitoring
  - Discord notifications with end-of-day summaries
  - Docker deployment ready (Oracle Cloud, AWS, Azure)
  - See [vibe/trading_bot/README.md](./vibe/trading_bot/README.md) for details

- **`vibe/backtester/`** — Advanced backtesting engine
  - Vectorized backtesting for speed
  - Event-driven backtesting for accuracy
  - Parameter optimization and walk-forward analysis
  - Performance analytics and visualization

- **`vibe/common/`** — Shared components
  - Strategy implementations (ORB, Mean Reversion, etc.)
  - Technical indicators with incremental calculation
  - Risk management and position sizing
  - Data providers and market models

### `pine/` — TradingView Scripts

Pine Script libraries and strategies for rapid prototyping on TradingView:
- `libraries/` — Modular components for display, entry, and risk management
- `strategies/` — Ready-to-use strategies (ORB, etc.) for alerts and screening

### `python/` — Legacy Codebase (Deprecated)

Original Python implementation - superseded by `vibe/`. Kept for reference.

## ✨ Key Features

### Trading Bot (`vibe/trading_bot/`)
- ✅ **Multi-market support** — Stocks (NYSE/NASDAQ), Forex (24/5), Crypto (24/7)
- ✅ **Smart caching** — 30-day cache TTL (historical data never changes)
- ✅ **Event-based logging** — ORB levels, breakouts, rejections (no spam!)
- ✅ **Position-aware intervals** — 1min active monitoring, 5min idle checking
- ✅ **Discord summaries** — End-of-day reports with P&L, ORB levels, activity
- ✅ **Exponential backoff** — Graceful handling of API failures
- ✅ **Docker deployment** — Production-ready with health checks
- ✅ **Multiple strategies** — ORB, Mean Reversion, extensible framework

### Backtester (`vibe/backtester/`)
- ✅ **Dual engines** — Vectorized (fast) and event-driven (realistic)
- ✅ **Rich analytics** — Sharpe ratio, drawdown, win rate, etc.
- ✅ **Parameter optimization** — Grid search, walk-forward validation
- ✅ **Multiple data sources** — Yahoo Finance, Polygon, IEX, custom providers

### Pine Script (`pine/`)
- ✅ **Modular libraries** — Reusable components for custom strategies
- ✅ **Visual alerts** — TradingView integration for rapid screening

## 📖 Documentation

- **Trading Bot**: [vibe/trading_bot/README.md](./vibe/trading_bot/README.md)
- **Multi-Market Guide**: [docs/trading-bot-mvp/MULTI_MARKET_GUIDE.md](./docs/trading-bot-mvp/MULTI_MARKET_GUIDE.md)
- **Deployment Guide**: [docs/trading-bot-mvp/DEPLOYMENT.md](./docs/trading-bot-mvp/DEPLOYMENT.md)
- **Backtester**: [vibe/backtester/README.md](./vibe/backtester/README.md)

## 🔧 Technology Stack

- **Python 3.11+** — Core language
- **Docker** — Containerized deployment
- **FastAPI** — REST API and WebSocket server
- **Streamlit** — Real-time dashboard
- **SQLite/PostgreSQL** — Trade storage
- **yfinance** — Market data (stocks)
- **pandas** — Data processing
- **Discord** — Notifications

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=vibe --cov-report=term-missing

# Run specific test suite
pytest vibe/trading_bot/tests/
```

### Pre-Commit Hooks

```bash
pip install pre-commit
pre-commit install
```

Tests run automatically before each commit. CI/CD via GitHub Actions.

## 🌐 Multi-Market Trading

Switch between markets via environment variables:

**Stocks (Default):**
```bash
MARKET_TYPE=stocks
EXCHANGE=NYSE
SYMBOLS=AAPL,GOOGL,MSFT
```

**Forex (24/5):**
```bash
MARKET_TYPE=forex
SYMBOLS=EURUSD,GBPUSD,USDJPY
```

**Crypto (24/7):**
```bash
MARKET_TYPE=crypto
SYMBOLS=BTCUSD,ETHUSD,SOLUSD
```

See [MULTI_MARKET_GUIDE.md](./docs/trading-bot-mvp/MULTI_MARKET_GUIDE.md) for details.

## 📊 Example Usage

**Live Trading:**
```bash
cd vibe/trading_bot
cp .env.example .env
# Edit .env with your API keys
docker-compose up -d

# Monitor logs
docker-compose logs -f
```

**Backtesting:**
```python
from vibe.backtester import VectorizedBacktester
from vibe.common.strategies import ORBStrategy

strategy = ORBStrategy()
backtester = VectorizedBacktester(initial_capital=10000)
results = backtester.run(df, strategy)
print(results.summary())
```

**Pine Script:**
1. Open TradingView
2. Pine Editor → New → Import `pine/strategies/orb-strategy.pine`
3. Configure parameters → Add to chart

## 🚀 Deployment

**Oracle Cloud (Free Tier):**
```bash
# SSH into instance
ssh ubuntu@your-instance-ip

# Clone and deploy
git clone https://github.com/your-username/strategy-lab.git
cd strategy-lab/vibe/trading_bot
docker-compose up -d
```

See [DEPLOYMENT.md](./docs/trading-bot-mvp/DEPLOYMENT.md) for complete guide.

## 📈 Roadmap

- [x] Multi-market support (stocks/forex/crypto)
- [x] Docker deployment with health monitoring
- [x] Discord notifications with daily summaries
- [x] Smart caching and position-aware intervals
- [ ] Real broker integrations (Alpaca, Interactive Brokers)
- [ ] Advanced strategies (ML-based, multi-timeframe)
- [ ] Portfolio optimization and multi-strategy execution
- [ ] Web dashboard with live charts

## 📄 License

MIT License

## 🙏 Acknowledgments

Built with contributions from Claude Sonnet 4.5 🤖
