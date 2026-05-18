Good—this is exactly the right mindset if you’re trying to turn ORB research into something production-grade instead of ad-hoc backtests.

Below is a **PRD for an Optimization Framework (Experimental Computation Infrastructure)** designed for trading strategy research: parameter sweeps, robustness, walk-forward, ranking, and stability scoring.

---

# PRD: Strategy Optimization Framework (v1)

## 1. Purpose

Build an experimental computation system leveraging the existing infrastructure that:

* Generates strategy variants via parameter sweeps
* Evaluates performance across time regimes
* Measures robustness and stability
* Performs walk-forward validation
* Ranks candidate strategies
* Produces diagnostic “surface analysis” (parameter sensitivity landscapes)

This system is **not live trading logic**. It is a **research + evaluation engine**.

---

## 2. Core Principles

* Deterministic runs (same input → same output)
* Fully reproducible experiments
* Separation of:

  * strategy logic
  * parameter space
  * evaluation metrics
  * execution engine
* Every result must be explainable via traceable inputs

---

## 3. System Architecture

### 3.1 Modules

1. **Indicator Generation Engine**

   * Pre-computes all technical indicators (ATR, ADX, slope, percentiles, etc.)
   * **CRITICAL**: Stores indicator data **before** parameter sweeping begins
   * **WHY**: Prevents redundant computation across parameter combinations
   * **CPU/TIME OPTIMIZATION**: Compute once, reuse for all parameter variants
   * Input: OHLCV data + indicator specs
   * Output: Cached feature table (indexed by timestamp)
   * Implementation: Leverage existing `FeatureEngine` from regime research framework

2. **Strategy Adapter Layer**

   * Wraps strategy into callable interface
   * Input: params + market data + pre-computed indicators
   * Output: trades or equity curve

3. **Parameter Space Generator**

   * Defines sweep ranges
   * Supports grid, random, Bayesian (future)

4. **Execution Engine**

   * Runs strategy variants over historical data
   * **Uses pre-computed indicators** (no re-calculation)
   * Parallelizable

5. **Evaluation Engine**

   * Computes metrics:

     * Sharpe
     * CAGR
     * Max drawdown
     * Win rate
     * Expectancy
     * Trade distribution stats

6. **Robustness Analyzer**

   * noise injection
   * parameter perturbation tests
   * sub-sample testing

7. **Walk-Forward Engine**

   * train window → test window rolling

8. **Ranking System**

   * multi-metric scoring function
   * stability weighting

9. **Surface Analyzer**

   * maps parameter space → performance surface
   * detects cliffs / flat regions

---

## 4. Data Model

### 4.1 StrategyVariant

```python
StrategyVariant:
    strategy_id: str
    parameters: dict
```

### 4.2 BacktestResult

```python
BacktestResult:
    variant_id: str
    equity_curve: list[float]
    trades: list[Trade]
    metrics: dict
```

### 4.3 EvaluationBundle

```python
EvaluationBundle:
    variant_id: str
    robustness_score: float
    stability_score: float
    walk_forward_score: float
    final_rank_score: float
```

---

## 5. Scoring Model (Initial)

Define:

```
final_score =
    0.4 * sharpe
  + 0.2 * stability_score
  + 0.2 * robustness_score
  + 0.2 * walk_forward_score
```

You will refine this later—but locking a formula early is important for TDD.

---

## 6. Functional Requirements

### FR1: Parameter Sweep

* Input: parameter grid
* Output: list of StrategyVariants

Supports:

* grid search
* bounded ranges (e.g. OR range 5–60 min)

---

### FR2: Backtest Execution

* Must support batch execution
* Must be deterministic
* Must cache results

---

### FR3: Robustness Testing

At minimum:

* Add noise to entry/exit thresholds ±x%
* Randomly drop trades (slippage simulation)
* Re-run N times

Output:

* variance of performance metrics
* robustness score (lower variance = higher score)

---

### FR4: Walk-Forward Analysis

* Split dataset into rolling windows:

Example:

* Train: 6 months
* Test: 1 month
* Roll forward by 1 month

Output:

* distribution of test performance
* degradation score (train vs test gap)

---

### FR5: Surface Analysis

* For 2D parameter pairs:

  * visualize performance surface
  * detect:

    * stable plateaus
    * sharp cliffs (overfitting risk)

---

### FR6: Candidate Ranking

* Normalize metrics
* Combine into final score
* Output ranked list

---

## 7. Non-Functional Requirements

* Must run batch experiments efficiently (parallelizable)
* Must support replayability
* Must log every experiment with hash ID
* Must not depend on live market data during evaluation

---

# 8. TEST-DRIVEN DESIGN (CORE OF PRD)

We now define tests FIRST.

---

## 8.1 Unit Tests

### Test 1: Parameter Sweep Generation

```python
def test_generate_parameter_grid():
    grid = {
        "lookback": [10, 20],
        "threshold": [0.5, 1.0]
    }

    variants = generate_variants(grid)

    assert len(variants) == 4
    assert all("lookback" in v.parameters for v in variants)
```

---

### Test 2: Backtest Determinism

```python
def test_backtest_is_deterministic():
    result1 = run_backtest(params, data)
    result2 = run_backtest(params, data)

    assert result1.metrics == result2.metrics
```

---

### Test 3: Metric Calculation

```python
def test_sharpe_calculation():
    returns = [0.01, -0.01, 0.02, -0.005]

    sharpe = compute_sharpe(returns)

    assert sharpe is not None
    assert -5 < sharpe < 5
```

---

### Test 4: Robustness Scoring

```python
def test_robustness_score_reduces_variance():
    results = run_robustness_tests(strategy_variant, runs=10)

    score = compute_robustness_score(results)

    assert 0 <= score <= 1
```

---

### Test 5: Walk Forward Split Integrity

```python
def test_walk_forward_no_leakage():
    splits = walk_forward_split(data, train=100, test=20)

    for split in splits:
        assert max(split.train) < min(split.test)
```

---

## 8.2 Integration Tests

---

### Test 6: Full Optimization Pipeline

```python
def test_full_optimization_pipeline():
    grid = {"lookback": [10, 20], "threshold": [0.5, 1.0]}

    results = run_optimization_pipeline(
        data=data,
        parameter_grid=grid
    )

    assert len(results.ranked_candidates) == 4
    assert results.best_candidate is not None
```

---

### Test 7: Stability vs Overfitting Detection

```python
def test_overfitting_penalty_applied():
    results = run_optimization_pipeline(data, grid)

    top = results.ranked_candidates[0]

    assert top.stability_score <= 1.0
    assert top.final_score is computed
```

---

### Test 8: Surface Analysis Output

```python
def test_surface_analysis_outputs_matrix():
    surface = compute_surface(
        data=data,
        param_x="lookback",
        param_y="threshold"
    )

    assert surface.matrix.shape == (len(x_vals), len(y_vals))
```

---

## 9. Outputs

System produces:

* Ranked strategy list
* Stability report
* Robustness report
* Walk-forward report
* Parameter surface maps
* Full experiment log (replayable)

---

## 10. What “Good” Looks Like

A successful run should tell you:

* “This parameter region is stable”
* “This configuration is fragile”
* “This result survives regime shifts”
* “This edge is likely noise vs structural”

---

## 11. Suggested Implementation Order (Important)

If you actually build this:

1. Parameter sweep generator
2. Backtest execution engine
3. Metric calculator
4. Basic ranking
5. Walk-forward engine
6. Robustness engine
7. Surface analysis

---

## 12. Reality Check (important)

This system will not magically create edge.

What it *does* is:

* separate real structure from overfitting
* expose fragile strategies early
* force discipline in research

That’s where most retail systems fail.

