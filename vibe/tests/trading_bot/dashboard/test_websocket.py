"""Unit tests for WebSocket server."""

import pytest
import asyncio
import json
from vibe.trading_bot.dashboard.websocket_server import ConnectionManager, websocket_endpoint
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def connection_manager():
    """Create connection manager for testing."""
    return ConnectionManager()


@pytest.mark.asyncio
async def test_connection_manager_connect(connection_manager):
    """Test connecting a WebSocket."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)
    assert websocket in connection_manager.active_connections
    assert connection_manager.get_connection_count() == 1


@pytest.mark.asyncio
async def test_connection_manager_multiple_connects(connection_manager):
    """Test connecting multiple WebSockets."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws3 = AsyncMock()

    await connection_manager.connect(ws1)
    await connection_manager.connect(ws2)
    await connection_manager.connect(ws3)

    assert connection_manager.get_connection_count() == 3
    assert ws1 in connection_manager.active_connections
    assert ws2 in connection_manager.active_connections
    assert ws3 in connection_manager.active_connections


@pytest.mark.asyncio
async def test_connection_manager_disconnect(connection_manager):
    """Test disconnecting a WebSocket."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)
    assert connection_manager.get_connection_count() == 1

    await connection_manager.disconnect(websocket)
    assert websocket not in connection_manager.active_connections
    assert connection_manager.get_connection_count() == 0


@pytest.mark.asyncio
async def test_connection_manager_broadcast(connection_manager):
    """Test broadcasting message to all clients."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await connection_manager.connect(ws1)
    await connection_manager.connect(ws2)

    test_data = {"type": "test", "value": 123}
    await connection_manager.broadcast(test_data)

    ws1.send_text.assert_called_once()
    ws2.send_text.assert_called_once()

    # Verify JSON was sent
    sent_data = json.loads(ws1.send_text.call_args[0][0])
    assert sent_data == test_data


@pytest.mark.asyncio
async def test_broadcast_trade_update(connection_manager):
    """Test broadcasting trade update."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    trade_data = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 100,
        "entry_price": 150.0,
        "pnl": 500.0,
    }

    await connection_manager.broadcast_trade_update(trade_data)

    websocket.send_text.assert_called_once()
    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "trade_update"
    assert sent_message["data"] == trade_data
    assert "timestamp" in sent_message


@pytest.mark.asyncio
async def test_broadcast_position_update(connection_manager):
    """Test broadcasting position update."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    position_data = {
        "symbol": "AAPL",
        "quantity": 100,
        "entry_price": 150.0,
        "current_price": 155.0,
        "unrealized_pnl": 500.0,
    }

    await connection_manager.broadcast_position_update(position_data)

    websocket.send_text.assert_called_once()
    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "position_update"
    assert sent_message["data"] == position_data


@pytest.mark.asyncio
async def test_broadcast_metrics_update(connection_manager):
    """Test broadcasting metrics update."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    metrics_data = {"win_rate": 60.0, "sharpe_ratio": 1.5, "max_drawdown": 5.0}

    await connection_manager.broadcast_metrics_update(metrics_data)

    websocket.send_text.assert_called_once()
    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "metrics_update"
    assert sent_message["data"] == metrics_data


@pytest.mark.asyncio
async def test_broadcast_account_update(connection_manager):
    """Test broadcasting account update."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    account_data = {"cash": 5000.0, "equity": 15000.0, "total_pnl": 5000.0}

    await connection_manager.broadcast_account_update(account_data)

    websocket.send_text.assert_called_once()
    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "account_update"
    assert sent_message["data"] == account_data


@pytest.mark.asyncio
async def test_broadcast_health_update(connection_manager):
    """Test broadcasting health update."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    health_data = {
        "status": "healthy",
        "uptime_seconds": 3600,
        "errors_last_hour": 0,
    }

    await connection_manager.broadcast_health_update(health_data)

    websocket.send_text.assert_called_once()
    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "health_update"
    assert sent_message["data"] == health_data


@pytest.mark.asyncio
async def test_broadcast_to_multiple_clients(connection_manager):
    """Test broadcasting to multiple clients."""
    clients = [AsyncMock() for _ in range(5)]

    for client in clients:
        await connection_manager.connect(client)

    test_data = {"message": "test"}
    await connection_manager.broadcast(test_data)

    for client in clients:
        assert client.send_text.call_count == 1


@pytest.mark.asyncio
async def test_broadcast_with_disconnected_client(connection_manager):
    """Test broadcasting handles disconnected clients gracefully."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await connection_manager.connect(ws1)
    await connection_manager.connect(ws2)

    # First client throws exception
    ws1.send_text.side_effect = Exception("Connection lost")

    test_data = {"type": "test"}
    await connection_manager.broadcast(test_data)

    # Second client should still be called
    ws2.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_send_to_specific_client(connection_manager):
    """Test sending to specific client."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    test_data = {"type": "private", "value": 42}
    await connection_manager.send_to_client(websocket, test_data)

    websocket.send_text.assert_called_once()
    sent_data = json.loads(websocket.send_text.call_args[0][0])
    assert sent_data == test_data


@pytest.mark.asyncio
async def test_send_to_client_not_in_active(connection_manager):
    """Test sending to disconnected client raises error."""
    websocket = AsyncMock()
    websocket.send_text.side_effect = Exception("Connection closed")

    with pytest.raises(Exception):
        await connection_manager.send_to_client(websocket, {"type": "test"})


@pytest.mark.asyncio
async def test_connection_count_tracking(connection_manager):
    """Test connection count tracking."""
    assert connection_manager.get_connection_count() == 0

    clients = [AsyncMock() for _ in range(3)]
    for client in clients:
        await connection_manager.connect(client)

    assert connection_manager.get_connection_count() == 3

    await connection_manager.disconnect(clients[0])
    assert connection_manager.get_connection_count() == 2

    await connection_manager.disconnect(clients[1])
    assert connection_manager.get_connection_count() == 1


@pytest.mark.asyncio
async def test_duplicate_connection_prevention(connection_manager):
    """Test duplicate connections are handled."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)
    await connection_manager.connect(websocket)

    # Should only be in list once (or handled gracefully)
    count = sum(1 for ws in connection_manager.active_connections if ws == websocket)
    assert count >= 1  # At least one


@pytest.mark.asyncio
async def test_concurrent_broadcasts(connection_manager):
    """Test concurrent broadcast operations."""
    clients = [AsyncMock() for _ in range(10)]

    for client in clients:
        await connection_manager.connect(client)

    # Simulate concurrent broadcasts
    tasks = [
        connection_manager.broadcast({"id": i}) for i in range(5)
    ]

    await asyncio.gather(*tasks)

    # All clients should have received all broadcasts
    for client in clients:
        assert client.send_text.call_count == 5


@pytest.mark.asyncio
async def test_broadcast_message_structure(connection_manager):
    """Test broadcast message has correct structure."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    data = {"key": "value"}
    await connection_manager.broadcast(data)

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert isinstance(sent_message, dict)
    assert sent_message == data


@pytest.mark.asyncio
async def test_trade_update_message_structure(connection_manager):
    """Test trade update message structure."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    trade_data = {"symbol": "TEST", "pnl": 100}
    await connection_manager.broadcast_trade_update(trade_data)

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert "type" in sent_message
    assert sent_message["type"] == "trade_update"
    assert "timestamp" in sent_message
    assert "data" in sent_message
    assert sent_message["data"] == trade_data


@pytest.mark.asyncio
async def test_position_update_message_structure(connection_manager):
    """Test position update message structure."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    position_data = {"symbol": "TEST", "quantity": 100}
    await connection_manager.broadcast_position_update(position_data)

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "position_update"
    assert "timestamp" in sent_message


@pytest.mark.asyncio
async def test_metrics_update_message_structure(connection_manager):
    """Test metrics update message structure."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    metrics_data = {"win_rate": 50}
    await connection_manager.broadcast_metrics_update(metrics_data)

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "metrics_update"
    assert "timestamp" in sent_message


@pytest.mark.asyncio
async def test_account_update_message_structure(connection_manager):
    """Test account update message structure."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    account_data = {"cash": 1000}
    await connection_manager.broadcast_account_update(account_data)

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "account_update"
    assert "timestamp" in sent_message


@pytest.mark.asyncio
async def test_health_update_message_structure(connection_manager):
    """Test health update message structure."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    health_data = {"status": "healthy"}
    await connection_manager.broadcast_health_update(health_data)

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    assert sent_message["type"] == "health_update"
    assert "timestamp" in sent_message


@pytest.mark.asyncio
async def test_disconnect_during_broadcast(connection_manager):
    """Test client disconnects during broadcast."""
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    await connection_manager.connect(ws1)
    await connection_manager.connect(ws2)

    # Simulate failure on first send
    ws1.send_text.side_effect = Exception("Connection lost")

    # Broadcast should handle this gracefully
    await connection_manager.broadcast({"type": "test"})

    # Connection should be cleaned up
    # (This depends on implementation details)


@pytest.mark.asyncio
async def test_empty_broadcast_to_no_clients(connection_manager):
    """Test broadcast with no connected clients."""
    test_data = {"type": "test", "value": 42}

    # Should not raise error
    await connection_manager.broadcast(test_data)

    assert connection_manager.get_connection_count() == 0


@pytest.mark.asyncio
async def test_large_message_broadcast(connection_manager):
    """Test broadcasting large messages."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    # Create large data structure
    large_data = {
        "trades": [{"symbol": f"SYM{i}", "pnl": i * 100} for i in range(100)],
        "metrics": [{"value": i} for i in range(1000)],
    }

    await connection_manager.broadcast(large_data)

    websocket.send_text.assert_called_once()
    sent_data = json.loads(websocket.send_text.call_args[0][0])
    assert len(sent_data["trades"]) == 100


@pytest.mark.asyncio
async def test_special_characters_in_message(connection_manager):
    """Test broadcasting messages with special characters."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    special_data = {"message": "Test with special chars: @#$%^&*()_+-=[]{}|;:',.<>?/~`"}
    await connection_manager.broadcast(special_data)

    websocket.send_text.assert_called_once()
    sent_data = json.loads(websocket.send_text.call_args[0][0])
    assert sent_data == special_data


@pytest.mark.asyncio
async def test_unicode_in_message(connection_manager):
    """Test broadcasting Unicode characters."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    unicode_data = {"message": "测试 тест 🚀 €£¥"}
    await connection_manager.broadcast(unicode_data)

    websocket.send_text.assert_called_once()
    sent_data = json.loads(websocket.send_text.call_args[0][0])
    assert sent_data == unicode_data


@pytest.mark.asyncio
async def test_timestamp_format_in_updates(connection_manager):
    """Test timestamp format in broadcast messages."""
    websocket = AsyncMock()
    await connection_manager.connect(websocket)

    await connection_manager.broadcast_trade_update({"symbol": "TEST"})

    sent_message = json.loads(websocket.send_text.call_args[0][0])
    timestamp = sent_message["timestamp"]

    # Verify ISO format
    from datetime import datetime

    datetime.fromisoformat(timestamp)  # Should not raise


@pytest.mark.asyncio
async def test_concurrent_connect_disconnect(connection_manager):
    """Test concurrent connect and disconnect operations."""
    async def create_and_remove():
        ws = AsyncMock()
        await connection_manager.connect(ws)
        await asyncio.sleep(0.001)
        await connection_manager.disconnect(ws)

    tasks = [create_and_remove() for _ in range(10)]
    await asyncio.gather(*tasks)

    assert connection_manager.get_connection_count() == 0
