# ADR-017: Broker Abstraction Protocol (Pluggable Execution Backends)

**Date**: 2026-06-14

**Status**: ✅ Accepted (Foundation for IB Integration)

---

## Context

The trading bot currently uses `MockExchange` for backtesting and needs to support live broker execution (Interactive Brokers, and potentially others in the future). The system must:

1. Support multiple broker implementations (IB, Alpaca, TD Ameritrade, etc.)
2. Maintain consistent order/position/account interfaces across brokers
3. Allow seamless switching between mock (backtest) and live (IB) execution
4. Follow the successful patterns from ROES (Protocol-based pluggable models)
5. Persist execution-quality metrics for paper trading analysis
6. Keep P0 infrastructure at zero incremental cost

---

## Decision

Implement **Broker Abstraction Protocol** using Python's `Protocol` (structural typing) to define a broker-agnostic interface.

Also implement a broker execution telemetry path:
- Normalize IB fill events into `FillEvent`
- Record expected fill, actual fill, adverse slippage, slippage bps, latency, commission, and fill quantity
- Persist locally to SQLite for P0
- Optionally mirror to a free-tier Supabase table for a Vercel-hosted P1/P2 dashboard

**Core Pattern**:

```python
# vibe/trading_bot/brokers/base.py
from typing import Protocol, List, Dict, Any
from dataclasses import dataclass
from datetime import datetime

@dataclass
class BrokerOrder:
    """Broker-agnostic order representation."""
    broker_order_id: str          # Broker's order ID
    strategy_order_id: str        # Our internal ID
    symbol: str
    quantity: int
    side: str                     # "BUY" or "SELL"
    order_type: str               # "MARKET", "LIMIT", "STOP"
    price: float | None           # Limit/stop price
    filled: int = 0
    remaining: int = 0
    status: str = "PENDING"
    filled_price: float | None = None
    filled_time: datetime | None = None

class BrokerAPI(Protocol):
    """Any object implementing these methods can act as a broker."""
    
    async def connect(self) -> bool: ...
    async def disconnect(self) -> bool: ...
    async def get_account_info(self) -> Dict[str, Any]: ...
    async def submit_order(self, order: BrokerOrder) -> str: ...
    async def cancel_order(self, broker_order_id: str) -> bool: ...
    async def modify_order(self, broker_order_id: str, **kwargs) -> bool: ...
    async def get_positions(self) -> List[Dict[str, Any]]: ...
    async def get_order_status(self, broker_order_id: str) -> BrokerOrder: ...
```

**Implementations**:
1. `vibe/trading_bot/brokers/interactive_brokers.py` - Interactive Brokers (ib_insync)
2. `vibe/trading_bot/brokers/mock_exchange.py` - Mock for backtesting (Protocol-compliant wrapper)
3. Future: Alpaca, TD Ameritrade, etc.

**Configuration-Driven Selection**:

```python
# vibe/trading_bot/core/orchestrator.py
broker_config = config.get("trading", {}).get("broker", {})
if broker_config["type"] == "interactive_brokers":
    broker = InteractiveBrokersAPI(broker_config)
elif broker_config["type"] == "mock":
    broker = MockExchangeBroker(broker_config)
else:
    raise ValueError(f"Unknown broker: {broker_config['type']}")
```

---

## Alternatives Considered

### 1. Inheritance-Based (BaseExecutor)
```python
class BaseExecutor(ABC):
    @abstractmethod
    async def submit_order(self, ...): ...
```
**Rejected**: Rigid, requires explicit inheritance, harder to compose, doesn't match Python idioms

### 2. Dependency Injection with Type Hints Only
```python
def orchestrator(broker: Any):
    # Rely on duck typing, no formal interface
    await broker.submit_order(...)
```
**Rejected**: No compile-time safety, no documentation of required methods, easy to introduce bugs

### 3. Configuration-Free (Always IB)
```python
orchestrator = TradingOrchestrator(broker=InteractiveBrokersAPI(...))
```
**Rejected**: Hard-coded to IB, can't switch to mock for testing without code changes

### 4. Enum-Based Routing
```python
if mode == BrokerMode.IB: broker = ...
elif mode == BrokerMode.MOCK: broker = ...
```
**Rejected**: Doesn't scale, hard to add new brokers, mixing implementation details into core logic

---

## Reasoning

**Why Protocol (Structural Typing)?**
- ✅ Matches Python idioms (duck typing with static guarantees)
- ✅ No explicit inheritance boilerplate
- ✅ Type checkers (mypy, Pylance) verify interface compliance
- ✅ Similar to successful ROES pattern (ExecutionModel protocol)
- ✅ Easy to test with mock implementations

**Why Configuration-Driven?**
- ✅ Seamless switch between mock (dev) and live (prod) without code changes
- ✅ Environment-specific settings (paper vs live IB account)
- ✅ Enables A/B testing (mock vs realistic fills)

**Why Separate Order Dataclass?**
- ✅ Broker-agnostic representation (decouples from ib_insync Order class)
- ✅ Easy serialization for logging and persistence
- ✅ Clear contract between strategy logic and broker implementation

**Why Supabase + Vercel for P1/P2 Dashboard?**
- ✅ Free tiers satisfy the P0 zero-cost constraint during paper validation
- ✅ Supabase REST API works without running a custom backend
- ✅ Vercel can host a lightweight operational analytics dashboard
- ✅ Local SQLite remains the source of truth when remote metrics are disabled

---

## Consequences

✅ **Benefits**:
- Pluggable broker implementations without modifying core orchestrator
- Easy to add new brokers (implement Protocol, add config option)
- Testable: can use MockBroker for all tests, only hit real IB in E2E tests
- Type-safe: mypy verifies all brokers implement required methods
- Configuration intent is explicit (dev uses mock, prod uses IB)
- Execution metrics become queryable for paper-trading quality review
- Dashboard work can proceed without paid cloud infrastructure

⚠️ **Drawbacks**:
- Requires `from __future__ import annotations` for Python 3.10 compatibility
- Protocol doesn't enforce async context managers (need runtime checks)
- Broker-specific features require adapter pattern (e.g., IB margin rates)
- New developers must understand Protocol typing
- Supabase free tier has retention/usage limits, so longer-term production telemetry may need a paid or self-hosted database

---

## Implementation Plan

### Phase 1: Define Protocol & MockBroker
- Create `vibe/trading_bot/brokers/base.py` with BrokerAPI Protocol
- Wrap `MockExchange` with Protocol-compliant adapter
- Add `BrokerOrder` dataclass with validation

### Phase 2: Interactive Brokers Implementation
- Create `vibe/trading_bot/brokers/interactive_brokers.py`
- Implement all BrokerAPI methods using ib_insync
- Add connection retry logic and health checks

### Phase 3: Configuration & Orchestration
- Add `config.brokers` schema to Pydantic settings
- Implement broker factory in orchestrator
- Wire broker lifecycle (connect/disconnect) into phase managers

### Phase 4: Testing
- Unit tests for broker implementations
- Integration tests (mock + real IB paper account)
- E2E tests (full trading cycle)

### Phase 5: Operational Metrics Dashboard
- Create a separate Vercel-hostable dashboard project under `apps/operational-metrics-dashboard`
- Read operational metrics from Supabase REST API
- Visualize slippage, latency, fill quality, order status, and daily execution health
- Filter by broker, symbol, side, session, order id, and time window

---

## Related ADRs

- **ADR-014**: Realistic Order Execution Simulator (ROES) - similar Protocol pattern
- **ADR-015**: ROES Execution Mode Contract - pluggable execution modes
- **ADR-001**: Event-driven backtester - system architecture

---

## Files Affected

**New Files**:
- `vibe/trading_bot/brokers/__init__.py`
- `vibe/trading_bot/brokers/base.py` (Protocol definition)
- `vibe/trading_bot/brokers/interactive_brokers.py` (IB implementation)
- `vibe/trading_bot/storage/operational_metrics.py` (local/remote execution metrics)
- `scripts/ib_paper_smoke.py` (P0 end-to-end paper validation command)
- `vibe/tests/trading_bot/test_brokers.py`
- `apps/operational-metrics-dashboard/` (Vercel-ready operational metrics dashboard)

**Modified Files**:
- `vibe/trading_bot/config/settings.py` (add BrokerConfig)
- `vibe/trading_bot/requirements.txt` (add ib-insync)
- `vibe/trading_bot/pyproject.toml` (add ib-insync)
- `vibe/trading_bot/.env.example` (IB and operational metrics env vars)
- `config/local.yaml` (add broker and operational metrics settings)
- `config/dev.yaml` (add broker settings)
- `config/prod.yaml` (add broker settings)

---

**Canonical Guidance**: See [memory-bank/features/ib-broker-integration-guide.md](../features/ib-broker-integration-guide.md) for implementation steps.

