"""Health check API endpoints for trading bot."""

import asyncio
import threading
from typing import Optional, Dict, Any
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from vibe.trading_bot.utils.logger import get_logger


logger = get_logger(__name__)


# Global health state
class HealthState(BaseModel):
    """Health state model."""

    is_alive: bool = True
    websocket_connected: bool = False
    recent_heartbeat: bool = False
    last_heartbeat: Optional[str] = None
    last_error: Optional[str] = None
    uptime_seconds: float = 0.0
    metrics: Dict[str, Any] = {}


# Thread-safe health state management
_health_state = HealthState()
_start_time = datetime.utcnow()
_health_lock = threading.Lock()  # Protect health state from race conditions


def set_health_state(
    is_alive: Optional[bool] = None,
    websocket_connected: Optional[bool] = None,
    recent_heartbeat: Optional[bool] = None,
    last_error: Optional[str] = None,
) -> None:
    """Update global health state (thread-safe).

    Args:
        is_alive: Whether process is alive
        websocket_connected: Whether websocket is connected
        recent_heartbeat: Whether recent heartbeat received
        last_error: Last error message
    """
    global _health_state

    with _health_lock:
        if is_alive is not None:
            _health_state.is_alive = is_alive
        if websocket_connected is not None:
            _health_state.websocket_connected = websocket_connected
        if recent_heartbeat is not None:
            _health_state.recent_heartbeat = recent_heartbeat
        if last_error is not None:
            _health_state.last_error = last_error
        if recent_heartbeat:
            _health_state.last_heartbeat = datetime.utcnow().isoformat()


def get_health_state() -> HealthState:
    """Get current health state (thread-safe).

    Returns:
        HealthState object (copy to prevent external modification)
    """
    global _health_state, _start_time

    with _health_lock:
        # Create a copy to prevent external modification
        state = _health_state.model_copy()
        state.uptime_seconds = (datetime.utcnow() - _start_time).total_seconds()
        return state


def add_metric(name: str, value: Any) -> None:
    """Add a metric to health state (thread-safe).

    Args:
        name: Metric name
        value: Metric value
    """
    global _health_state

    with _health_lock:
        _health_state.metrics[name] = value


# Response models
class LiveResponse(BaseModel):
    """Liveness probe response."""

    status: str
    timestamp: str


class ReadyResponse(BaseModel):
    """Readiness probe response."""

    status: str
    timestamp: str
    checks: Dict[str, bool]


class MetricsResponse(BaseModel):
    """Metrics response."""

    status: str
    timestamp: str
    uptime_seconds: float
    metrics: Dict[str, Any]


# Create FastAPI app
def create_health_app() -> FastAPI:
    """Create FastAPI app for health checks.

    Returns:
        FastAPI application instance
    """
    app = FastAPI(title="Trading Bot Health Check")

    @app.get("/api/health", response_model=LiveResponse)
    async def api_health() -> LiveResponse:
        """Simple health check for Docker healthcheck.

        Returns:
            200 if alive, 503 if dead
        """
        state = get_health_state()

        if not state.is_alive:
            raise HTTPException(status_code=503, detail="Process not alive")

        return LiveResponse(
            status="alive",
            timestamp=datetime.utcnow().isoformat(),
        )

    @app.get("/health/live", response_model=LiveResponse)
    async def liveness_probe() -> LiveResponse:
        """Liveness probe - returns 200 if process is alive.

        Returns:
            200 if alive, 503 if dead
        """
        state = get_health_state()

        if not state.is_alive:
            raise HTTPException(status_code=503, detail="Process not alive")

        return LiveResponse(
            status="alive",
            timestamp=datetime.utcnow().isoformat(),
        )

    @app.get("/health/ready", response_model=ReadyResponse)
    async def readiness_probe() -> ReadyResponse:
        """Readiness probe - returns 200 if ready to trade.

        Returns:
            200 if ready, 503 if not ready
        """
        state = get_health_state()

        checks = {
            "alive": state.is_alive,
            "websocket_connected": state.websocket_connected,
            "recent_heartbeat": state.recent_heartbeat,
        }

        # Consider ready if alive and has recent heartbeat
        is_ready = all([
            state.is_alive,
            state.websocket_connected or state.recent_heartbeat,
        ])

        if not is_ready:
            raise HTTPException(
                status_code=503,
                detail="Not ready to trade",
            )

        return ReadyResponse(
            status="ready",
            timestamp=datetime.utcnow().isoformat(),
            checks=checks,
        )

    @app.get("/metrics", response_model=MetricsResponse)
    async def metrics() -> MetricsResponse:
        """Get Prometheus-compatible metrics.

        Returns:
            Metrics in JSON format (can be extended for Prometheus)
        """
        state = get_health_state()

        return MetricsResponse(
            status="ok",
            timestamp=datetime.utcnow().isoformat(),
            uptime_seconds=state.uptime_seconds,
            metrics=state.metrics,
        )

    @app.get("/health/state")
    async def get_full_state() -> HealthState:
        """Get full health state (internal use).

        Returns:
            Complete HealthState object
        """
        return get_health_state()

    return app


# Create global app instance
app = create_health_app()


async def run_health_server(
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Run health check server.

    Args:
        host: Server host
        port: Server port
    """
    import uvicorn

    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    logger.info(f"Starting health check server on {host}:{port}")

    try:
        await server.serve()
    except Exception as e:
        logger.error(f"Health server error: {e}")


async def start_health_server_task(
    host: str = "0.0.0.0",
    port: int = 8080,
) -> asyncio.Task:
    """Start health server as background task.

    Args:
        host: Server host
        port: Server port

    Returns:
        Asyncio task running the server
    """
    task = asyncio.create_task(run_health_server(host, port))
    return task
