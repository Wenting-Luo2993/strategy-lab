# Realistic Order Execution Simulator — Design Document

## Overview

Upgrade the existing `FillSimulator` (fixed tick-based slippage, always-full fills, no volume/impact modeling) into a modular execution simulation engine with volume constraints, dynamic slippage, latency effects, market impact, and limit order support. The implementation builds on the existing event-driven backtester architecture (ADR-001) without breaking backward compatibility.

---

## Current State Analysis

### Existing Components (what we have)

| Component | File | Current Capability | Gap |
|-----------|------|-------------------|-----|
| `FillSimulator` | `core/fill_simulator.py` | Fixed tick slippage, always-full fills | No volume/impact/latency |
| `FillResult` | `core/fill_simulator.py` | symbol, side, qty, avg_price, commission | Missing timestamp, order_id |
| `BacktestEngine` | `core/engine.py` | Bar-by-bar loop, immediate execution | No pending orders, no latency queue |
| `PortfolioManager` | `core/portfolio.py` | Full position open/close, equity tracking | No partial fill handling |
| `Bar` (Pydantic) | `common/models/bar.py` | OHLCV with validation | Sufficient — already has volume |
| `Trade` (Pydantic) | `common/models/trade.py` | Full trade lifecycle | Sufficient |

### Key Constraint

The engine currently uses `price_override` on entries (ORB breakout price + slippage). This bypasses `FillSimulator`'s slippage logic for entries but still uses it for exits. The new design must preserve this pattern for ORB entries while enabling realistic fills for general use.

---

## Architecture

### Component Diagram

```
BacktestEngine (event loop)
  │
  ├── SimulatedClock
  ├── RuleSetRunner (strategy signals)
  ├── PortfolioManager (positions, equity)
  │
  └── ExecutionSimulator (NEW — replaces FillSimulator)
        │
        ├── OrderBook (pending orders queue)
        │
        ├── SlippageModel (Protocol)         ← pluggable
        │     ├── FixedTickSlippage           ← current behavior (default)
        │     └── SqrtVolumeSlippage          ← FR3
        │
        ├── VolumeModel (Protocol)            ← pluggable
        │     ├── UnlimitedVolume             ← current behavior (default)
        │     └── ParticipationRateVolume     ← FR2
        │
        └── ImpactModel (Protocol)            ← pluggable
              ├── NoImpact                    ← current behavior (default)
              └── SqrtImpact                  ← FR6
```

### Data Flow

```
Signal → Order (new dataclass)
  → OrderBook (if latency > 0, hold for N bars)
  → ExecutionSimulator.try_fill(order, bar)
    → VolumeModel.max_fill_qty(order_size, bar_volume)
    → SlippageModel.adjust_price(base_price, order_size, bar)
    → ImpactModel.price_impact(order_size, bar_volume, adv)
    → Fill (new dataclass, replaces FillResult)
  → PortfolioManager.open_position(fill, ...)
```

**Slippage + Impact Interaction:** Impact and slippage are applied additively: `final_price = base_price + slippage + impact`. This is a conservative estimate as they may partially overlap in practice (large orders experience both). For v1, additive is acceptable; future versions could use a combined model.

### Protocol Definitions

```python
from typing import Protocol

class SlippageModel(Protocol):
    def calculate(self, base_price: float, side: str,
                  order_size: float, bar: Bar) -> float:
        """Return adjusted fill price after slippage."""
        ...

class VolumeModel(Protocol):
    def max_fill_qty(self, order_size: float, bar_volume: float) -> float:
        """Return maximum fillable quantity for this bar."""
        ...

class ImpactModel(Protocol):
    def price_impact(self, order_size: float, bar_volume: float,
                     side: str, adv: float | None = None) -> float:
        """Return price adjustment from market impact (always positive)."""
        ...
```

### Data Models

```python
@dataclass
class Order:
    id: str
    symbol: str
    side: str              # "buy" | "sell"
    size: float
    order_type: str        # "market" | "limit"
    limit_price: float | None
    timestamp: datetime
    signal_bar_index: int  # bar index when signal was generated (for latency)
    price_override: float | None = None  # Skip slippage/impact if set (for ORB entries)

@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str
    price: float
    qty: float
    timestamp: datetime
    slippage: float        # price deviation from base
    impact: float          # price deviation from market impact
```

---

## Backward Compatibility Strategy

The engine currently works with `FillSimulator(slippage_ticks=N)`. To preserve this:

1. **Default models** replicate current behavior exactly:
   - `FixedTickSlippage(ticks=5)` — same as current `slippage_ticks`
   - `UnlimitedVolume()` — always fills full quantity (current behavior)
   - `NoImpact()` — zero market impact (current behavior)
   - `latency_bars=0` — immediate execution (current behavior)

2. **`FillSimulator` becomes a thin wrapper** around `ExecutionSimulator` with legacy defaults, so existing tests pass without changes.

3. **Engine integration** is opt-in: `BacktestEngine(execution_config=...)` enables realistic fills; without it, behavior is identical to today.

---

## Key Decisions

### 1. Protocol-based model pluggability (not inheritance)

Slippage, volume, and impact models use Python `Protocol` (structural typing) rather than ABC inheritance. This allows any callable/object with the right signature to work, avoids deep class hierarchies, and makes testing trivial with simple lambdas or dataclasses.

### 2. Latency via pending order queue (not bar offset)

Rather than shifting DataFrame indices (fragile, breaks with missing bars), the engine maintains a `pending_orders` list. Each bar tick, it checks if any pending order's `signal_bar_index + latency_bars <= current_bar_index`. This is simpler, handles gaps, and is closer to real execution.

### 3. Partial fills accumulate, unfilled remainder stays in queue

When volume constrains a fill, the filled portion creates a `Fill` and the remainder stays as a reduced `Order` in the pending queue. The portfolio handles partial positions. Unfilled orders expire at EOD (configurable).

### 4. ADV computed from trailing window, not global

Market impact model uses a rolling N-day average daily volume (default 20 days), computed from the same bar data the engine already loads. This avoids look-ahead bias and handles changing liquidity.

**Performance:** ADV is pre-computed once before the event loop (not per-bar) by resampling bar data to daily volumes and applying a rolling window. This is O(n) vs O(n × w) if computed per-bar, a 20x performance improvement.

### 5. One active signal per symbol

The portfolio can have at most one open position per symbol. New entry signals for symbols with existing positions (including partial fills) are ignored. This matches the current ORB one-trade-per-day behavior and avoids complex multi-entry accumulation logic.

---

## Risks & Considerations

1. **Performance**: Adding per-bar volume/impact calculations to the event loop adds overhead. For 7 years of 5-min data (~500k bars), this should still complete in seconds. ADV is pre-computed once before the loop to avoid O(n × w) cost. If per-bar calculations become a bottleneck, the models can be vectorized as a post-processing step.

   **Pending order queue:** Linear scan per bar is O(m) where m = pending orders (typically < 10). For worst-case m=100, this is 140M operations over 1.4M bars, still acceptable. Can optimize with time-indexed buckets if needed.

2. **Partial fill complexity**: Partial fills introduce state management complexity (partial positions, order remainder tracking, EOD cleanup). Phase 1 can ship without partial fills (just cap quantity, reject remainder) and add accumulation in Phase 2.

3. **ORB entry price override**: The current engine computes ORB entry price externally (`orb_high + $0.01 + slippage`). The new simulator must not double-apply slippage when `price_override` is used. The design handles this by skipping slippage/impact models when `price_override` is set, matching current behavior.

---

## File Organization

```
vibe/backtester/core/
├── engine.py                    # Modified — add pending orders, execution config
├── portfolio.py                 # Modified — handle partial fills
├── clock.py                     # Unchanged
├── fill_simulator.py            # Preserved as legacy wrapper
└── execution/                   # NEW module
    ├── __init__.py
    ├── models.py                # Order, Fill dataclasses
    ├── simulator.py             # ExecutionSimulator (orchestrator)
    ├── slippage.py              # SlippageModel protocol + implementations
    ├── volume.py                # VolumeModel protocol + implementations
    ├── impact.py                # ImpactModel protocol + implementations
    └── config.py                # ExecutionConfig dataclass
```
