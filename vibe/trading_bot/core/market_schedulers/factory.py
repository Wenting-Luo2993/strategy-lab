"""Factory for creating market schedulers based on market type."""

from typing import Optional
from .base import BaseMarketScheduler
from .stock import StockMarketScheduler
from .forex import ForexMarketScheduler
from .crypto import CryptoMarketScheduler


def create_scheduler(
    market_type: str,
    exchange: Optional[str] = None,
    timezone: Optional[str] = None,
) -> BaseMarketScheduler:
    """
    Create appropriate market scheduler based on market type.

    Args:
        market_type: Market type ('stocks', 'forex', 'crypto')
        exchange: Exchange name (required for stocks, e.g., 'NYSE', 'NASDAQ')
        timezone: Timezone string (optional, uses market-appropriate default)

    Returns:
        Market scheduler instance

    Raises:
        ValueError: If market_type is invalid or required parameters are missing

    Examples:
        >>> scheduler = create_scheduler('stocks', exchange='NYSE')
        >>> scheduler = create_scheduler('forex')
        >>> scheduler = create_scheduler('crypto')
    """
    market_type = market_type.lower()

    if market_type == "stocks":
        if not exchange:
            raise ValueError("exchange parameter is required for stocks market type")

        tz = timezone or "US/Eastern"
        return StockMarketScheduler(exchange=exchange, timezone=tz)

    elif market_type == "forex":
        tz = timezone or "US/Eastern"
        return ForexMarketScheduler(timezone=tz)

    elif market_type == "crypto":
        tz = timezone or "UTC"
        return CryptoMarketScheduler(timezone=tz)

    else:
        raise ValueError(
            f"Invalid market_type: {market_type}. "
            f"Must be one of: 'stocks', 'forex', 'crypto'"
        )
