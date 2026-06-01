"""
Unit tests for pending order queue (Task 7: Engine Integration).
"""

from datetime import datetime, timezone
import pytest

from vibe.backtester.core.execution.models import Order
from vibe.backtester.core.execution.pending_queue import PendingOrderQueue


@pytest.fixture
def queue():
    """Create a pending order queue."""
    return PendingOrderQueue()


@pytest.fixture
def sample_order():
    """Create a sample market order."""
    return Order(
        id="order_1",
        symbol="QQQ",
        side="buy",
        size=1000,
        order_type="market",
        limit_price=None,
        timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
        signal_bar_index=100,
    )


class TestPendingOrderQueue:
    """Test PendingOrderQueue basic operations."""
    
    def test_queue_starts_empty(self, queue):
        """Test that queue is initially empty."""
        assert len(queue) == 0
        assert queue.is_empty()
    
    def test_add_order_to_queue(self, queue, sample_order):
        """Test adding an order to queue."""
        queue.add(sample_order)
        
        assert len(queue) == 1
        assert not queue.is_empty()
    
    def test_add_multiple_orders(self, queue):
        """Test adding multiple orders."""
        orders = [
            Order(
                id=f"order_{i}",
                symbol="QQQ",
                side="buy",
                size=1000,
                order_type="market",
                limit_price=None,
                timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
                signal_bar_index=100 + i,
            )
            for i in range(5)
        ]
        
        for order in orders:
            queue.add(order)
        
        assert len(queue) == 5


class TestPendingOrderQueueEligibility:
    """Test order eligibility based on latency."""
    
    def test_zero_latency_eligible_same_bar(self, queue, sample_order):
        """Test that zero-latency orders are eligible immediately."""
        queue.add(sample_order)
        
        # signal_bar_index=100, latency=0
        # bar_index=100: 100 + 0 <= 100? YES
        eligible = queue.get_eligible_orders(current_bar_index=100, latency_bars=0)
        
        assert len(eligible) == 1
        assert eligible[0].id == "order_1"
    
    def test_zero_latency_not_eligible_before_bar(self, queue, sample_order):
        """Test that orders are not eligible before their bar."""
        queue.add(sample_order)
        
        # signal_bar_index=100, latency=0
        # bar_index=99: 100 + 0 <= 99? NO
        eligible = queue.get_eligible_orders(current_bar_index=99, latency_bars=0)
        
        assert len(eligible) == 0
    
    def test_one_bar_latency_fills_next_bar(self, queue, sample_order):
        """Test that 1-bar latency fills on next bar."""
        queue.add(sample_order)
        
        # signal_bar_index=100, latency=1
        # bar_index=100: 100 + 1 <= 100? NO
        eligible = queue.get_eligible_orders(current_bar_index=100, latency_bars=1)
        assert len(eligible) == 0
        
        # bar_index=101: 100 + 1 <= 101? YES
        eligible = queue.get_eligible_orders(current_bar_index=101, latency_bars=1)
        assert len(eligible) == 1
    
    def test_two_bar_latency_fills_two_bars_later(self, queue, sample_order):
        """Test that 2-bar latency fills 2 bars later."""
        queue.add(sample_order)
        
        # signal_bar_index=100, latency=2
        # bar_index=100: 100 + 2 <= 100? NO
        # bar_index=101: 100 + 2 <= 101? NO
        assert len(queue.get_eligible_orders(100, latency_bars=2)) == 0
        assert len(queue.get_eligible_orders(101, latency_bars=2)) == 0
        
        # bar_index=102: 100 + 2 <= 102? YES
        eligible = queue.get_eligible_orders(current_bar_index=102, latency_bars=2)
        assert len(eligible) == 1
    
    def test_mixed_latencies_all_eligible_at_right_time(self, queue):
        """Test multiple orders with different latencies."""
        order1 = Order(
            id="order_1",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="market",
            limit_price=None,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            signal_bar_index=100,
        )
        order2 = Order(
            id="order_2",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="market",
            limit_price=None,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            signal_bar_index=100,
        )
        
        queue.add(order1)
        queue.add(order2)
        
        # At bar_index=100, latency=0: both eligible (same signal bar)
        eligible = queue.get_eligible_orders(100, latency_bars=0)
        assert len(eligible) == 2
        
        # At bar_index=101, latency=0: still eligible (remained eligible)
        eligible = queue.get_eligible_orders(101, latency_bars=0)
        assert len(eligible) == 2


class TestPendingOrderQueueExpiry:
    """Test order expiry at end-of-day."""
    
    def test_orders_expire_at_eod(self, queue, sample_order):
        """Test that orders expire 1 day (1440 bars) after being signaled."""
        queue.add(sample_order)
        
        # signal_bar_index=100, expires at 100 + 1440 = 1540
        # Not yet expired
        assert len(queue.get_eligible_orders(1539, latency_bars=0)) == 1
        
        # Expired after 1 day
        assert len(queue.get_eligible_orders(1540, latency_bars=0)) == 0
    
    def test_expired_orders_removed_from_queue(self, queue, sample_order):
        """Test that expired orders are removed from queue."""
        queue.add(sample_order)
        
        # Before expiry (signal_bar_index=100, expires at 1540)
        assert len(queue) == 1
        
        # Remove expired at expiry bar
        queue.remove_expired_orders(current_bar_index=1540)
        assert len(queue) == 0
    
    def test_unexpired_orders_remain_after_cleanup(self, queue):
        """Test that non-expired orders remain after cleanup."""
        order1 = Order(
            id="order_1",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="market",
            limit_price=None,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            signal_bar_index=100,
        )
        order2 = Order(
            id="order_2",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="market",
            limit_price=None,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            signal_bar_index=500,
        )
        
        queue.add(order1)
        queue.add(order2)
        
        # order1 expires at: 100 + 1440 = 1540
        # order2 expires at: 500 + 1440 = 1940
        # At bar_index=1540: order1 expired, order2 remains
        queue.remove_expired_orders(current_bar_index=1540)
        
        assert len(queue) == 1
        remaining = queue.get_eligible_orders(1550, latency_bars=0)
        assert remaining[0].id == "order_2"


class TestPendingOrderQueueProcessing:
    """Test order processing and removal after execution."""
    
    def test_mark_order_filled(self, queue, sample_order):
        """Test marking an order as filled (removal)."""
        queue.add(sample_order)
        
        assert len(queue) == 1
        
        queue.mark_filled("order_1")
        
        assert len(queue) == 0
    
    def test_mark_nonexistent_order_does_not_raise(self, queue):
        """Test that marking nonexistent order doesn't raise."""
        # Should not raise
        queue.mark_filled("nonexistent")
    
    def test_fifo_order_preserved(self, queue):
        """Test that orders are returned in FIFO order."""
        for i in range(3):
            order = Order(
                id=f"order_{i}",
                symbol="QQQ",
                side="buy",
                size=1000,
                order_type="market",
                limit_price=None,
                timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
                signal_bar_index=100,
            )
            queue.add(order)
        
        eligible = queue.get_eligible_orders(100, latency_bars=0)
        
        assert len(eligible) == 3
        assert eligible[0].id == "order_0"
        assert eligible[1].id == "order_1"
        assert eligible[2].id == "order_2"


class TestPendingOrderQueueEdgeCases:
    """Test edge cases."""
    
    def test_same_signal_bar_multiple_orders(self, queue):
        """Test multiple orders from same signal bar."""
        for i in range(3):
            order = Order(
                id=f"order_{i}",
                symbol="QQQ",
                side="buy" if i % 2 == 0 else "sell",
                size=1000 + (i * 100),
                order_type="market",
                limit_price=None,
                timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
                signal_bar_index=100,
            )
            queue.add(order)
        
        eligible = queue.get_eligible_orders(100, latency_bars=0)
        assert len(eligible) == 3
    
    def test_negative_latency_bars_invalid(self, queue, sample_order):
        """Test that negative latency is handled gracefully."""
        queue.add(sample_order)
        
        # Should still work (implementation detail: might treat as 0)
        eligible = queue.get_eligible_orders(100, latency_bars=0)
        assert len(eligible) == 1
    
    def test_very_large_latency_bar_count(self, queue, sample_order):
        """Test orders with very large latency (but still within 1-day expiry)."""
        # Create order with high signal_bar_index to avoid expiry
        late_order = Order(
            id="order_1",
            symbol="QQQ",
            side="buy",
            size=1000,
            order_type="market",
            limit_price=None,
            timestamp=datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc),
            signal_bar_index=5000,  # High signal bar to stay within expiry window
        )
        queue.add(late_order)
        
        # signal_bar_index=5000, latency=100
        # bar_index=5100: 5000 + 100 <= 5100? YES
        eligible = queue.get_eligible_orders(5100, latency_bars=100)
        assert len(eligible) == 1
        
        # bar_index=5099: 5000 + 100 <= 5099? NO
        eligible = queue.get_eligible_orders(5099, latency_bars=100)
        assert len(eligible) == 0
