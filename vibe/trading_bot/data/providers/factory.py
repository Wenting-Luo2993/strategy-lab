"""
Data provider factory for creating real-time data sources.
"""

import logging
from typing import Optional, Union

from .types import RealtimeDataProvider, WebSocketDataProvider, RESTDataProvider
from .finnhub import FinnhubWebSocketClient
from .polygon import PolygonDataProvider
from .yahoo import YahooDataProvider

logger = logging.getLogger(__name__)


class DataProviderFactory:
    """Factory for creating data provider instances."""

    @staticmethod
    def create_realtime_provider(
        provider_type: str,
        finnhub_api_key: Optional[str] = None,
        polygon_api_key: Optional[str] = None,
        alpaca_api_key: Optional[str] = None,
        alpaca_secret_key: Optional[str] = None,
    ) -> Optional[RealtimeDataProvider]:
        """
        Create a real-time data provider instance.

        Args:
            provider_type: Type of provider ('finnhub', 'polygon', 'alpaca', 'yfinance')
            finnhub_api_key: Finnhub API key (if using Finnhub)
            polygon_api_key: Polygon.io API key (if using Polygon)
            alpaca_api_key: Alpaca API key (if using Alpaca)
            alpaca_secret_key: Alpaca secret key (if using Alpaca)

        Returns:
            Provider instance or None if creation failed

        Raises:
            ValueError: If provider_type is invalid or required API key is missing
        """
        provider_type = provider_type.lower()

        if provider_type == "finnhub":
            if not finnhub_api_key:
                raise ValueError("Finnhub API key is required for 'finnhub' provider")

            logger.info("Creating Finnhub WebSocket provider")
            return FinnhubWebSocketClient(api_key=finnhub_api_key)

        elif provider_type == "polygon":
            if not polygon_api_key:
                raise ValueError("Polygon.io API key is required for 'polygon' provider")

            logger.info("Creating Polygon.io REST API provider")
            return PolygonDataProvider(
                api_key=polygon_api_key,
                rate_limit_per_minute=5  # Free tier limit
            )

        elif provider_type == "alpaca":
            if not alpaca_api_key or not alpaca_secret_key:
                raise ValueError(
                    "Alpaca API key and secret are required for 'alpaca' provider"
                )

            logger.info("Creating Alpaca provider (not yet implemented)")
            # TODO: Implement AlpacaDataProvider
            raise NotImplementedError("Alpaca provider not yet implemented")

        elif provider_type == "yfinance":
            logger.info("Creating Yahoo Finance provider (fallback only)")
            return YahooDataProvider()

        else:
            raise ValueError(
                f"Unknown provider type: {provider_type}. "
                f"Valid options: finnhub, polygon, alpaca, yfinance"
            )

    @staticmethod
    def get_provider_info(provider_type: str) -> dict:
        """
        Get information about a provider.

        Args:
            provider_type: Type of provider

        Returns:
            Dict with provider information
        """
        provider_type = provider_type.lower()

        providers = {
            "finnhub": {
                "name": "Finnhub WebSocket",
                "type": "websocket",
                "real_time": True,
                "free_tier": True,
                "rate_limit": "60 calls/min, 1 websocket",
                "reliability": "Low (frequent disconnects)",
                "data_quality": "Medium",
                "recommended": False,
                "notes": "Good for testing, unreliable for production"
            },
            "polygon": {
                "name": "Polygon.io (Massive)",
                "type": "rest",
                "real_time": False,  # Free tier is 15-min delayed
                "free_tier": True,
                "rate_limit": "5 calls/min",
                "reliability": "High",
                "data_quality": "High",
                "recommended": True,
                "notes": "Most reliable free option, 15-min delay on free tier"
            },
            "alpaca": {
                "name": "Alpaca Markets",
                "type": "websocket",
                "real_time": True,
                "free_tier": True,
                "rate_limit": "Unlimited with paper account",
                "reliability": "High",
                "data_quality": "High",
                "recommended": True,
                "notes": "Best free real-time option, requires paper trading account"
            },
            "yfinance": {
                "name": "Yahoo Finance",
                "type": "rest",
                "real_time": False,  # 15-min delayed
                "free_tier": True,
                "rate_limit": "No official limit",
                "reliability": "Medium",
                "data_quality": "Medium",
                "recommended": False,
                "notes": "Good fallback, 15-min delay"
            }
        }

        return providers.get(provider_type, {
            "name": "Unknown",
            "error": f"Unknown provider: {provider_type}"
        })
