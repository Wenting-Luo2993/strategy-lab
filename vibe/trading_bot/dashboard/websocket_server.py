"""WebSocket server for real-time trading dashboard updates."""

from typing import List, Set
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime
import json
import asyncio
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for broadcasting updates."""

    def __init__(self):
        """Initialize connection manager."""
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        """Register a new WebSocket connection.

        Args:
            websocket: WebSocket connection to register

        Raises:
            RuntimeError: If connection fails
        """
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a WebSocket connection.

        Args:
            websocket: WebSocket connection to unregister
        """
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, data: dict) -> None:
        """Broadcast message to all connected clients.

        Args:
            data: Dictionary to broadcast as JSON
        """
        message = json.dumps(data)
        disconnected = []

        async with self._lock:
            for connection in self.active_connections:
                try:
                    await connection.send_text(message)
                except Exception as e:
                    logger.error(f"Error sending message: {e}")
                    disconnected.append(connection)

        # Clean up disconnected clients
        for connection in disconnected:
            await self.disconnect(connection)

    async def broadcast_trade_update(self, trade: dict) -> None:
        """Broadcast a trade update to all clients.

        Args:
            trade: Trade data to broadcast
        """
        await self.broadcast(
            {
                "type": "trade_update",
                "timestamp": datetime.utcnow().isoformat(),
                "data": trade,
            }
        )

    async def broadcast_position_update(self, position: dict) -> None:
        """Broadcast a position update to all clients.

        Args:
            position: Position data to broadcast
        """
        await self.broadcast(
            {
                "type": "position_update",
                "timestamp": datetime.utcnow().isoformat(),
                "data": position,
            }
        )

    async def broadcast_metrics_update(self, metrics: dict) -> None:
        """Broadcast metrics update to all clients.

        Args:
            metrics: Metrics data to broadcast
        """
        await self.broadcast(
            {
                "type": "metrics_update",
                "timestamp": datetime.utcnow().isoformat(),
                "data": metrics,
            }
        )

    async def broadcast_account_update(self, account: dict) -> None:
        """Broadcast account update to all clients.

        Args:
            account: Account data to broadcast
        """
        await self.broadcast(
            {
                "type": "account_update",
                "timestamp": datetime.utcnow().isoformat(),
                "data": account,
            }
        )

    async def broadcast_health_update(self, health: dict) -> None:
        """Broadcast health status update to all clients.

        Args:
            health: Health status to broadcast
        """
        await self.broadcast(
            {
                "type": "health_update",
                "timestamp": datetime.utcnow().isoformat(),
                "data": health,
            }
        )

    def get_connection_count(self) -> int:
        """Get current number of active connections.

        Returns:
            Number of active WebSocket connections
        """
        return len(self.active_connections)

    async def send_to_client(self, websocket: WebSocket, data: dict) -> None:
        """Send message to specific client.

        Args:
            websocket: Target WebSocket connection
            data: Dictionary to send as JSON

        Raises:
            RuntimeError: If connection is not active
        """
        message = json.dumps(data)
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"Error sending to client: {e}")
            await self.disconnect(websocket)
            raise


# Global connection manager instance
manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time updates.

    Args:
        websocket: WebSocket connection

    Raises:
        WebSocketDisconnect: When client disconnects
    """
    await manager.connect(websocket)
    try:
        # Send welcome message
        await websocket.send_text(
            json.dumps(
                {
                    "type": "connected",
                    "timestamp": datetime.utcnow().isoformat(),
                    "message": "Connected to dashboard",
                }
            )
        )

        # Keep connection alive and listen for client messages
        while True:
            data = await websocket.receive_text()
            # Echo back or process client messages if needed
            if data:
                try:
                    message = json.loads(data)
                    logger.debug(f"Received message from client: {message}")
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received: {data}")
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await manager.disconnect(websocket)
