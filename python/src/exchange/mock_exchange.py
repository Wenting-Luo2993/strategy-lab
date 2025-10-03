"""
Mock Exchange Implementation

This module provides a mock exchange that simulates order execution
with configurable slippage and commissions for paper trading.
"""

import uuid
import random
import logging
import pandas as pd
import csv
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from collections import defaultdict
import os

from .base import Exchange
from .models import Order, OrderResponse, Position, Trade, AccountState

# Configure logging
logger = logging.getLogger(__name__)


class MockExchange(Exchange):
    """
    Mock exchange implementation for paper trading and testing.
    
    This exchange simulates order execution with configurable slippage and
    commissions, maintaining internal state for positions and cash balance.
    """
    
    def __init__(
        self,
        initial_capital: float = 10000.0,
        slippage_pct: float = 0.001,  # 0.1% default slippage
        commission_per_share: float = 0.005,  # $0.005 per share
        min_commission: float = 1.0,  # Minimum commission per trade
        price_data: Optional[Dict[str, pd.DataFrame]] = None,
        fill_probability_limit: float = 0.7,  # 70% chance for limit orders to fill
        partial_fill_probability: float = 0.3,  # 30% chance for partial fills
        trade_log_path: Optional[str] = None,
    ):
        """
        Initialize the mock exchange.
        
        Args:
            initial_capital: Starting cash balance
            slippage_pct: Market order slippage as percentage of price
            commission_per_share: Commission per share traded
            min_commission: Minimum commission per trade
            price_data: Dictionary of ticker -> DataFrame with OHLCV data
            fill_probability_limit: Probability that limit orders will be filled
            partial_fill_probability: Probability of partial fills
            trade_log_path: Path to save trade logs (None for no logging)
        """
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.slippage_pct = slippage_pct
        self.commission_per_share = commission_per_share
        self.min_commission = min_commission
        self.price_data = price_data or {}
        self.fill_probability_limit = fill_probability_limit
        self.partial_fill_probability = partial_fill_probability
        
        # Internal state
        self.positions: Dict[str, Position] = {}  # ticker -> Position
        self.open_orders: Dict[str, Order] = {}  # order_id -> Order
        self.trade_log: List[Trade] = []
        self.connected = False
        
        # Trade logging to CSV
        self.trade_log_path = trade_log_path
        self._setup_trade_log()
    
    def _setup_trade_log(self) -> None:
        """Setup CSV logging for trades if a path is provided."""
        if self.trade_log_path:
            path = Path(self.trade_log_path)
            # Create directory if it doesn't exist
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create file with headers if it doesn't exist
            if not path.exists():
                with open(path, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=[
                        'timestamp', 'ticker', 'side', 'qty',
                        'price', 'commission', 'order_id'
                    ])
                    writer.writeheader()
    
    def connect(self) -> bool:
        """
        Connect to the mock exchange.
        
        Returns:
            bool: True (always successful for mock)
        """
        self.connected = True
        logger.info("Connected to mock exchange")
        return True
    
    def disconnect(self) -> bool:
        """
        Disconnect from the mock exchange.
        
        Returns:
            bool: True (always successful for mock)
        """
        self.connected = False
        logger.info("Disconnected from mock exchange")
        return True
    
    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an order to the mock exchange.
        
        Args:
            order: Order information dictionary
            
        Returns:
            dict: Order response with execution details
        """
        if not self.connected:
            return OrderResponse(
                order_id=order.get("order_id", str(uuid.uuid4())),
                status="cancelled",
                filled_qty=0,
                message="Exchange not connected"
            ).to_dict()
        
        # If order is passed as a dict, convert to Order object
        if isinstance(order, dict):
            # Ensure order_id is present
            if "order_id" not in order:
                order["order_id"] = str(uuid.uuid4())
            # Convert dict to Order object for easier processing
            order_obj = Order(**order)
        else:
            order_obj = order
        
        # Store the order
        self.open_orders[order_obj.order_id] = order_obj
        
        # Get current market price for the ticker
        current_price = self._get_current_price(order_obj.ticker)
        if current_price is None:
            return OrderResponse(
                order_id=order_obj.order_id,
                status="cancelled",
                filled_qty=0,
                message=f"No price data available for {order_obj.ticker}"
            ).to_dict()
        
        # Process order based on type
        if order_obj.order_type == "market":
            return self._process_market_order(order_obj, current_price).to_dict()
        elif order_obj.order_type == "limit":
            return self._process_limit_order(order_obj, current_price).to_dict()
        else:
            return OrderResponse(
                order_id=order_obj.order_id,
                status="cancelled",
                filled_qty=0,
                message=f"Unsupported order type: {order_obj.order_type}"
            ).to_dict()
    
    def _process_market_order(self, order: Order, current_price: float) -> OrderResponse:
        """
        Process a market order with simulated slippage.
        
        Args:
            order: The order to process
            current_price: Current market price for the ticker
            
        Returns:
            OrderResponse: The execution response
        """
        # Apply slippage: worse price for the trader
        slippage_factor = 1 + (self.slippage_pct * (1 if order.side == "buy" else -1))
        execution_price = current_price * slippage_factor
        
        # Calculate commission
        commission = max(
            self.min_commission,
            order.qty * self.commission_per_share
        )
        
        # Check if we have enough cash for buys
        if order.side == "buy":
            total_cost = (execution_price * order.qty) + commission
            if total_cost > self.cash:
                # Adjust quantity to fit available cash
                max_qty = int((self.cash - commission) / execution_price)
                if max_qty <= 0:
                    return OrderResponse(
                        order_id=order.order_id,
                        status="cancelled",
                        filled_qty=0,
                        message="Insufficient funds"
                    )
                # Partial fill
                filled_qty = max_qty
                status = "partial"
                # Recalculate commission based on actual fill
                commission = max(
                    self.min_commission,
                    filled_qty * self.commission_per_share
                )
            else:
                filled_qty = order.qty
                status = "filled"
        else:  # sell order
            # Check if we have enough shares
            position = self.positions.get(order.ticker)
            if not position or abs(position.qty) < order.qty:
                if not position:
                    available_qty = 0
                else:
                    # For shorts, we check if short position can be increased
                    if position.qty < 0:
                        # Already short, can sell more (increase short position)
                        filled_qty = order.qty
                        status = "filled"
                    else:
                        # Long position, but not enough shares
                        available_qty = position.qty
                        if available_qty <= 0:
                            return OrderResponse(
                                order_id=order.order_id,
                                status="cancelled",
                                filled_qty=0,
                                message="Insufficient shares"
                            )
                        # Partial fill based on available shares
                        filled_qty = available_qty
                        status = "partial"
                        # Recalculate commission
                        commission = max(
                            self.min_commission,
                            filled_qty * self.commission_per_share
                        )
            else:
                filled_qty = order.qty
                status = "filled"
        
        # Update positions and cash
        self._update_position(order.ticker, filled_qty if order.side == "buy" else -filled_qty, execution_price)
        
        # Deduct commission from cash
        self.cash -= commission
        
        # Create and log the trade
        trade = Trade(
            order_id=order.order_id,
            ticker=order.ticker,
            side=order.side,
            qty=filled_qty,
            price=execution_price,
            timestamp=pd.Timestamp.now(),
            commission=commission
        )
        self.trade_log.append(trade)
        self._log_trade_to_csv(trade)
        
        # Remove the order if filled completely
        if status == "filled":
            del self.open_orders[order.order_id]
        
        # Return response
        return OrderResponse(
            order_id=order.order_id,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=execution_price,
            commission=commission,
            timestamp=pd.Timestamp.now()
        )
    
    def _process_limit_order(self, order: Order, current_price: float) -> OrderResponse:
        """
        Process a limit order with probabilistic fill simulation.
        
        Args:
            order: The order to process
            current_price: Current market price for the ticker
            
        Returns:
            OrderResponse: The execution response
        """
        # Check if limit price is favorable
        price_favorable = (
            (order.side == "buy" and order.limit_price >= current_price) or
            (order.side == "sell" and order.limit_price <= current_price)
        )
        
        # Determine if order will be filled based on probability
        fill_probability = self.fill_probability_limit if price_favorable else 0.1
        will_fill = random.random() < fill_probability
        
        if not will_fill:
            # Order remains open
            return OrderResponse(
                order_id=order.order_id,
                status="open",
                filled_qty=0,
                timestamp=pd.Timestamp.now(),
                message="Limit order queued"
            )
        
        # Determine if partial fill
        partial_fill = random.random() < self.partial_fill_probability
        filled_qty = order.qty
        
        if partial_fill:
            # Fill between 10% and 90% of the order
            fill_pct = random.uniform(0.1, 0.9)
            filled_qty = max(1, int(order.qty * fill_pct))
        
        # Use limit price as execution price
        execution_price = order.limit_price
        
        # Calculate commission
        commission = max(
            self.min_commission,
            filled_qty * self.commission_per_share
        )
        
        # Check if we have enough cash for buys
        if order.side == "buy":
            total_cost = (execution_price * filled_qty) + commission
            if total_cost > self.cash:
                return OrderResponse(
                    order_id=order.order_id,
                    status="open",  # Keep the order open
                    filled_qty=0,
                    message="Insufficient funds"
                )
        
        # Update positions and cash
        self._update_position(order.ticker, filled_qty if order.side == "buy" else -filled_qty, execution_price)
        
        # Deduct commission from cash
        self.cash -= commission
        
        # Create and log the trade
        trade = Trade(
            order_id=order.order_id,
            ticker=order.ticker,
            side=order.side,
            qty=filled_qty,
            price=execution_price,
            timestamp=pd.Timestamp.now(),
            commission=commission
        )
        self.trade_log.append(trade)
        self._log_trade_to_csv(trade)
        
        # Update order status
        status = "filled" if filled_qty == order.qty else "partial"
        
        # Remove the order if filled completely
        if status == "filled":
            del self.open_orders[order.order_id]
        
        # Return response
        return OrderResponse(
            order_id=order.order_id,
            status=status,
            filled_qty=filled_qty,
            avg_fill_price=execution_price,
            commission=commission,
            timestamp=pd.Timestamp.now()
        )
    
    def _update_position(self, ticker: str, qty_change: int, price: float) -> None:
        """
        Update position after a trade.
        
        Args:
            ticker: Symbol of the security
            qty_change: Change in quantity (positive for buys, negative for sells)
            price: Execution price
        """
        # Update cash (credit for sells, debit for buys)
        self.cash -= qty_change * price
        
        if ticker not in self.positions:
            # New position
            self.positions[ticker] = Position(
                ticker=ticker,
                qty=qty_change,
                avg_price=price,
                market_price=price
            )
        else:
            # Update existing position
            position = self.positions[ticker]
            
            if position.qty * qty_change < 0:  # Position is being reduced or reversed
                if abs(qty_change) < abs(position.qty):
                    # Position size is reduced but not reversed
                    position.qty += qty_change
                    # Average price remains the same
                else:
                    # Position is reversed
                    position.qty += qty_change
                    position.avg_price = price
            else:
                # Position is being increased
                # Update average price
                total_value = position.avg_price * position.qty + price * qty_change
                position.qty += qty_change
                position.avg_price = total_value / position.qty if position.qty != 0 else 0
            
            # Update market price
            position.market_price = price
            
            # Remove if position is closed
            if position.qty == 0:
                del self.positions[ticker]
    
    def _get_current_price(self, ticker: str) -> Optional[float]:
        """
        Get current market price for a ticker.
        
        Args:
            ticker: Symbol to get price for
            
        Returns:
            float or None: Current market price if available, None otherwise
        """
        if ticker in self.price_data:
            df = self.price_data[ticker]
            if not df.empty:
                # Get the latest price
                return df.iloc[-1]["close"]
        # If we reach here, no price data is available
        logger.warning(f"No price data found for {ticker}")
        return None
    
    def update_market_data(self, price_data: Dict[str, pd.DataFrame]) -> None:
        """
        Update the market data used by the exchange.
        
        Args:
            price_data: Dictionary of ticker -> DataFrame with OHLCV data
        """
        self.price_data.update(price_data)
        
        # Update market prices for existing positions
        for ticker, position in list(self.positions.items()):
            current_price = self._get_current_price(ticker)
            if current_price is not None:
                position.market_price = current_price
            else:
                logger.warning(f"Cannot update price for position {ticker}")
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an existing open order.
        
        Args:
            order_id: ID of the order to cancel
            
        Returns:
            bool: True if order was cancelled, False if not found or already executed
        """
        if order_id in self.open_orders:
            del self.open_orders[order_id]
            return True
        return False
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get current positions.
        
        Returns:
            list[dict]: List of position dictionaries
        """
        return [position.to_dict() for position in self.positions.values()]
    
    def get_account(self) -> Dict[str, Any]:
        """
        Get account information.
        
        Returns:
            dict: Account information dictionary
        """
        # Calculate total position value
        positions_value = sum(p.market_value for p in self.positions.values())
        
        return {
            "cash": self.cash,
            "equity": self.cash + positions_value,
            "initial_capital": self.initial_capital,
            "buying_power": self.cash * 2,  # Simple margin simulation
            "positions_value": positions_value
        }
    
    def _log_trade_to_csv(self, trade: Trade) -> None:
        """Log a trade to the CSV file if a path is provided."""
        if self.trade_log_path:
            with open(self.trade_log_path, 'a', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'timestamp', 'ticker', 'side', 'qty',
                    'price', 'commission', 'order_id'
                ])
                writer.writerow(trade.to_dict())
    
    def dump_trades_to_csv(self, path: Optional[str] = None) -> None:
        """
        Dump all trades to a CSV file.
        
        Args:
            path: Path to the CSV file (defaults to self.trade_log_path)
        """
        if not path and not self.trade_log_path:
            logger.warning("No trade log path specified")
            return
            
        output_path = path or self.trade_log_path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'timestamp', 'ticker', 'side', 'qty',
                'price', 'commission', 'order_id'
            ])
            writer.writeheader()
            for trade in self.trade_log:
                writer.writerow(trade.to_dict())