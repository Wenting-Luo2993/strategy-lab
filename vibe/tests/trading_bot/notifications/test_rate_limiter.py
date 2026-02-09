"""Tests for token bucket rate limiter."""

import pytest
import asyncio
import time

from vibe.trading_bot.notifications.rate_limiter import TokenBucketRateLimiter


class TestTokenBucketRateLimiter:
    """Test token bucket rate limiter."""

    @pytest.fixture
    def limiter(self):
        """Create rate limiter."""
        return TokenBucketRateLimiter(
            tokens_per_period=5,
            period_seconds=2.0
        )

    @pytest.mark.asyncio
    async def test_initialization(self, limiter):
        """Test limiter initializes correctly."""
        assert limiter.tokens_per_period == 5
        assert limiter.period_seconds == 2.0
        assert limiter.max_tokens == 5
        assert limiter.tokens == 5.0

    @pytest.mark.asyncio
    async def test_acquire_immediate(self, limiter):
        """Test acquiring available tokens returns immediately."""
        wait_time = await limiter.acquire()
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_acquire_burst(self, limiter):
        """Test burst capacity - can acquire up to max_tokens immediately."""
        # Acquire 5 tokens immediately
        for _ in range(5):
            wait_time = await limiter.acquire()
            assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_acquire_blocks_when_exhausted(self, limiter):
        """Test acquire blocks when tokens exhausted."""
        # Exhaust tokens
        for _ in range(5):
            await limiter.acquire()

        # 6th request should wait
        start = time.time()
        wait_time = await limiter.acquire()
        elapsed = time.time() - start

        # Should wait at least period_seconds / tokens_per_period = 0.4s
        assert elapsed >= 0.3
        assert wait_time > 0.0

    @pytest.mark.asyncio
    async def test_acquire_multiple_tokens(self, limiter):
        """Test acquiring multiple tokens at once."""
        # Request 3 tokens - should succeed
        wait_time = await limiter.acquire(tokens=3)
        assert wait_time == 0.0

        # Should have 2 left
        wait_time = await limiter.acquire(tokens=2)
        assert wait_time == 0.0

        # Request 1 more - should wait
        start = time.time()
        wait_time = await limiter.acquire(tokens=1)
        elapsed = time.time() - start
        assert elapsed >= 0.3

    @pytest.mark.asyncio
    async def test_acquire_invalid_tokens(self, limiter):
        """Test error when requesting more than max_tokens."""
        with pytest.raises(ValueError, match="Requested tokens"):
            await limiter.acquire(tokens=10)

    @pytest.mark.asyncio
    async def test_try_acquire_success(self, limiter):
        """Test try_acquire succeeds with tokens available."""
        result = limiter.try_acquire()
        assert result is True

    @pytest.mark.asyncio
    async def test_try_acquire_failure(self, limiter):
        """Test try_acquire fails when no tokens."""
        # Exhaust tokens
        for _ in range(5):
            limiter.try_acquire()

        # Next should fail
        result = limiter.try_acquire()
        assert result is False

    @pytest.mark.asyncio
    async def test_try_acquire_non_blocking(self, limiter):
        """Test try_acquire doesn't block."""
        start = time.time()
        for _ in range(10):
            limiter.try_acquire()
        elapsed = time.time() - start

        # Should complete very quickly (non-blocking)
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_tokens_refill(self, limiter):
        """Test tokens refill over time."""
        # Exhaust tokens
        for _ in range(5):
            await limiter.acquire()

        # Wait for refill
        await asyncio.sleep(2.0)

        # Should have tokens again
        wait_time = await limiter.acquire()
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_reset(self, limiter):
        """Test reset refills bucket to max."""
        # Exhaust tokens
        for _ in range(5):
            await limiter.acquire()

        # Reset
        await limiter.reset()

        # Should have all tokens again
        wait_time = await limiter.acquire()
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_get_state(self, limiter):
        """Test get_state returns current state."""
        state = limiter.get_state()

        assert "tokens" in state
        assert "max_tokens" in state
        assert "tokens_per_period" in state
        assert "period_seconds" in state
        assert "last_refill_time" in state

    @pytest.mark.asyncio
    async def test_concurrent_access(self, limiter):
        """Test concurrent access is thread-safe."""
        async def worker():
            for _ in range(5):
                await limiter.acquire()

        # Run multiple workers concurrently
        tasks = [worker() for _ in range(2)]
        await asyncio.gather(*tasks)

        # Should still be functional
        assert limiter.tokens <= limiter.max_tokens

    @pytest.mark.asyncio
    async def test_refill_during_wait(self, limiter):
        """Test tokens refill while waiting."""
        # Exhaust tokens
        for _ in range(5):
            await limiter.acquire()

        # Request more - should wait for refill
        start = time.time()
        wait_time = await limiter.acquire()
        elapsed = time.time() - start

        # Should have waited
        assert elapsed >= 0.3
        assert wait_time > 0.0

    @pytest.mark.asyncio
    async def test_custom_max_tokens(self):
        """Test custom max_tokens parameter."""
        limiter = TokenBucketRateLimiter(
            tokens_per_period=10,
            period_seconds=1.0,
            max_tokens=5  # Less than tokens_per_period
        )

        assert limiter.max_tokens == 5

        # Can only burst up to max_tokens
        for _ in range(5):
            wait_time = await limiter.acquire()
            assert wait_time == 0.0

        # 6th request should wait
        start = time.time()
        wait_time = await limiter.acquire()
        elapsed = time.time() - start
        assert elapsed >= 0.05

    @pytest.mark.asyncio
    async def test_discord_rate_limit_compliance(self, limiter):
        """Test Discord rate limit compliance (5 req/2s)."""
        # Send 10 notifications
        start = time.time()
        for _ in range(10):
            await limiter.acquire()
        elapsed = time.time() - start

        # Should take at least 2 seconds (5 immediate + 5 waiting)
        assert elapsed >= 1.9


class TestTokenBucketRateLimiterIntegration:
    """Integration tests for rate limiter."""

    @pytest.mark.asyncio
    async def test_multiple_limiters_independent(self):
        """Test multiple limiters operate independently."""
        limiter1 = TokenBucketRateLimiter(tokens_per_period=5, period_seconds=2.0)
        limiter2 = TokenBucketRateLimiter(tokens_per_period=5, period_seconds=2.0)

        # Exhaust limiter1
        for _ in range(5):
            await limiter1.acquire()

        # limiter2 should still have tokens
        wait_time = await limiter2.acquire()
        assert wait_time == 0.0

    @pytest.mark.asyncio
    async def test_realistic_discord_scenario(self):
        """Test realistic Discord webhook rate limiting scenario."""
        limiter = TokenBucketRateLimiter(
            tokens_per_period=5,
            period_seconds=2.0
        )

        notifications_sent = 0
        start = time.time()

        # Simulate sending 20 notifications
        while notifications_sent < 20:
            wait_time = await limiter.acquire()
            notifications_sent += 1

            if notifications_sent % 5 == 0:
                elapsed = time.time() - start
                print(f"Sent {notifications_sent} notifications in {elapsed:.1f}s")

        total_time = time.time() - start

        # 20 notifications at 5/2s = at least ~6 seconds (5 bursts)
        # Allow some tolerance for system timing
        assert total_time >= 5.5
