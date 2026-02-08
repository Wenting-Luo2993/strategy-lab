"""Tests for health check API."""

import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from vibe.trading_bot.api.health import (
    app,
    HealthState,
    set_health_state,
    get_health_state,
    add_metric,
    create_health_app,
)


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


class TestLivenessProbe:
    """Tests for liveness probe endpoint."""

    def test_liveness_alive(self, client):
        """Test liveness probe when alive."""
        set_health_state(is_alive=True)
        response = client.get("/health/live")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"
        assert "timestamp" in data

    def test_liveness_dead(self, client):
        """Test liveness probe when dead."""
        set_health_state(is_alive=False)
        response = client.get("/health/live")

        assert response.status_code == 503
        assert "detail" in response.json()

    def test_liveness_timestamp(self, client):
        """Test that liveness includes timestamp."""
        set_health_state(is_alive=True)
        response = client.get("/health/live")

        data = response.json()
        # Try to parse timestamp as ISO format
        datetime.fromisoformat(data["timestamp"])


class TestReadinessProbe:
    """Tests for readiness probe endpoint."""

    def test_readiness_ready(self, client):
        """Test readiness probe when ready."""
        set_health_state(
            is_alive=True,
            websocket_connected=True,
            recent_heartbeat=True,
        )
        response = client.get("/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["checks"]["alive"] is True
        assert data["checks"]["websocket_connected"] is True

    def test_readiness_not_ready_no_heartbeat(self, client):
        """Test readiness probe when no recent heartbeat."""
        set_health_state(
            is_alive=True,
            websocket_connected=False,
            recent_heartbeat=False,
        )
        response = client.get("/health/ready")

        assert response.status_code == 503

    def test_readiness_not_ready_dead(self, client):
        """Test readiness probe when dead."""
        set_health_state(
            is_alive=False,
            websocket_connected=True,
            recent_heartbeat=True,
        )
        response = client.get("/health/ready")

        assert response.status_code == 503

    def test_readiness_with_heartbeat_only(self, client):
        """Test readiness with heartbeat but no websocket."""
        set_health_state(
            is_alive=True,
            websocket_connected=False,
            recent_heartbeat=True,
        )
        response = client.get("/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["checks"]["recent_heartbeat"] is True

    def test_readiness_checks_included(self, client):
        """Test that readiness response includes all checks."""
        set_health_state(
            is_alive=True,
            websocket_connected=True,
            recent_heartbeat=True,
        )
        response = client.get("/health/ready")

        data = response.json()
        assert "checks" in data
        assert "alive" in data["checks"]
        assert "websocket_connected" in data["checks"]
        assert "recent_heartbeat" in data["checks"]


class TestMetricsEndpoint:
    """Tests for metrics endpoint."""

    def test_metrics_response(self, client):
        """Test metrics endpoint returns data."""
        add_metric("trades_count", 42)
        add_metric("total_pnl", 1250.50)

        response = client.get("/metrics")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "metrics" in data

    def test_metrics_include_custom(self, client):
        """Test metrics include custom metrics."""
        add_metric("custom_metric", "custom_value")
        add_metric("another_metric", 123)

        response = client.get("/metrics")

        data = response.json()
        assert "custom_metric" in data["metrics"]
        assert "another_metric" in data["metrics"]

    def test_metrics_timestamp(self, client):
        """Test metrics include timestamp."""
        response = client.get("/metrics")

        data = response.json()
        datetime.fromisoformat(data["timestamp"])

    def test_metrics_uptime(self, client):
        """Test metrics include uptime."""
        response = client.get("/metrics")

        data = response.json()
        assert data["uptime_seconds"] >= 0


class TestHealthState:
    """Tests for health state management."""

    def test_health_state_model(self):
        """Test HealthState model."""
        state = HealthState()
        assert state.is_alive is True
        assert state.websocket_connected is False
        assert state.recent_heartbeat is False

    def test_set_health_state_alive(self):
        """Test setting alive state."""
        set_health_state(is_alive=True)
        state = get_health_state()
        assert state.is_alive is True

    def test_set_health_state_websocket(self):
        """Test setting websocket connected state."""
        set_health_state(websocket_connected=True)
        state = get_health_state()
        assert state.websocket_connected is True

    def test_set_health_state_heartbeat(self):
        """Test setting heartbeat state."""
        set_health_state(recent_heartbeat=True)
        state = get_health_state()
        assert state.recent_heartbeat is True
        assert state.last_heartbeat is not None

    def test_set_health_state_error(self):
        """Test setting error state."""
        error_msg = "Connection failed"
        set_health_state(last_error=error_msg)
        state = get_health_state()
        assert state.last_error == error_msg

    def test_get_health_state_uptime(self):
        """Test that uptime is calculated."""
        state = get_health_state()
        assert state.uptime_seconds >= 0

    def test_add_metric(self):
        """Test adding metrics to health state."""
        add_metric("test_metric", 42)
        state = get_health_state()
        assert state.metrics["test_metric"] == 42

    def test_multiple_metrics(self):
        """Test adding multiple metrics."""
        add_metric("metric1", "value1")
        add_metric("metric2", "value2")
        add_metric("metric3", 123)

        state = get_health_state()
        assert len(state.metrics) >= 3
        assert state.metrics["metric1"] == "value1"
        assert state.metrics["metric3"] == 123


class TestHealthStateEndpoint:
    """Tests for full state endpoint."""

    def test_health_state_endpoint(self, client):
        """Test getting full health state."""
        set_health_state(is_alive=True)
        response = client.get("/health/state")

        assert response.status_code == 200
        data = response.json()
        assert "is_alive" in data
        assert "websocket_connected" in data
        assert "metrics" in data

    def test_health_state_full_structure(self, client):
        """Test full health state structure."""
        set_health_state(
            is_alive=True,
            websocket_connected=True,
            recent_heartbeat=True,
            last_error="Previous error",
        )

        response = client.get("/health/state")
        data = response.json()

        assert data["is_alive"] is True
        assert data["websocket_connected"] is True
        assert data["recent_heartbeat"] is True
        assert data["last_error"] == "Previous error"


class TestCreateHealthApp:
    """Tests for health app creation."""

    def test_create_health_app(self):
        """Test creating a new health app."""
        new_app = create_health_app()
        assert new_app is not None

        # Verify it has the expected routes
        routes = [route.path for route in new_app.routes]
        assert "/health/live" in routes
        assert "/health/ready" in routes
        assert "/metrics" in routes

    def test_multiple_app_instances(self):
        """Test creating multiple app instances."""
        app1 = create_health_app()
        app2 = create_health_app()

        assert app1 is not app2  # Different instances
        assert len(app1.routes) == len(app2.routes)


class TestIntegration:
    """Integration tests for health check API."""

    def test_health_flow(self, client):
        """Test typical health check flow."""
        # Initially not ready
        set_health_state(
            is_alive=True,
            websocket_connected=False,
            recent_heartbeat=False,
        )
        response = client.get("/health/live")
        assert response.status_code == 200

        response = client.get("/health/ready")
        assert response.status_code == 503

        # Become ready
        set_health_state(recent_heartbeat=True)
        response = client.get("/health/ready")
        assert response.status_code == 200

        # Get metrics
        add_metric("trades_processed", 10)
        response = client.get("/metrics")
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"]["trades_processed"] == 10

    def test_failure_scenario(self, client):
        """Test failure scenario."""
        set_health_state(
            is_alive=False,
            last_error="Critical error occurred",
        )

        # Should fail liveness
        response = client.get("/health/live")
        assert response.status_code == 503

        # Verify error is in state
        state = get_health_state()
        assert state.last_error == "Critical error occurred"
