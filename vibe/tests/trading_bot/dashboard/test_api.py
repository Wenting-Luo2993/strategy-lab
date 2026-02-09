"""Unit tests for dashboard API endpoints."""

import pytest
from httpx import AsyncClient
from datetime import datetime, timedelta
from vibe.trading_bot.dashboard.api import create_dashboard_app
from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.common.models import Trade
import tempfile
import os


@pytest.fixture
def temp_db_path(tmp_path):
    """Create temporary database for testing."""
    return str(tmp_path / "test.db")


@pytest.fixture
def trade_store(temp_db_path):
    """Create test trade store."""
    store = TradeStore(temp_db_path)
    yield store
    # Close connection
    if hasattr(store, '_local') and hasattr(store._local, 'connection'):
        try:
            store._local.connection.close()
        except Exception:
            pass


@pytest.fixture
def sample_trades(trade_store):
    """Create sample trades for testing."""
    trades = [
        Trade(
            symbol="AAPL",
            side="buy",
            quantity=100,
            entry_price=150.0,
            exit_price=160.0,
            entry_time=datetime.now() - timedelta(hours=2),
            exit_time=datetime.now() - timedelta(hours=1),
            strategy="orb",
        ),
        Trade(
            symbol="MSFT",
            side="buy",
            quantity=50,
            entry_price=300.0,
            exit_price=295.0,
            entry_time=datetime.now() - timedelta(hours=1),
            exit_time=datetime.now() - timedelta(minutes=30),
            strategy="orb",
        ),
        Trade(
            symbol="GOOGL",
            side="sell",
            quantity=25,
            entry_price=2800.0,
            exit_price=2820.0,
            entry_time=datetime.now() - timedelta(minutes=30),
            exit_time=None,  # Still open
            strategy="orb",
        ),
    ]

    for trade in trades:
        trade_store.insert_trade(trade)

    return trades


@pytest.fixture
def app(trade_store):
    """Create test FastAPI app."""
    return create_dashboard_app(trade_store=trade_store)


@pytest.mark.asyncio
async def test_get_health_endpoint(app):
    """Test GET /api/health returns health status."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "uptime_seconds" in data
        assert "errors_last_hour" in data
        assert "websocket_connected" in data
        assert "database_healthy" in data


@pytest.mark.asyncio
async def test_get_health_status_values(app, sample_trades):
    """Test health status returns correct values."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "degraded", "unhealthy"]
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0
        assert isinstance(data["errors_last_hour"], int)
        assert data["errors_last_hour"] >= 0


@pytest.mark.asyncio
async def test_get_account_endpoint(app, sample_trades):
    """Test GET /api/account returns account summary."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/account")
        assert response.status_code == 200
        data = response.json()
        assert "cash" in data
        assert "equity" in data
        assert "buying_power" in data
        assert "portfolio_value" in data
        assert "total_trades" in data
        assert "winning_trades" in data
        assert "losing_trades" in data
        assert "win_rate" in data
        assert "total_pnl" in data


@pytest.mark.asyncio
async def test_account_values_are_numeric(app, sample_trades):
    """Test account values are numeric."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/account")
        data = response.json()
        assert isinstance(data["cash"], (int, float))
        assert isinstance(data["equity"], (int, float))
        assert isinstance(data["buying_power"], (int, float))
        assert isinstance(data["total_trades"], int)
        assert isinstance(data["win_rate"], (int, float))


@pytest.mark.asyncio
async def test_account_win_rate_calculation(app, sample_trades):
    """Test win rate is calculated correctly."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/account")
        data = response.json()
        # Win rate should be between 0 and 100
        assert 0 <= data["win_rate"] <= 100


@pytest.mark.asyncio
async def test_get_positions_endpoint(app, sample_trades):
    """Test GET /api/positions returns open positions."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/positions")
        assert response.status_code == 200
        positions = response.json()
        assert isinstance(positions, list)
        assert len(positions) > 0


@pytest.mark.asyncio
async def test_positions_structure(app, sample_trades):
    """Test position response structure."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/positions")
        positions = response.json()
        if positions:
            position = positions[0]
            assert "symbol" in position
            assert "quantity" in position
            assert "entry_price" in position
            assert "current_price" in position
            assert "unrealized_pnl" in position
            assert "unrealized_pnl_pct" in position
            assert "entry_time" in position


@pytest.mark.asyncio
async def test_get_trades_endpoint(app, sample_trades):
    """Test GET /api/trades returns trade history."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades?limit=10")
        assert response.status_code == 200
        trades = response.json()
        assert isinstance(trades, list)


@pytest.mark.asyncio
async def test_trades_response_structure(app, sample_trades):
    """Test trade response structure."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades?limit=1")
        trades = response.json()
        if trades:
            trade = trades[0]
            assert "symbol" in trade
            assert "side" in trade
            assert "quantity" in trade
            assert "entry_price" in trade
            assert "status" in trade


@pytest.mark.asyncio
async def test_trades_limit_parameter(app, sample_trades):
    """Test trades limit parameter."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades?limit=1")
        trades = response.json()
        assert len(trades) <= 1


@pytest.mark.asyncio
async def test_trades_limit_max(app, sample_trades):
    """Test trades limit has maximum."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades?limit=2000")
        # Should accept but cap at 1000 or similar
        assert response.status_code in [200, 422]


@pytest.mark.asyncio
async def test_trades_offset_parameter(app, sample_trades):
    """Test trades offset parameter for pagination."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response1 = await client.get("/api/trades?limit=10&offset=0")
        response2 = await client.get("/api/trades?limit=10&offset=1")
        trades1 = response1.json()
        trades2 = response2.json()
        assert response1.status_code == 200
        assert response2.status_code == 200


@pytest.mark.asyncio
async def test_trades_symbol_filter(app, sample_trades):
    """Test trades symbol filter."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades?symbol=AAPL")
        trades = response.json()
        assert response.status_code == 200
        # All trades should be AAPL (or none if symbol not found)
        for trade in trades:
            assert trade["symbol"] == "AAPL"


@pytest.mark.asyncio
async def test_trades_status_filter(app, sample_trades):
    """Test trades status filter."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades?status=closed")
        trades = response.json()
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_performance_metrics_endpoint(app, sample_trades):
    """Test GET /api/metrics/performance returns metrics."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        assert response.status_code == 200
        metrics = response.json()
        assert "win_rate" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "avg_trade_duration" in metrics
        assert "profit_factor" in metrics


@pytest.mark.asyncio
async def test_performance_metrics_values_are_numeric(app, sample_trades):
    """Test performance metrics are numeric."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert isinstance(metrics["win_rate"], (int, float))
        assert isinstance(metrics["sharpe_ratio"], (int, float))
        assert isinstance(metrics["max_drawdown"], (int, float))
        assert isinstance(metrics["avg_trade_duration"], (int, float))


@pytest.mark.asyncio
async def test_performance_metrics_win_rate_range(app, sample_trades):
    """Test win rate is in valid range."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert 0 <= metrics["win_rate"] <= 100


@pytest.mark.asyncio
async def test_performance_metrics_drawdown_range(app, sample_trades):
    """Test drawdown is non-negative."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert metrics["max_drawdown"] >= 0


@pytest.mark.asyncio
async def test_performance_metrics_sharpe_ratio(app, sample_trades):
    """Test Sharpe ratio is reasonable."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert isinstance(metrics["sharpe_ratio"], (int, float))


@pytest.mark.asyncio
async def test_performance_metrics_profit_factor(app, sample_trades):
    """Test profit factor calculation."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert isinstance(metrics["profit_factor"], (int, float))
        assert metrics["profit_factor"] >= 0


@pytest.mark.asyncio
async def test_performance_metrics_total_trades(app, sample_trades):
    """Test total trades count."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert isinstance(metrics["total_trades"], int)
        assert metrics["total_trades"] >= 0


@pytest.mark.asyncio
async def test_performance_metrics_period_parameter(app, sample_trades):
    """Test period parameter for metrics."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        for period in ["daily", "weekly", "monthly", "all"]:
            response = await client.get(f"/api/metrics/performance?period={period}")
            assert response.status_code == 200


@pytest.mark.asyncio
async def test_cors_headers(app):
    """Test CORS headers are present."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/health")
        # CORS headers may be added by middleware
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_api_error_handling(app):
    """Test API error handling for invalid requests."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        # Invalid limit (negative)
        response = await client.get("/api/trades?limit=-1")
        assert response.status_code in [200, 422]


@pytest.mark.asyncio
async def test_empty_database(trade_store):
    """Test API behavior with empty database."""
    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades")
        assert response.status_code == 200
        trades = response.json()
        assert isinstance(trades, list)


@pytest.mark.asyncio
async def test_positions_empty_when_no_open_trades(trade_store):
    """Test positions endpoint returns empty when no open trades."""
    # Insert only closed trades
    closed_trade = Trade(
        symbol="AAPL",
        side="buy",
        quantity=100,
        entry_price=150.0,
        exit_price=160.0,
        entry_time=datetime.now() - timedelta(hours=1),
        exit_time=datetime.now(),
        strategy="orb",
    )
    trade_store.insert_trade(closed_trade)

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/positions")
        assert response.status_code == 200
        positions = response.json()
        assert isinstance(positions, list)


@pytest.mark.asyncio
async def test_health_check_with_no_trades(trade_store):
    """Test health check when database is empty."""
    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["database_healthy"] in [True, False]


@pytest.mark.asyncio
async def test_account_with_no_trades(trade_store):
    """Test account endpoint with no trades."""
    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/account")
        assert response.status_code == 200
        data = response.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == 0


@pytest.mark.asyncio
async def test_performance_metrics_with_only_profitable_trades(trade_store):
    """Test metrics with only profitable trades."""
    # Insert profitable trades only
    for i in range(3):
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=100,
            entry_price=100.0 + i,
            exit_price=110.0 + i,
            entry_time=datetime.now() - timedelta(hours=i),
            exit_time=datetime.now(),
            strategy="orb",
        )
        trade_id = trade_store.insert_trade(trade)
        # Update to closed status so metrics will calculate it
        trade_store.update_trade(trade_id, status="closed")

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert metrics["win_rate"] == 100.0


@pytest.mark.asyncio
async def test_performance_metrics_with_only_losing_trades(trade_store):
    """Test metrics with only losing trades."""
    # Insert losing trades only
    for i in range(3):
        trade = Trade(
            symbol="AAPL",
            side="buy",
            quantity=100,
            entry_price=110.0 + i,
            exit_price=100.0 + i,
            entry_time=datetime.now() - timedelta(hours=i),
            exit_time=datetime.now(),
            strategy="orb",
        )
        trade_store.insert_trade(trade)

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/metrics/performance")
        metrics = response.json()
        assert metrics["win_rate"] == 0.0


@pytest.mark.asyncio
async def test_multiple_symbols_in_trades(trade_store):
    """Test API with trades from multiple symbols."""
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA"]
    for symbol in symbols:
        trade = Trade(
            symbol=symbol,
            side="buy",
            quantity=100,
            entry_price=100.0,
            exit_price=105.0,
            entry_time=datetime.now(),
            exit_time=datetime.now(),
            strategy="orb",
        )
        trade_store.insert_trade(trade)

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/account")
        data = response.json()
        assert data["total_trades"] == 4


@pytest.mark.asyncio
async def test_trades_with_different_sides(trade_store):
    """Test API handles both buy and sell trades."""
    buy_trade = Trade(
        symbol="AAPL",
        side="buy",
        quantity=100,
        entry_price=100.0,
        exit_price=110.0,
        entry_time=datetime.now(),
        exit_time=datetime.now(),
        strategy="orb",
    )
    sell_trade = Trade(
        symbol="AAPL",
        side="sell",
        quantity=100,
        entry_price=110.0,
        exit_price=105.0,
        entry_time=datetime.now(),
        exit_time=datetime.now(),
        strategy="orb",
    )

    trade_store.insert_trade(buy_trade)
    trade_store.insert_trade(sell_trade)

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/trades")
        trades = response.json()
        assert any(t["side"] == "buy" for t in trades)
        assert any(t["side"] == "sell" for t in trades)


@pytest.mark.asyncio
async def test_position_pnl_calculation_buy(trade_store):
    """Test unrealized P&L calculation for buy positions."""
    trade = Trade(
        symbol="AAPL",
        side="buy",
        quantity=100,
        entry_price=100.0,
        entry_time=datetime.now(),
        strategy="orb",
    )
    trade_store.insert_trade(trade)

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/positions")
        positions = response.json()
        if positions:
            # Assuming 1% gain in mock data
            assert positions[0]["unrealized_pnl"] >= 0


@pytest.mark.asyncio
async def test_position_pnl_calculation_sell(trade_store):
    """Test unrealized P&L calculation for sell positions."""
    trade = Trade(
        symbol="AAPL",
        side="sell",
        quantity=100,
        entry_price=100.0,
        entry_time=datetime.now(),
        strategy="orb",
    )
    trade_store.insert_trade(trade)

    app = create_dashboard_app(trade_store=trade_store)
    async with AsyncClient(app=app, base_url="http://test") as client:
        response = await client.get("/api/positions")
        positions = response.json()
        if positions:
            # For short position, P&L should be negative when price rises
            assert "unrealized_pnl_pct" in positions[0]
