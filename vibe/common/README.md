# Vibe Common - Shared Components Foundation

This module contains shared components used by both the trading bot and backtester. It provides a foundation for consistent data models and abstract interfaces that enable multiple implementations.

## Structure

### `models/`
Core data models used throughout the system:
- **Bar**: OHLCV candlestick data
- **Order**: Trading order with status tracking
- **OrderStatus**: Order state enumeration (CREATED, PENDING, SUBMITTED, PARTIAL, FILLED, CANCELLED, REJECTED)
- **Position**: Open trading position
- **Trade**: Completed trade with P&L calculations
- **Signal**: Trading signal from strategies
- **AccountState**: Account state and statistics

All models are built with Pydantic for validation and serialization.

### `execution/`
Abstract interfaces for order execution:
- **ExecutionEngine**: ABC for order execution, order cancellation, and position/account queries
- **OrderResponse**: Dataclass for standardized execution responses

### `data/`
Abstract interfaces for data access:
- **DataProvider**: ABC for OHLCV bar data and current price queries
- Defines standard column names: OPEN, HIGH, LOW, CLOSE, VOLUME

### `clock/`
Time management interfaces:
- **Clock**: ABC for time queries and market hours checking
- **LiveClock**: Real-time clock implementation for live trading
- **is_market_open()**: Helper function to check NYSE trading hours (9:30 AM - 4:00 PM ET, Mon-Fri)

### `strategies/`, `indicators/`, `risk/`, `validation/`, `utils/`
Placeholder directories for future implementation

## Usage

### Data Models
```python
from vibe.common.models import Bar, Order, OrderStatus, Trade
from datetime import datetime

# Create a bar
bar = Bar(
    timestamp=datetime.now(),
    open=100.0,
    high=105.0,
    low=99.0,
    close=102.0,
    volume=1000000
)

# Create a trade with automatic P&L calculation
trade = Trade(
    symbol="AAPL",
    side="buy",
    quantity=10,
    entry_price=100,
    exit_price=110
)
print(f"P&L: {trade.pnl}")  # Output: P&L: 100.0
```

### Execution Engine
```python
from vibe.common.execution import ExecutionEngine, OrderResponse

class MyExecutionEngine(ExecutionEngine):
    async def submit_order(self, symbol, side, quantity, order_type="limit", price=None):
        # Implementation
        return OrderResponse(...)

    async def cancel_order(self, order_id):
        # Implementation
        pass

    # ... implement other abstract methods
```

### Data Provider
```python
from vibe.common.data import DataProvider

class MyDataProvider(DataProvider):
    async def get_bars(self, symbol, timeframe="1m", limit=None, start_time=None, end_time=None):
        # Return DataFrame with columns: open, high, low, close, volume
        pass

    async def get_current_price(self, symbol):
        # Return current price
        pass

    # ... implement other abstract methods
```

### Clock
```python
from vibe.common.clock import LiveClock, is_market_open
from datetime import datetime

clock = LiveClock()
now = clock.now()
is_open = clock.is_market_open()

# Check if market is open at specific time
if is_market_open(datetime(2025, 2, 3, 10, 30)):
    print("Market is open")
```

## Testing

Run all tests with:
```bash
pytest vibe/tests/common/ -v
```

Test coverage includes:
- **Project structure** (Task 0.1): Module imports and circular dependency checks
- **Data models** (Task 0.2): Model validation, serialization, and P&L calculations
- **Execution interface** (Task 0.3): ABC enforcement and OrderResponse
- **Data provider interface** (Task 0.4): ABC enforcement and return types
- **Clock interface** (Task 0.5): ABC enforcement, LiveClock, and market hours logic

## Phase 0 Implementation

### Task 0.1: Shared Project Structure Setup
- Created `vibe/` root and `vibe/common/` subdirectories
- Established proper package structure with `__init__.py` and `__all__` exports
- All imports work without circular dependencies

### Task 0.2: Shared Data Models
- Implemented 7 data models with Pydantic validation
- All models support JSON serialization/deserialization
- Trade model includes automatic P&L calculations

### Task 0.3: Abstract Execution Interface
- Defined ExecutionEngine ABC with 5 abstract methods
- OrderResponse dataclass for standardized responses
- Clear method signatures for order submission, cancellation, and querying

### Task 0.4: Abstract Data Provider Interface
- Defined DataProvider ABC with 3 abstract methods
- Standard column name constants for OHLCV data
- Supports flexible bar queries with filtering

### Task 0.5: Abstract Clock Interface
- Defined Clock ABC with 2 abstract methods
- LiveClock implementation using system time
- Market hours function for NYSE (9:30 AM - 4:00 PM ET, Mon-Fri)

## Dependencies

- **pydantic**: Data model validation and serialization
- **pandas**: DataFrame support for data providers
- **pytz**: Timezone support for market hours

## Future Extensions

Phase 1 and beyond will implement:
- Configuration system
- Logging infrastructure
- SQLite trade store
- Metrics storage
- Trading service orchestration
- Health check endpoints
