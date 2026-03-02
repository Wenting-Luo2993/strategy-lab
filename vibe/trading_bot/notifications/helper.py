"""Helper utilities for Discord notifications.

This module provides convenient context managers and helper functions for working
with Discord notifications, ensuring proper lifecycle management and reducing
boilerplate code.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from vibe.trading_bot.notifications.discord import DiscordNotifier


@asynccontextmanager
async def discord_notification_context(webhook_url: str) -> AsyncIterator[DiscordNotifier]:
    """Async context manager for Discord notifications.

    Automatically manages notifier lifecycle (start/stop), ensuring the notifier
    is properly initialized and cleaned up even if exceptions occur.

    This is the recommended way to send Discord notifications, as it:
    - Automatically calls notifier.start() before the block
    - Automatically calls notifier.stop() after the block (even on error)
    - Reduces boilerplate code

    Usage:
        async with discord_notification_context(webhook_url) as notifier:
            await notifier.send_system_status(payload)
            # notifier.stop() is called automatically

    Args:
        webhook_url: Discord webhook URL

    Yields:
        Started DiscordNotifier instance ready to send messages

    Example:
        >>> from vibe.trading_bot.notifications.helper import discord_notification_context
        >>> from vibe.trading_bot.notifications.payloads import SystemStatusPayload
        >>>
        >>> async with discord_notification_context(webhook_url) as notifier:
        ...     payload = SystemStatusPayload(
        ...         event_type="MARKET_START",
        ...         timestamp=datetime.now(),
        ...         overall_status="healthy"
        ...     )
        ...     await notifier.send_system_status(payload)
    """
    notifier = DiscordNotifier(webhook_url=webhook_url)
    await notifier.start()

    try:
        yield notifier
    finally:
        await notifier.stop()
