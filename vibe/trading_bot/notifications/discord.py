"""Discord webhook notification service with rate limiting and queueing."""

import asyncio
import logging
from datetime import datetime
from typing import Optional, List
import aiohttp

from vibe.trading_bot.notifications.payloads import OrderNotificationPayload, SystemStatusPayload
from vibe.trading_bot.notifications.formatter import DiscordNotificationFormatter
from vibe.trading_bot.notifications.rate_limiter import TokenBucketRateLimiter


logger = logging.getLogger(__name__)


class DiscordNotifier:
    """Sends order notifications to Discord via webhook with rate limiting.

    Integrates token bucket rate limiter, message queue, and error handling
    for Discord webhook compliance.
    """

    # Discord webhook rate limit: 5 requests per 2 seconds
    DEFAULT_TOKENS_PER_PERIOD = 5
    DEFAULT_PERIOD_SECONDS = 2.0

    def __init__(
        self,
        webhook_url: str,
        tokens_per_period: int = DEFAULT_TOKENS_PER_PERIOD,
        period_seconds: float = DEFAULT_PERIOD_SECONDS,
        queue_size: int = 100,
        max_retries: int = 3,
    ):
        """Initialize Discord notifier.

        Args:
            webhook_url: Discord webhook URL
            tokens_per_period: Rate limit tokens per period
            period_seconds: Rate limit period in seconds
            queue_size: Maximum size of notification queue
            max_retries: Maximum retry attempts for failed requests
        """
        self.webhook_url = webhook_url
        self.formatter = DiscordNotificationFormatter()
        self.rate_limiter = TokenBucketRateLimiter(
            tokens_per_period=tokens_per_period,
            period_seconds=period_seconds
        )
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.max_retries = max_retries
        self._worker_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        """Start the notification worker."""
        self._session = aiohttp.ClientSession()
        self._worker_task = asyncio.create_task(self._worker())
        logger.info("Discord notifier started")

    async def stop(self) -> None:
        """Stop the notification worker and cleanup."""
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()

        logger.info("Discord notifier stopped")

    async def send_order_event(self, payload: OrderNotificationPayload) -> bool:
        """Queue an order event notification.

        Args:
            payload: Order notification payload

        Returns:
            True if queued successfully, False if queue is full
        """
        try:
            self.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            logger.warning(f"Notification queue full, dropped event: {payload.event_type}")
            return False

    async def send_system_status(self, payload: SystemStatusPayload) -> bool:
        """Send a system status notification immediately (bypasses queue).

        System status messages are sent immediately rather than queued,
        as they are infrequent and time-sensitive.

        Args:
            payload: System status notification payload

        Returns:
            True if sent successfully, False otherwise
        """
        if not self._session:
            logger.error("Session not initialized")
            return False

        try:
            # Format message using system status formatter
            message = self.formatter.format_system_status(payload)

            # Apply rate limiting
            wait_time = await self.rate_limiter.acquire()
            if wait_time > 0:
                logger.debug(f"Rate limit: waiting {wait_time:.2f}s before sending")

            # Send directly (bypass queue for time-sensitive status messages)
            async with self._session.post(
                self.webhook_url,
                json=message,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    logger.info(f"System status notification sent: {payload.event_type}")
                    return True
                else:
                    logger.error(
                        f"Failed to send system status: {response.status} "
                        f"(text: {await response.text()})"
                    )
                    return False

        except Exception as e:
            logger.error(f"Error sending system status: {e}", exc_info=True)
            return False

    async def _worker(self) -> None:
        """Process notification queue with rate limiting."""
        while True:
            try:
                # Get next notification from queue
                payload = await self.queue.get()

                # Apply rate limiting
                wait_time = await self.rate_limiter.acquire()
                if wait_time > 0:
                    logger.debug(f"Rate limit: waiting {wait_time:.2f}s before sending")

                # Send notification
                success = await self._send_webhook(payload)

                if success:
                    logger.info(f"Notification sent: {payload.event_type} {payload.symbol}")
                else:
                    logger.warning(f"Failed to send notification: {payload.event_type}")

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
                await asyncio.sleep(1)  # Back off on error

    async def _send_webhook(self, payload: OrderNotificationPayload, attempt: int = 1) -> bool:
        """Send webhook request to Discord.

        Args:
            payload: Order notification payload
            attempt: Current attempt number

        Returns:
            True if successful, False otherwise
        """
        if not self._session:
            logger.error("Session not initialized")
            return False

        try:
            message = self.formatter.format(payload)

            async with self._session.post(
                self.webhook_url,
                json=message,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    return True

                # Handle rate limit (429)
                if response.status == 429:
                    retry_after = response.headers.get("Retry-After", "1")
                    try:
                        wait_seconds = float(retry_after)
                    except (ValueError, TypeError):
                        wait_seconds = 1.0

                    logger.warning(
                        f"Discord rate limited, waiting {wait_seconds}s "
                        f"(attempt {attempt}/{self.max_retries})"
                    )

                    if attempt < self.max_retries:
                        await asyncio.sleep(wait_seconds)
                        return await self._send_webhook(payload, attempt + 1)
                    return False

                # Handle other errors
                if response.status >= 500:
                    # Server error, retry
                    if attempt < self.max_retries:
                        logger.warning(
                            f"Discord server error ({response.status}), "
                            f"retrying (attempt {attempt}/{self.max_retries})"
                        )
                        await asyncio.sleep(min(2 ** attempt, 10))  # Exponential backoff
                        return await self._send_webhook(payload, attempt + 1)

                # Client error (4xx except 429)
                logger.error(
                    f"Discord API error: {response.status} "
                    f"(text: {await response.text()})"
                )
                return False

        except asyncio.TimeoutError:
            logger.warning(f"Webhook request timeout (attempt {attempt}/{self.max_retries})")
            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** attempt, 10))
                return await self._send_webhook(payload, attempt + 1)
            return False

        except aiohttp.ClientError as e:
            logger.error(f"Network error sending webhook: {e}")
            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** attempt, 10))
                return await self._send_webhook(payload, attempt + 1)
            return False

        except Exception as e:
            logger.error(f"Unexpected error in webhook send: {e}", exc_info=True)
            return False

    def get_queue_size(self) -> int:
        """Get current queue size."""
        return self.queue.qsize()

    def get_queue_maxsize(self) -> int:
        """Get queue max size."""
        return self.queue.maxsize

    async def flush(self) -> None:
        """Wait for all queued notifications to be sent."""
        await self.queue.join()
