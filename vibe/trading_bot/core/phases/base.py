"""Base class for trading bot phase managers.

This module defines the base interface that all phase managers must implement,
providing a consistent pattern for managing different phases of the trading bot
lifecycle (warmup, trading, cooldown, etc.).
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.trading_bot.core.orchestrator import TradingOrchestrator


class BasePhase(ABC):
    """Base class for phase managers (warmup, cooldown, etc.).

    Phase managers encapsulate the logic for different lifecycle phases of the
    trading bot, providing modularity, testability, and clear separation of concerns.

    Each phase manager has access to the orchestrator's dependencies through
    convenient properties, avoiding tight coupling while maintaining access to
    necessary components.
    """

    def __init__(self, orchestrator: 'TradingOrchestrator'):
        """Initialize phase with orchestrator reference.

        Args:
            orchestrator: Parent orchestrator for dependency access
        """
        self.orchestrator = orchestrator
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def execute(self) -> bool:
        """Execute the phase logic.

        This method should be implemented by each phase manager to perform
        the specific actions required for that phase.

        Returns:
            True if phase completed successfully, False otherwise
        """
        pass

    # Convenience properties for common dependencies
    # These provide easy access to orchestrator components without tight coupling

    @property
    def config(self):
        """Get application configuration."""
        return self.orchestrator.config

    @property
    def market_scheduler(self):
        """Get market scheduler (timing, session info)."""
        return self.orchestrator.market_scheduler

    @property
    def data_manager(self):
        """Get data manager (Yahoo Finance historical data)."""
        return self.orchestrator.data_manager

    @property
    def primary_provider(self):
        """Get primary real-time data provider (WebSocket or REST)."""
        return self.orchestrator.primary_provider

    @property
    def secondary_provider(self):
        """Get secondary/fallback data provider."""
        return self.orchestrator.secondary_provider

    @property
    def active_provider(self):
        """Get currently active data provider."""
        return self.orchestrator.active_provider

    @property
    def indicator_engine(self):
        """Get indicator calculation engine (ATR, etc.)."""
        return self.orchestrator.indicator_engine

    @property
    def health_monitor(self):
        """Get system health monitor."""
        return self.orchestrator.health_monitor
