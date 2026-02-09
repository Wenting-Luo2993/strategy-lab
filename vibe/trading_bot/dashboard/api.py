"""FastAPI application for dashboard REST endpoints."""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from vibe.trading_bot.storage.trade_store import TradeStore
from vibe.trading_bot.storage.metrics_store import MetricsStore
from vibe.common.models import Trade, AccountState, Position


class TradeResponse(BaseModel):
    """Response model for trades."""

    trade_id: Optional[int] = None
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: Optional[float] = None
    entry_time: str
    exit_time: Optional[str] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    status: str
    strategy: Optional[str] = None


class PositionResponse(BaseModel):
    """Response model for positions."""

    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    entry_time: str


class AccountResponse(BaseModel):
    """Response model for account summary."""

    cash: float = Field(..., description="Available cash balance")
    equity: float = Field(..., description="Total account equity")
    buying_power: float = Field(..., description="Buying power")
    portfolio_value: float = Field(..., description="Total portfolio value")
    total_trades: int = Field(..., description="Total number of trades")
    winning_trades: int = Field(..., description="Number of winning trades")
    losing_trades: int = Field(..., description="Number of losing trades")
    win_rate: float = Field(..., description="Win rate percentage")
    total_pnl: float = Field(..., description="Total realized P&L")


class PerformanceMetrics(BaseModel):
    """Response model for performance metrics."""

    win_rate: float = Field(..., description="Win rate percentage")
    sharpe_ratio: float = Field(..., description="Sharpe ratio")
    max_drawdown: float = Field(..., description="Maximum drawdown percentage")
    avg_trade_duration: float = Field(..., description="Average trade duration in minutes")
    profit_factor: float = Field(..., description="Profit factor (gross profit / gross loss)")
    total_trades: int = Field(..., description="Total number of trades")


class HealthStatus(BaseModel):
    """Response model for health status."""

    status: str = Field(..., description="Overall status: healthy, degraded, unhealthy")
    uptime_seconds: float = Field(..., description="System uptime in seconds")
    errors_last_hour: int = Field(..., description="Number of errors in last hour")
    websocket_connected: bool = Field(..., description="WebSocket connection status")
    database_healthy: bool = Field(..., description="Database connectivity status")
    last_trade_time: Optional[str] = None
    total_active_connections: int = Field(default=0, description="WebSocket connections")


def create_dashboard_app(
    trade_store: Optional[TradeStore] = None,
    metrics_store: Optional[MetricsStore] = None,
) -> FastAPI:
    """Create FastAPI application for dashboard.

    Args:
        trade_store: TradeStore instance (uses default if None)
        metrics_store: MetricsStore instance (uses default if None)

    Returns:
        FastAPI application
    """
    # Use defaults if not provided
    if trade_store is None:
        trade_store = TradeStore()
    if metrics_store is None:
        metrics_store = MetricsStore()

    app = FastAPI(
        title="Trading Bot Dashboard API",
        description="Real-time dashboard for trading bot monitoring",
        version="1.0.0",
    )

    # Add CORS middleware for Streamlit access
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, restrict to specific domains
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store instances for dependency injection
    app.state.trade_store = trade_store
    app.state.metrics_store = metrics_store

    @app.get("/api/health", response_model=HealthStatus)
    async def get_health() -> Dict[str, Any]:
        """Get system health status.

        Returns:
            Health status information
        """
        try:
            # Get recent trades to check database health
            recent_trades = trade_store.get_trades(limit=1)
            database_healthy = True
            last_trade_time = None

            if recent_trades:
                last_trade = recent_trades[0]
                # Try exit_time first, fall back to entry_time
                last_trade_time = last_trade.get("exit_time") or last_trade.get("entry_time")

            return {
                "status": "healthy" if database_healthy else "unhealthy",
                "uptime_seconds": 0.0,  # Would be calculated from start time in production
                "errors_last_hour": 0,
                "websocket_connected": True,
                "database_healthy": database_healthy,
                "last_trade_time": last_trade_time,
                "total_active_connections": 0,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

    @app.get("/api/account", response_model=AccountResponse)
    async def get_account() -> Dict[str, Any]:
        """Get account summary.

        Returns:
            Account summary with capital, P&L, and trade statistics
        """
        try:
            # Get all trades to calculate account state
            all_trades = trade_store.get_trades(limit=10000)

            cash = 10000.0  # Initial capital
            total_pnl = 0.0
            winning_trades = 0
            losing_trades = 0

            for trade in all_trades:
                if trade["status"] == "closed" and trade.get("pnl") is not None:
                    pnl = trade["pnl"]
                    total_pnl += pnl
                    if pnl > 0:
                        winning_trades += 1
                    elif pnl < 0:
                        losing_trades += 1

            total_trades = len(all_trades)
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

            equity = cash + total_pnl
            buying_power = equity * 2  # Assume 2x leverage available

            return {
                "cash": cash,
                "equity": equity,
                "buying_power": buying_power,
                "portfolio_value": equity,
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": win_rate,
                "total_pnl": total_pnl,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get account: {str(e)}")

    @app.get("/api/positions", response_model=List[PositionResponse])
    async def get_positions() -> List[Dict[str, Any]]:
        """Get open positions.

        Returns:
            List of open positions with unrealized P&L
        """
        try:
            open_trades = trade_store.get_trades(status="open", limit=1000)
            positions = []

            for trade in open_trades:
                # In production, would fetch current price from market data
                current_price = trade["entry_price"] * 1.01  # Mock: assume 1% gain

                if trade["side"] == "buy":
                    unrealized_pnl = (current_price - trade["entry_price"]) * trade["quantity"]
                    unrealized_pnl_pct = (
                        (current_price - trade["entry_price"]) / trade["entry_price"] * 100
                    )
                else:  # sell
                    unrealized_pnl = (trade["entry_price"] - current_price) * trade["quantity"]
                    unrealized_pnl_pct = (
                        (trade["entry_price"] - current_price) / trade["entry_price"] * 100
                    )

                positions.append(
                    {
                        "symbol": trade["symbol"],
                        "quantity": trade["quantity"],
                        "entry_price": trade["entry_price"],
                        "current_price": current_price,
                        "unrealized_pnl": unrealized_pnl,
                        "unrealized_pnl_pct": unrealized_pnl_pct,
                        "entry_time": trade["entry_time"],
                    }
                )

            return positions
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get positions: {str(e)}")

    @app.get("/api/trades", response_model=List[TradeResponse])
    async def get_trades(
        limit: int = Query(50, ge=1, le=1000, description="Number of trades to return"),
        offset: int = Query(0, ge=0, description="Offset for pagination"),
        symbol: Optional[str] = Query(None, description="Filter by symbol"),
        status: Optional[str] = Query(None, description="Filter by status"),
    ) -> List[Dict[str, Any]]:
        """Get trade history with optional filtering.

        Args:
            limit: Number of trades to return
            offset: Pagination offset
            symbol: Optional symbol filter
            status: Optional status filter (open, closed)

        Returns:
            List of trades matching filters
        """
        try:
            # Use get_trades with filters (pagination handled internally)
            trades = trade_store.get_trades(
                symbol=symbol,
                status=status,
                limit=limit,
                offset=offset,
            )

            result = []
            for trade in trades:
                result.append(
                    {
                        "trade_id": trade.get("id"),
                        "symbol": trade["symbol"],
                        "side": trade["side"],
                        "quantity": trade["quantity"],
                        "entry_price": trade["entry_price"],
                        "exit_price": trade.get("exit_price"),
                        "entry_time": trade["entry_time"],
                        "exit_time": trade.get("exit_time"),
                        "pnl": trade.get("pnl"),
                        "pnl_pct": trade.get("pnl_pct"),
                        "status": trade.get("status", "open"),
                        "strategy": trade.get("strategy"),
                    }
                )

            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get trades: {str(e)}")

    @app.get("/api/metrics/performance", response_model=PerformanceMetrics)
    async def get_performance_metrics(
        period: Optional[str] = Query("all", description="Time period: daily, weekly, monthly, all")
    ) -> Dict[str, Any]:
        """Get performance metrics.

        Args:
            period: Time period for metrics calculation

        Returns:
            Performance metrics including win rate, Sharpe ratio, etc.
        """
        try:
            closed_trades = trade_store.get_trades(status="closed", limit=10000)

            # Calculate metrics
            winning_trades = sum(1 for t in closed_trades if t.get("pnl", 0) > 0)
            losing_trades = sum(1 for t in closed_trades if t.get("pnl", 0) < 0)
            total_trades = len(closed_trades)

            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

            # Calculate profit factor
            gross_profit = sum(max(0, t.get("pnl", 0)) for t in closed_trades)
            gross_loss = abs(sum(min(0, t.get("pnl", 0)) for t in closed_trades))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

            # Calculate average trade duration (in minutes)
            total_duration = 0
            valid_trades = 0
            for trade in closed_trades:
                if trade.get("exit_time") and trade.get("entry_time"):
                    try:
                        entry = datetime.fromisoformat(trade["entry_time"])
                        exit_time = datetime.fromisoformat(trade["exit_time"])
                        duration = (exit_time - entry).total_seconds() / 60
                        total_duration += duration
                        valid_trades += 1
                    except (ValueError, TypeError):
                        pass

            avg_trade_duration = (
                total_duration / valid_trades if valid_trades > 0 else 0.0
            )

            # Mock Sharpe ratio and max drawdown (would need returns history in production)
            sharpe_ratio = 1.5  # Placeholder
            max_drawdown = 5.0  # Placeholder

            return {
                "win_rate": win_rate,
                "sharpe_ratio": sharpe_ratio,
                "max_drawdown": max_drawdown,
                "avg_trade_duration": avg_trade_duration,
                "profit_factor": profit_factor,
                "total_trades": total_trades,
            }
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to get performance metrics: {str(e)}"
            )

    return app


# Create default app instance
dashboard_app = create_dashboard_app()
