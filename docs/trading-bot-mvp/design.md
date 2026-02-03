# Trading Bot Architecture Design

## Overview

A cloud-agnostic, 24/7 Python trading bot supporting multiple strategies (day trading, FOREX, swing trading, crypto) with real-time data streaming, incremental indicator calculations, and comprehensive monitoring. The MVP implements an Opening Range Breakout (ORB) strategy for 5 US stocks using Yahoo Finance for historical data and Finnhub WebSocket for real-time prices.

**MVP Parameters:**
- Initial Capital: $10,000
- Symbols: AAPL, MSFT, AMZN, TSLA, GOOGL
- Primary Timeframe: 5m
- Validation Timeframes: 15m, 1h

## High-Level Architecture

```
+====================================================================================+
|                              VIBE TRADING PLATFORM                                  |
+====================================================================================+
|                                                                                      |
|  +---------------------------+     +---------------------------+                     |
|  |   vibe/trading-bot/       |     |   vibe/backtester/        |                     |
|  |   (Live Trading)          |     |   (Future)                |                     |
|  +-------------+-------------+     +-------------+-------------+                     |
|                |                                 |                                    |
|                +----------------+----------------+                                    |
|                                 |                                                     |
|                                 v                                                     |
|  +-----------------------------------------------------------------------+           |
|  |                        vibe/common/                                    |           |
|  |   (Shared Components - IDENTICAL logic for live & backtest)           |           |
|  +-----------------------------------------------------------------------+           |
|  | strategies/  | indicators/ | risk/      | models/    | validation/    |           |
|  | - ORB        | - Engine    | - Manager  | - Trade    | - MTF Rules    |           |
|  | - Base       | - Wrappers  | - Sizer    | - Order    | - Validator    |           |
|  | - Factory    | - ORB Calc  | - Stops    | - Position |                |           |
|  +-----------------------------------------------------------------------+           |
|                                 |                                                     |
+====================================================================================+
                                  |
        +-------------------------+-------------------------+
        |                         |                         |
        v                         v                         v
+---------------+       +------------------+       +------------------+
| Data Layer    |       | Execution Layer  |       | Monitoring Layer |
+---------------+       +------------------+       +------------------+
| Live:         |       | Live:            |       | Dashboard API    |
| - Finnhub WS  |       | - Mock Exchange  |       | - FastAPI        |
| - Yahoo API   |       | - Order Manager  |       | - Metrics Export |
|               |       |                  |       |                  |
| Backtest:     |       | Backtest:        |       | Dashboard UI     |
| - Parquet     |       | - Instant Sim    |       | - Streamlit      |
| - CSV Files   |       | - Event-driven   |       | - Real-time WS   |
+---------------+       +------------------+       +------------------+
        |                         |                         |
        v                         v                         v
+---------------+       +------------------+       +------------------+
| Storage       |       | Notifications    |       | Infrastructure   |
+---------------+       +------------------+       +------------------+
| - SQLite      |       | - Discord        |       | - Docker         |
| - Cloud Sync  |       | - Rate Limiter   |       | - Health Checks  |
| - Parquet     |       |                  |       | - Graceful Stop  |
+---------------+       +------------------+       +------------------+
```

## Shared Components Architecture (vibe/common/)

### Design Philosophy

The `vibe/common/` module contains all components that MUST produce **identical results** between live trading and backtesting. This ensures:

1. **No Strategy Drift**: A strategy that works in backtest behaves exactly the same live
2. **Reproducible Results**: Same inputs produce same outputs regardless of runtime context
3. **Single Source of Truth**: Bug fixes apply to both live and backtest simultaneously

### Component Classification

```
+===========================================================================+
|                    SHARED (vibe/common/)                                   |
|   Must be IDENTICAL between live trading and backtesting                   |
+===========================================================================+
|                                                                            |
|  STRATEGIES                    INDICATORS                                  |
|  - StrategyBase               - IncrementalIndicatorEngine                 |
|  - ORBStrategy                - TalippWrapper                              |
|  - StrategyFactory            - ORBCalculator                              |
|  - Signal generation logic    - All indicator calculations                 |
|                                                                            |
|  RISK MANAGEMENT              VALIDATION                                   |
|  - RiskManager                - MTFValidator                               |
|  - PositionSizer              - MTFDataStore                               |
|  - StopLossManager            - ValidationRule (ABC)                       |
|  - ExposureController         - TrendAlignmentRule                         |
|  - All risk calculations      - VolumeConfirmationRule                     |
|                               - SupportResistanceRule                      |
|                                                                            |
|  DATA MODELS                  UTILITIES                                    |
|  - Trade                      - Timezone helpers                           |
|  - Order                      - Market hours logic                         |
|  - Position                   - Price/volume calculations                  |
|  - Bar/OHLCV                  - Math utilities                             |
|  - Fill                                                                    |
|  - Signal                                                                  |
|                                                                            |
+===========================================================================+
|                    DIFFERENT (runtime-specific)                            |
|   Implementations differ between live trading and backtesting              |
+===========================================================================+
|                                                                            |
|  LIVE (vibe/trading-bot/)              BACKTEST (vibe/backtester/)         |
|  -------------------------             ----------------------------         |
|  Data Providers:                       Data Providers:                      |
|  - FinnhubWebSocketClient              - ParquetDataLoader                  |
|  - YahooDataProvider                   - CSVDataLoader                      |
|  - Real-time streaming                 - Historical file reading            |
|                                                                            |
|  Execution:                            Execution:                           |
|  - MockExchange (realistic)            - InstantFillSimulator               |
|  - OrderManager with retries           - Event-driven execution             |
|  - Network latency handling            - No latency simulation              |
|                                                                            |
|  Timing:                               Timing:                              |
|  - Real clock (datetime.now)           - Simulated clock                    |
|  - asyncio event loop                  - Event-driven iteration             |
|  - Sleep during off-hours              - Fast-forward through data          |
|                                                                            |
|  Infrastructure:                       Infrastructure:                      |
|  - Discord notifications               - Results aggregation                |
|  - Health monitoring                   - Performance reports                |
|  - Cloud sync                          - Visualization                      |
|                                                                            |
+===========================================================================+
```

### Interface Boundaries

```python
# vibe/common/execution/base.py
class ExecutionEngine(ABC):
    """
    Abstract interface that both live and backtest implement.
    Strategies interact with this interface, not concrete implementations.
    """

    @abstractmethod
    async def submit_order(self, order: Order) -> OrderResponse:
        """Submit order for execution."""
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        pass

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for symbol."""
        pass

    @abstractmethod
    def get_account(self) -> AccountState:
        """Get account state (cash, equity, etc.)."""
        pass


# vibe/common/data/base.py
class DataProvider(ABC):
    """
    Abstract interface for data access.
    Strategies request data through this interface.
    """

    @abstractmethod
    def get_bars(self, symbol: str, timeframe: str, n: int) -> pd.DataFrame:
        """Get last N bars for symbol at timeframe."""
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Get current/latest price for symbol."""
        pass


# vibe/common/clock/base.py
class Clock(ABC):
    """
    Abstract interface for time.
    Allows backtest to control simulated time.
    """

    @abstractmethod
    def now(self) -> datetime:
        """Get current timestamp."""
        pass

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if market is currently open."""
        pass
```

---

## Runtime Model

### Service Architecture

The trading bot runs as a **long-running service** that can be:
1. Started directly with `python main.py` for development
2. Deployed as a Docker container with health checks
3. Managed by systemd/supervisor for production

```
+------------------------------------------------------------------+
|                      RUNTIME ARCHITECTURE                         |
+------------------------------------------------------------------+
|                                                                    |
|  main.py                                                           |
|  +--------------------------------------------------------------+  |
|  |  1. Parse CLI arguments                                      |  |
|  |  2. Load configuration                                       |  |
|  |  3. Setup signal handlers (SIGTERM, SIGINT)                  |  |
|  |  4. Initialize components                                    |  |
|  |  5. Start health check server (optional)                     |  |
|  |  6. Run main trading loop                                    |  |
|  |  7. Graceful shutdown on signal                              |  |
|  +--------------------------------------------------------------+  |
|                                                                    |
|  Trading Loop (asyncio)                                            |
|  +--------------------------------------------------------------+  |
|  |  while running:                                              |  |
|  |    if market_open:                                           |  |
|  |      - Process incoming data                                 |  |
|  |      - Generate signals                                      |  |
|  |      - Execute orders                                        |  |
|  |      - Update positions                                      |  |
|  |    else:                                                     |  |
|  |      - Sleep until next market open                          |  |
|  |      - Periodic health checks                                |  |
|  |    - Sync to cloud (every 5 min)                             |  |
|  +--------------------------------------------------------------+  |
|                                                                    |
|  Health Check Server (FastAPI, port 8080)                          |
|  +--------------------------------------------------------------+  |
|  |  GET /health/live   -> 200 if process alive                  |  |
|  |  GET /health/ready  -> 200 if ready to trade                 |  |
|  |  GET /metrics       -> Prometheus metrics                    |  |
|  +--------------------------------------------------------------+  |
|                                                                    |
+------------------------------------------------------------------+
```

### Graceful Shutdown Implementation

```python
# vibe/trading-bot/main.py
import signal
import asyncio
from contextlib import asynccontextmanager

class TradingService:
    """Main trading service with graceful shutdown support."""

    def __init__(self, config: Config):
        self.config = config
        self._shutdown_event = asyncio.Event()
        self._components_initialized = False

    def _setup_signal_handlers(self):
        """Setup handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
            )

    async def _handle_shutdown(self, sig: signal.Signals):
        """Handle shutdown signal gracefully."""
        logger.info(f"Received {sig.name}, initiating graceful shutdown...")

        # 1. Stop accepting new signals
        self._shutdown_event.set()

        # 2. Close open positions (if configured)
        if self.config.close_positions_on_shutdown:
            await self._close_all_positions()

        # 3. Cancel pending orders
        await self._cancel_pending_orders()

        # 4. Sync database to cloud
        await self._sync_to_cloud()

        # 5. Disconnect from data sources
        await self._disconnect_data_sources()

        # 6. Save indicator state
        await self._save_indicator_state()

        logger.info("Graceful shutdown complete")

    async def run(self):
        """Main entry point."""
        self._setup_signal_handlers()

        try:
            await self._initialize_components()
            await self._run_trading_loop()
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            raise
        finally:
            await self._cleanup()

    async def _run_trading_loop(self):
        """Main trading loop."""
        while not self._shutdown_event.is_set():
            try:
                if self.scheduler.is_market_open():
                    await self._process_trading_cycle()
                else:
                    await self._sleep_until_market_open()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                await asyncio.sleep(5)  # Brief pause before retry


# Entry point
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Vibe Trading Bot")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--dry-run", action="store_true", help="Run without executing orders")
    parser.add_argument("--health-port", type=int, default=8080, help="Health check port")
    args = parser.parse_args()

    config = load_config(args.config)
    service = TradingService(config)

    asyncio.run(service.run())
```

### Dockerfile for Production

```dockerfile
FROM python:3.11-slim

# Use exec form to ensure PID 1 receives signals
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY vibe/ ./vibe/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health/live || exit 1

# Use exec form for proper signal handling
ENTRYPOINT ["python", "-m", "vibe.trading_bot.main"]
CMD ["--config", "/app/config/config.yaml"]
```

### Health Check Endpoints

```python
# vibe/trading-bot/api/health.py
from fastapi import FastAPI, Response, status
from datetime import datetime, timedelta

app = FastAPI()

# Global state (set by trading service)
_health_state = {
    "started_at": None,
    "last_heartbeat": None,
    "websocket_connected": False,
    "last_trade_time": None,
    "error_count": 0,
}

@app.get("/health/live")
async def liveness():
    """
    Liveness probe - is the process alive?
    Kubernetes restarts container if this fails.
    """
    return {
        "status": "alive",
        "uptime_seconds": (datetime.now() - _health_state["started_at"]).total_seconds()
    }

@app.get("/health/ready")
async def readiness():
    """
    Readiness probe - is the service ready to trade?
    Kubernetes stops sending traffic if this fails.
    """
    checks = {
        "websocket": _health_state["websocket_connected"],
        "recent_heartbeat": (
            _health_state["last_heartbeat"] and
            datetime.now() - _health_state["last_heartbeat"] < timedelta(minutes=2)
        ),
        "low_error_rate": _health_state["error_count"] < 10,
    }

    all_healthy = all(checks.values())

    if all_healthy:
        return {"status": "ready", "checks": checks}
    else:
        return Response(
            content=json.dumps({"status": "not_ready", "checks": checks}),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json"
        )

@app.get("/metrics")
async def metrics():
    """Prometheus-compatible metrics endpoint."""
    return {
        "trading_bot_uptime_seconds": (datetime.now() - _health_state["started_at"]).total_seconds(),
        "trading_bot_websocket_connected": 1 if _health_state["websocket_connected"] else 0,
        "trading_bot_error_count": _health_state["error_count"],
    }
```

---

## Mock Exchange Library Analysis

### Library Comparison

| Library | Slippage | Partial Fills | Order Types | Maintenance | Recommendation |
|---------|----------|---------------|-------------|-------------|----------------|
| [Backtrader](https://www.backtrader.com/) | Built-in, configurable | Supported | Market, Limit, Stop, StopLimit, OCO | Active community | **Best for reference patterns** |
| [CCXT](https://github.com/ccxt/ccxt) | Exchange-dependent | Supported | Exchange-dependent | Very active | Crypto only, overkill for mock |
| [Zipline](https://github.com/quantopian/zipline) | Basic | Limited | Market, Limit | Unmaintained | Not recommended |
| [VectorBT](https://github.com/polakowo/vectorbt) | Configurable | Limited | Basic | Active | Vectorized, not event-driven |
| **Custom** | Full control | Full control | All | Self-maintained | **Selected for MVP** |

### Decision: Custom Mock Exchange

**Rationale:**
1. **Backtrader** is excellent but brings heavy dependencies and its own event system that conflicts with our asyncio architecture
2. **CCXT** is designed for real exchange connectivity, not paper trading simulation
3. **Zipline** is effectively abandoned and has Python version issues
4. **VectorBT** is vectorized (great for backtest) but doesn't fit live trading's event-driven model

**Selected Approach:** Build a **lightweight custom MockExchange** using patterns from Backtrader's broker simulation, specifically:
- Slippage model (volume-based, percentage-based)
- Fill simulation with configurable delays
- Partial fill probability
- Order lifecycle management

### Minimal Mock Exchange Requirements

```python
# vibe/trading-bot/exchange/mock_exchange.py
class MockExchange(ExecutionEngine):
    """
    Paper trading exchange for MVP.

    Implements realistic simulation without heavy dependencies.
    Based on patterns from Backtrader's broker module.
    """

    def __init__(self, config: MockExchangeConfig):
        self.initial_capital = config.initial_capital  # $10,000
        self.slippage_model = SlippageModel(
            base_pct=config.slippage_pct,        # 0.05%
            volatility_factor=config.vol_factor,  # 0.5
            size_factor=config.size_factor        # 0.0001
        )
        self.fill_delay_ms = config.fill_delay_ms  # 100-500ms
        self.partial_fill_prob = config.partial_fill_prob  # 0.1 (10%)

        # State
        self.cash = self.initial_capital
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.pending_orders: List[Order] = []

    # Required order types for MVP
    ORDER_TYPES = {
        "MARKET": self._execute_market_order,
        "LIMIT": self._execute_limit_order,
        "STOP_LOSS": self._execute_stop_order,
        "STOP_LIMIT": self._execute_stop_limit_order,
    }
```

### Future: Live Broker Integration

When ready for live trading, implement the same `ExecutionEngine` interface:

```python
# vibe/trading-bot/exchange/alpaca_exchange.py (future)
class AlpacaExchange(ExecutionEngine):
    """Live trading via Alpaca API."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True):
        self.api = tradeapi.REST(api_key, secret_key, base_url=...)

    async def submit_order(self, order: Order) -> OrderResponse:
        # Real API call to Alpaca
        alpaca_order = self.api.submit_order(...)
        return self._convert_response(alpaca_order)
```

---

## Real-Time Monitoring Dashboard

### Architecture Overview

```
+===========================================================================+
|                    MONITORING ARCHITECTURE                                 |
+===========================================================================+
|                                                                            |
|  +-------------------+     +-------------------+     +-------------------+  |
|  |  Trading Bot      |     |  Dashboard API    |     |  Dashboard UI     |  |
|  |  (vibe/trading-   |     |  (FastAPI)        |     |  (Streamlit)      |  |
|  |   bot/)           |     |                   |     |                   |  |
|  +--------+----------+     +--------+----------+     +--------+----------+  |
|           |                         |                         ^            |
|           v                         v                         |            |
|  +-------------------+     +-------------------+              |            |
|  |  SQLite DB        |---->|  REST API         |--------------+            |
|  |  - trades         |     |  GET /trades      |                           |
|  |  - orders         |     |  GET /positions   |     +-------------------+  |
|  |  - metrics        |     |  GET /metrics     |     |  WebSocket        |  |
|  |  - health         |     |  GET /health      |---->|  (real-time       |  |
|  +-------------------+     +-------------------+     |   updates)        |  |
|                                                      +-------------------+  |
+===========================================================================+
```

### Technology Selection: Streamlit (Recommended for MVP)

| Option | Pros | Cons | Cost | Recommendation |
|--------|------|------|------|----------------|
| **Streamlit** | Python-native, fast development, real-time support, free hosting | Limited customization, session-based | Free (Community Cloud) | **RECOMMENDED** |
| Grafana | Excellent for metrics, built-in alerting | Requires time-series DB, steeper learning curve | Free (OSS) | Good for ops monitoring |
| Dash (Plotly) | More control, better for complex visualizations | More boilerplate, steeper learning curve | Free | Good alternative |
| Custom (React) | Full control | Significant development effort | Free | Overkill for MVP |

**Rationale for Streamlit:**
1. **Python-native**: No frontend expertise needed
2. **Real-time capable**: Built-in `st.rerun()` and experimental streaming
3. **Free hosting**: Streamlit Community Cloud offers free deployment
4. **Trading dashboard examples**: Many existing templates for trading bots
5. **Fast iteration**: Changes reflect immediately

### Dashboard API Design (FastAPI)

```python
# vibe/trading-bot/api/dashboard.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import asyncio

app = FastAPI(title="Trading Bot Dashboard API")

# CORS for Streamlit
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ REST Endpoints ============

@app.get("/api/trades")
async def get_trades(
    limit: int = 100,
    symbol: str = None,
    status: str = None
) -> List[Trade]:
    """Get recent trades with optional filters."""
    return trade_store.get_trades(limit=limit, symbol=symbol, status=status)

@app.get("/api/positions")
async def get_positions() -> List[Position]:
    """Get current open positions."""
    return position_manager.get_all_positions()

@app.get("/api/account")
async def get_account() -> AccountSummary:
    """Get account summary."""
    return {
        "cash": exchange.cash,
        "equity": exchange.get_equity(),
        "unrealized_pnl": exchange.get_unrealized_pnl(),
        "daily_pnl": metrics.get_daily_pnl(),
        "total_trades": trade_store.count_trades(),
    }

@app.get("/api/metrics/performance")
async def get_performance_metrics(period: str = "daily") -> PerformanceMetrics:
    """Get performance metrics."""
    return {
        "pnl": metrics.get_pnl(period),
        "win_rate": metrics.get_win_rate(period),
        "sharpe_ratio": metrics.get_sharpe_ratio(period),
        "max_drawdown": metrics.get_max_drawdown(period),
        "trade_count": metrics.get_trade_count(period),
        "avg_trade_duration": metrics.get_avg_duration(period),
    }

@app.get("/api/metrics/health")
async def get_health_metrics() -> HealthMetrics:
    """Get service health metrics."""
    return {
        "uptime_seconds": health_monitor.uptime_seconds,
        "websocket_status": health_monitor.websocket_status,
        "last_data_time": health_monitor.last_data_time,
        "cpu_percent": health_monitor.cpu_percent,
        "memory_mb": health_monitor.memory_mb,
        "error_count_1h": health_monitor.error_count_1h,
    }

# ============ WebSocket for Real-Time Updates ============

class ConnectionManager:
    """Manage WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

@app.websocket("/ws/updates")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, receive any client messages
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Called by trading bot when events occur
async def broadcast_trade_update(trade: Trade):
    await manager.broadcast({"type": "trade", "data": trade.dict()})

async def broadcast_position_update(position: Position):
    await manager.broadcast({"type": "position", "data": position.dict()})

async def broadcast_metrics_update(metrics: dict):
    await manager.broadcast({"type": "metrics", "data": metrics})
```

### Streamlit Dashboard Implementation

```python
# vibe/dashboard/app.py
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import time

# Configuration
API_BASE = st.secrets.get("API_BASE", "http://localhost:8080")
REFRESH_INTERVAL = 5  # seconds

st.set_page_config(
    page_title="Vibe Trading Bot",
    page_icon="chart_with_upwards_trend",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============ Sidebar ============
st.sidebar.title("Vibe Trading Bot")
st.sidebar.markdown("---")

# Auto-refresh toggle
auto_refresh = st.sidebar.checkbox("Auto-refresh", value=True)
if auto_refresh:
    time.sleep(REFRESH_INTERVAL)
    st.rerun()

# ============ Main Content ============

# Row 1: Account Summary
col1, col2, col3, col4 = st.columns(4)

account = requests.get(f"{API_BASE}/api/account").json()

col1.metric("Cash", f"${account['cash']:,.2f}")
col2.metric("Equity", f"${account['equity']:,.2f}")
col3.metric(
    "Daily P&L",
    f"${account['daily_pnl']:,.2f}",
    delta=f"{account['daily_pnl']/account['equity']*100:.2f}%"
)
col4.metric("Total Trades", account['total_trades'])

# Row 2: Performance Metrics
st.markdown("### Performance Metrics")
col1, col2, col3, col4 = st.columns(4)

perf = requests.get(f"{API_BASE}/api/metrics/performance").json()

col1.metric("Win Rate", f"{perf['win_rate']*100:.1f}%")
col2.metric("Sharpe Ratio", f"{perf['sharpe_ratio']:.2f}")
col3.metric("Max Drawdown", f"{perf['max_drawdown']*100:.1f}%")
col4.metric("Avg Trade Duration", f"{perf['avg_trade_duration']:.0f} min")

# Row 3: Current Positions
st.markdown("### Open Positions")
positions = requests.get(f"{API_BASE}/api/positions").json()

if positions:
    df_positions = pd.DataFrame(positions)
    st.dataframe(
        df_positions[['symbol', 'quantity', 'entry_price', 'current_price', 'unrealized_pnl', 'unrealized_pnl_pct']],
        use_container_width=True
    )
else:
    st.info("No open positions")

# Row 4: Recent Trades
st.markdown("### Recent Trades")
trades = requests.get(f"{API_BASE}/api/trades?limit=20").json()

if trades:
    df_trades = pd.DataFrame(trades)
    df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'])

    # Color code P&L
    def highlight_pnl(val):
        if val > 0:
            return 'background-color: #d4edda'
        elif val < 0:
            return 'background-color: #f8d7da'
        return ''

    st.dataframe(
        df_trades[['symbol', 'side', 'quantity', 'entry_price', 'exit_price', 'pnl', 'status', 'entry_time']]
        .style.applymap(highlight_pnl, subset=['pnl']),
        use_container_width=True
    )

# Row 5: P&L Chart
st.markdown("### Cumulative P&L")
if trades:
    df_trades_sorted = df_trades.sort_values('entry_time')
    df_trades_sorted['cumulative_pnl'] = df_trades_sorted['pnl'].cumsum()

    fig = px.line(
        df_trades_sorted,
        x='entry_time',
        y='cumulative_pnl',
        title='Cumulative P&L Over Time'
    )
    st.plotly_chart(fig, use_container_width=True)

# Row 6: Service Health
st.markdown("### Service Health")
health = requests.get(f"{API_BASE}/api/metrics/health").json()

col1, col2, col3 = st.columns(3)

ws_status = "Connected" if health['websocket_status'] else "Disconnected"
ws_color = "green" if health['websocket_status'] else "red"
col1.markdown(f"**WebSocket:** :{ws_color}[{ws_status}]")

col2.metric("Uptime", f"{health['uptime_seconds']/3600:.1f} hours")
col3.metric("Errors (1h)", health['error_count_1h'])

# Footer
st.markdown("---")
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
```

### Data Flow: Bot to Dashboard

```
Trading Bot                    SQLite DB                 Dashboard API              Streamlit UI
     |                             |                          |                          |
     | 1. Trade executed           |                          |                          |
     |--------------------------->>|                          |                          |
     |    INSERT INTO trades       |                          |                          |
     |                             |                          |                          |
     | 2. Broadcast via WS         |                          |                          |
     |-------------------------------------------->>|                          |
     |    {"type": "trade", ...}   |                          |                          |
     |                             |                          |                          |
     |                             |                          | 3. WS push to clients    |
     |                             |                          |------------------------>>|
     |                             |                          |    Real-time update      |
     |                             |                          |                          |
     |                             | 4. Periodic poll         |                          |
     |                             |<<------------------------|                          |
     |                             |    SELECT * FROM trades  |                          |
     |                             |                          |                          |
     |                             |                          | 5. REST response         |
     |                             |                          |------------------------>>|
     |                             |                          |    Display in table      |
```

### Free Deployment Options

| Component | Free Option | Limits | Notes |
|-----------|-------------|--------|-------|
| Trading Bot | Oracle Cloud Free Tier | 4 ARM cores, 24GB RAM | Run Docker container |
| Dashboard API | Same Oracle instance | - | FastAPI runs alongside bot |
| Streamlit UI | Streamlit Community Cloud | 1GB RAM, public apps | Connect to API via internet |
| Database | SQLite (local) | - | Synced to cloud storage |

---

## Updated Directory Structure

```
vibe/
|-- __init__.py
|
|-- common/                         # SHARED: Identical for live & backtest
|   |-- __init__.py
|   |
|   |-- strategies/                 # Strategy implementations
|   |   |-- __init__.py
|   |   |-- base.py                 # StrategyBase ABC
|   |   |-- orb.py                  # ORB strategy
|   |   |-- factory.py              # Strategy factory
|   |
|   |-- indicators/                 # Indicator calculations
|   |   |-- __init__.py
|   |   |-- engine.py               # IncrementalIndicatorEngine
|   |   |-- wrappers.py             # talipp wrappers
|   |   |-- orb_levels.py           # ORB-specific calculations
|   |   |-- mtf_manager.py          # Multi-timeframe management
|   |
|   |-- risk/                       # Risk management
|   |   |-- __init__.py
|   |   |-- manager.py              # RiskManager
|   |   |-- position_sizer.py       # Position sizing
|   |   |-- stop_loss.py            # Stop loss management
|   |   |-- exposure.py             # Exposure limits
|   |
|   |-- validation/                 # MTF validation framework
|   |   |-- __init__.py
|   |   |-- mtf_validator.py        # MTF validation orchestrator
|   |   |-- mtf_data_store.py       # Multi-timeframe data store
|   |   |-- rules/
|   |   |   |-- __init__.py
|   |   |   |-- base.py             # ValidationRule ABC
|   |   |   |-- trend_alignment.py
|   |   |   |-- volume_confirmation.py
|   |   |   |-- support_resistance.py
|   |
|   |-- models/                     # Data models (shared)
|   |   |-- __init__.py
|   |   |-- trade.py                # Trade model
|   |   |-- order.py                # Order model
|   |   |-- position.py             # Position model
|   |   |-- bar.py                  # OHLCV bar model
|   |   |-- signal.py               # Trading signal model
|   |   |-- account.py              # Account state model
|   |
|   |-- execution/                  # Execution interfaces
|   |   |-- __init__.py
|   |   |-- base.py                 # ExecutionEngine ABC
|   |
|   |-- data/                       # Data interfaces
|   |   |-- __init__.py
|   |   |-- base.py                 # DataProvider ABC
|   |
|   |-- clock/                      # Time interfaces
|   |   |-- __init__.py
|   |   |-- base.py                 # Clock ABC
|   |   |-- live_clock.py           # Real-time clock
|   |   |-- market_hours.py         # Market hours logic
|   |
|   |-- utils/                      # Shared utilities
|       |-- __init__.py
|       |-- timezone.py             # Timezone helpers
|       |-- math_utils.py           # Math utilities
|
|-- trading-bot/                    # LIVE TRADING specific
|   |-- __init__.py
|   |-- main.py                     # Entry point
|   |
|   |-- config/
|   |   |-- __init__.py
|   |   |-- settings.py             # Pydantic settings
|   |   |-- constants.py            # Constants
|   |   |-- logging_config.py       # Logging setup
|   |
|   |-- core/
|   |   |-- __init__.py
|   |   |-- orchestrator.py         # Main trading loop
|   |   |-- scheduler.py            # Market hours scheduler
|   |   |-- health_monitor.py       # Health monitoring
|   |
|   |-- data/
|   |   |-- __init__.py
|   |   |-- providers/
|   |   |   |-- __init__.py
|   |   |   |-- yahoo.py            # Yahoo Finance provider
|   |   |   |-- finnhub.py          # Finnhub WebSocket
|   |   |   |-- factory.py          # Provider factory
|   |   |-- aggregator.py           # Bar aggregator
|   |   |-- mtf_aggregator.py       # MTF bar aggregator
|   |   |-- cache.py                # Data cache
|   |   |-- manager.py              # Data manager
|   |
|   |-- exchange/
|   |   |-- __init__.py
|   |   |-- mock_exchange.py        # Paper trading
|   |   |-- slippage.py             # Slippage model
|   |   |-- order_manager.py        # Order lifecycle
|   |   |-- retry_policy.py         # Retry logic
|   |
|   |-- storage/
|   |   |-- __init__.py
|   |   |-- trade_store.py          # Trade database
|   |   |-- order_store.py          # Order history
|   |   |-- metrics_store.py        # Metrics storage
|   |   |-- cloud/
|   |   |   |-- __init__.py
|   |   |   |-- base.py             # CloudStorageProvider ABC
|   |   |   |-- oracle.py           # Oracle Object Storage
|   |   |   |-- aws.py              # AWS S3
|   |   |   |-- azure.py            # Azure Blob
|   |   |   |-- local.py            # Local filesystem
|   |   |   |-- factory.py          # Provider factory
|   |   |-- sync.py                 # Database sync
|   |
|   |-- notifications/
|   |   |-- __init__.py
|   |   |-- base.py                 # NotificationService ABC
|   |   |-- discord.py              # Discord webhook
|   |   |-- formatter.py            # Message formatting
|   |   |-- payloads.py             # Notification payloads
|   |   |-- rate_limiter.py         # Rate limiting
|   |
|   |-- api/
|   |   |-- __init__.py
|   |   |-- health.py               # Health check endpoints
|   |   |-- dashboard.py            # Dashboard API endpoints
|   |
|   |-- utils/
|       |-- __init__.py
|       |-- logger.py               # Logging utilities
|       |-- retry.py                # Retry decorators
|
|-- backtester/                     # BACKTESTING specific (future)
|   |-- __init__.py
|   |-- main.py                     # Backtest entry point
|   |-- engine.py                   # Backtest engine
|   |-- data_loader.py              # Historical data loader
|   |-- simulator.py                # Instant fill simulator
|   |-- clock.py                    # Simulated clock
|   |-- results.py                  # Results aggregation
|
|-- dashboard/                      # Streamlit dashboard
|   |-- __init__.py
|   |-- app.py                      # Main Streamlit app
|   |-- pages/
|   |   |-- trades.py               # Trades page
|   |   |-- positions.py            # Positions page
|   |   |-- performance.py          # Performance page
|   |   |-- health.py               # Health page
|   |-- components/
|       |-- charts.py               # Chart components
|       |-- tables.py               # Table components
|
|-- tests/
    |-- __init__.py
    |-- conftest.py
    |-- common/                     # Tests for shared components
    |   |-- test_strategies.py
    |   |-- test_indicators.py
    |   |-- test_risk.py
    |   |-- test_validation.py
    |   |-- test_models.py
    |-- trading_bot/                # Tests for live trading
    |   |-- test_exchange.py
    |   |-- test_data_providers.py
    |   |-- test_notifications.py
    |-- integration/
    |   |-- test_full_cycle.py
    |-- e2e/
        |-- test_mock_trading.py
```

---

## Configuration and Environment Management

### Overview

The trading bot uses a **layered configuration system** that enables seamless deployment across local, development, and production environments **without code changes**.

**See detailed documentation:** [`configuration-system.md`](./configuration-system.md)

### Configuration Stack

```
┌─────────────────────────────────────────────────────────────┐
│  Priority: CLI > Environment Variables > YAML > Defaults     │
├─────────────────────────────────────────────────────────────┤
│  1. Hardcoded Defaults (in code)                             │
│  2. YAML Config File (config/{env}.yaml)                     │
│  3. Environment Variables (.env file)                        │
│  4. CLI Arguments (--env prod)                               │
└─────────────────────────────────────────────────────────────┘
```

### Environment Detection

Automatic environment detection based on:
1. `TRADING_ENV` environment variable
2. Hostname heuristics (Oracle Cloud detection)
3. CI/CD indicators
4. Default to `local`

### Environment-Specific Behavior

| Environment | Bot Location | Dashboard Access | Auth Required | Config File |
|-------------|--------------|------------------|---------------|-------------|
| **local** | Laptop | http://localhost:8501 | No | config/local.yaml |
| **dev** | Oracle Cloud | http://localhost:8501 (SSH tunnel) | No | config/dev.yaml |
| **prod** | Oracle Cloud | https://your-app.streamlit.app | Yes | config/prod.yaml |

### Key Configuration Sections

```python
# Configuration schema
class TradingBotConfig:
    environment: Environment            # local, dev, prod
    api: APIConfig                     # Host, port, auth, CORS
    dashboard: DashboardConfig         # Streamlit mode, refresh rate
    data: DataProviderConfig           # Yahoo, Finnhub, API keys
    exchange: ExchangeConfig           # Mock, Alpaca, paper trading
    notifications: NotificationConfig  # Discord, webhooks
    storage: StorageConfig             # Database, cloud sync
    features: FeatureFlags             # Enable/disable features
```

### Feature Flags

Toggle functionality without redeployment:

```python
features:
  enable_dashboard_api: true
  enable_health_checks: true
  enable_cloud_sync: true
  enable_notifications: true
  enable_mtf_validation: true
  enable_risk_management: true
  allow_short_selling: false
  allow_options_trading: false
```

### Deployment Workflows

**Local Development:**
```bash
TRADING_ENV=local python -m vibe.trading_bot.main
streamlit run vibe/dashboard/app.py
# Dashboard: http://localhost:8501
```

**Oracle Cloud + SSH Tunnel (Dev):**
```bash
# On Oracle Cloud
TRADING_ENV=dev docker-compose up -d

# On laptop
ssh -L 8501:localhost:8501 ubuntu@oracle-ip
# Dashboard: http://localhost:8501 (via tunnel)
```

**Oracle Cloud + Streamlit Cloud (Prod):**
```bash
# On Oracle Cloud
TRADING_ENV=prod docker-compose up -d

# Dashboard: https://your-app.streamlit.app (remote)
```

### Example Configuration Files

**config/local.yaml** (Development on laptop):
```yaml
environment: local
api:
  base_url: "http://localhost:8080"
  enable_auth: false
dashboard:
  streamlit_mode: "local"
notifications:
  discord_enabled: false
storage:
  cloud_sync_enabled: false
```

**config/dev.yaml** (Oracle Cloud with SSH tunnel):
```yaml
environment: dev
api:
  base_url: "http://localhost:8080"  # Accessed via tunnel
  enable_auth: false                 # SSH provides security
dashboard:
  streamlit_mode: "local"            # Runs on Oracle, accessed via tunnel
notifications:
  discord_enabled: true
storage:
  cloud_sync_enabled: true
  cloud_provider: "oracle"
```

**config/prod.yaml** (Oracle Cloud with Streamlit Cloud):
```yaml
environment: prod
api:
  base_url: "https://123.456.789.0:8080"  # Public URL
  enable_auth: true                        # API key required
dashboard:
  streamlit_mode: "remote"                 # Streamlit Cloud
notifications:
  discord_enabled: true
storage:
  cloud_sync_enabled: true
  cloud_provider: "oracle"
```

### Secrets Management

**Never commit secrets to git!**

```bash
# .env.example (commit this)
TRADING_ENV=local
DATA__FINNHUB_API_KEY=your_key_here

# .env (DO NOT commit)
TRADING_ENV=prod
DATA__FINNHUB_API_KEY=actual_production_key
API__API_KEY=550e8400-e29b-41d4-a716-446655440000
NOTIFICATION__DISCORD_WEBHOOK_URL=https://discord.com/...
```

---

## Component Breakdown (Updated)

### 1. Strategy Engine (vibe/common/strategies/)
**Responsibility:** Generate trading signals - SHARED between live and backtest

| Component | Description | Location |
|-----------|-------------|----------|
| `StrategyBase` | Abstract base class defining signal generation interface | vibe/common/strategies/base.py |
| `ORBStrategy` | Opening Range Breakout implementation for MVP | vibe/common/strategies/orb.py |
| `StrategyFactory` | Creates strategy instances from configuration | vibe/common/strategies/factory.py |

### 2. Indicator Engine (vibe/common/indicators/)
**Responsibility:** Calculate technical indicators - SHARED between live and backtest

| Component | Description | Location |
|-----------|-------------|----------|
| `IncrementalIndicatorEngine` | Orchestrates incremental calculation with state | vibe/common/indicators/engine.py |
| `TalippWrapper` | Adapter for talipp library indicators | vibe/common/indicators/wrappers.py |
| `ORBCalculator` | Day-scoped ORB level calculation | vibe/common/indicators/orb_levels.py |
| `MTFIndicatorManager` | Manages indicators across multiple timeframes | vibe/common/indicators/mtf_manager.py |

### 3. Risk Management (vibe/common/risk/)
**Responsibility:** Position sizing, stop losses, and exposure control - SHARED

| Component | Description | Location |
|-----------|-------------|----------|
| `RiskManager` | Coordinates all risk checks before order execution | vibe/common/risk/manager.py |
| `PositionSizer` | Calculates position size based on account/risk rules | vibe/common/risk/position_sizer.py |
| `StopLossManager` | ATR-based and percentage-based stop management | vibe/common/risk/stop_loss.py |
| `ExposureController` | Max positions, sector limits, correlation checks | vibe/common/risk/exposure.py |

### 4. MTF Validation (vibe/common/validation/)
**Responsibility:** Multi-timeframe signal validation - SHARED

| Component | Description | Location |
|-----------|-------------|----------|
| `MTFValidator` | Orchestrates validation rules against signal | vibe/common/validation/mtf_validator.py |
| `MTFDataStore` | Maintains OHLCV data for multiple timeframes | vibe/common/validation/mtf_data_store.py |
| `ValidationRule` (ABC) | Interface for pluggable validation rules | vibe/common/validation/rules/base.py |
| `TrendAlignmentRule` | Checks if higher TF trend aligns with signal | vibe/common/validation/rules/trend_alignment.py |

### 5. Data Models (vibe/common/models/)
**Responsibility:** Shared data structures - SHARED

| Component | Description | Location |
|-----------|-------------|----------|
| `Trade` | Trade record with entry, exit, P&L | vibe/common/models/trade.py |
| `Order` | Order with type, price, quantity, status | vibe/common/models/order.py |
| `Position` | Current position state | vibe/common/models/position.py |
| `Bar` | OHLCV bar data | vibe/common/models/bar.py |

### 6. Live Data Providers (vibe/trading-bot/data/)
**Responsibility:** Real-time and historical data - LIVE ONLY

| Component | Description | Location |
|-----------|-------------|----------|
| `YahooDataProvider` | Historical OHLCV via yfinance | vibe/trading-bot/data/providers/yahoo.py |
| `FinnhubWebSocketClient` | Real-time trade stream | vibe/trading-bot/data/providers/finnhub.py |
| `BarAggregator` | Tick-to-bar conversion | vibe/trading-bot/data/aggregator.py |

### 7. Mock Exchange (vibe/trading-bot/exchange/)
**Responsibility:** Paper trading execution - LIVE ONLY

| Component | Description | Location |
|-----------|-------------|----------|
| `MockExchange` | Paper trading with slippage and fill simulation | vibe/trading-bot/exchange/mock_exchange.py |
| `SlippageModel` | Realistic slippage calculation | vibe/trading-bot/exchange/slippage.py |
| `OrderManager` | Order lifecycle with retry logic | vibe/trading-bot/exchange/order_manager.py |

### 8. Dashboard (vibe/dashboard/)
**Responsibility:** Real-time monitoring UI

| Component | Description | Location |
|-----------|-------------|----------|
| `app.py` | Main Streamlit application | vibe/dashboard/app.py |
| Dashboard API | FastAPI endpoints for data access | vibe/trading-bot/api/dashboard.py |
| Health API | Health check endpoints | vibe/trading-bot/api/health.py |

---

## Key Design Decisions (Updated)

### 8. Shared Components Architecture
**Decision:** Extract all deterministic logic to `vibe/common/` for backtest compatibility

**Trade-off:**
- (+) Identical behavior between live and backtest
- (+) Single source of truth for strategy logic
- (+) Bug fixes apply everywhere
- (-) More upfront design effort
- (-) Stricter interface requirements

**Mitigation:** Well-defined ABCs at boundaries (ExecutionEngine, DataProvider, Clock)

### 9. Custom Mock Exchange over Library
**Decision:** Build lightweight MockExchange using Backtrader patterns instead of integrating full library

**Trade-off:**
- (+) No heavy dependencies
- (+) Full control over simulation behavior
- (+) Matches our asyncio architecture
- (-) More implementation effort
- (-) Need to validate accuracy

**Mitigation:** Validate against Backtrader's broker simulation; unit test slippage model

### 10. Streamlit for Dashboard
**Decision:** Use Streamlit over Grafana or custom React dashboard

**Trade-off:**
- (+) Python-native - fast development
- (+) Free hosting on Streamlit Community Cloud
- (+) Real-time updates via `st.rerun()` + WebSocket
- (-) Limited customization
- (-) Session-based state model

**Mitigation:** FastAPI backend provides flexibility; can migrate to React later if needed

### 11. Service Runtime Model
**Decision:** Long-running asyncio service with signal handling and health checks

**Trade-off:**
- (+) Proper graceful shutdown
- (+) Container orchestration compatibility
- (+) Health monitoring integration
- (-) More complex than simple script
- (-) Requires signal handling code

**Mitigation:** Well-tested shutdown sequence; health check endpoints for debugging

---

## Technology Selection Analysis

This section provides detailed analysis of key technology choices, comparing alternatives and explaining trade-offs.

### Dashboard: Streamlit vs Grafana vs Custom Solutions

#### The Fundamental Difference

**Grafana** excels at **infrastructure monitoring** (CPU, memory, network, service health)
**Streamlit** excels at **business logic monitoring** (trades, P&L, positions, custom analytics)

#### Comprehensive Comparison Matrix

| Criterion | Grafana | Streamlit | Custom React/Vue | Winner |
|-----------|---------|-----------|------------------|--------|
| **Setup Complexity** | Requires time-series DB (Prometheus/InfluxDB), exporters | Just SQLite + Python script | Frontend + Backend + hosting | **Streamlit** |
| **Infrastructure Metrics** | Excellent (CPU, memory, network) | Basic via psutil | Custom implementation | **Grafana** |
| **Trading Metrics** | Requires custom panels, complex queries | Native Python + pandas | Full control | **Streamlit** |
| **Custom Business Logic** | Limited (query language only) | Full Python power | Full JavaScript power | **Tie** |
| **Interactive Controls** | Read-only dashboards | Buttons, inputs, forms | Full interactivity | **Tie** |
| **Time-to-Dashboard** | Hours to days (DB setup, metrics design, PromQL) | Minutes to hours | Days to weeks | **Streamlit** |
| **Free Hosting** | Self-host only | Streamlit Community Cloud (free) | Vercel/Netlify (free tier) | **Streamlit** |
| **Alerting** | Built-in, sophisticated (Slack, email, webhooks) | Manual implementation | Manual implementation | **Grafana** |
| **Professional Polish** | Industry standard, beautiful | Good enough, modern | Depends on effort | **Grafana** |
| **Python Integration** | JSON config, PromQL/SQL | Native Python, direct imports | API calls | **Streamlit** |
| **Development Speed** | Medium (learn PromQL, panel config) | Fast (write Python) | Slow (two codebases) | **Streamlit** |
| **Maintenance** | Maintain DB, exporters, panels | Single Python script | Frontend + Backend | **Streamlit** |
| **Real-Time Updates** | Excellent (built-in refresh) | Good (st.rerun + WebSocket) | Excellent (WebSocket) | **Tie** |

#### Why Streamlit Wins for Trading Dashboard

**1. Trading-Specific Visualizations**

```python
# Streamlit: Natural Python with full pandas/numpy power
df_trades = pd.read_sql("SELECT * FROM trades", conn)
df_trades['cumulative_pnl'] = df_trades['pnl'].cumsum()
st.line_chart(df_trades[['timestamp', 'cumulative_pnl']])

# Calculate custom metrics directly
sharpe_ratio = (df_trades['pnl'].mean() / df_trades['pnl'].std()) * np.sqrt(252)
st.metric("Sharpe Ratio", f"{sharpe_ratio:.2f}")

# Grafana: Need to write PromQL or SQL queries
# histogram_quantile(0.95, rate(trade_pnl_bucket[5m]))
# More difficult for complex trading calculations
```

**2. Interactive Controls (Future-Proofing)**

```python
# Streamlit: Can add controls easily
col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Close All Positions"):
        api.post(f"{API_BASE}/admin/close_all")
        st.success("All positions closed")

with col2:
    if st.button("Pause Trading"):
        api.post(f"{API_BASE}/admin/pause")

with col3:
    if st.button("Resume Trading"):
        api.post(f"{API_BASE}/admin/resume")

# Grafana: Read-only, would need separate admin panel
```

**3. Direct Access to Trade Data and Models**

```python
# Streamlit: Reuse your trading bot's models directly
from vibe.common.models import Trade, Position

trades = [Trade(**row) for row in api.get("/api/trades")]
losing_trades = [t for t in trades if t.pnl < 0]

# Display with custom logic
st.dataframe(
    pd.DataFrame([t.dict() for t in losing_trades])
    .style.applymap(lambda x: 'background-color: red' if x < 0 else '')
)

# Grafana: Need to expose all metrics via Prometheus format
# More rigid structure
```

**4. Ad-Hoc Analysis**

```python
# Streamlit: Quick explorations
symbol = st.selectbox("Symbol", ["AAPL", "MSFT", "AMZN", "TSLA", "GOOGL"])
date_range = st.date_input("Date Range", [])

# Custom query with dynamic filters
df = pd.read_sql(f"""
    SELECT * FROM trades
    WHERE symbol = ? AND date BETWEEN ? AND ?
""", conn, params=[symbol, date_range[0], date_range[1]])

# Instant visualization
st.plotly_chart(px.histogram(df, x='pnl', nbins=50))

# Grafana: More rigid, requires pre-configured panels
```

#### When to Use Each Tool

| Use Case | Best Tool | Reason |
|----------|-----------|--------|
| System health monitoring | Grafana | Built for infrastructure metrics |
| Trading P&L dashboard | Streamlit | Trading-specific logic |
| Alerting (CPU/memory/errors) | Grafana | Sophisticated alerting built-in |
| Interactive trade management | Streamlit | Python controls, direct API access |
| Multi-tenant monitoring | Grafana | Enterprise features |
| Rapid prototyping | Streamlit | Fast iteration cycle |
| Professional ops dashboard | Grafana | Industry standard |
| Custom business metrics | Streamlit | Full Python flexibility |

#### The Hybrid Approach (Future Enhancement)

For production scale, consider using **BOTH**:

```
┌─────────────────────────────────────────────────────────┐
│              Production Monitoring Stack                 │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Grafana (Infrastructure Layer)                          │
│  ├── CPU/Memory/Disk utilization                         │
│  ├── WebSocket connection status                         │
│  ├── API response times (p50, p95, p99)                  │
│  ├── Error rates and error types                         │
│  ├── Database query performance                          │
│  └── Alerts → PagerDuty/Slack                            │
│                                                          │
│  Streamlit (Business Layer)                              │
│  ├── Trade P&L table and charts                          │
│  ├── Open positions viewer                               │
│  ├── Strategy performance metrics                        │
│  ├── Risk metrics (exposure, drawdown)                   │
│  ├── Order history and fills                             │
│  └── Manual controls (pause, resume, close)              │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

**For MVP:** Streamlit alone is sufficient - faster to build, easier to maintain, zero cost.

---

### Mock Exchange: Custom vs CCXT vs Hummingbot vs LEAN

#### The Core Question: Framework vs Component

The key insight is that **CCXT, Hummingbot, and LEAN are complete frameworks**, not components. They expect to control your application architecture.

#### Option 1: CCXT (ccxt.mock / ccxt.pro sandbox)

**What it is:**
- Cryptocurrency exchange connectivity library
- Unified API for 100+ crypto exchanges
- "Mock" mode connects to exchange sandbox/testnet APIs

**Detailed Analysis:**

```python
# CCXT Usage
import ccxt

exchange = ccxt.binance({
    'apiKey': 'YOUR_API_KEY',
    'secret': 'YOUR_SECRET',
    'enableRateLimit': True,
    'options': {'defaultType': 'future'},  # or 'spot'
    'sandbox': True  # Connects to Binance testnet
})

# Place order
order = exchange.create_market_buy_order('BTC/USDT', 0.01)
```

**Pros:**
- ✅ Battle-tested by thousands of crypto traders
- ✅ Unified API across 100+ exchanges
- ✅ Handles exchange-specific quirks
- ✅ Active development and community

**Cons:**
- ❌ **Requires internet** (sandbox mode connects to real exchange testnets)
- ❌ **Exchange uptime dependency** (testnet may be down)
- ❌ **Heavyweight** (50,000+ lines for 100+ exchanges we don't need)
- ❌ **Crypto-focused** (less realistic for US stock trading simulation)
- ❌ **Exchange-specific rate limits** even in sandbox
- ❌ **Authentication required** for most sandbox APIs
- ❌ **Not fully local simulation** (network latency, testnet behavior)

**Size:** 50,000+ lines of code

**Our need:** ~200 lines for basic local stock order simulation

**Verdict:** Overkill for MVP. Consider for production crypto trading.

#### Option 2: Hummingbot

**What it is:**
- Complete crypto market-making and arbitrage bot framework
- Opinionated architecture with built-in strategies, connectors, UI

**Detailed Analysis:**

```python
# Hummingbot requires adopting their entire structure
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase

class MyStrategy(ScriptStrategyBase):
    """Must inherit from Hummingbot base classes"""

    markets = {"binance": {"BTC/USDT"}}

    def on_tick(self):
        # Must structure code within their lifecycle
        pass
```

**Pros:**
- ✅ Full-featured framework with UI
- ✅ Many built-in strategies (market making, arbitrage)
- ✅ Exchange connectors included

**Cons:**
- ❌ **Framework adoption required** (can't use our asyncio design)
- ❌ **Crypto market-making focus** (not suitable for day trading stocks)
- ❌ **Complex architecture** (strategies, connectors, executors, UI)
- ❌ **Heavy configuration** (YAML configs, database, networking)
- ❌ **Learning curve** (must learn Hummingbot's patterns)
- ❌ **Inflexible** (fighting framework opinions about structure)

**Size:** 100,000+ lines, complete bot framework with UI

**Our need:** Just a mock order executor

**Verdict:** Wrong tool for the job. Built for crypto market making, not stock day trading.

#### Option 3: LEAN (QuantConnect)

**What it is:**
- Institutional-grade algorithmic trading engine
- Powers QuantConnect cloud platform
- Supports stocks, options, forex, crypto, futures
- C# core with Python bindings

**Detailed Analysis:**

```python
# LEAN requires adopting their QCAlgorithm pattern
from AlgorithmImports import *

class MyAlgorithm(QCAlgorithm):
    """Must inherit from QCAlgorithm"""

    def Initialize(self):
        # Must use LEAN's initialization pattern
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2023, 12, 31)
        self.SetCash(10000)
        self.AddEquity("SPY", Resolution.Minute)

    def OnData(self, data):
        # Must structure logic within their callbacks
        if not self.Portfolio.Invested:
            self.SetHoldings("SPY", 1)
```

**Pros:**
- ✅ **Institutional quality** (used by hedge funds)
- ✅ **Multi-asset support** (stocks, options, forex, crypto, futures)
- ✅ **Excellent backtesting** engine
- ✅ **Realistic fill models** and slippage
- ✅ **Well-maintained** by QuantConnect
- ✅ **Great documentation** and examples
- ✅ **Active community**

**Cons:**
- ❌ **Full framework adoption** (not a component library)
- ❌ **C# core with Python bindings** (potential friction, debugging complexity)
- ❌ **Steep learning curve** (extensive API, many concepts)
- ❌ **Architecture lock-in** (QCAlgorithm pattern, event callbacks)
- ❌ **Our design already complete** (asyncio, FastAPI, our patterns)
- ❌ **Would require full refactor** to fit LEAN's architecture
- ❌ **Overkill for MVP** (LEAN is for serious production/institutional use)

**Size:** 250,000+ lines (C# + Python), complete trading platform

**When to use LEAN:**
- Building a new platform from scratch
- Need institutional-grade backtesting
- Want to use QuantConnect cloud
- Multi-asset algorithmic trading at scale

**Verdict:** Excellent framework, but would require rebuilding entire project around LEAN's architecture. Not suitable for MVP where we've already designed our system.

#### Option 4: Custom MockExchange (Selected)

**What it is:**
- Lightweight custom implementation (~200 lines)
- Implements our `ExecutionEngine` interface
- Based on proven patterns from Backtrader

**Implementation:**

```python
# vibe/trading-bot/exchange/mock_exchange.py
from vibe.common.execution.base import ExecutionEngine
from vibe.common.models import Order, OrderResponse, Position

class MockExchange(ExecutionEngine):
    """
    Lightweight paper trading exchange.

    Features:
    - Realistic slippage model
    - Fill delay simulation (100-500ms)
    - Partial fill support
    - Order types: Market, Limit, Stop, StopLimit
    - $10,000 initial capital
    """

    def __init__(self, config: MockExchangeConfig):
        self.initial_capital = config.initial_capital  # $10,000
        self.cash = self.initial_capital
        self.positions: Dict[str, Position] = {}

        # Slippage model (based on Backtrader patterns)
        self.slippage_model = SlippageModel(
            base_pct=0.05,         # 0.05% base slippage
            volatility_factor=0.5,  # Increase slippage in volatile conditions
            size_factor=0.0001      # Increase slippage for large orders
        )

        # Fill simulation
        self.fill_delay_ms = (100, 500)  # Random delay between 100-500ms
        self.partial_fill_prob = 0.1     # 10% chance of partial fill

    async def submit_order(self, order: Order) -> OrderResponse:
        """Submit order with realistic simulation."""

        # 1. Simulate network/processing delay
        await asyncio.sleep(random.uniform(*self.fill_delay_ms) / 1000)

        # 2. Calculate slippage
        slippage = self.slippage_model.calculate(
            order=order,
            current_price=self._get_current_price(order.symbol),
            volatility=self._get_volatility(order.symbol)
        )

        # 3. Determine fill quantity (partial fills)
        if random.random() < self.partial_fill_prob:
            filled_qty = order.quantity * random.uniform(0.3, 0.9)
        else:
            filled_qty = order.quantity

        # 4. Execute fill
        fill_price = self._apply_slippage(order.price, slippage, order.side)
        self._update_position(order.symbol, filled_qty, fill_price, order.side)

        return OrderResponse(
            order_id=order.id,
            status=OrderStatus.FILLED if filled_qty == order.quantity else OrderStatus.PARTIAL,
            filled_qty=filled_qty,
            avg_price=fill_price,
            remaining_qty=order.quantity - filled_qty
        )
```

**Pros:**
- ✅ **Lightweight** (~200 lines vs 50,000+)
- ✅ **Fits our architecture** (implements our ExecutionEngine interface)
- ✅ **Full control** over simulation behavior
- ✅ **No dependencies** (pure Python + our models)
- ✅ **Fully local** (no internet, no external services)
- ✅ **Fast iteration** (modify behavior instantly)
- ✅ **Easy to debug** (can read/understand entire implementation)
- ✅ **Asyncio native** (matches our event loop design)
- ✅ **Testable** (unit test slippage, fills, edge cases)

**Cons:**
- ❌ **Implementation effort** (~1-2 days)
- ❌ **Need to validate accuracy** (compare with real broker fills)
- ❌ **Less battle-tested** than LEAN or Backtrader
- ❌ **No exchange-specific quirks** simulated

**Mitigation:**
- Validate slippage model against real broker data
- Unit test all edge cases (partial fills, rejections)
- Reference Backtrader's broker simulation patterns
- Plan to replace with real broker when ready for live trading

**Verdict:** Best choice for MVP. Simple, focused, fully under our control.

#### Migration Path for Production

```python
# MVP: Mock Exchange
from vibe.trading_bot.exchange.mock_exchange import MockExchange
exchange = MockExchange(initial_capital=10000)

# Production Stocks: Alpaca
from vibe.trading_bot.exchange.alpaca_exchange import AlpacaExchange
exchange = AlpacaExchange(
    api_key=config.alpaca_api_key,
    secret_key=config.alpaca_secret_key,
    paper=False  # Live trading
)

# Production Crypto: CCXT
from vibe.trading_bot.exchange.ccxt_exchange import CCXTExchange
exchange = CCXTExchange(
    exchange_id='binance',
    api_key=config.binance_api_key,
    secret_key=config.binance_secret_key
)

# All implement the same ExecutionEngine interface!
# Strategy code doesn't change
```

#### Decision Matrix Summary

```
Question: Should we use library X for mock exchange?

├─ Is it a complete framework that controls architecture?
│  ├─ YES (Hummingbot, LEAN)
│  └─ → Don't use for MVP (framework lock-in)
│
├─ Does it require internet/external services?
│  ├─ YES (CCXT sandbox, LEAN cloud)
│  └─ → Not ideal for local development
│
├─ Does it add >10,000 lines of dependencies?
│  ├─ YES (All three: CCXT, Hummingbot, LEAN)
│  └─ → Too heavy for simple mock trading
│
├─ Can we build what we need in <500 lines?
│  ├─ YES (Basic order simulation)
│  └─ → Build custom (full control, zero dependencies)
│
└─ Is it for our production asset class?
   ├─ YES (CCXT for crypto, Alpaca for stocks)
   └─ → Use in production, not for MVP mock
```

#### Recommendation Summary

| Stage | Recommended Solution | Reason |
|-------|---------------------|--------|
| **MVP Development** | Custom MockExchange | Simple, fast, full control, zero cost |
| **Production Stocks** | Alpaca SDK or Interactive Brokers | Battle-tested, regulatory compliance |
| **Production Crypto** | CCXT | Industry standard, 100+ exchanges |
| **Institutional Backtesting** | LEAN | Gold standard, multi-asset |
| **Testing/Staging** | Keep custom MockExchange | Fast, deterministic, no API costs |

---

## Risks and Considerations (Updated)

### 7. Shared Component Drift
**Risk:** Live and backtest implementations diverge over time

**Mitigations:**
- All shared logic in `vibe/common/`
- Integration tests run same strategy in both modes
- Code review checks for shared vs specific placement

### 8. Dashboard Performance
**Risk:** Streamlit may struggle with high-frequency updates

**Mitigations:**
- Rate-limit UI updates (5 second refresh)
- Use WebSocket for critical real-time updates
- Paginate large datasets
- Consider Dash if performance issues persist

### 9. Graceful Shutdown Failures
**Risk:** Process killed before completing shutdown

**Mitigations:**
- Docker stop timeout set to 30s (default 10s)
- Critical state saved frequently (every 5 min)
- Idempotent startup (resume from saved state)

---

## References (Updated)

### Trading Frameworks & Libraries
- [Backtrader Framework](https://www.backtrader.com/) - Slippage model patterns, broker simulation
- [CCXT Library](https://github.com/ccxt/ccxt) - Cryptocurrency exchange connectivity (100+ exchanges)
- [Hummingbot](https://github.com/hummingbot/hummingbot) - Crypto market-making bot framework
- [LEAN (QuantConnect)](https://github.com/QuantConnect/Lean) - Institutional algorithmic trading engine
- [VectorBT](https://github.com/polakowo/vectorbt) - High-performance backtesting library
- [Zipline](https://github.com/quantopian/zipline) - Pythonic algorithmic trading (legacy)

### Dashboard & Monitoring
- [Streamlit Documentation](https://docs.streamlit.io/) - Python dashboard framework
- [Grafana](https://grafana.com/docs/) - Infrastructure monitoring and observability
- [Streamlit Trading Dashboard Example](https://jaydeep4mgcet.medium.com/algo-trading-dashboard-using-python-and-streamlit-live-index-prices-current-positions-and-payoff-f44173a5b6d7)
- [Real-Time Forex Dashboard with Streamlit](https://medium.com/data-science-collective/building-a-real-time-forex-dashboard-with-streamlit-and-websocket-56a14a985f42)

### Infrastructure & Deployment
- [Graceful Shutdown in Docker](https://lemanchet.fr/articles/gracefully-stop-python-docker-container.html) - Python signal handling
- [FastAPI Health Checks](https://fastapi.tiangolo.com/) - Kubernetes-style readiness/liveness probes

### Comparisons & Analysis
- [Comparing Backtesting Frameworks](https://medium.com/@trading.dude/battle-tested-backtesters-comparing-vectorbt-zipline-and-backtrader-for-financial-strategy-dee33d33a9e0) - VectorBT vs Zipline vs Backtrader
