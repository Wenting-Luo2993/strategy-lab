"""
Base class for live data providers with rate limiting, retry, and health tracking.
"""

import asyncio
import logging
import time
from abc import ABC
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import pandas as pd

from vibe.common.data import DataProvider


logger = logging.getLogger(__name__)


class ProviderHealth(str, Enum):
    """Health status of a data provider."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class RateLimiter:
    """Token bucket rate limiter for controlling request rate."""

    def __init__(self, rate: float = 5.0, period: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            rate: Number of requests allowed per period
            period: Time period in seconds
        """
        self.rate = rate
        self.period = period
        self.tokens = rate
        self.last_refill = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """
        Acquire a token. Wait if no tokens available.
        """
        async with self._lock:
            # Refill tokens based on elapsed time
            now = time.time()
            elapsed = now - self.last_refill
            tokens_to_add = (elapsed / self.period) * self.rate
            self.tokens = min(self.rate, self.tokens + tokens_to_add)
            self.last_refill = now

            # Wait if no tokens
            if self.tokens < 1:
                wait_time = (1 - self.tokens) * (self.period / self.rate)
                await asyncio.sleep(wait_time)
                self.tokens = 1.0  # Now we have exactly 1 token after waiting

            # Consume the token
            self.tokens -= 1


class LiveDataProvider(DataProvider, ABC):
    """
    Base class for live data providers.

    Extends the abstract DataProvider with rate limiting, retry logic,
    and health status tracking.
    """

    def __init__(
        self,
        rate_limit: float = 5.0,
        rate_limit_period: float = 1.0,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
    ):
        """
        Initialize live data provider.

        Args:
            rate_limit: Requests per period allowed by rate limiter
            rate_limit_period: Period for rate limiting (seconds)
            max_retries: Maximum number of retry attempts
            retry_backoff_base: Initial backoff time in seconds
            retry_backoff_multiplier: Multiplier for exponential backoff
        """
        self.rate_limit = rate_limit
        self.rate_limit_period = rate_limit_period
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_multiplier = retry_backoff_multiplier

        # Rate limiter
        self._rate_limiter = RateLimiter(rate=rate_limit, period=rate_limit_period)

        # Health tracking
        self._health = ProviderHealth.HEALTHY
        self._last_error: Optional[str] = None
        self._last_error_time: Optional[datetime] = None
        self._successful_requests = 0
        self._failed_requests = 0
        self._health_check_interval = timedelta(minutes=5)
        self._last_health_check = datetime.now()

    @property
    def health(self) -> ProviderHealth:
        """Get current provider health status."""
        return self._health

    @property
    def is_healthy(self) -> bool:
        """Check if provider is healthy."""
        return self._health == ProviderHealth.HEALTHY

    def get_health_status(self) -> dict:
        """
        Get detailed health status.

        Returns:
            Dictionary with health information
        """
        return {
            "status": self._health.value,
            "last_error": self._last_error,
            "last_error_time": self._last_error_time.isoformat() if self._last_error_time else None,
            "successful_requests": self._successful_requests,
            "failed_requests": self._failed_requests,
            "error_rate": (
                self._failed_requests / (self._successful_requests + self._failed_requests)
                if (self._successful_requests + self._failed_requests) > 0
                else 0
            ),
        }

    async def _apply_rate_limit(self) -> None:
        """Apply rate limiting before request."""
        await self._rate_limiter.acquire()

    async def _retry_with_backoff(self, coro_func, attempt: int = 0) -> any:
        """
        Execute coroutine with exponential backoff retry.

        Args:
            coro_func: Callable that returns a coroutine to execute
            attempt: Current attempt number

        Returns:
            Result from coroutine

        Raises:
            Exception from coroutine if all retries exhausted
        """
        try:
            # Call the function to get a fresh coroutine each time
            if asyncio.iscoroutinefunction(coro_func):
                result = await coro_func()
            else:
                result = await coro_func
            self._successful_requests += 1
            self._health = ProviderHealth.HEALTHY
            return result
        except Exception as e:
            self._failed_requests += 1

            if attempt < self.max_retries:
                # Calculate backoff time
                backoff_time = self.retry_backoff_base * (
                    self.retry_backoff_multiplier ** attempt
                )
                logger.warning(
                    f"Request failed (attempt {attempt + 1}/{self.max_retries}), "
                    f"retrying in {backoff_time:.1f}s: {str(e)}"
                )
                await asyncio.sleep(backoff_time)
                return await self._retry_with_backoff(coro_func, attempt + 1)
            else:
                # All retries exhausted
                self._last_error = str(e)
                self._last_error_time = datetime.now()

                # Determine health status based on error rate
                error_rate = self._failed_requests / (
                    self._successful_requests + self._failed_requests
                )
                if error_rate > 0.5:
                    self._health = ProviderHealth.UNHEALTHY
                elif error_rate > 0.2:
                    self._health = ProviderHealth.DEGRADED
                else:
                    self._health = ProviderHealth.HEALTHY

                logger.error(f"Request failed after {self.max_retries} retries: {str(e)}")
                raise

    def reset_health(self) -> None:
        """Reset health status and error counters."""
        self._health = ProviderHealth.HEALTHY
        self._last_error = None
        self._last_error_time = None
        self._successful_requests = 0
        self._failed_requests = 0
