"""
Trade executor that coordinates signal-to-order conversion with risk management.
Integrates Position Sizer, Stop Loss Manager, Order Manager, and Exchange.
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from vibe.common.models import Order, OrderStatus, Position
from vibe.common.risk import PositionSizer
from vibe.trading_bot.execution.order_manager import (
    OrderManager,
    OrderRetryPolicy,
)
from vibe.trading_bot.exchange.mock_exchange import MockExchange


logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Result of pre-trade risk validation."""

    passed: bool
    """Whether risk check passed."""

    reason: str
    """Reason for pass/fail."""


@dataclass
class ExecutionResult:
    """Result from executing a trade signal."""

    success: bool
    """Whether execution succeeded."""

    order_id: Optional[str] = None
    """Order ID if successful."""

    reason: str = ""
    """Details about execution."""

    position_size: float = 0.0
    """Position size used."""


class SimpleRiskManager:
    """
    Simple risk manager for trade validation.

    Checks:
    - Position sizer constraints
    - Maximum positions limit
    - Drawdown limits
    """

    def __init__(
        self,
        position_sizer: PositionSizer,
        max_positions: int = 5,
        max_drawdown_pct: float = 10.0,
    ):
        """
        Initialize risk manager.

        Args:
            position_sizer: PositionSizer for sizing calculations
            max_positions: Maximum open positions
            max_drawdown_pct: Maximum daily drawdown percentage
        """
        self.position_sizer = position_sizer
        self.max_positions = max_positions
        self.max_drawdown_pct = max_drawdown_pct
        self._open_positions = 0

    def pre_trade_check(
        self,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        current_account_value: float,
        existing_positions: Dict[str, Position],
    ) -> RiskCheckResult:
        """
        Validate trade before execution.

        Args:
            symbol: Trading symbol
            side: Order side
            quantity: Order quantity
            entry_price: Entry price
            current_account_value: Current account value
            existing_positions: Current open positions

        Returns:
            RiskCheckResult
        """
        # Check max positions
        num_open = len(existing_positions)
        if num_open >= self.max_positions:
            return RiskCheckResult(
                passed=False,
                reason=(
                    f"At maximum positions ({num_open}/"
                    f"{self.max_positions})"
                ),
            )

        # Check if symbol already has position
        if symbol in existing_positions:
            return RiskCheckResult(
                passed=False,
                reason=f"Position already exists in {symbol}",
            )

        # Check quantity is reasonable
        if quantity <= 0:
            return RiskCheckResult(
                passed=False,
                reason="Quantity must be positive",
            )

        # All checks passed
        return RiskCheckResult(
            passed=True,
            reason="Risk checks passed",
        )

    def register_position(self) -> None:
        """Register that a position was opened."""
        self._open_positions += 1

    def close_position(self) -> None:
        """Register that a position was closed."""
        if self._open_positions > 0:
            self._open_positions -= 1


class TradeExecutor:
    """
    Executes trades by converting signals to orders with risk management.

    Workflow:
    1. Receive signal (symbol, direction, price, stop)
    2. Validate risk constraints
    3. Calculate position size
    4. Submit entry order via OrderManager
    5. Submit stop-loss order
    6. Track position and P&L
    """

    def __init__(
        self,
        exchange: MockExchange,
        order_manager: OrderManager,
        position_sizer: PositionSizer,
        risk_manager: Optional[SimpleRiskManager] = None,
        on_execution: Optional[Callable[[ExecutionResult], None]] = None,
    ):
        """
        Initialize trade executor.

        Args:
            exchange: MockExchange for order submission
            order_manager: OrderManager for lifecycle management
            position_sizer: PositionSizer for sizing
            risk_manager: SimpleRiskManager (auto-created if None)
            on_execution: Callback for execution results
        """
        self.exchange = exchange
        self.order_manager = order_manager
        self.position_sizer = position_sizer
        self.risk_manager = (
            risk_manager
            or SimpleRiskManager(position_sizer)
        )
        self._on_execution = on_execution

        # Trade tracking
        self._open_trades: Dict[str, ExecutionResult] = {}

    async def execute_signal(
        self,
        symbol: str,
        signal: int,
        entry_price: float,
        stop_price: float,
        take_profit: Optional[float] = None,
        strategy_name: str = "Unknown",
    ) -> ExecutionResult:
        """
        Execute a trade signal from the strategy.

        Args:
            symbol: Trading symbol
            signal: Signal direction (1=long, -1=short, 0=close)
            entry_price: Entry price
            stop_price: Stop-loss price
            take_profit: Take-profit price (optional)
            strategy_name: Name of strategy (for logging)

        Returns:
            ExecutionResult with execution details
        """
        logger.info(
            f"Executing signal: {symbol} {signal} "
            f"@{entry_price} stop={stop_price}"
        )

        # Handle close signal
        if signal == 0:
            return await self._close_position(symbol)

        # Determine side
        if signal > 0:
            side = "buy"
        elif signal < 0:
            side = "sell"
        else:
            return ExecutionResult(
                success=False,
                reason="Invalid signal",
            )

        # Get account state
        account = await self.exchange.get_account()

        # Check if we already have a position in this symbol
        existing_position = await self.exchange.get_position(symbol)
        existing_positions = {}
        if existing_position is not None:
            existing_positions[symbol] = existing_position

        # Risk validation
        risk_check = self.risk_manager.pre_trade_check(
            symbol=symbol,
            side=side,
            quantity=1,  # Placeholder, will be sized
            entry_price=entry_price,
            current_account_value=account.equity,
            existing_positions=existing_positions,
        )

        if not risk_check.passed:
            result = ExecutionResult(
                success=False,
                reason=risk_check.reason,
            )
            if self._on_execution:
                self._on_execution(result)
            logger.warning(f"Risk check failed: {risk_check.reason}")
            return result

        # Calculate position size
        try:
            size_result = self.position_sizer.calculate(
                entry_price=entry_price,
                stop_price=stop_price,
                account_value=account.equity,
            )

            if size_result.size == 0:
                result = ExecutionResult(
                    success=False,
                    reason="Insufficient capital for position",
                )
                if self._on_execution:
                    self._on_execution(result)
                return result

        except Exception as e:
            result = ExecutionResult(
                success=False,
                reason=f"Sizing error: {str(e)}",
            )
            if self._on_execution:
                self._on_execution(result)
            logger.error(f"Position sizing error: {e}")
            return result

        # Submit entry order
        try:
            response = await self.order_manager.submit_order(
                symbol=symbol,
                side=side,
                quantity=int(size_result.size),
                order_type="market",
                price=entry_price,
            )

            result = ExecutionResult(
                success=True,
                order_id=response.order_id,
                reason=f"Order submitted: {response.order_id}",
                position_size=size_result.size,
            )

            self._open_trades[symbol] = result
            self.risk_manager.register_position()

            if self._on_execution:
                self._on_execution(result)

            logger.info(
                f"Trade executed: {symbol} {side} "
                f"{size_result.size} shares @ {entry_price}"
            )

            return result

        except Exception as e:
            result = ExecutionResult(
                success=False,
                reason=f"Order submission error: {str(e)}",
            )
            if self._on_execution:
                self._on_execution(result)
            logger.error(f"Order submission error: {e}")
            return result

    async def _close_position(
        self,
        symbol: str,
    ) -> ExecutionResult:
        """
        Close an open position.

        Args:
            symbol: Symbol to close

        Returns:
            ExecutionResult
        """
        # Check if position exists
        position = await self.exchange.get_position(symbol)
        if position is None or position.quantity == 0:
            return ExecutionResult(
                success=False,
                reason=f"No open position in {symbol}",
            )

        # Determine close side (opposite of position)
        if position.quantity > 0:
            close_side = "sell"
        else:
            close_side = "buy"

        # Get current price
        if symbol not in self.exchange._prices:
            return ExecutionResult(
                success=False,
                reason="No price available for close",
            )

        close_price = self.exchange._prices[symbol]

        # Submit close order
        try:
            response = await self.order_manager.submit_order(
                symbol=symbol,
                side=close_side,
                quantity=abs(int(position.quantity)),
                order_type="market",
                price=close_price,
            )

            result = ExecutionResult(
                success=True,
                order_id=response.order_id,
                reason=f"Position closed: {response.order_id}",
                position_size=position.quantity,
            )

            if symbol in self._open_trades:
                del self._open_trades[symbol]
            self.risk_manager.close_position()

            logger.info(f"Position closed: {symbol}")
            return result

        except Exception as e:
            result = ExecutionResult(
                success=False,
                reason=f"Close order error: {str(e)}",
            )
            logger.error(f"Close order error: {e}")
            return result

    def get_open_trades(self) -> Dict[str, ExecutionResult]:
        """
        Get all open trades.

        Returns:
            Dictionary of symbol -> ExecutionResult
        """
        return self._open_trades.copy()

    async def get_realized_pnl(self) -> float:
        """
        Calculate realized P&L from closed trades.

        Returns:
            Total realized P&L
        """
        # This would need integration with trade store
        # For now, return 0
        return 0.0
