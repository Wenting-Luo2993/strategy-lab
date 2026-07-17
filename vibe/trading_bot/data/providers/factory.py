"""
Data provider factory for creating real-time data sources.
"""

import logging
from typing import Optional, Union

from .types import RealtimeDataProvider, WebSocketDataProvider, RESTDataProvider
from .finnhub import FinnhubWebSocketClient
from .interactive_brokers import InteractiveBrokersDataProvider
from .polygon import PolygonDataProvider
from .yahoo import YahooDataProvider
from vibe.trading_bot.brokers.interactive_brokers import InteractiveBrokersAPI

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
        ib_host: str = "127.0.0.1",
        ib_port: int = 4002,
        ib_client_id: int = 202,
        ib_account_id: Optional[str] = None,
        ib_exchange: str = "SMART",
        ib_currency: str = "USD",
        ib_market_data_type: int = 1,
        ib_connect_timeout: float = 20.0,
        ib_connect_max_retries: int = 3,
        ib_connect_retry_delay_seconds: float = 2.0,
    ) -> Optional[RealtimeDataProvider]:
        """
        Create a real-time data provider instance.

        Args:
            provider_type: Type of provider ('finnhub', 'polygon', 'alpaca', 'yfinance', 'interactive_brokers')
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

        elif provider_type in {"interactive_brokers", "ib", "ibkr"}:
            logger.info("Creating Interactive Brokers market data provider")
            return InteractiveBrokersDataProvider(
                InteractiveBrokersAPI(
                    host=ib_host,
                    port=ib_port,
                    client_id=ib_client_id,
                    account_id=ib_account_id,
                    exchange=ib_exchange,
                    currency=ib_currency,
                    market_data_type=ib_market_data_type,
                    connect_timeout=ib_connect_timeout,
                    connect_max_retries=ib_connect_max_retries,
                    connect_retry_delay_seconds=ib_connect_retry_delay_seconds,
                    readonly=True,
                )
            )

        else:
            raise ValueError(
                f"Unknown provider type: {provider_type}. "
                f"Valid options: finnhub, polygon, alpaca, yfinance, interactive_brokers"
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
            },
            "interactive_brokers": {
                "name": "Interactive Brokers",
                "type": "rest",
                "real_time": True,
                "free_tier": False,
                "rate_limit": "Broker API pacing limits apply",
                "reliability": "High when Gateway is healthy",
                "data_quality": "High with active market data subscription",
                "recommended": True,
                "notes": "Uses IB Gateway live market data snapshots"
            }
        }

        return providers.get(provider_type, {
            "name": "Unknown",
            "error": f"Unknown provider: {provider_type}"
        })
