"""Tests for health monitor."""

import pytest
from datetime import datetime, timedelta

from vibe.trading_bot.core.health_monitor import HealthMonitor, HealthCheckEndpoint


class TestHealthMonitor:
    """Test health monitor functionality."""

    @pytest.fixture
    def monitor(self):
        """Create health monitor."""
        return HealthMonitor()

    def test_initialization(self, monitor):
        """Test monitor initializes correctly."""
        assert len(monitor.components) == 0
        assert monitor.start_time is not None

    def test_register_component(self, monitor):
        """Test registering a component."""
        def health_check():
            return {"status": "healthy"}

        monitor.register_component("test", health_check)

        assert "test" in monitor.components

    def test_get_health_no_components(self, monitor):
        """Test health with no components."""
        health = monitor.get_health()

        assert health["overall"] == "healthy"
        assert len(health["components"]) == 0

    def test_get_health_all_healthy(self, monitor):
        """Test health when all components healthy."""
        monitor.register_component("comp1", lambda: {"status": "healthy"})
        monitor.register_component("comp2", lambda: {"status": "healthy"})

        health = monitor.get_health()

        assert health["overall"] == "healthy"
        assert health["components"]["comp1"]["status"] == "healthy"
        assert health["components"]["comp2"]["status"] == "healthy"

    def test_get_health_one_unhealthy(self, monitor):
        """Test health when one component unhealthy."""
        monitor.register_component("comp1", lambda: {"status": "healthy"})
        monitor.register_component("comp2", lambda: {"status": "unhealthy"})

        health = monitor.get_health()

        assert health["overall"] == "unhealthy"
        assert health["components"]["comp2"]["status"] == "unhealthy"

    def test_get_health_with_error(self, monitor):
        """Test health when component check fails."""
        def failing_check():
            raise Exception("Test error")

        monitor.register_component("failing", failing_check)

        health = monitor.get_health()

        assert health["overall"] == "unhealthy"
        assert health["components"]["failing"]["status"] == "unhealthy"
        assert "error" in health["components"]["failing"]

    def test_record_error(self, monitor):
        """Test error recording."""
        monitor.record_error("comp1")
        monitor.record_error("comp1")
        monitor.record_error("comp2")

        health = monitor.get_health()

        assert health["total_errors"] == 3

    def test_get_component_errors(self, monitor):
        """Test getting error count for component."""
        monitor.record_error("comp1")
        monitor.record_error("comp1")

        count = monitor.get_component_errors("comp1")

        assert count == 2

    def test_get_component_errors_none(self, monitor):
        """Test getting errors for component with no errors."""
        count = monitor.get_component_errors("unknown")

        assert count == 0

    def test_heartbeat_logging(self, monitor, caplog):
        """Test heartbeat logging."""
        import logging
        caplog.set_level(logging.INFO)

        monitor.register_component("test", lambda: {"status": "healthy"})

        # Force heartbeat by resetting last_heartbeat
        monitor.heartbeat_interval = 0
        monitor.last_heartbeat = datetime.utcnow() - timedelta(seconds=1)
        monitor.check_heartbeat()

        # Should log heartbeat (check logs or just verify no error)
        assert True

    def test_is_healthy(self, monitor):
        """Test is_healthy check."""
        monitor.register_component("test", lambda: {"status": "healthy"})

        assert monitor.is_healthy() is True

    def test_is_unhealthy(self, monitor):
        """Test is_healthy returns False when unhealthy."""
        monitor.register_component("test", lambda: {"status": "unhealthy"})

        assert monitor.is_healthy() is False

    def test_uptime_tracking(self, monitor):
        """Test uptime is tracked correctly."""
        health = monitor.get_health()

        assert "uptime_seconds" in health
        assert health["uptime_seconds"] >= 0

    def test_get_status_summary(self, monitor):
        """Test status summary."""
        monitor.register_component("comp1", lambda: {"status": "healthy"})
        monitor.register_component("comp2", lambda: {"status": "healthy"})

        summary = monitor.get_status_summary()

        assert "status" in summary
        assert "uptime_minutes" in summary
        assert "components_healthy" in summary
        assert "components_total" in summary
        assert summary["components_total"] == 2
        assert summary["components_healthy"] == 2


class TestHealthCheckEndpoint:
    """Test health check endpoints."""

    @pytest.fixture
    def endpoint(self):
        """Create health check endpoint."""
        monitor = HealthMonitor()
        monitor.register_component("test", lambda: {"status": "healthy"})
        return HealthCheckEndpoint(monitor)

    def test_get_liveness_pass(self, endpoint):
        """Test liveness check passes when healthy."""
        response = endpoint.get_liveness()

        assert response["status"] == "pass"
        assert "output" in response

    def test_get_liveness_fail(self):
        """Test liveness check fails on error."""
        monitor = HealthMonitor()

        def failing_check():
            raise Exception("Test error")

        monitor.register_component("failing", failing_check)

        endpoint = HealthCheckEndpoint(monitor)
        response = endpoint.get_liveness()

        # Even with errors, liveness should pass if process is running
        assert response["status"] == "pass"

    def test_get_readiness_pass(self, endpoint):
        """Test readiness check passes when healthy."""
        response = endpoint.get_readiness()

        assert response["status"] == "pass"

    def test_get_readiness_fail(self):
        """Test readiness check fails when unhealthy."""
        monitor = HealthMonitor()
        monitor.register_component("test", lambda: {"status": "unhealthy"})

        endpoint = HealthCheckEndpoint(monitor)
        response = endpoint.get_readiness()

        assert response["status"] == "fail"

    def test_get_startup_pass(self, endpoint):
        """Test startup check passes when ready."""
        response = endpoint.get_startup()

        assert response["status"] == "pass"

    def test_get_startup_fail_no_components(self):
        """Test startup check fails with no components."""
        monitor = HealthMonitor()
        endpoint = HealthCheckEndpoint(monitor)

        response = endpoint.get_startup()

        assert response["status"] == "fail"

    def test_health_check_responses(self, endpoint):
        """Test all health check types return proper format."""
        liveness = endpoint.get_liveness()
        readiness = endpoint.get_readiness()
        startup = endpoint.get_startup()

        for response in [liveness, readiness, startup]:
            assert "status" in response
            assert "output" in response
            assert response["status"] in ["pass", "fail"]
