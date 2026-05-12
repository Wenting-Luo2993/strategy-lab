# Unit Test / Validation Spec — Regime Research Framework

The goal of these tests is NOT only correctness, but also prevention of:

* future leakage
* silent feature misalignment
* fake regime relationships
* broken attribution logic

The framework should fail loudly if assumptions break.

---

## Test Priority Tiers

| Tier | Description |
|------|-------------|
| **P0** | Must pass before trusting any research output. Catches catastrophic bugs: leakage, wrong attribution, broken metrics. |
| **P1** | Important for research integrity. Catches edge cases and silent data errors. |
| **P2** | Statistical guardrails and performance tests. Catches overfitting signals and scalability issues. |

---

# 1. Feature Engine Tests

## Test: Feature row count preserved — P1

### Goal

Ensure feature generation does not accidentally drop rows.

### Input

100 OHLCV rows

### Expected

Output feature dataframe:

* same index length
* same timestamps preserved

---

## Test: No future leakage in rolling indicators — P0

### Example

ATR(14), MA(20), rolling volatility.

### Validation

For timestamp `T`:

* feature value must depend only on data <= `T`

### Method

Create synthetic dataset:

* sudden price spike at row 50

Verify:

* rows < 50 unaffected
* feature only changes at/after 50

---

## Test: Gap calculation correctness — P1

### Input

Known synthetic daily bars:

```text
Prev close = 100
Open = 102
```

### Expected

```text
gap_pct = 2%
```

---

## Test: Trend slope direction correctness — P1

### Input

Synthetic monotonic uptrend

### Expected

Positive slope.

Repeat with downtrend:
Negative slope.

---

## Test: Percentile feature boundedness — P1

### Expected

Percentile-based features:

* always within [0, 1]
  or [0, 100], depending on implementation.

Fail otherwise.

---

# 2. Trade Attribution Tests

## Test: No future leakage in attribution — P0

### Critical

### Goal

Trade must only receive feature values available BEFORE entry.

### Method

Create:

* trade at timestamp T
* artificially modify feature value at T+1

### Expected

Trade attribution unchanged.

---

## Test: Correct timestamp alignment — P0

### Goal

Trade receives feature snapshot from correct bar.

### Example

Trade entry:

```text
09:45
```

Expected:

* feature values from <=09:45 only

NOT:

* later bars

---

## Test: Missing feature handling — P1

### Goal

System behaves safely when feature unavailable.

### Example

MA(50) during first 20 days.

### Expected

Either:

* NaN assigned explicitly
  OR
* trade excluded with logged reason

No silent filling.

---

## Test: Trade count preserved after attribution — P0

### Expected

Number of trades before and after attribution identical.

---

# 3. Regime Analyzer Tests

## Test: Bucket partition integrity — P0

### Goal

All trades assigned to exactly one bucket.

### Expected

Sum of bucket counts == total trades.

No duplicates.
No missing trades.

---

## Test: Conditional metric correctness — P0

### Input

Synthetic trades with known expectancy.

### Expected

Analyzer computes:

* expectancy
* win rate
* PF
  correctly.

---

## Test: Monotonicity detector sanity — P1

### Input

Synthetic data:

* expectancy rises with ATR

### Expected

Monotonic trend detected.

---

## Test: Stability analysis consistency — P2

### Input

Identical yearly datasets.

### Expected

Yearly regime metrics identical.

---

# 4. Filter Evaluator Tests

## Test: Filter removes correct trades — P0

### Example

Filter:

```text
ATR_pctile < 0.5
```

### Expected

Only qualifying trades remain.

---

## Test: No-filter baseline equivalence — P0

### Goal

Empty filter should reproduce original strategy metrics exactly.

### Expected

Identical:

* expectancy
* trade count
* Sharpe
* drawdown

---

## Test: Filter cannot increase trade count — P1

### Expected

Filtered trade count <= original count always.

---

## Test: Extreme filter sanity — P1

### Example

Impossible filter:

```text
ATR_pctile > 2
```

### Expected

Zero trades returned cleanly.
No crash.

---

## Test: Filter metric recomputation — P1

### Goal

Metrics recomputed from filtered trades only.

### Validate

No stale cached metrics.

---

# 5. Reporting Tests

## Test: Report reproducibility — P2

### Goal

Same config + same seed → identical report outputs.

---

## Test: JSON serialization integrity — P2

### Expected

All report outputs serialize cleanly.

No NaNs causing corruption.

---

## Test: Markdown report generation — P2

### Expected

Report generated successfully even:

* no profitable filters
* no trades after filtering

---

# 6. Statistical Guardrail Tests

These are VERY important.

---

## Test: Random strategy produces no stable edge — P1

### Goal

Framework should not falsely discover strong filters on random trades.

### Method

Generate:

* random entries/exits

Run regime analysis.

### Expected

No:

* strong monotonic relationships
* stable profitable filters

This protects against data mining bugs.

---

## Test: Shuffled trade robustness — P1

### Method

Shuffle trade outcomes randomly.

### Expected

Most regime relationships disappear.

If not:

* leakage or analyzer bug likely.

---

# 7. Future Leakage Detection Suite (CRITICAL)

This deserves its own category.

---

## Test: Intentional future leak injection — P2

### Method

Create fake feature:

```python
future_return_5d
```

Run analyzer.

### Expected

System should:

* flag feature as future-derived
  OR
  require explicit override

---

## Test: Timestamp causality validation — P0

### Expected

Every feature timestamp:

```text
feature_time <= trade_entry_time
```

Hard assert.

---

# 8. Performance / Scalability Tests

## Test: Large dataset handling — P2

### Input

Multi-year 1-min dataset.

### Expected

Pipeline completes without:

* memory explosion
* quadratic slowdowns

---

## Test: Feature caching consistency — P2

If caching implemented:

### Expected

Cached and fresh computations identical.

---

# 9. Research Integrity Tests (VERY IMPORTANT)

These are conceptual guardrails.

---

## Test: Narrow-threshold overfit warning — P2

### Example

Best filter:

```text
ATR between 0.423 and 0.427
```

### Expected

System flags:

* likely overfit
* unstable threshold

---

## Test: Tiny sample warning — P2

### Example

Filter leaves:

```text
12 trades
```

### Expected

Framework warns:

* insufficient sample size

---

## Test: Stability degradation warning — P2

### Goal

Flag filters that:

* work only in one year
* fail elsewhere

---

# Recommended Testing Philosophy

Use:

* deterministic synthetic datasets
* tiny controlled OHLCV samples
* explicit expected outputs

Do NOT rely only on:

* real market data
  because:
* ambiguous failures
* hard-to-debug drift

---

# Most Important Tests (If Prioritizing)

**P0 — implement before writing any research code:**
1. No future leakage in rolling indicators
2. No future leakage in attribution (critical)
3. Timestamp causality validation (hard assert)
4. Trade count preserved after attribution
5. Bucket partition integrity
6. Conditional metric correctness
7. Filter removes correct trades
8. No-filter baseline equivalence

**P1 — implement before trusting any regime finding:**
1. Feature row count preserved
2. Gap calculation correctness
3. Trend slope direction correctness
4. Percentile feature boundedness
5. Missing feature handling
6. Filter cannot increase trade count
7. Extreme filter sanity
8. Filter metric recomputation
9. Random strategy produces no stable edge
10. Shuffled trade robustness
11. Monotonicity detector sanity

**P2 — implement before promoting a filter to production:**
1. Stability analysis consistency
2. Narrow-threshold overfit warning
3. Tiny sample warning
4. Stability degradation warning
5. Report reproducibility / JSON serialization
6. Intentional future leak injection guard
7. Large dataset / caching tests

Those catch most catastrophic research bugs.

---

# Final Guidance

The most dangerous failure mode is NOT:

* crashes

It is:

> producing believable but false research conclusions.

These tests are designed primarily to prevent that.
