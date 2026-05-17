# ORB No-TP Strategy — Validation Roadmap Before Optimization

## Current Status

The latest analysis significantly upgraded confidence in the ORB strategy structure.

Key findings:
- Removing fixed 2R TP transformed expectancy:
  - from: `-0.012R`
  - to: `+0.11R`
- Behavior now matches a plausible convex trend-following system:
  - lower win rate
  - larger tail winners
  - positive expectancy
- 6 of 7 years profitable
- 2020 still fails, which is actually a healthy realism signal

This suggests:
- the entry logic may contain real edge
- previous exit design was likely destroying convexity

However:
- strategy is NOT yet considered production-ready
- optimization should NOT begin yet

The next phase is:
> validation and robustness confirmation.

Only after passing these checks should optimization begin.

---

# Validation Objectives

To consider the strategy genuinely promising, we need evidence that:

1. Edge survives out-of-sample
2. Edge survives realistic execution friction
3. Edge is not driven by a tiny number of outlier trades
4. Edge is structurally coherent
5. Edge generalizes across long/short and regimes
6. Exit logic preserves convexity without overfitting

---

# Phase 1 — Distribution & Convexity Validation

## 1. Trade Distribution Analysis (Highest Priority)

### Goal
Confirm strategy truly has convex payoff structure.

### Required Analysis
Generate:
- R-multiple histogram
- cumulative contribution curve
- top 1%, 5%, 10% trade contribution
- skewness
- kurtosis

### Questions
- Are profits broadly distributed?
- Or do a few extreme trades carry everything?
- Is the payoff structure stable?

### Risk
If:
- top 10 trades generate most profits
then:
- live reliability may be weak
- strategy may be statistically fragile

### Desired Outcome
- positive skew
- convex payoff
- but not extreme over-concentration

---

## 2. Time-In-Trade Analysis

### Goal
Understand where profits emerge intraday.

### Required Analysis
Measure:
- average holding duration
- expectancy by holding duration
- cumulative PnL by hour
- exit time distribution

### Questions
- Does edge primarily emerge late-day?
- Is EOD drift carrying the strategy?
- Are winners mostly afternoon trend extensions?

### Desired Outcome
Clear understanding of:
- intraday edge mechanics
- time-based behavior

---

# Phase 2 — Out-of-Sample Validation

## 3. 2025 Out-of-Sample Test (Critical)

### Goal
Validate whether edge persists beyond research sample.

### Requirements
Run:
- Jan 2025 → current date

WITHOUT:
- parameter changes
- threshold tuning
- strategy modifications

### Evaluate
- expectancy
- PF
- Sharpe
- drawdown
- regime behavior

### Desired Outcome
- positive or near-stable expectancy
- no catastrophic degradation

### Failure Condition
If:
- 2025 collapses significantly
then:
- likely overfit or regime-specific edge

---

# Phase 3 — Execution Robustness

## 4. Slippage Stress Testing (Critical)

### Goal
Determine whether edge survives realistic execution degradation.

### Test Matrix
Run:
- 5 ticks
- 10 ticks
- 15 ticks

for:
- entries
- stops
- exits

### Evaluate
- expectancy degradation
- PF degradation
- Sharpe degradation

### Desired Outcome
Edge remains:
- positive
- economically meaningful

even under worse friction assumptions.

### Failure Condition
If:
- edge disappears rapidly
then:
- edge likely too fragile for live deployment

---

## 5. Spread / Open Auction Approximation

### Goal
Model realistic opening conditions.

### Add:
- volatility-scaled spread penalty
- open execution penalty
- optional delayed fill simulation

### Questions
- Is opening fill assumption too optimistic?
- Are breakout fills realistic?

---

# Phase 4 — Structural Validation

## 6. Long vs Short Decomposition

### Goal
Determine whether one side carries the strategy.

### Analyze Separately
- long-only
- short-only

### Evaluate
- expectancy
- PF
- Sharpe
- yearly stability

### Questions
- Are longs carrying everything?
- Are shorts only useful in panic regimes?
- Is one side structurally weak?

### Desired Outcome
Understand:
- true directional edge structure

---

## 7. Regime Validation Re-Test

### Goal
Re-evaluate regime relationships under no-TP structure.

### Important
Previous TP-based conclusions may now be invalid.

### Re-Test:
- ATR filters
- volatility regimes
- trend regimes
- gap behavior

### Desired Outcome
Determine:
- whether filters still matter
- whether edge is now broader and more stable

---

# Phase 5 — Exit Logic Research

## 8. Trailing Stop Research

### Goal
Improve drawdown profile without destroying convexity.

### Start Simple
Test:
- move stop to breakeven after +1R
- trail prior 5m low/high
- ATR trailing stop

### Compare Against
- pure EOD exit

### Evaluate
- expectancy
- PF
- skewness
- max drawdown
- tail preservation

### Warning
Do NOT:
- aggressively optimize trailing parameters

Goal:
- preserve convexity
NOT:
- maximize backtest CAGR

---

# Phase 6 — Framework Integrity Validation

## 9. Randomized Robustness Tests

### Goal
Ensure framework is not hallucinating edge.

### Run:
- randomized entries
- shuffled trade outcomes

### Expected
No stable positive expectancy.

---

## 10. Future Leakage Audit

### Goal
Ensure causal correctness.

### Validate
For all features:
    feature_timestamp <= entry_timestamp

### Add Assertions
Hard-fail on leakage.

---

# Criteria Before Optimization Begins

Optimization should ONLY begin if most of the following hold:

| Validation | Required Outcome |
|---|---|
| 2025 OOS | Positive or stable |
| Slippage Stress | Edge survives 10+ ticks |
| Distribution Analysis | Not dominated by tiny outlier set |
| Long/Short Analysis | Coherent directional structure |
| Regime Analysis | Stable relationships |
| Execution Realism | No major degradation |
| Trailing Stop Tests | Convexity preserved |
| Leakage Audit | Clean |

---

# When Optimization Should Begin

Only after:
- edge appears real
- edge survives realism tests
- edge survives OOS validation
- framework integrity is trusted

THEN optimization becomes worthwhile.

Otherwise:
- optimization risks amplifying noise or artifacts.

---

# Optimization Philosophy (Future Phase)

When optimization begins:

## Prioritize:
- robustness
- broad stable regions
- monotonic relationships
- simplicity
- causal explanations

## Avoid:
- narrow thresholds
- parameter spikes
- year-specific tuning
- maximizing CAGR blindly

Goal:
- durable edge
NOT:
- best historical curve.

---

# Current Assessment

The strategy has evolved from:
> "possibly no real edge"

to:
> "plausible convex breakout edge with promising structure"

This is a major improvement.

However:
the strategy is currently in:
> advanced validation phase

NOT:
> production certainty phase.

The next milestone is proving:
- robustness
- realism
- persistence
under stress and out-of-sample conditions.