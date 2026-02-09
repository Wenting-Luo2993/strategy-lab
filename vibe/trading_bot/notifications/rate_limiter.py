"""Token bucket rate limiter for Discord API compliance."""

import asyncio
import time
from typing import Optional


class TokenBucketRateLimiter:
    """Token bucket algorithm for rate limiting Discord webhook requests.

    Discord allows 5 requests per 2 seconds. This implementation supports
    configurable rate limits with burst capability and fair queueing.
    """

    def __init__(
        self,
        tokens_per_period: int = 5,
        period_seconds: float = 2.0,
        max_tokens: Optional[int] = None
    ):
        """Initialize token bucket rate limiter.

        Args:
            tokens_per_period: Number of tokens generated per period
            period_seconds: Period duration in seconds
            max_tokens: Maximum tokens in bucket (default: tokens_per_period)
        """
        self.tokens_per_period = tokens_per_period
        self.period_seconds = period_seconds
        self.max_tokens = max_tokens or tokens_per_period

        # Current tokens in bucket
        self.tokens = float(self.max_tokens)

        # Last time bucket was refilled
        self.last_refill_time = time.time()

        # Lock for thread-safe access
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> float:
        """Acquire tokens from bucket, waiting if necessary.

        Args:
            tokens: Number of tokens to acquire (default: 1)

        Returns:
            Wait time in seconds before this request can proceed

        Raises:
            ValueError: If tokens > max_tokens
        """
        if tokens > self.max_tokens:
            raise ValueError(
                f"Requested tokens ({tokens}) exceeds max_tokens ({self.max_tokens})"
            )

        async with self._lock:
            # Refill bucket based on elapsed time
            now = time.time()
            elapsed = now - self.last_refill_time

            # Calculate tokens to add
            tokens_to_add = (elapsed / self.period_seconds) * self.tokens_per_period
            self.tokens = min(self.max_tokens, self.tokens + tokens_to_add)
            self.last_refill_time = now

            # Check if we have enough tokens
            if self.tokens >= tokens:
                # We have tokens, use them immediately
                self.tokens -= tokens
                return 0.0

            # Not enough tokens, calculate wait time
            tokens_needed = tokens - self.tokens
            wait_time = (tokens_needed / self.tokens_per_period) * self.period_seconds

            # Wait for tokens to refill
            await asyncio.sleep(wait_time)

            # After sleep, refill and acquire
            now = time.time()
            elapsed = now - self.last_refill_time
            tokens_to_add = (elapsed / self.period_seconds) * self.tokens_per_period
            self.tokens = min(self.max_tokens, self.tokens + tokens_to_add)
            self.last_refill_time = now

            # Now we should have tokens
            self.tokens -= tokens

            return wait_time

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Try to acquire tokens without blocking.

        This is a non-async version for checking if tokens are available.

        Args:
            tokens: Number of tokens to acquire

        Returns:
            True if tokens were acquired, False if not enough available
        """
        now = time.time()
        elapsed = now - self.last_refill_time

        # Calculate available tokens
        tokens_to_add = (elapsed / self.period_seconds) * self.tokens_per_period
        available = min(self.max_tokens, self.tokens + tokens_to_add)

        if available >= tokens:
            # We have tokens
            self.tokens = available - tokens
            self.last_refill_time = now
            return True

        return False

    async def reset(self) -> None:
        """Reset limiter to full capacity."""
        async with self._lock:
            self.tokens = float(self.max_tokens)
            self.last_refill_time = time.time()

    def get_state(self) -> dict:
        """Get current limiter state for debugging.

        Returns:
            Dictionary with current tokens and refill time
        """
        return {
            "tokens": self.tokens,
            "max_tokens": self.max_tokens,
            "tokens_per_period": self.tokens_per_period,
            "period_seconds": self.period_seconds,
            "last_refill_time": self.last_refill_time,
        }
