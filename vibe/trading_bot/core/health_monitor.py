"""Health monitoring for trading bot and its components."""

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Aggregates health status from all bot components.

    Tracks component health, system metrics, and triggers alerts on degradation.
    """

    def __init__(self, heartbeat_interval_seconds: int = 60):
        """Initialize health monitor.

        Args:
            heartbeat_interval_seconds: Interval for heartbeat logs
        """
        self.heartbeat_interval = heartbeat_interval_seconds
        self.last_heartbeat = datetime.utcnow()

        # Component health checks: name -> callable returning health dict
        self.components: Dict[str, Callable] = {}

        # Metrics tracking
        self.error_count = defaultdict(int)  # component_name -> error count
        self.start_time = datetime.utcnow()

    def register_component(
        self,
        name: str,
        health_check: Callable[[], Dict[str, Any]]
    ) -> None:
        """Register a component with health check callback.

        Args:
            name: Component name
            health_check: Callable that returns health dict with 'status' key
        """
        self.components[name] = health_check
        logger.info(f"Registered health check for: {name}")

    def record_error(self, component: str) -> None:
        """Record an error for a component.

        Args:
            component: Component name
        """
        self.error_count[component] += 1

    def get_health(self) -> Dict[str, Any]:
        """Get aggregated health status of all components.

        Returns:
            Dict with 'overall' status and 'components' with individual statuses
        """
        components_health = {}
        overall_status = "healthy"

        for name, health_check in self.components.items():
            try:
                health = health_check()
                components_health[name] = health

                # Update overall status if any component unhealthy
                if health.get("status") == "unhealthy":
                    overall_status = "unhealthy"

            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                components_health[name] = {
                    "status": "unhealthy",
                    "error": str(e),
                }
                overall_status = "unhealthy"

        return {
            "overall": overall_status,
            "timestamp": datetime.utcnow().isoformat(),
            "components": components_health,
            "uptime_seconds": (datetime.utcnow() - self.start_time).total_seconds(),
            "total_errors": sum(self.error_count.values()),
        }

    def check_heartbeat(self) -> None:
        """Log heartbeat if interval elapsed.

        Call this periodically to track that bot is running.
        """
        now = datetime.utcnow()
        if (now - self.last_heartbeat).total_seconds() >= self.heartbeat_interval:
            health = self.get_health()
            logger.info(
                f"Heartbeat: overall={health['overall']}, "
                f"uptime={health['uptime_seconds']:.0f}s, "
                f"errors={health['total_errors']}"
            )
            self.last_heartbeat = now

    def get_status_summary(self) -> Dict[str, Any]:
        """Get summary status for quick health checks.

        Returns:
            Dict with key health indicators
        """
        health = self.get_health()

        return {
            "status": health["overall"],
            "uptime_minutes": health["uptime_seconds"] / 60,
            "components_healthy": sum(
                1 for c in health["components"].values()
                if c.get("status") == "healthy"
            ),
            "components_total": len(health["components"]),
            "total_errors": health["total_errors"],
        }

    def is_healthy(self) -> bool:
        """Quick check if system is healthy.

        Returns:
            True if overall status is healthy
        """
        return self.get_health()["overall"] == "healthy"

    def get_component_errors(self, component: str) -> int:
        """Get error count for a specific component.

        Args:
            component: Component name

        Returns:
            Number of recorded errors
        """
        return self.error_count.get(component, 0)


class HealthCheckEndpoint:
    """Provides health check data for container orchestration and monitoring.

    Designed for Kubernetes/Docker health checks and monitoring services.
    """

    def __init__(self, monitor: HealthMonitor):
        """Initialize endpoint.

        Args:
            monitor: HealthMonitor instance to check
        """
        self.monitor = monitor

    def get_liveness(self) -> Dict[str, Any]:
        """Get liveness probe response.

        Liveness checks if process is running and responsive.

        Returns:
            Dict with 'status' (pass/fail) and optional message
        """
        try:
            health = self.monitor.get_health()
            return {
                "status": "pass",
                "output": f"Process healthy with {len(health['components'])} components",
            }
        except Exception as e:
            return {
                "status": "fail",
                "output": f"Liveness check failed: {str(e)}",
            }

    def get_readiness(self) -> Dict[str, Any]:
        """Get readiness probe response.

        Readiness checks if service is ready to accept requests.

        Returns:
            Dict with 'status' (pass/fail)
        """
        health = self.monitor.get_health()

        if health["overall"] != "healthy":
            return {
                "status": "fail",
                "output": "Service not ready: unhealthy component detected",
            }

        return {
            "status": "pass",
            "output": "Service ready",
        }

    def get_startup(self) -> Dict[str, Any]:
        """Get startup probe response.

        Startup checks if initialization is complete.

        Returns:
            Dict with 'status' (pass/fail)
        """
        # Simple check: if any components registered and responding
        if not self.monitor.components:
            return {
                "status": "fail",
                "output": "Startup incomplete: no components registered",
            }

        try:
            health = self.monitor.get_health()
            components_ok = sum(
                1 for c in health["components"].values()
                if c.get("status") != "unknown"
            )

            if components_ok == len(health["components"]):
                return {
                    "status": "pass",
                    "output": "Startup complete",
                }
            else:
                return {
                    "status": "fail",
                    "output": f"Startup in progress: {components_ok}/{len(health['components'])} ready",
                }
        except Exception as e:
            return {
                "status": "fail",
                "output": f"Startup check failed: {str(e)}",
            }
