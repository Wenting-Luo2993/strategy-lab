"""
Base Exchange Interface for Trading Systems

This module defines the abstract base class that all exchange implementations
must follow, ensuring consistent functionality across different trading venues.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import pandas as pd


class Exchange(ABC):
    """
    Abstract base class for exchange implementations.
    
    All exchange implementations must provide the following core functionality:
    - Connection management (connect, disconnect)
    - Order management (submit, cancel)
    - Account and position queries
    """
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Connect to the exchange and authenticate.
        
        Returns:
            bool: True if connection successful, False otherwise
        """
        pass
        
    @abstractmethod
    def disconnect(self) -> bool:
        """
        Disconnect from the exchange.
        
        Returns:
            bool: True if disconnection successful, False otherwise
        """
        pass
        
    @abstractmethod
    def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an order to the exchange.
        
        Order format should include:
            - ticker (str): The symbol to trade
            - side (str): "buy" or "sell"
            - qty (int): Number of shares/contracts
            - order_type (str): "market" or "limit"
            - limit_price (float, optional): Required for limit orders
            - order_id (str, optional): Client-generated order ID
            - timestamp (pd.Timestamp, optional): Order timestamp
            
        Returns:
            dict: Order response with the following fields:
                - order_id (str): Exchange-assigned order ID
                - status (str): "filled", "partial", "open", or "cancelled"
                - filled_qty (int): Number of shares/contracts filled
                - avg_fill_price (float): Average fill price
                - commission (float): Commission charged for this order
                - timestamp (pd.Timestamp): Timestamp of order response
        """
        pass
        
    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an existing open order.
        
        Args:
            order_id (str): The ID of the order to cancel
            
        Returns:
            bool: True if cancellation successful, False otherwise
        """
        pass
        
    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get current positions from the exchange.
        
        Returns:
            list[dict]: List of position dictionaries with the following fields:
                - ticker (str): Symbol
                - qty (int): Position size (positive for long, negative for short)
                - avg_price (float): Average entry price
                - market_price (float): Current market price
                - market_value (float): Current market value
                - unrealized_pnl (float): Unrealized profit/loss
        """
        pass
        
    @abstractmethod
    def get_account(self) -> Dict[str, Any]:
        """
        Get account information from the exchange.
        
        Returns:
            dict: Account information with the following fields:
                - cash (float): Available cash
                - equity (float): Total account value
                - buying_power (float): Buying power
                - maintenance_margin (float, optional): Maintenance margin requirement
                - initial_margin (float, optional): Initial margin requirement
        """
        pass