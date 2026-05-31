# PRD: Realistic Order Execution Simulator (TDD-Driven)

## 1. Overview

### Problem

Current backtesting assumes unrealistic fills (instant execution, mid-price fills, no liquidity constraints), leading to overly optimistic strategy performance.

### Goal

Build a modular execution simulation engine that produces **realistic trade fills** using:

* volume constraints
* slippage models
* latency effects
* (optional) order book simulation

### Non-goal (for v1)

* Full HFT-grade matching engine
* Exchange connectivity
* True tick-by-tick order book reconstruction

---

## 2. Core Principle: Test-Driven Design

Every component must be defined with:

1. **Expected behavior**
2. **Edge cases**
3. **Failing unit tests first**
4. Implementation only after tests are defined

---

## 3. System Architecture

```
Signal Engine → Execution Simulator → Portfolio Engine → PnL Report
```

We are building:

### Execution Simulator (core scope)

Responsible for converting:

```
Order → Trades (fills with price, size, timestamp)
```

---

## 4. Functional Requirements

## FR1 — Market Order Execution (Baseline)

### Behavior

A market order is filled using:

* available volume
* slippage model
* price impact model

### Inputs

* order_size
* side (buy/sell)
* bar OHLCV data

### Outputs

* list of fills:

  * price
  * quantity
  * timestamp

---

### TDD Test Cases

#### Test 1.1: Full fill within volume

```python
def test_market_order_fully_filled_when_volume_sufficient():
    order = MarketOrder(size=100)
    bar = OHLCV(volume=1000)

    fills = simulator.execute(order, bar)

    assert sum(f.qty for f in fills) == 100
```

#### Test 1.2: Partial fill when volume insufficient

```python
def test_partial_fill_when_volume_insufficient():
    order = MarketOrder(size=500)
    bar = OHLCV(volume=200)

    fills = simulator.execute(order, bar)

    assert sum(f.qty for f in fills) == 200
```

---

## FR2 — Volume Participation Model

### Behavior

Orders are executed as a fraction of available volume:

```
max_fill = participation_rate * bar_volume
```

Default:

* participation_rate = 0.1 (10%)

---

### Tests

```python
def test_participation_limit_applied():
    simulator.participation_rate = 0.1
    order = MarketOrder(size=1000)
    bar = OHLCV(volume=500)

    fills = simulator.execute(order, bar)

    assert sum(f.qty for f in fills) <= 50
```

---

## FR3 — Slippage Model

### Behavior

Execution price deviates from mid/close based on:

* volatility proxy
* trade size

### Simple model:

```
slippage = k * sqrt(order_size / volume)
```

---

### Tests

```python
def test_slippage_increases_with_size():
    small_order = MarketOrder(size=10)
    large_order = MarketOrder(size=1000)

    small_fill = simulator.execute(small_order, bar)[0].price
    large_fill = simulator.execute(large_order, bar)[0].price

    assert abs(large_fill - mid_price) > abs(small_fill - mid_price)
```

---

## FR4 — Latency Model

### Behavior

Orders are executed with delay:

```
execution_time = signal_time + latency
```

Default:

* latency = 1 bar (v1 simplification)

---

### Tests

```python
def test_latency_delays_execution():
    simulator.latency_bars = 2

    fills = simulator.execute(order, bars, signal_bar=0)

    assert fills.execution_bar == 2
```

---

## FR5 — Limit Order Execution (Simplified v1)

### Behavior

A limit order:

* fills only if price crosses limit
* fills partially based on volume

---

### Tests

```python
def test_limit_order_not_filled_if_price_not_reached():
    order = LimitOrder(price=105)
    bar = OHLCV(high=100)

    fills = simulator.execute(order, bar)

    assert len(fills) == 0
```

```python
def test_limit_order_fills_when_price_crossed():
    order = LimitOrder(price=100)
    bar = OHLCV(low=95, high=110, volume=1000)

    fills = simulator.execute(order, bar)

    assert len(fills) > 0
```

---

## FR6 — Market Impact Model

### Behavior

Large orders move price:

```
impact = k * sqrt(order_size / ADV)
```

Execution price adjusted accordingly.

---

### Tests

```python
def test_large_order_has_more_price_impact():
    small = simulator.execute(MarketOrder(10), bar)
    large = simulator.execute(MarketOrder(1000), bar)

    assert avg_price(large) > avg_price(small)
```

---

## 5. Data Model

### Order

```python
@dataclass
class Order:
    id: str
    side: "buy" | "sell"
    size: float
    order_type: "market" | "limit"
    limit_price: float | None
    timestamp: datetime
```

---

### Fill

```python
@dataclass
class Fill:
    order_id: str
    price: float
    qty: float
    timestamp: datetime
```

---

### OHLCV Bar

```python
@dataclass
class OHLCV:
    open: float
    high: float
    low: float
    close: float
    volume: float
```

---

## 6. Non-Functional Requirements

* Deterministic outputs for same seed/data
* Fully unit-testable components
* Modular execution models (pluggable slippage / impact)
* Runs on vectorized backtest loop efficiently

---

## 7. TDD Implementation Plan (Build Order)

### Phase 1 (Core correctness)

1. Market order fills
2. Volume constraint logic
3. Basic fill aggregation

### Phase 2 (Realism)

4. Slippage model
5. Latency model

### Phase 3 (Strategy realism)

6. Limit orders
7. Market impact

### Phase 4 (upgrade path)

8. Optional L2 order book simulator

---

## 8. Definition of Done

A strategy is considered “execution-realistic tested” if:

* It passes all FR1–FR6 tests
* Backtest results degrade meaningfully vs naive fill model
* PnL variance increases when slippage enabled
* Performance stability holds under different liquidity regimes

---

## 9. Key Insight (important)

If your strategy:

> “still works the same after this layer”

then either:

* it is extremely robust
  or
* your execution model is still too weak

---

If you want next step, I can help you turn this into:

* a real Python package structure (`execution_sim/`)
* pytest suite scaffold
* or integrate it directly into your ORB research pipeline so every optimization run automatically includes realistic fills

Just say the direction.
