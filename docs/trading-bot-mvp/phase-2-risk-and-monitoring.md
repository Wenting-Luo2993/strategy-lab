# Phase 2: Risk Management & Cloud Monitoring

## Overview

This document outlines **Phase 2 / Milestone 2** of the trading bot development, focusing on:

1. **Risk Management Module** - Advanced position sizing, stop loss management, portfolio-level risk controls, and exposure limits
2. **Service Health Monitoring Dashboard** - Real-time cloud dashboard for monitoring bot health, uptime, connectivity, and system resources
3. **Trading Performance Dashboard** - Real-time and historical trade monitoring, P&L tracking, strategy performance metrics, and analytics

**Phase 1 Recap:** The MVP implemented the core trading infrastructure with ORB strategy, real-time data streaming (Finnhub WebSocket + Yahoo Finance), paper trading (MockExchange), Discord notifications, and graceful shutdown. Phase 1 has basic risk management (no risk module).

**Phase 2 Goals:**
- Protect capital with sophisticated risk management
- Enable real-time monitoring without SSH access to server
- Provide performance analytics for strategy optimization
- Support multiple users/viewers (shareable dashboard links)

---

## Table of Contents

1. [Risk Management Architecture](#risk-management-architecture)
2. [Service Health Monitoring Dashboard](#service-health-monitoring-dashboard)
3. [Trading Performance Dashboard](#trading-performance-dashboard)
4. [Technology Stack](#technology-stack)
5. [Integration with Phase 1](#integration-with-phase-1)
6. [Implementation Plan](#implementation-plan)
7. [Deployment Architecture](#deployment-architecture)
8. [Security Considerations](#security-considerations)
9. [Cost Analysis](#cost-analysis)

---

## Risk Management Architecture

### Design Philosophy

**Goal:** Protect capital through multiple layers of risk controls that operate independently and can be enabled/disabled via configuration.

**Key Principles:**
1. **Defense in depth** - Multiple independent risk checks (position sizing, stop losses, exposure limits, drawdown protection)
2. **Fail-safe defaults** - Conservative risk settings out of the box
3. **Configurability** - All risk parameters adjustable via YAML config
4. **Observable** - All risk decisions logged and visible in dashboard
5. **Testable** - Risk rules unit tested in isolation

### High-Level Architecture

```
+============================================================================+
|                         RISK MANAGEMENT LAYERS                              |
+============================================================================+
|                                                                             |
|  +-------------------------------------------------------------------+     |
|  |  Layer 1: Trade-Level Risk (Per Trade)                            |     |
|  +-------------------------------------------------------------------+     |
|  |  - Position sizing (% of capital, Kelly criterion, fixed $)       |     |
|  |  - Stop loss placement (ATR-based, percentage, technical levels)  |     |
|  |  - Take profit targets                                            |     |
|  |  - Risk/Reward ratio validation (min 1:2)                         |     |
|  +-------------------------------------------------------------------+     |
|                                    |                                        |
|                                    v                                        |
|  +-------------------------------------------------------------------+     |
|  |  Layer 2: Position-Level Risk (Per Symbol)                        |     |
|  +-------------------------------------------------------------------+     |
|  |  - Max position size per symbol ($5,000 or 50% of capital)        |     |
|  |  - Trailing stop loss (ATR-based or percentage)                   |     |
|  |  - Time-based stops (max hold time: 2 hours for day trading)      |     |
|  |  - Break-even stops after 1 ATR profit                            |     |
|  +-------------------------------------------------------------------+     |
|                                    |                                        |
|                                    v                                        |
|  +-------------------------------------------------------------------+     |
|  |  Layer 3: Portfolio-Level Risk (Account)                          |     |
|  +-------------------------------------------------------------------+     |
|  |  - Max total exposure (e.g., 80% of capital)                      |     |
|  |  - Max number of concurrent positions (e.g., 3)                   |     |
|  |  - Correlation limits (no more than 2 correlated positions)       |     |
|  |  - Sector/industry concentration limits                           |     |
|  |  - Daily loss limit ($500 or 5% of capital)                       |     |
|  +-------------------------------------------------------------------+     |
|                                    |                                        |
|                                    v                                        |
|  +-------------------------------------------------------------------+     |
|  |  Layer 4: Drawdown Protection (System)                            |     |
|  +-------------------------------------------------------------------+     |
|  |  - Max drawdown circuit breaker (stop trading if -10% drawdown)   |     |
|  |  - Consecutive losses limit (stop after 5 consecutive losses)     |     |
|  |  - Volatility regime detection (reduce size in high volatility)   |     |
|  |  - Market hours validation (no trading pre-market/after-hours)    |     |
|  +-------------------------------------------------------------------+     |
|                                                                             |
+============================================================================+
```

### Component Breakdown

#### 1. RiskManager (Orchestrator)

**Location:** `vibe/common/risk/manager.py` (shared between live and backtest)

**Responsibilities:**
- Coordinate all risk checks before order submission
- Validate proposed trades against all risk layers
- Enforce daily loss limits and drawdown circuit breakers
- Provide risk metrics for dashboard

```python
# vibe/common/risk/manager.py
from dataclasses import dataclass
from typing import Optional, List
from vibe.common.models import Order, Position, Signal, AccountState

@dataclass
class RiskCheckResult:
    """Result of risk validation."""
    approved: bool
    reason: str
    adjusted_quantity: Optional[int] = None  # If position size was reduced
    stop_loss_price: Optional[float] = None  # Recommended stop loss
    take_profit_price: Optional[float] = None  # Recommended take profit

class RiskManager:
    """
    Centralized risk management for the trading bot.

    Coordinates position sizing, stop loss placement, exposure limits,
    and drawdown protection.
    """

    def __init__(self, config: RiskConfig, account: AccountState):
        self.config = config
        self.account = account

        # Sub-components
        self.position_sizer = PositionSizer(config.position_sizing)
        self.stop_loss_manager = StopLossManager(config.stop_loss)
        self.exposure_controller = ExposureController(config.exposure)
        self.drawdown_protector = DrawdownProtector(config.drawdown)

        # State
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.peak_equity = account.equity
        self.current_drawdown = 0.0

    async def validate_trade(
        self,
        signal: Signal,
        current_positions: List[Position],
        current_price: float,
        volatility: float  # ATR
    ) -> RiskCheckResult:
        """
        Validate a trade signal against all risk layers.

        Returns:
            RiskCheckResult with approval status and adjusted parameters
        """

        # Layer 4: Drawdown protection (circuit breaker)
        if not self.drawdown_protector.can_trade(
            daily_pnl=self.daily_pnl,
            consecutive_losses=self.consecutive_losses,
            current_drawdown=self.current_drawdown
        ):
            return RiskCheckResult(
                approved=False,
                reason="Trading paused due to drawdown/loss limits"
            )

        # Layer 3: Portfolio exposure limits
        if not self.exposure_controller.can_add_position(
            signal.symbol,
            current_positions,
            self.account
        ):
            return RiskCheckResult(
                approved=False,
                reason="Portfolio exposure limits exceeded"
            )

        # Layer 1: Position sizing
        position_size = self.position_sizer.calculate_size(
            signal=signal,
            account=self.account,
            volatility=volatility,
            current_price=current_price
        )

        if position_size == 0:
            return RiskCheckResult(
                approved=False,
                reason="Calculated position size is zero (insufficient capital or risk too high)"
            )

        # Layer 1: Stop loss placement
        stop_loss_price = self.stop_loss_manager.calculate_stop_loss(
            entry_price=current_price,
            side=signal.side,
            volatility=volatility,
            technical_level=signal.stop_loss  # From strategy, if provided
        )

        # Layer 1: Risk/Reward validation
        if signal.take_profit:
            risk_reward_ratio = self._calculate_risk_reward(
                entry=current_price,
                stop=stop_loss_price,
                target=signal.take_profit,
                side=signal.side
            )

            if risk_reward_ratio < self.config.min_risk_reward_ratio:
                return RiskCheckResult(
                    approved=False,
                    reason=f"Risk/reward ratio {risk_reward_ratio:.2f} below minimum {self.config.min_risk_reward_ratio:.2f}"
                )

        # Layer 2: Position size limits per symbol
        max_position_value = min(
            self.config.max_position_pct * self.account.equity,
            self.config.max_position_dollars
        )

        position_value = position_size * current_price
        if position_value > max_position_value:
            # Reduce position size
            position_size = int(max_position_value / current_price)
            logger.warning(
                f"Position size reduced from {position_value:.0f} to {position_size * current_price:.0f} "
                f"due to max position limits"
            )

        return RiskCheckResult(
            approved=True,
            reason="All risk checks passed",
            adjusted_quantity=position_size,
            stop_loss_price=stop_loss_price,
            take_profit_price=signal.take_profit
        )

    def update_after_trade(self, trade: Trade) -> None:
        """Update risk state after trade closes."""
        self.daily_pnl += trade.pnl

        if trade.pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Update drawdown tracking
        if self.account.equity > self.peak_equity:
            self.peak_equity = self.account.equity

        self.current_drawdown = (self.peak_equity - self.account.equity) / self.peak_equity

    def reset_daily_state(self) -> None:
        """Reset daily tracking at end of trading day."""
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
```

#### 2. PositionSizer

**Location:** `vibe/common/risk/position_sizer.py`

**Strategies:**
- **Fixed Dollar** - Always use $1,000 per trade
- **Percentage of Capital** - Use 2% of account equity per trade
- **Volatility-Adjusted** - Size based on ATR (more volatile = smaller size)
- **Kelly Criterion** - Optimal size based on win rate and average win/loss
- **Risk-Based** - Size to risk fixed $ amount (e.g., risk $100 per trade)

```python
# vibe/common/risk/position_sizer.py
from enum import Enum
from typing import Optional

class PositionSizingMethod(str, Enum):
    FIXED_DOLLAR = "fixed_dollar"
    PERCENT_CAPITAL = "percent_capital"
    VOLATILITY_ADJUSTED = "volatility_adjusted"
    KELLY_CRITERION = "kelly"
    RISK_BASED = "risk_based"

class PositionSizer:
    """Calculate position size based on configured strategy."""

    def __init__(self, config: PositionSizingConfig):
        self.method = config.method
        self.config = config

    def calculate_size(
        self,
        signal: Signal,
        account: AccountState,
        volatility: float,  # ATR in dollars
        current_price: float
    ) -> int:
        """
        Calculate position size (number of shares).

        Args:
            signal: Trading signal
            account: Current account state
            volatility: ATR in dollars (e.g., $2.50 means price moves $2.50 on average)
            current_price: Current price of the symbol

        Returns:
            Number of shares to buy/sell (integer)
        """
        if self.method == PositionSizingMethod.FIXED_DOLLAR:
            return self._fixed_dollar(current_price)

        elif self.method == PositionSizingMethod.PERCENT_CAPITAL:
            return self._percent_capital(account.equity, current_price)

        elif self.method == PositionSizingMethod.VOLATILITY_ADJUSTED:
            return self._volatility_adjusted(account.equity, current_price, volatility)

        elif self.method == PositionSizingMethod.KELLY_CRITERION:
            return self._kelly_criterion(account.equity, current_price, signal)

        elif self.method == PositionSizingMethod.RISK_BASED:
            return self._risk_based(current_price, volatility, signal)

        else:
            raise ValueError(f"Unknown position sizing method: {self.method}")

    def _fixed_dollar(self, price: float) -> int:
        """Use fixed dollar amount per trade."""
        return int(self.config.fixed_amount / price)

    def _percent_capital(self, equity: float, price: float) -> int:
        """Use percentage of account equity."""
        position_value = equity * self.config.percent_per_trade
        return int(position_value / price)

    def _volatility_adjusted(self, equity: float, price: float, atr: float) -> int:
        """
        Adjust position size based on volatility.

        Logic: In high volatility, use smaller positions to maintain consistent risk.

        Formula: position_value = (equity * pct) * (target_volatility / current_volatility)
        """
        base_position_value = equity * self.config.percent_per_trade

        # Calculate volatility as percentage of price
        volatility_pct = atr / price

        # Adjust based on target volatility (e.g., 2% daily moves)
        target_volatility = self.config.target_volatility_pct
        adjustment_factor = min(1.0, target_volatility / volatility_pct)

        adjusted_value = base_position_value * adjustment_factor
        return int(adjusted_value / price)

    def _kelly_criterion(self, equity: float, price: float, signal: Signal) -> int:
        """
        Kelly Criterion: optimal position size based on edge.

        Formula: f* = (p * b - q) / b
        Where:
            f* = fraction of capital to bet
            p = probability of winning
            q = probability of losing (1 - p)
            b = ratio of win amount to loss amount

        Note: Use fractional Kelly (e.g., 0.25 * Kelly) to reduce volatility.
        """
        if not signal.win_probability or not signal.expected_win_loss_ratio:
            # Fall back to percent capital if signal doesn't provide edge info
            return self._percent_capital(equity, price)

        p = signal.win_probability
        q = 1 - p
        b = signal.expected_win_loss_ratio

        kelly_fraction = (p * b - q) / b

        # Use fractional Kelly to reduce risk
        fractional_kelly = kelly_fraction * self.config.kelly_fraction

        # Cap at max percentage
        fraction = min(fractional_kelly, self.config.max_kelly_pct)
        fraction = max(fraction, 0)  # Don't go negative

        position_value = equity * fraction
        return int(position_value / price)

    def _risk_based(self, price: float, atr: float, signal: Signal) -> int:
        """
        Size position to risk fixed dollar amount.

        Logic: If stop loss is 1 ATR away, and we want to risk $100,
        then position size = $100 / (1 * ATR)

        Example: Price = $100, ATR = $2, risk $100, stop = 1 ATR
        Position size = $100 / $2 = 50 shares
        If stopped out, loss = 50 shares * $2 = $100
        """
        # Determine stop distance in dollars
        if signal.stop_loss:
            stop_distance = abs(price - signal.stop_loss)
        else:
            # Default to 2 ATR if no stop specified
            stop_distance = 2 * atr

        # Calculate position size to risk fixed amount
        shares = int(self.config.risk_per_trade_dollars / stop_distance)

        # Cap by maximum position value
        max_shares = int((self.config.max_position_value) / price)
        return min(shares, max_shares)
```

#### 3. StopLossManager

**Location:** `vibe/common/risk/stop_loss.py`

**Stop Types:**
- **ATR-based** - Stop at entry ± N * ATR (e.g., 2 ATR)
- **Percentage-based** - Stop at entry ± X% (e.g., 2%)
- **Technical levels** - Use support/resistance from strategy
- **Trailing stop** - Follow price at fixed distance (ATR or %)
- **Break-even stop** - Move to break-even after target profit
- **Time-based stop** - Exit after max hold time (e.g., 2 hours)

```python
# vibe/common/risk/stop_loss.py
from datetime import datetime, timedelta
from typing import Optional

class StopLossManager:
    """Manage stop loss placement and trailing stops."""

    def __init__(self, config: StopLossConfig):
        self.config = config

    def calculate_stop_loss(
        self,
        entry_price: float,
        side: OrderSide,  # BUY or SELL
        volatility: float,  # ATR
        technical_level: Optional[float] = None
    ) -> float:
        """
        Calculate initial stop loss price.

        Priority:
        1. Technical level from strategy (if provided and within limits)
        2. ATR-based stop (if enabled)
        3. Percentage-based stop (fallback)
        """
        if technical_level and self._is_stop_within_limits(entry_price, technical_level, side):
            return technical_level

        if self.config.use_atr_stops:
            return self._atr_stop(entry_price, side, volatility)
        else:
            return self._percentage_stop(entry_price, side)

    def _atr_stop(self, entry: float, side: OrderSide, atr: float) -> float:
        """Calculate ATR-based stop loss."""
        stop_distance = self.config.atr_multiplier * atr

        if side == OrderSide.BUY:
            return entry - stop_distance
        else:  # SELL
            return entry + stop_distance

    def _percentage_stop(self, entry: float, side: OrderSide) -> float:
        """Calculate percentage-based stop loss."""
        stop_distance_pct = self.config.stop_loss_pct

        if side == OrderSide.BUY:
            return entry * (1 - stop_distance_pct)
        else:  # SELL
            return entry * (1 + stop_distance_pct)

    def update_trailing_stop(
        self,
        position: Position,
        current_price: float,
        volatility: float
    ) -> Optional[float]:
        """
        Update trailing stop if price moved favorably.

        Returns:
            New stop loss price if updated, None if no change
        """
        if not self.config.enable_trailing_stop:
            return None

        # Calculate trailing distance
        if self.config.trailing_stop_type == "atr":
            trail_distance = self.config.trailing_atr_multiplier * volatility
        else:  # percentage
            trail_distance = current_price * self.config.trailing_stop_pct

        if position.side == OrderSide.BUY:
            # Long position: trail stop up
            new_stop = current_price - trail_distance

            if new_stop > position.stop_loss:
                logger.info(
                    f"Trailing stop updated for {position.symbol}: "
                    f"{position.stop_loss:.2f} -> {new_stop:.2f}"
                )
                return new_stop

        else:  # SHORT position
            # Short position: trail stop down
            new_stop = current_price + trail_distance

            if new_stop < position.stop_loss:
                logger.info(
                    f"Trailing stop updated for {position.symbol}: "
                    f"{position.stop_loss:.2f} -> {new_stop:.2f}"
                )
                return new_stop

        return None

    def should_move_to_breakeven(
        self,
        position: Position,
        current_price: float,
        volatility: float
    ) -> bool:
        """
        Check if stop should be moved to break-even.

        Rule: Move to break-even after price moves favorably by N * ATR
        """
        if not self.config.enable_breakeven_stop:
            return False

        # Already at or beyond break-even
        if position.side == OrderSide.BUY and position.stop_loss >= position.entry_price:
            return False
        if position.side == OrderSide.SELL and position.stop_loss <= position.entry_price:
            return False

        # Calculate profit threshold
        breakeven_threshold = self.config.breakeven_trigger_atr * volatility

        if position.side == OrderSide.BUY:
            profit = current_price - position.entry_price
        else:
            profit = position.entry_price - current_price

        return profit >= breakeven_threshold

    def should_time_exit(
        self,
        position: Position,
        current_time: datetime
    ) -> bool:
        """Check if position should be exited due to time limit."""
        if not self.config.enable_time_stops:
            return False

        time_held = current_time - position.entry_time
        max_hold_time = timedelta(hours=self.config.max_hold_hours)

        return time_held >= max_hold_time
```

#### 4. ExposureController

**Location:** `vibe/common/risk/exposure.py`

**Limits:**
- Max number of concurrent positions (e.g., 3)
- Max total portfolio exposure (e.g., 80% of capital)
- Max exposure per symbol (e.g., 30% of capital)
- Sector/industry concentration limits
- Correlation limits (avoid highly correlated positions)

```python
# vibe/common/risk/exposure.py
from typing import List, Dict

class ExposureController:
    """Control portfolio-level exposure and diversification."""

    def __init__(self, config: ExposureConfig):
        self.config = config

    def can_add_position(
        self,
        symbol: str,
        current_positions: List[Position],
        account: AccountState
    ) -> bool:
        """
        Check if new position would violate exposure limits.

        Returns:
            True if position can be added, False otherwise
        """
        # Check max number of positions
        if len(current_positions) >= self.config.max_positions:
            logger.warning(f"Cannot add position: max positions ({self.config.max_positions}) reached")
            return False

        # Check total exposure
        total_exposure = sum(p.market_value for p in current_positions)
        max_total_exposure = account.equity * self.config.max_total_exposure_pct

        if total_exposure >= max_total_exposure:
            logger.warning(
                f"Cannot add position: total exposure ${total_exposure:.0f} "
                f"exceeds limit ${max_total_exposure:.0f}"
            )
            return False

        # Check if already have position in this symbol
        if any(p.symbol == symbol for p in current_positions):
            logger.warning(f"Cannot add position: already have position in {symbol}")
            return False

        # Check sector concentration (if enabled)
        if self.config.enable_sector_limits:
            if not self._check_sector_limits(symbol, current_positions, account):
                return False

        # Check correlation limits (if enabled)
        if self.config.enable_correlation_limits:
            if not self._check_correlation_limits(symbol, current_positions):
                return False

        return True

    def _check_sector_limits(
        self,
        symbol: str,
        current_positions: List[Position],
        account: AccountState
    ) -> bool:
        """Check if adding position would violate sector concentration limits."""
        # Get sector for symbol (would need sector mapping data)
        new_sector = self._get_sector(symbol)

        # Calculate current sector exposure
        sector_exposure = sum(
            p.market_value for p in current_positions
            if self._get_sector(p.symbol) == new_sector
        )

        max_sector_exposure = account.equity * self.config.max_sector_exposure_pct

        if sector_exposure >= max_sector_exposure:
            logger.warning(
                f"Cannot add {symbol}: sector {new_sector} exposure "
                f"${sector_exposure:.0f} exceeds limit ${max_sector_exposure:.0f}"
            )
            return False

        return True

    def _check_correlation_limits(
        self,
        symbol: str,
        current_positions: List[Position]
    ) -> bool:
        """
        Check if new position is too correlated with existing positions.

        Rule: Don't hold more than N positions with correlation > X
        (e.g., max 2 positions with correlation > 0.7)
        """
        # Calculate correlation with existing positions
        highly_correlated_count = 0

        for position in current_positions:
            correlation = self._get_correlation(symbol, position.symbol)

            if correlation > self.config.correlation_threshold:
                highly_correlated_count += 1

        if highly_correlated_count >= self.config.max_correlated_positions:
            logger.warning(
                f"Cannot add {symbol}: already have {highly_correlated_count} "
                f"positions with correlation > {self.config.correlation_threshold}"
            )
            return False

        return True

    def _get_sector(self, symbol: str) -> str:
        """Get sector for symbol (simplified - would use real sector data)."""
        # This would query a sector mapping database or API
        # For MVP, could use a simple dict or yfinance info
        sector_map = {
            "AAPL": "Technology",
            "MSFT": "Technology",
            "GOOGL": "Technology",
            "AMZN": "Consumer Cyclical",
            "TSLA": "Consumer Cyclical",
        }
        return sector_map.get(symbol, "Unknown")

    def _get_correlation(self, symbol1: str, symbol2: str) -> float:
        """
        Get correlation coefficient between two symbols.

        Returns correlation from recent price history (e.g., 60 days).
        """
        # This would calculate correlation from historical returns
        # For MVP, could use pre-calculated correlation matrix or approximate
        # Real implementation would use pandas correlation on returns data

        # Simplified example (would need actual price history)
        tech_stocks = {"AAPL", "MSFT", "GOOGL"}

        if symbol1 in tech_stocks and symbol2 in tech_stocks:
            return 0.8  # Tech stocks highly correlated
        elif symbol1 == symbol2:
            return 1.0  # Perfect correlation with self
        else:
            return 0.3  # Low correlation
```

#### 5. DrawdownProtector

**Location:** `vibe/common/risk/drawdown.py`

**Protections:**
- Max drawdown circuit breaker (stop trading at -10% drawdown)
- Daily loss limit (stop trading after losing $500 or 5% in one day)
- Consecutive losses limit (stop after 5 losses in a row)
- Volatility regime detection (reduce sizing in high volatility)
- Cool-off period after hitting limits (resume next day)

```python
# vibe/common/risk/drawdown.py
from datetime import datetime, timedelta

class DrawdownProtector:
    """Protect against excessive losses through circuit breakers."""

    def __init__(self, config: DrawdownConfig):
        self.config = config
        self.trading_paused_until: Optional[datetime] = None

    def can_trade(
        self,
        daily_pnl: float,
        consecutive_losses: int,
        current_drawdown: float
    ) -> bool:
        """
        Check if trading is allowed based on loss limits.

        Args:
            daily_pnl: P&L for current day (negative = loss)
            consecutive_losses: Number of consecutive losing trades
            current_drawdown: Current drawdown from peak equity (0.0 to 1.0)

        Returns:
            True if trading allowed, False if paused
        """
        # Check if still in cool-off period
        if self.trading_paused_until:
            if datetime.now() < self.trading_paused_until:
                logger.warning(
                    f"Trading paused until {self.trading_paused_until} "
                    f"due to risk limits"
                )
                return False
            else:
                # Cool-off period ended
                logger.info("Cool-off period ended, resuming trading")
                self.trading_paused_until = None

        # Check max drawdown
        if current_drawdown > self.config.max_drawdown_pct:
            logger.error(
                f"⛔ CIRCUIT BREAKER: Max drawdown {current_drawdown:.1%} "
                f"exceeded limit {self.config.max_drawdown_pct:.1%}"
            )
            self._pause_trading(reason="max_drawdown")
            return False

        # Check daily loss limit
        if daily_pnl < -self.config.daily_loss_limit_dollars:
            logger.error(
                f"⛔ CIRCUIT BREAKER: Daily loss ${-daily_pnl:.2f} "
                f"exceeded limit ${self.config.daily_loss_limit_dollars:.2f}"
            )
            self._pause_trading(reason="daily_loss_limit")
            return False

        # Check consecutive losses
        if consecutive_losses >= self.config.max_consecutive_losses:
            logger.error(
                f"⛔ CIRCUIT BREAKER: {consecutive_losses} consecutive losses "
                f"exceeded limit {self.config.max_consecutive_losses}"
            )
            self._pause_trading(reason="consecutive_losses")
            return False

        return True

    def _pause_trading(self, reason: str) -> None:
        """Pause trading until cool-off period expires."""
        cool_off_hours = self.config.cooloff_hours
        self.trading_paused_until = datetime.now() + timedelta(hours=cool_off_hours)

        logger.error(
            f"🛑 Trading paused due to {reason}. "
            f"Will resume at {self.trading_paused_until}"
        )

        # Send alert notification (Discord, email, etc.)
        # notification_service.send_alert(
        #     title="Trading Paused - Risk Limit Hit",
        #     message=f"Reason: {reason}\nResume time: {self.trading_paused_until}",
        #     priority="HIGH"
        # )

    def reset_daily_limits(self) -> None:
        """Reset daily tracking at end of trading day."""
        # Daily limits reset automatically
        # Only clear pause if it was due to daily limits
        if self.trading_paused_until:
            logger.info("Daily limits reset, clearing trading pause if applicable")
            self.trading_paused_until = None
```

### Configuration Schema

```yaml
# config/risk_management.yaml
risk_management:
  # Enable/disable risk management entirely
  enabled: true

  # Position Sizing
  position_sizing:
    method: "volatility_adjusted"  # fixed_dollar, percent_capital, volatility_adjusted, kelly, risk_based

    # Fixed dollar method
    fixed_amount: 1000  # Use $1,000 per trade

    # Percent capital method
    percent_per_trade: 0.02  # 2% of capital per trade

    # Volatility-adjusted method
    target_volatility_pct: 0.02  # Target 2% daily volatility

    # Kelly criterion method
    kelly_fraction: 0.25  # Use 25% of full Kelly (conservative)
    max_kelly_pct: 0.10  # Never exceed 10% of capital

    # Risk-based method
    risk_per_trade_dollars: 100  # Risk $100 per trade
    max_position_value: 5000  # Max $5,000 per position

  # Stop Loss Management
  stop_loss:
    use_atr_stops: true
    atr_multiplier: 2.0  # Stop at 2 ATR from entry
    stop_loss_pct: 0.02  # 2% stop if ATR not available

    # Trailing stops
    enable_trailing_stop: true
    trailing_stop_type: "atr"  # atr or percentage
    trailing_atr_multiplier: 2.5  # Trail at 2.5 ATR
    trailing_stop_pct: 0.03  # Or trail at 3%

    # Break-even stops
    enable_breakeven_stop: true
    breakeven_trigger_atr: 1.0  # Move to break-even after 1 ATR profit

    # Time-based stops
    enable_time_stops: true
    max_hold_hours: 2  # Day trading: max 2 hours per position

  # Take Profit Management
  take_profit:
    use_atr_targets: true
    atr_multiplier: 3.0  # Target at 3 ATR (1.5:1 risk/reward if stop is 2 ATR)

    # Or use percentage
    take_profit_pct: 0.05  # 5% profit target

  # Risk/Reward Validation
  min_risk_reward_ratio: 1.5  # Require at least 1.5:1 risk/reward

  # Position Limits (Layer 2)
  max_position_pct: 0.50  # Max 50% of capital in one position
  max_position_dollars: 5000  # Max $5,000 in one position

  # Portfolio Exposure (Layer 3)
  exposure:
    max_positions: 3  # Max 3 concurrent positions
    max_total_exposure_pct: 0.80  # Use at most 80% of capital

    # Sector limits
    enable_sector_limits: true
    max_sector_exposure_pct: 0.50  # Max 50% in one sector

    # Correlation limits
    enable_correlation_limits: true
    correlation_threshold: 0.7  # Correlation above 0.7 is "high"
    max_correlated_positions: 2  # Max 2 positions with correlation > 0.7

  # Drawdown Protection (Layer 4)
  drawdown:
    max_drawdown_pct: 0.10  # Stop trading at 10% drawdown
    daily_loss_limit_dollars: 500  # Stop after losing $500 in one day
    daily_loss_limit_pct: 0.05  # Or 5% of capital
    max_consecutive_losses: 5  # Stop after 5 losses in a row
    cooloff_hours: 24  # Pause trading for 24 hours after hitting limit

  # Volatility regime detection
  volatility_scaling:
    enabled: true
    high_volatility_threshold: 0.03  # 3% daily ATR is "high volatility"
    size_reduction_factor: 0.5  # Reduce position size by 50% in high volatility
```

### Integration with Strategy (ORBStrategy)

```python
# vibe/common/strategies/orb.py (updated)
class ORBStrategy(StrategyBase):
    """Opening Range Breakout strategy with integrated risk management."""

    def __init__(self, config: ORBConfig, risk_manager: RiskManager):
        super().__init__(config)
        self.risk_manager = risk_manager

    async def generate_signals(self, data: Dict[str, pd.DataFrame]) -> List[Signal]:
        """Generate trading signals with risk management integration."""
        signals = []

        for symbol, df in data.items():
            # ... existing ORB logic to detect breakout ...

            if breakout_detected:
                # Create preliminary signal
                signal = Signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    signal_type=SignalType.ENTRY,
                    entry_price=current_price,
                    stop_loss=orb_low,  # Support level from ORB
                    take_profit=orb_high + (orb_high - orb_low),  # 1:1 risk/reward
                    strategy="ORB",
                    timeframe="5m",
                    confidence=0.8,
                    # Provide edge info for Kelly sizing
                    win_probability=0.55,  # Based on backtest
                    expected_win_loss_ratio=1.8  # Based on backtest
                )

                # Validate signal with risk manager
                risk_check = await self.risk_manager.validate_trade(
                    signal=signal,
                    current_positions=self.positions,
                    current_price=current_price,
                    volatility=self._calculate_atr(df)
                )

                if risk_check.approved:
                    # Update signal with risk manager adjustments
                    signal.quantity = risk_check.adjusted_quantity
                    signal.stop_loss = risk_check.stop_loss_price
                    signal.take_profit = risk_check.take_profit_price
                    signals.append(signal)
                else:
                    logger.warning(
                        f"Signal for {symbol} rejected by risk manager: {risk_check.reason}"
                    )

        return signals
```

---

## Service Health Monitoring Dashboard

### Overview

A **cloud-hosted dashboard** for monitoring the trading bot's operational health in real-time, accessible from anywhere without SSH access to the server.

**Key Metrics:**
- Service uptime and restart history
- WebSocket connection status (Finnhub)
- Data feed health (last bar time, gaps detected)
- System resources (CPU, memory, disk)
- Error rates and recent errors
- Order execution latency
- API response times

### Architecture

```
+===========================================================================+
|                    SERVICE HEALTH MONITORING STACK                         |
+===========================================================================+
|                                                                            |
|  +--------------------------+        +--------------------------+         |
|  |  Trading Bot             |        |  Health API              |         |
|  |  (Oracle Cloud VM)       |        |  (FastAPI)               |         |
|  |                          |        |  Port 8080               |         |
|  |  - Health collector      |------->|                          |         |
|  |  - Metrics exporter      |        |  GET /health/live        |         |
|  |  - Resource monitor      |        |  GET /health/ready       |         |
|  |  - Error tracker         |        |  GET /health/detailed    |         |
|  +--------------------------+        |  GET /metrics/prometheus |         |
|               |                      +--------------------------+         |
|               |                                   |                       |
|               v                                   v                       |
|  +--------------------------+        +--------------------------+         |
|  |  SQLite Database         |        |  WebSocket Server        |         |
|  |                          |        |  (real-time updates)     |         |
|  |  - health_snapshots      |        |                          |         |
|  |  - error_log             |        |  ws://api:8080/ws/health |         |
|  |  - metrics_history       |        +--------------------------+         |
|  +--------------------------+                     |                       |
|                                                   |                       |
|                                                   v                       |
|                                      +--------------------------+         |
|                                      |  Streamlit Dashboard     |         |
|                                      |  (Streamlit Cloud)       |         |
|                                      |                          |         |
|                                      |  https://your-app        |         |
|                                      |    .streamlit.app        |         |
|                                      +--------------------------+         |
|                                                                            |
+===========================================================================+
```

### Dashboard Pages

#### Page 1: System Overview

**Layout:**
```
+------------------------------------------------------------------+
|  🟢 Bot Status: RUNNING                     Last Update: 14:32:05 |
+------------------------------------------------------------------+
|                                                                   |
|  Uptime: 3d 14h 22m        |  Restart Count: 0                   |
|  Start Time: 2026-02-17    |  Last Restart: N/A                  |
+------------------------------------------------------------------+
|                     COMPONENT HEALTH STATUS                       |
+------------------------------------------------------------------+
|  Component              |  Status      |  Last Check             |
|  ---------------------|-------------|------------------------- |
|  🟢 WebSocket (Finnhub) |  Connected   |  2s ago                 |
|  🟢 Data Manager        |  Healthy     |  5s ago                 |
|  🟢 Order Manager       |  Healthy     |  10s ago                |
|  🟢 Strategy Engine     |  Healthy     |  15s ago                |
|  🟢 Risk Manager        |  Healthy     |  20s ago                |
|  🟢 Discord Notifier    |  Healthy     |  30s ago                |
+------------------------------------------------------------------+
|                      SYSTEM RESOURCES                             |
+------------------------------------------------------------------+
|  CPU Usage: ▓▓▓▓▓▓░░░░ 42%                                       |
|  Memory: ▓▓▓▓▓▓░░░░ 1.2GB / 2.0GB (60%)                          |
|  Disk: ▓░░░░░░░░░ 2.3GB / 20GB (12%)                             |
+------------------------------------------------------------------+
|                      DATA FEED HEALTH                             |
+------------------------------------------------------------------+
|  Symbol  |  Last Bar Time      |  Age      |  Status             |
|  --------|---------------------|-----------|-------------------- |
|  AAPL    |  14:30:00           |  2m 5s    |  🟢 Current         |
|  MSFT    |  14:30:00           |  2m 5s    |  🟢 Current         |
|  GOOGL   |  14:25:00           |  7m 5s    |  🟡 Stale (>5min)   |
+------------------------------------------------------------------+
```

**Implementation:**

```python
# vibe/dashboard/pages/01_system_health.py
import streamlit as st
import requests
from datetime import datetime

API_BASE = st.secrets["API_BASE"]

st.set_page_config(page_title="System Health", page_icon="🏥", layout="wide")

st.title("🏥 System Health Dashboard")

# Auto-refresh
if st.checkbox("Auto-refresh (5s)", value=True):
    time.sleep(5)
    st.rerun()

# Fetch health data
health = requests.get(f"{API_BASE}/health/detailed").json()

# Row 1: Overall Status
col1, col2 = st.columns([3, 1])
with col1:
    status_color = "🟢" if health["status"] == "healthy" else "🔴"
    st.metric(
        f"{status_color} Bot Status",
        health["status"].upper(),
        delta=f"Uptime: {health['uptime_human']}"
    )
with col2:
    st.metric("Last Update", datetime.now().strftime("%H:%M:%S"))

# Row 2: Basic Metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Uptime", health["uptime_human"])
col2.metric("Start Time", health["started_at"].split("T")[0])
col3.metric("Restart Count", health["restart_count"])
col4.metric("Last Restart", health.get("last_restart", "N/A"))

# Row 3: Component Health
st.markdown("### Component Health Status")
components_df = pd.DataFrame(health["components"])
components_df["status_icon"] = components_df["status"].map({
    "healthy": "🟢",
    "degraded": "🟡",
    "unhealthy": "🔴"
})
st.dataframe(components_df, use_container_width=True)

# Row 4: System Resources
st.markdown("### System Resources")
col1, col2, col3 = st.columns(3)

with col1:
    cpu_pct = health["system"]["cpu_percent"]
    st.metric("CPU Usage", f"{cpu_pct:.1f}%")
    st.progress(cpu_pct / 100)

with col2:
    mem_used = health["system"]["memory_mb"]
    mem_total = health["system"]["memory_total_mb"]
    mem_pct = (mem_used / mem_total) * 100
    st.metric("Memory", f"{mem_used:.0f}MB / {mem_total:.0f}MB")
    st.progress(mem_pct / 100)

with col3:
    disk_used = health["system"]["disk_gb"]
    disk_total = health["system"]["disk_total_gb"]
    disk_pct = (disk_used / disk_total) * 100
    st.metric("Disk", f"{disk_used:.1f}GB / {disk_total:.1f}GB")
    st.progress(disk_pct / 100)

# Row 5: Data Feed Health
st.markdown("### Data Feed Health")
data_health = health["data_feed"]

df_data = pd.DataFrame(data_health["symbols"])
df_data["status_icon"] = df_data.apply(
    lambda row: "🟢" if row["age_seconds"] < 300 else "🟡" if row["age_seconds"] < 600 else "🔴",
    axis=1
)
st.dataframe(df_data, use_container_width=True)
```

#### Page 2: Error Tracking

**Layout:**
```
+------------------------------------------------------------------+
|  ERROR TRACKING                                                   |
+------------------------------------------------------------------+
|  Error Rate (1h): ▓░░░░░░░░░ 0.2/min                             |
|  Total Errors (24h): 12                                           |
+------------------------------------------------------------------+
|  RECENT ERRORS (Last 50)                                          |
+------------------------------------------------------------------+
|  Time      |  Severity  |  Component      |  Message             |
|  ----------|-----------|----------------|---------------------- |
|  14:25:32  |  ERROR    |  FinnhubWS     |  Connection timeout   |
|  14:22:18  |  WARNING  |  DataManager   |  Cache stale for GOOGL|
|  14:15:44  |  ERROR    |  OrderManager  |  Retry limit exceeded |
+------------------------------------------------------------------+
|  ERROR DISTRIBUTION (Last 24h)                                    |
+------------------------------------------------------------------+
|  [Bar chart showing errors by component]                          |
|  [Line chart showing errors over time]                            |
+------------------------------------------------------------------+
```

#### Page 3: Performance Metrics

**Layout:**
```
+------------------------------------------------------------------+
|  PERFORMANCE METRICS                                              |
+------------------------------------------------------------------+
|  Order Execution Latency                                          |
|  p50: 125ms  |  p95: 450ms  |  p99: 890ms                        |
|  [Histogram of execution times]                                   |
+------------------------------------------------------------------+
|  Data Processing Latency                                          |
|  Bar aggregation: 15ms  |  Indicator calc: 22ms                  |
|  [Line chart over time]                                           |
+------------------------------------------------------------------+
|  API Response Times                                               |
|  /health: 5ms  |  /trades: 28ms  |  /positions: 12ms             |
|  [Percentile chart]                                               |
+------------------------------------------------------------------+
```

### Health API Endpoints

```python
# vibe/trading_bot/api/health.py (expanded)
from fastapi import FastAPI
from typing import Dict, List
import psutil
from datetime import datetime, timedelta

app = FastAPI()

@app.get("/health/detailed")
async def health_detailed() -> Dict:
    """Comprehensive health check with all metrics."""

    # Get process info
    process = psutil.Process()
    started_at = datetime.fromtimestamp(process.create_time())
    uptime_seconds = (datetime.now() - started_at).total_seconds()

    return {
        "status": "healthy" if _all_components_healthy() else "degraded",
        "timestamp": datetime.now().isoformat(),
        "started_at": started_at.isoformat(),
        "uptime_seconds": uptime_seconds,
        "uptime_human": _format_uptime(uptime_seconds),
        "restart_count": _get_restart_count(),
        "last_restart": _get_last_restart(),

        # Component health
        "components": _get_component_health(),

        # System resources
        "system": {
            "cpu_percent": process.cpu_percent(interval=0.1),
            "memory_mb": process.memory_info().rss / 1024 / 1024,
            "memory_total_mb": psutil.virtual_memory().total / 1024 / 1024,
            "disk_gb": psutil.disk_usage('/').used / 1024 / 1024 / 1024,
            "disk_total_gb": psutil.disk_usage('/').total / 1024 / 1024 / 1024,
            "thread_count": process.num_threads(),
            "open_files": len(process.open_files()),
        },

        # Data feed health
        "data_feed": _get_data_feed_health(),

        # Error tracking
        "errors": {
            "last_hour_count": _get_error_count(hours=1),
            "last_24h_count": _get_error_count(hours=24),
            "error_rate_per_minute": _get_error_rate(),
        }
    }

def _get_component_health() -> List[Dict]:
    """Get health status of all components."""
    return [
        {
            "name": "WebSocket (Finnhub)",
            "status": "healthy" if finnhub_ws.connected else "unhealthy",
            "last_check": datetime.now().isoformat(),
            "details": f"Connected: {finnhub_ws.connected}, Last message: {finnhub_ws.last_message_time}"
        },
        {
            "name": "Data Manager",
            "status": "healthy" if data_manager.is_healthy() else "degraded",
            "last_check": datetime.now().isoformat(),
            "details": f"Cache entries: {data_manager.cache.size()}"
        },
        {
            "name": "Order Manager",
            "status": "healthy",
            "last_check": datetime.now().isoformat(),
            "details": f"Pending orders: {order_manager.pending_count()}"
        },
        # ... other components
    ]

def _get_data_feed_health() -> Dict:
    """Get health status of data feeds."""
    symbols_health = []

    for symbol in trading_config.symbols:
        last_bar = data_manager.get_last_bar(symbol)
        if last_bar:
            age_seconds = (datetime.now() - last_bar.timestamp).total_seconds()
            status = "current" if age_seconds < 300 else "stale" if age_seconds < 600 else "dead"
        else:
            age_seconds = None
            status = "no_data"

        symbols_health.append({
            "symbol": symbol,
            "last_bar_time": last_bar.timestamp.isoformat() if last_bar else None,
            "age_seconds": age_seconds,
            "status": status
        })

    return {
        "symbols": symbols_health,
        "websocket_connected": finnhub_ws.connected,
        "last_reconnect": finnhub_ws.last_reconnect_time,
    }

@app.get("/errors/recent")
async def get_recent_errors(limit: int = 50) -> List[Dict]:
    """Get recent errors from log."""
    return error_tracker.get_recent_errors(limit=limit)

@app.get("/metrics/performance")
async def get_performance_metrics() -> Dict:
    """Get performance metrics (latency, throughput)."""
    return {
        "order_execution": {
            "p50_ms": metrics.get_percentile("order_latency", 0.50),
            "p95_ms": metrics.get_percentile("order_latency", 0.95),
            "p99_ms": metrics.get_percentile("order_latency", 0.99),
            "avg_ms": metrics.get_avg("order_latency"),
        },
        "data_processing": {
            "bar_aggregation_ms": metrics.get_avg("bar_aggregation_latency"),
            "indicator_calc_ms": metrics.get_avg("indicator_calc_latency"),
            "signal_generation_ms": metrics.get_avg("signal_generation_latency"),
        },
        "api_response_times": {
            endpoint: metrics.get_avg(f"api_{endpoint}_latency")
            for endpoint in ["health", "trades", "positions", "metrics"]
        }
    }
```

---

## Trading Performance Dashboard

### Overview

A **cloud-hosted dashboard** for monitoring trades, positions, P&L, and strategy performance metrics in real-time.

**Key Features:**
- Real-time open positions with unrealized P&L
- Trade history with filtering and sorting
- Cumulative P&L chart
- Performance metrics (win rate, Sharpe ratio, drawdown)
- Risk metrics (exposure, position sizes, stop distances)
- Strategy-specific metrics (ORB success rate, avg hold time)

### Architecture

Same as Service Health Dashboard (shares FastAPI backend, uses different Streamlit pages).

### Dashboard Pages

#### Page 1: Positions & Account

**Layout:**
```
+------------------------------------------------------------------+
|  ACCOUNT SUMMARY                                                  |
+------------------------------------------------------------------+
|  Cash: $8,245.32    |  Equity: $10,128.50    |  P&L: +$128.50   |
|  Daily P&L: +$52.18 (+0.52%)    |  Total Trades: 15             |
+------------------------------------------------------------------+
|  OPEN POSITIONS (2)                                               |
+------------------------------------------------------------------+
|  Symbol |  Side  |  Qty  |  Entry   |  Current |  P&L    |  %    |
|  -------|--------|-------|----------|----------|---------|------ |
|  AAPL   |  LONG  |  10   |  $145.20 |  $147.80 |  +$26.00|  +1.8%|
|  MSFT   |  LONG  |  5    |  $380.50 |  $385.12 |  +$23.10|  +1.2%|
+------------------------------------------------------------------+
|  Position Details (expandable)                                    |
|  AAPL:                                                            |
|    Entry Time: 9:35:22                                            |
|    Stop Loss: $143.10 (-1.4%)                                     |
|    Take Profit: $149.50 (+3.0%)                                   |
|    Hold Time: 1h 22m                                              |
|    Risk Amount: $21.00                                            |
|    R/R Ratio: 2.05:1                                              |
+------------------------------------------------------------------+
```

#### Page 2: Trade History

**Layout:**
```
+------------------------------------------------------------------+
|  TRADE HISTORY                                                    |
+------------------------------------------------------------------+
|  Filters: [Symbol: All ▼]  [Status: All ▼]  [Date: Last 7 days ▼]|
+------------------------------------------------------------------+
|  Entry Time  |  Symbol  |  Side  |  Qty  |  Entry  |  Exit  |  P&L |
|  ------------|----------|--------|-------|---------|--------|---------|
|  14:22:18    |  AAPL    |  LONG  |  10   |  $145.20|  $147.50|  +$23.00|
|  13:55:32    |  MSFT    |  LONG  |  5    |  $380.00|  $378.50|  -$7.50 |
|  11:42:05    |  GOOGL   |  LONG  |  8    |  $142.80|  $145.20|  +$19.20|
+------------------------------------------------------------------+
|  [Pagination: << 1 2 3 4 5 >>]                                    |
+------------------------------------------------------------------+
|  Trade Details (click to expand)                                  |
|  AAPL @ 14:22:18:                                                 |
|    Strategy: ORB                                                  |
|    Entry Reason: Breakout above $145.00 with volume              |
|    Exit Reason: Take profit hit at $147.50                        |
|    Hold Time: 45 minutes                                          |
|    Max Adverse Excursion: -0.3%                                   |
|    Max Favorable Excursion: +1.8%                                 |
+------------------------------------------------------------------+
```

#### Page 3: Performance Analytics

**Layout:**
```
+------------------------------------------------------------------+
|  PERFORMANCE METRICS                                              |
+------------------------------------------------------------------+
|  Win Rate: 60.0%    |  Avg Win: $45.20  |  Avg Loss: -$22.10   |
|  Profit Factor: 2.05|  Expectancy: $16.32 |  Sharpe: 1.42      |
|  Max Drawdown: -8.2%|  Recovery: 3 days   |  Current DD: -2.1% |
+------------------------------------------------------------------+
|  CUMULATIVE P&L                                                   |
+------------------------------------------------------------------+
|  [Line chart showing cumulative P&L over time]                    |
|  [Drawdown chart below]                                           |
+------------------------------------------------------------------+
|  TRADE DISTRIBUTION                                               |
+------------------------------------------------------------------+
|  [Histogram of P&L per trade]                                     |
|  [Scatter plot: Hold Time vs P&L]                                 |
+------------------------------------------------------------------+
|  RISK METRICS                                                     |
+------------------------------------------------------------------+
|  Avg Position Size: $1,850  |  Max Position: $3,200              |
|  Avg Stop Distance: 1.8%    |  Avg R/R Ratio: 2.1:1              |
|  Portfolio Utilization: 65% |  Max Concurrent: 3 positions       |
+------------------------------------------------------------------+
```

#### Page 4: Strategy-Specific Metrics (ORB)

**Layout:**
```
+------------------------------------------------------------------+
|  ORB STRATEGY PERFORMANCE                                         |
+------------------------------------------------------------------+
|  Total ORB Trades: 25       |  Win Rate: 64.0%                   |
|  Avg Opening Range: $1.85   |  Breakout Success: 72%             |
|  Avg Hold Time: 52 minutes  |  Max Hold: 2h 15m                  |
+------------------------------------------------------------------+
|  ORB STATISTICS BY TIME                                           |
+------------------------------------------------------------------+
|  Best Entry Time: 9:35-9:40 (75% win rate)                        |
|  Worst Entry Time: 9:50-10:00 (40% win rate)                      |
+------------------------------------------------------------------+
|  [Bar chart: Win rate by entry time bucket]                       |
|  [Heatmap: Symbol x Entry Time -> Win Rate]                       |
+------------------------------------------------------------------+
|  ORB BREAKDOWN BY SYMBOL                                          |
+------------------------------------------------------------------+
|  Symbol  |  Trades  |  Win %  |  Avg P&L  |  Best Trade           |
|  --------|----------|---------|-----------|----------------------|
|  AAPL    |  8       |  75.0%  |  +$32.50  |  +$85.20 (2/18)      |
|  MSFT    |  6       |  66.7%  |  +$28.10  |  +$62.00 (2/17)      |
|  GOOGL   |  5       |  40.0%  |  -$5.20   |  +$45.80 (2/15)      |
+------------------------------------------------------------------+
```

### Trading API Endpoints

```python
# vibe/trading_bot/api/trading.py
from fastapi import FastAPI, Query
from typing import List, Optional
from datetime import datetime, timedelta

app = FastAPI()

@app.get("/account")
async def get_account() -> Dict:
    """Get current account summary."""
    return {
        "cash": exchange.cash,
        "equity": exchange.get_equity(),
        "unrealized_pnl": exchange.get_unrealized_pnl(),
        "daily_pnl": metrics.get_daily_pnl(),
        "daily_pnl_pct": metrics.get_daily_pnl() / exchange.get_equity(),
        "total_trades": trade_store.count_trades(),
        "open_positions_count": len(position_manager.get_all_positions()),
    }

@app.get("/positions")
async def get_positions() -> List[Dict]:
    """Get current open positions with details."""
    positions = []

    for position in position_manager.get_all_positions():
        current_price = data_manager.get_current_price(position.symbol)

        positions.append({
            "symbol": position.symbol,
            "side": position.side,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "current_price": current_price,
            "unrealized_pnl": position.calculate_unrealized_pnl(current_price),
            "unrealized_pnl_pct": (current_price - position.entry_price) / position.entry_price,
            "entry_time": position.entry_time.isoformat(),
            "hold_time_seconds": (datetime.now() - position.entry_time).total_seconds(),
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "risk_amount": abs(position.entry_price - position.stop_loss) * position.quantity,
            "risk_reward_ratio": position.calculate_risk_reward_ratio() if position.take_profit else None,
        })

    return positions

@app.get("/trades")
async def get_trades(
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
) -> Dict:
    """Get trade history with filtering and pagination."""

    trades = trade_store.get_trades(
        symbol=symbol,
        status=status,
        start_date=datetime.fromisoformat(start_date) if start_date else None,
        end_date=datetime.fromisoformat(end_date) if end_date else None,
        limit=limit,
        offset=offset
    )

    total_count = trade_store.count_trades(
        symbol=symbol,
        status=status,
        start_date=datetime.fromisoformat(start_date) if start_date else None,
        end_date=datetime.fromisoformat(end_date) if end_date else None
    )

    return {
        "trades": [trade.dict() for trade in trades],
        "total_count": total_count,
        "page": offset // limit + 1,
        "total_pages": (total_count + limit - 1) // limit
    }

@app.get("/performance/metrics")
async def get_performance_metrics(period: str = "all") -> Dict:
    """Get performance metrics for specified period."""

    # period: "today", "week", "month", "all"
    trades = trade_store.get_trades_for_period(period)

    if not trades:
        return {"error": "No trades in period"}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl < 0]

    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

    total_wins = sum(t.pnl for t in wins)
    total_losses = abs(sum(t.pnl for t in losses))
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    # Calculate Sharpe ratio
    returns = [t.pnl / t.entry_value for t in trades]
    sharpe_ratio = (sum(returns) / len(returns)) / (pd.Series(returns).std()) * np.sqrt(252)

    # Calculate max drawdown
    equity_curve = metrics.calculate_equity_curve(trades)
    max_drawdown, max_drawdown_pct, recovery_days = metrics.calculate_max_drawdown(equity_curve)

    return {
        "total_trades": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": metrics.calculate_sortino_ratio(returns),
        "max_drawdown_dollars": max_drawdown,
        "max_drawdown_pct": max_drawdown_pct,
        "recovery_days": recovery_days,
        "current_drawdown_pct": metrics.get_current_drawdown_pct(),
        "total_pnl": sum(t.pnl for t in trades),
        "total_return_pct": sum(t.pnl for t in trades) / initial_capital,
    }

@app.get("/performance/equity_curve")
async def get_equity_curve(period: str = "all") -> Dict:
    """Get equity curve data for charting."""
    trades = trade_store.get_trades_for_period(period)
    equity_curve = metrics.calculate_equity_curve(trades)

    return {
        "timestamps": [point["timestamp"].isoformat() for point in equity_curve],
        "equity": [point["equity"] for point in equity_curve],
        "cash": [point["cash"] for point in equity_curve],
        "unrealized_pnl": [point["unrealized_pnl"] for point in equity_curve],
    }

@app.get("/strategy/orb/metrics")
async def get_orb_metrics() -> Dict:
    """Get ORB strategy-specific metrics."""
    orb_trades = trade_store.get_trades(strategy="ORB")

    if not orb_trades:
        return {"error": "No ORB trades found"}

    return {
        "total_trades": len(orb_trades),
        "win_rate": len([t for t in orb_trades if t.pnl > 0]) / len(orb_trades),
        "avg_opening_range": metrics.calculate_avg_orb_range(orb_trades),
        "breakout_success_rate": metrics.calculate_breakout_success_rate(orb_trades),
        "avg_hold_time_minutes": metrics.calculate_avg_hold_time(orb_trades),
        "max_hold_time_minutes": max(metrics.get_hold_times(orb_trades)),

        # By time bucket
        "win_rate_by_entry_time": metrics.calculate_win_rate_by_entry_time(orb_trades),

        # By symbol
        "performance_by_symbol": {
            symbol: metrics.calculate_performance_for_symbol(orb_trades, symbol)
            for symbol in set(t.symbol for t in orb_trades)
        },
    }
```

---

## Technology Stack

### Backend (Trading Bot + API)

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Core Bot | Python 3.11 + asyncio | Trading logic, event loop |
| API Framework | FastAPI | REST API for dashboards |
| WebSocket | FastAPI WebSocket | Real-time dashboard updates |
| Database | SQLite | Trade history, metrics, errors |
| Data Validation | Pydantic | Type-safe configuration and models |
| Metrics | Custom + psutil | Performance and resource monitoring |

### Frontend (Dashboards)

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Dashboard Framework | Streamlit | Python-based UI framework |
| Hosting | Streamlit Community Cloud | Free hosting for Streamlit apps |
| Charts | Plotly | Interactive charts and graphs |
| Data Processing | pandas + numpy | Data manipulation for display |
| WebSocket Client | streamlit-autorefresh | Real-time data updates |

### Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Compute | Oracle Cloud (ARM) | Free tier VM for trading bot |
| Container | Docker + docker-compose | Deployment and orchestration |
| Reverse Proxy | Nginx (optional) | HTTPS, rate limiting, CORS |
| Monitoring | Custom health checks | Liveness/readiness probes |
| Secrets | .env files | API keys and credentials |

---

## Integration with Phase 1

### Code Changes Required

#### 1. Add RiskManager to Orchestrator

```python
# vibe/trading_bot/core/orchestrator.py
from vibe.common.risk import RiskManager

class TradingOrchestrator:
    def __init__(self, config: Config):
        # ... existing initialization ...

        # NEW: Initialize risk manager
        self.risk_manager = RiskManager(
            config=config.risk_management,
            account=self.exchange.get_account()
        )

        # Pass risk manager to strategies
        self.strategy = ORBStrategy(
            config=config.strategy,
            risk_manager=self.risk_manager  # NEW
        )
```

#### 2. Update Strategy to Use RiskManager

```python
# vibe/common/strategies/orb.py
async def generate_signals(self, data: Dict[str, pd.DataFrame]) -> List[Signal]:
    # ... existing signal generation ...

    # NEW: Validate with risk manager
    if breakout_detected:
        signal = Signal(...)

        risk_check = await self.risk_manager.validate_trade(
            signal=signal,
            current_positions=self.positions,
            current_price=current_price,
            volatility=self._calculate_atr(df)
        )

        if risk_check.approved:
            signal.quantity = risk_check.adjusted_quantity
            signal.stop_loss = risk_check.stop_loss_price
            signal.take_profit = risk_check.take_profit_price
            signals.append(signal)
```

#### 3. Add Health Monitoring to Components

```python
# vibe/trading_bot/data/manager.py
class DataManager:
    def is_healthy(self) -> bool:
        """Check if data manager is healthy."""
        # Check cache size
        if self.cache.size() > 10000:
            return False

        # Check last update time
        for symbol in self.symbols:
            last_bar = self.get_last_bar(symbol)
            if last_bar:
                age = (datetime.now() - last_bar.timestamp).total_seconds()
                if age > 600:  # 10 minutes
                    return False

        return True
```

#### 4. Add Performance Metrics Collection

```python
# vibe/trading_bot/core/orchestrator.py
async def _process_trading_cycle(self):
    start_time = time.time()

    # ... existing trading logic ...

    # NEW: Record metrics
    self.metrics.record("trading_cycle_latency_ms", (time.time() - start_time) * 1000)
    self.metrics.record("active_positions", len(self.positions))
    self.metrics.record("daily_pnl", self.risk_manager.daily_pnl)
```

#### 5. Add Error Tracking

```python
# vibe/trading_bot/utils/logger.py (enhanced)
class ErrorTracker:
    """Track errors for dashboard display."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self._create_table()

    def _create_table(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS error_log (
                timestamp TEXT,
                severity TEXT,
                component TEXT,
                message TEXT,
                traceback TEXT
            )
        """)

    def log_error(self, severity: str, component: str, message: str, traceback: str = None):
        self.db.execute(
            "INSERT INTO error_log VALUES (?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), severity, component, message, traceback)
        )
        self.db.commit()

    def get_recent_errors(self, limit: int = 50) -> List[Dict]:
        cursor = self.db.execute(
            "SELECT * FROM error_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
        return [
            {"timestamp": row[0], "severity": row[1], "component": row[2], "message": row[3]}
            for row in cursor.fetchall()
        ]
```

### New Files to Create

```
vibe/
├── common/
│   └── risk/                          # NEW
│       ├── __init__.py
│       ├── manager.py                 # RiskManager
│       ├── position_sizer.py          # PositionSizer
│       ├── stop_loss.py               # StopLossManager
│       ├── exposure.py                # ExposureController
│       └── drawdown.py                # DrawdownProtector
│
├── trading_bot/
│   ├── api/
│   │   ├── health.py                  # ENHANCED (add detailed endpoint)
│   │   ├── trading.py                 # NEW (trading dashboard API)
│   │   └── websocket.py               # NEW (real-time updates)
│   │
│   └── utils/
│       ├── error_tracker.py           # NEW
│       └── metrics.py                 # NEW
│
└── dashboard/                         # NEW
    ├── app.py                         # Main Streamlit app
    ├── config.py                      # Dashboard configuration
    │
    ├── pages/
    │   ├── 01_system_health.py        # System health page
    │   ├── 02_error_tracking.py       # Error tracking page
    │   ├── 03_performance.py          # Performance metrics page
    │   ├── 04_positions.py            # Positions & account page
    │   ├── 05_trade_history.py        # Trade history page
    │   ├── 06_analytics.py            # Performance analytics page
    │   └── 07_orb_metrics.py          # ORB strategy metrics page
    │
    └── components/
        ├── charts.py                  # Reusable chart components
        ├── tables.py                  # Reusable table components
        └── metrics.py                 # Reusable metric cards
```

---

## Implementation Plan

### Phase 2.1: Risk Management Core (Week 1-2)

**Goal:** Implement basic risk management module

**Tasks:**
1. Create `vibe/common/risk/` module structure
2. Implement `PositionSizer` with all 5 sizing methods
3. Implement `StopLossManager` with ATR and percentage stops
4. Implement `ExposureController` with position limits
5. Implement `DrawdownProtector` with circuit breakers
6. Implement `RiskManager` orchestrator
7. Add unit tests for each component
8. Update `ORBStrategy` to integrate `RiskManager`
9. Add risk management configuration to `config.yaml`
10. Test with historical data (paper trading)

**Success Criteria:**
- All risk components pass unit tests
- ORB strategy correctly integrates risk checks
- Circuit breakers trigger correctly in test scenarios
- Position sizing varies appropriately with volatility

### Phase 2.2: Service Health Monitoring (Week 3)

**Goal:** Build service health monitoring dashboard

**Tasks:**
1. Enhance `/health/detailed` API endpoint
2. Implement error tracking system (`ErrorTracker`)
3. Implement metrics collection (`MetricsCollector`)
4. Create `vibe/dashboard/` module structure
5. Build System Health page (Streamlit)
6. Build Error Tracking page
7. Build Performance Metrics page
8. Add WebSocket real-time updates
9. Deploy dashboard to Streamlit Cloud
10. Test remote access and auto-refresh

**Success Criteria:**
- Dashboard accessible via public URL
- Real-time updates working (5-second refresh)
- All health metrics displayed correctly
- Error tracking shows recent errors
- Performance metrics show latency percentiles

### Phase 2.3: Trading Performance Dashboard (Week 4)

**Goal:** Build trading performance monitoring dashboard

**Tasks:**
1. Implement `/account`, `/positions`, `/trades` API endpoints
2. Implement `/performance/metrics` API endpoint
3. Implement `/performance/equity_curve` API endpoint
4. Implement `/strategy/orb/metrics` API endpoint
5. Build Positions & Account page (Streamlit)
6. Build Trade History page with filtering
7. Build Performance Analytics page with charts
8. Build ORB Strategy Metrics page
9. Add export functionality (CSV download)
10. User acceptance testing

**Success Criteria:**
- All positions visible with unrealized P&L
- Trade history filterable by symbol/date/status
- Cumulative P&L chart displays correctly
- Performance metrics accurate (win rate, Sharpe, drawdown)
- ORB-specific metrics show success rate by time/symbol

### Phase 2.4: Integration & Testing (Week 5)

**Goal:** Integrate all components and test end-to-end

**Tasks:**
1. Integration testing: Risk + Strategy + Dashboard
2. Load testing: Simulate high-frequency trades
3. Edge case testing: Circuit breakers, rate limits, errors
4. Documentation: Update README with Phase 2 features
5. Create configuration examples for different risk profiles
6. Security audit: API authentication, rate limiting
7. Performance optimization: Database queries, API response times
8. Deployment to production (Oracle Cloud)
9. Monitor for 3 trading days
10. Bug fixes and refinements

**Success Criteria:**
- All tests passing
- No critical bugs in 3-day monitoring period
- Dashboard responsive (<2s load time)
- API endpoints return in <100ms
- Risk circuit breakers working in live environment

---

## Deployment Architecture

### Production Setup

```
+===========================================================================+
|                    PRODUCTION DEPLOYMENT ARCHITECTURE                      |
+===========================================================================+
|                                                                            |
|  +-----------------------------------------------------------------+      |
|  |  Oracle Cloud Free Tier (ARM VM)                                |      |
|  +-----------------------------------------------------------------+      |
|  |                                                                  |      |
|  |  Docker Compose:                                                 |      |
|  |                                                                  |      |
|  |  +-------------------------+     +-------------------------+     |      |
|  |  |  trading-bot            |     |  nginx (optional)       |     |      |
|  |  |  - Main trading loop    |     |  - HTTPS termination    |     |      |
|  |  |  - FastAPI (port 8080)  |<----|  - Rate limiting        |     |      |
|  |  |  - WebSocket server     |     |  - CORS                 |     |      |
|  |  +-------------------------+     +-------------------------+     |      |
|  |            |                              |                      |      |
|  |            v                              v                      |      |
|  |  +------------------------------------------------------+        |      |
|  |  |  SQLite Database (volume-mounted)                    |        |      |
|  |  |  - trades.db                                         |        |      |
|  |  |  - metrics.db                                        |        |      |
|  |  +------------------------------------------------------+        |      |
|  |                                                                  |      |
|  +-----------------------------------------------------------------+      |
|                                   |                                        |
|                                   | HTTPS                                  |
|                                   v                                        |
|  +-----------------------------------------------------------------+      |
|  |  Streamlit Community Cloud (Free Hosting)                        |      |
|  +-----------------------------------------------------------------+      |
|  |                                                                  |      |
|  |  https://your-trading-bot.streamlit.app                          |      |
|  |                                                                  |      |
|  |  Pages:                                                          |      |
|  |  - System Health                                                 |      |
|  |  - Error Tracking                                                |      |
|  |  - Performance Metrics                                           |      |
|  |  - Positions & Account                                           |      |
|  |  - Trade History                                                 |      |
|  |  - Analytics                                                     |      |
|  |  - ORB Metrics                                                   |      |
|  |                                                                  |      |
|  +-----------------------------------------------------------------+      |
|                                                                            |
+===========================================================================+
```

### Deployment Commands

```bash
# On Oracle Cloud VM

# 1. Pull latest code
git pull origin main

# 2. Update configuration
# Edit config/prod.yaml with production settings

# 3. Deploy with Docker Compose
docker-compose -f docker-compose.prod.yaml up -d --build

# 4. Check logs
docker-compose logs -f trading-bot

# 5. Verify health
curl http://localhost:8080/health/detailed
```

### Streamlit Cloud Deployment

1. **Connect GitHub repo** to Streamlit Cloud
2. **Set secrets** in Streamlit Cloud dashboard:
   ```toml
   # .streamlit/secrets.toml
   API_BASE = "https://your-oracle-ip:8080"
   API_KEY = "your-api-key-here"
   ```
3. **Deploy** - Streamlit Cloud auto-deploys on git push
4. **Access** at `https://your-trading-bot.streamlit.app`

---

## Security Considerations

### API Authentication

```python
# vibe/trading_bot/api/auth.py
from fastapi import Security, HTTPException
from fastapi.security.api_key import APIKeyHeader

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key

# Use in endpoints
@app.get("/positions", dependencies=[Depends(verify_api_key)])
async def get_positions():
    ...
```

### Rate Limiting

```python
# vibe/trading_bot/api/middleware.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.get("/trades")
@limiter.limit("10/minute")
async def get_trades(request: Request):
    ...
```

### CORS Configuration

```python
# vibe/trading_bot/api/main.py
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://your-trading-bot.streamlit.app",  # Streamlit dashboard
        "http://localhost:8501"  # Local development
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

### Secrets Management

**DO NOT** commit secrets to git!

```yaml
# .env.example (commit this)
API_KEY=your_api_key_here
FINNHUB_API_KEY=your_finnhub_key_here

# .env (DO NOT commit)
API_KEY=550e8400-e29b-41d4-a716-446655440000
FINNHUB_API_KEY=actual_production_key
```

---

## Cost Analysis

### Phase 2 Infrastructure Costs

| Component | Service | Cost | Notes |
|-----------|---------|------|-------|
| **Compute** | Oracle Cloud Free Tier | $0 | ARM VM (4 cores, 24GB RAM) |
| **Dashboard Hosting** | Streamlit Community Cloud | $0 | Free tier (1 app, 1GB RAM) |
| **Database** | SQLite (local) | $0 | File-based, no external DB needed |
| **Domain (optional)** | Namecheap | ~$12/year | Optional custom domain |
| **SSL (optional)** | Let's Encrypt | $0 | Free SSL certificates |
| **Total** | | **$0-1/month** | Essentially free! |

### Paid Tier Upgrade Options (Future)

| Component | Service | Cost | When to Upgrade |
|-----------|---------|------|-----------------|
| **Dashboard** | Streamlit Enterprise | $250/month | >3 apps, need auth, SLA |
| **Data Feed** | Finnhub Pro | $99/month | Need more real-time data |
| **Compute** | Oracle Cloud Paid | ~$10-50/month | Need more resources |
| **Monitoring** | Grafana Cloud | $49/month | Need advanced monitoring |

**Recommendation:** Start with free tier, upgrade only when necessary (e.g., multiple users, enterprise features).

---

## References

### Risk Management
- [Risk Management for Traders](https://www.investopedia.com/articles/trading/09/risk-management.asp) - Investopedia guide
- [Kelly Criterion Calculator](https://www.investopedia.com/articles/trading/04/091504.asp) - Optimal position sizing
- [Stop Loss Strategies](https://www.tradestation.com/education/labs/analysis-concepts/stop-loss-strategies/) - TradeStation guide

### Dashboards & Monitoring
- [Streamlit Documentation](https://docs.streamlit.io/) - Dashboard framework
- [Streamlit Cloud Deployment](https://docs.streamlit.io/streamlit-community-cloud) - Free hosting
- [Real-Time Streamlit Dashboards](https://blog.streamlit.io/how-to-build-a-real-time-dashboard-using-streamlit/) - Tutorial

### Performance Metrics
- [Trading Performance Metrics](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/understanding-algorithm-performance) - QuantConnect guide
- [Sharpe Ratio Calculator](https://www.investopedia.com/terms/s/sharperatio.asp) - Risk-adjusted returns
- [Maximum Drawdown](https://www.investopedia.com/terms/m/maximum-drawdown-mdd.asp) - Understanding drawdowns

---

## Appendix: Configuration Examples

### Conservative Risk Profile

```yaml
# config/risk_profiles/conservative.yaml
risk_management:
  enabled: true

  position_sizing:
    method: "risk_based"
    risk_per_trade_dollars: 50  # Risk only $50 per trade
    max_position_value: 2000    # Max $2,000 per position

  stop_loss:
    atr_multiplier: 1.5         # Tight stops

  exposure:
    max_positions: 2            # Only 2 concurrent positions
    max_total_exposure_pct: 0.5 # Use only 50% of capital

  drawdown:
    max_drawdown_pct: 0.05      # Stop at 5% drawdown
    daily_loss_limit_dollars: 200  # Stop at $200 daily loss
```

### Aggressive Risk Profile

```yaml
# config/risk_profiles/aggressive.yaml
risk_management:
  enabled: true

  position_sizing:
    method: "kelly"
    kelly_fraction: 0.5         # Use 50% Kelly (still fractional)
    max_kelly_pct: 0.25         # Allow up to 25% of capital

  stop_loss:
    atr_multiplier: 3.0         # Wider stops

  exposure:
    max_positions: 5            # Up to 5 concurrent positions
    max_total_exposure_pct: 0.95  # Use 95% of capital

  drawdown:
    max_drawdown_pct: 0.15      # Stop at 15% drawdown
    daily_loss_limit_dollars: 800  # Stop at $800 daily loss
```

---

## Next Steps After Phase 2

**Phase 3 (Future):** Advanced Features
- Multi-strategy support (add more strategies beyond ORB)
- Machine learning integration (signal confidence scoring)
- Options trading support
- Cryptocurrency support
- Live broker integration (Alpaca, Interactive Brokers)
- Backtesting engine (share code with live trading)

**Phase 4 (Future):** Enterprise Features
- Multi-user support (team collaboration)
- Advanced alerting (SMS, Slack, PagerDuty)
- Strategy optimization engine
- Paper trading competition mode
- Portfolio management across multiple accounts

---

**End of Phase 2 Design Document**
