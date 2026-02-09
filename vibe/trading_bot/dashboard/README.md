# Trading Bot Dashboard

A real-time monitoring dashboard for the trading bot with REST API, WebSocket support for live updates, and an interactive Streamlit UI.

## Components

### 1. FastAPI REST Endpoints (`api.py`)

RESTful API for dashboard data access with the following endpoints:

#### Account & Positions

- **GET `/api/account`** - Account summary
  - Returns: cash balance, equity, buying power, portfolio value, trade statistics
  - Response: `AccountResponse`

- **GET `/api/positions`** - Open positions
  - Returns: list of open positions with unrealized P&L
  - Response: `List[PositionResponse]`

- **GET `/api/trades`** - Trade history with pagination
  - Parameters: `limit` (1-1000), `offset`, `symbol`, `status`
  - Returns: List of trades matching filters
  - Response: `List[TradeResponse]`

#### Performance Metrics

- **GET `/api/metrics/performance`** - Performance statistics
  - Parameters: `period` (daily, weekly, monthly, all)
  - Returns: win rate, Sharpe ratio, max drawdown, profit factor, avg trade duration
  - Response: `PerformanceMetrics`

#### Health Status

- **GET `/api/health`** - System health status
  - Returns: system status, uptime, error count, database health, WebSocket status
  - Response: `HealthStatus`

### 2. WebSocket Server (`websocket_server.py`)

Real-time update mechanism using WebSocket protocol.

#### ConnectionManager

Manages WebSocket connections and broadcasts updates:

```python
manager = ConnectionManager()

# Broadcast different update types
await manager.broadcast_trade_update(trade_data)
await manager.broadcast_position_update(position_data)
await manager.broadcast_metrics_update(metrics_data)
await manager.broadcast_account_update(account_data)
await manager.broadcast_health_update(health_data)
```

All messages have the structure:
```json
{
  "type": "trade_update|position_update|metrics_update|account_update|health_update",
  "timestamp": "2024-01-15T10:30:00",
  "data": { /* specific data */ }
}
```

#### WebSocket Endpoint

Connect to `ws://host:port/ws/updates` for real-time updates.

### 3. Chart Generation (`charts.py`)

Interactive Plotly charts for visualizing trading performance:

- **create_pnl_chart()** - Cumulative P&L over time
- **create_trade_distribution_chart()** - Trade count by symbol
- **create_win_rate_chart()** - Pie chart of winning/losing/breakeven trades
- **create_drawdown_chart()** - Running drawdown over time
- **create_pnl_by_symbol_chart()** - Total P&L aggregated by symbol
- **create_monthly_performance_chart()** - Monthly P&L summary

### 4. Streamlit Dashboard (`app.py`)

Interactive web UI for monitoring and analysis:

#### Sections

1. **Account Summary** - Cash, equity, daily P&L, total trades
2. **Performance Metrics** - Win rate, Sharpe ratio, max drawdown, avg trade duration
3. **System Health** - Status indicators, uptime, error count, connectivity
4. **Open Positions** - Table with real-time unrealized P&L
5. **Trade History** - Paginated trade table with P&L highlighting
6. **Performance Charts** - Interactive Plotly visualizations

#### Features

- Auto-refresh with configurable interval (1-60 seconds)
- Manual refresh button
- Responsive design
- Color-coded metrics (green for profit, red for loss)
- Customizable API URL via secrets

## Installation

Add to requirements.txt:
```
fastapi>=0.104
uvicorn>=0.24
websockets>=11.0
streamlit>=1.28
plotly>=5.0
```

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Running the API Server

```python
from vibe.trading_bot.dashboard.api import dashboard_app
from vibe.trading_bot.storage.trade_store import TradeStore

# Initialize with trade store
trade_store = TradeStore("./data/trades.db")
app = create_dashboard_app(trade_store=trade_store)

# Run with uvicorn
# uvicorn vibe.trading_bot.dashboard.api:dashboard_app --host 0.0.0.0 --port 8000
```

### Running the Streamlit Dashboard

```bash
streamlit run vibe/trading_bot/dashboard/app.py
```

Configuration via Streamlit secrets (`.streamlit/secrets.toml`):
```toml
api_url = "http://localhost:8000"
```

## Integration with Trading Bot

### Registering WebSocket Broadcaster

In your trading bot's order manager or trade executor:

```python
from vibe.trading_bot.dashboard.websocket_server import manager

# When trade executes
await manager.broadcast_trade_update({
    "symbol": trade.symbol,
    "side": trade.side,
    "pnl": trade.pnl,
    "status": trade.status,
})

# When position updates
await manager.broadcast_position_update({
    "symbol": position.symbol,
    "quantity": position.quantity,
    "unrealized_pnl": position.unrealized_pnl,
})
```

## Testing

Comprehensive test coverage with 97 tests:

```bash
# Run all dashboard tests
pytest vibe/tests/trading_bot/dashboard/ -v

# API endpoint tests (34 tests)
pytest vibe/tests/trading_bot/dashboard/test_api.py -v

# WebSocket server tests (29 tests)
pytest vibe/tests/trading_bot/dashboard/test_websocket.py -v

# Chart generator tests (34 tests)
pytest vibe/tests/trading_bot/dashboard/test_charts.py -v
```

Test coverage includes:
- All API endpoints with various data scenarios
- WebSocket connection management and broadcasting
- Chart generation with edge cases
- Error handling and validation
- Empty database scenarios
- Concurrent operations

## Architecture Decisions

### 1. REST API over WebSocket for Primary Queries

**Why**: HTTP is simpler for dashboard initialization and doesn't require persistent connections.

**Trade-off**: Slight latency on page load vs. reduced complexity.

### 2. WebSocket for Real-Time Updates

**Why**: Efficient for pushing updates to all connected clients simultaneously.

**Trade-off**: Requires client to maintain connection.

### 3. Plotly for Charts

**Why**: Streamlit integrates well, interactive (zoom, pan, hover), many chart types.

**Trade-off**: Larger bundle size vs. functionality.

### 4. Streamlit for UI

**Why**: Rapid development, live reloading, built-in widgets, minimal code.

**Trade-off**: Less customizable than React but extremely fast to build.

## Performance Considerations

### API Response Times

- `/api/account`: ~50ms (aggregates all trades)
- `/api/positions`: ~10ms (filters open trades)
- `/api/trades`: ~5ms with pagination
- `/api/metrics/performance`: ~30ms (calculates statistics)

### Optimization Tips

1. Set appropriate `limit` parameters in paginated endpoints
2. Use `symbol` and `status` filters to reduce data
3. Consider caching metrics for less frequent updates
4. Use WebSocket for real-time updates vs. polling

### Database Queries

All queries use indexed columns:
- `symbol`, `status`, `strategy`, `entry_time`
- Composite indexes on `(symbol, status)` and `(status, entry_time)`

## Deployment

### Local Development

```bash
# Terminal 1: Start API server
uvicorn vibe.trading_bot.dashboard.api:dashboard_app --reload --port 8000

# Terminal 2: Start Streamlit dashboard
streamlit run vibe/trading_bot/dashboard/app.py --server.port 8501
```

### Production (Docker)

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# API server
EXPOSE 8000
CMD ["uvicorn", "vibe.trading_bot.dashboard.api:dashboard_app", "--host", "0.0.0.0", "--port", "8000"]
```

### Streamlit Cloud Deployment

1. Push code to GitHub
2. Go to [streamlit.io/cloud](https://streamlit.io/cloud)
3. Deploy from repo
4. Add secrets in dashboard:
   ```
   api_url = "https://your-api-domain.com"
   ```

## API Response Examples

### GET /api/account

```json
{
  "cash": 9500.0,
  "equity": 14500.0,
  "buying_power": 29000.0,
  "portfolio_value": 14500.0,
  "total_trades": 42,
  "winning_trades": 28,
  "losing_trades": 14,
  "win_rate": 66.67,
  "total_pnl": 4500.0
}
```

### GET /api/trades

```json
[
  {
    "trade_id": 123,
    "symbol": "AAPL",
    "side": "buy",
    "quantity": 100,
    "entry_price": 150.0,
    "exit_price": 155.0,
    "entry_time": "2024-01-15T09:30:00",
    "exit_time": "2024-01-15T10:15:00",
    "pnl": 500.0,
    "pnl_pct": 3.33,
    "status": "closed",
    "strategy": "orb"
  }
]
```

### GET /api/metrics/performance

```json
{
  "win_rate": 66.67,
  "sharpe_ratio": 1.5,
  "max_drawdown": 2.5,
  "avg_trade_duration": 45.2,
  "profit_factor": 2.1,
  "total_trades": 42
}
```

## Troubleshooting

### Dashboard shows "API Error"

1. Check API server is running: `http://localhost:8000/api/health`
2. Verify API URL in Streamlit secrets matches running server
3. Check CORS settings if running on different host

### WebSocket connection fails

1. Ensure WebSocket endpoint is enabled in API (`/ws/updates`)
2. Check firewall allows WebSocket connections
3. Verify proxy/load balancer supports WebSocket upgrade

### Charts not loading

1. Ensure trades have valid `entry_time` and `exit_time` fields
2. Check `pnl` values are numeric
3. Verify date parsing (ISO format required)

### Performance Issues

1. Reduce `limit` parameter in API calls
2. Add filters (`symbol`, `status`) to narrow data
3. Check database size and consider archiving old trades
4. Enable pagination for large result sets

## Future Enhancements

1. Real-time P&L chart updates via WebSocket
2. Trade entry/exit signals in live time
3. Risk metrics (VaR, expected shortfall)
4. Equity curve with drawdown visualization
5. Trade analysis by time of day, day of week
6. Heatmaps of symbol performance
7. Trade journal with notes
8. Export to CSV/PDF reports
9. Mobile-responsive improvements
10. Dark/light theme toggle

## License

Part of Trading Bot MVP Project
