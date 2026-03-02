"""Phase managers for trading bot lifecycle.

This package contains phase managers that encapsulate different phases of the
trading bot lifecycle: warmup, trading, and cooldown.
"""

from vibe.trading_bot.core.phases.base import BasePhase
from vibe.trading_bot.core.phases.warmup import WarmupPhaseManager
from vibe.trading_bot.core.phases.cooldown import CooldownPhaseManager

__all__ = ["BasePhase", "WarmupPhaseManager", "CooldownPhaseManager"]
