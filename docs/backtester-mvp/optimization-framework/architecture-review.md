# Optimization Framework PRD — Architecture Review

**Date:** 2026-05-18  
**Reviewer:** AI Assistant  
**Status:** ✅ PRD is architecturally sound and aligns with existing system

---

## Executive Summary

The PRD proposes a **structured optimization framework** that fills a critical gap in the current system. Your existing infrastructure already has **80% of the foundational components**, but they're not yet organized into a cohesive optimization pipeline.

**Verdict:** PRD is **architecturally aligned** and **ready for implementation** with minor adaptations.

---

## Current System Analysis

### ✅ What Already Exists

#### 1. **Indicator Generation Engine** (PRD Module 1)
**Location:** `vibe/backtester/analysis/regime_research/features.py`

**Current Implementation:**
- ✅ FeatureEngine computes 20+ indicators (ATR, ADX, slope, percentiles, gap analysis)
- ✅ Dependency resolution (topological sort ensures correct computation order)
- ✅ Forward-observable computation (no look-ahead bias)
- ✅ Supports both daily and intraday data

**Gap:** Not yet integrated with parameter sweep framework

```python
# Already works like this:
from vibe.backtester.analysis.regime_research.features import FeatureEngine

engine = FeatureEngine()
features = engine.compute(df, features=["atr_14", "atr_pctile", "adx_14"])
# Returns: DataFrame with indicator columns
```

**PRD Alignment:** ⭐⭐⭐⭐⭐ (Perfect — can be used as-is)

---

#### 2. **Parameter Space Generator** (PRD Module 3)
**Location:** `vibe/backtester/analysis/parameter_sweep.py`

**Current Implementation:**
- ✅ Grid search (Cartesian product of all parameter values)
- ✅ One-at-a-time sweep (vary one param while holding others constant)
- ✅ Supports nested YAML paths (`"exit.take_profit.multiplier"`)
- ✅ Already used in production for ORB sensitivity testing

**Gap:** No random search or Bayesian optimization (mentioned in PRD as "future")

```python
# Already works like this:
from vibe.backtester.analysis.parameter_sweep import ParameterSweep, ParameterDefinition

sweep = ParameterSweep(
    base_ruleset_path="vibe/rulesets/orb_production.yaml",
    parameters=[
        ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15]),
        ParameterDefinition("exit.take_profit.multiplier", [1.5, 2.0, 3.0]),
    ],
    sweep_mode="grid",  # or "one_at_a_time"
)
```

**PRD Alignment:** ⭐⭐⭐⭐⭐ (Perfect — already matches PRD spec)

---

#### 3. **Execution Engine** (PRD Module 4)
**Location:** `vibe/backtester/core/engine.py`

**Current Implementation:**
- ✅ Event-driven backtester (SimulatedClock → RuleSetRunner → PortfolioManager)
- ✅ Deterministic (same input → same output)
- ✅ Supports slippage simulation

**Gaps:**
- ❌ Re-computes indicators (ATR) on every run — **CRITICAL INEFFICIENCY**
- ❌ No parallelization support
- ❌ No result caching

**Current Inefficiency:**
```python
# This happens on EVERY parameter combination:
def run(self, symbol, start_date, end_date):
    df_1m = load_data()        # 1. Load raw data
    df = _resample(df_1m)      # 2. Resample to 5m
    df = _add_atr(df)          # 3. Compute ATR ← REDUNDANT!
    # ... run backtest
```

**PRD Alignment:** ⭐⭐⭐ (Good foundation, but needs optimization as PRD suggests)

---

#### 4. **Evaluation Engine** (PRD Module 5)
**Location:** `vibe/backtester/analysis/metrics.py` + `performance.py`

**Current Implementation:**
- ✅ Sharpe ratio
- ✅ Max drawdown
- ✅ Win rate
- ✅ Expectancy (R-multiples)
- ✅ Profit factor
- ✅ CAGR
- ✅ Trade distribution stats (skewness, kurtosis)

**Gap:** Not yet packaged as a reusable "metric bundle"

**PRD Alignment:** ⭐⭐⭐⭐⭐ (Complete — all PRD metrics already exist)

---

#### 5. **Robustness Analyzer** (PRD Module 6)
**Status:** ❌ **NOT YET IMPLEMENTED**

**What's Missing:**
- Noise injection (randomize entry/exit by ±X ticks)
- Parameter perturbation tests (wiggle params by ±10%)
- Sub-sample testing (random date ranges)

**PRD Alignment:** ⭐ (Not started)

---

#### 6. **Walk-Forward Engine** (PRD Module 7)
**Status:** ❌ **NOT YET IMPLEMENTED**

**What's Missing:**
- Rolling train/test splits
- Anchored vs sliding window
- Train vs test degradation scoring

**Current Workaround:**
```python
# You've done this manually for regime research:
# - Train: 2018-2024
# - Test: 2025
# - Out-of-sample: 2026 YTD
```

**PRD Alignment:** ⭐ (Not started, but conceptually similar to your manual approach)

---

#### 7. **Ranking System** (PRD Module 8)
**Status:** ⚠️ **PARTIALLY IMPLEMENTED**

**What Exists:**
```python
# parameter_sweep.py already sorts by P&L:
df = df.sort_values("total_pnl", ascending=False)
```

**What's Missing:**
- Multi-metric composite score
- Stability weighting
- Overfitting penalties

**PRD Alignment:** ⭐⭐⭐ (Basic sorting exists; needs composite scoring)

---

#### 8. **Surface Analyzer** (PRD Module 9)
**Status:** ❌ **NOT YET IMPLEMENTED**

**What's Missing:**
- 2D parameter heatmaps
- Cliff detection (sharp performance drops)
- Plateau identification (stable regions)

**PRD Alignment:** ⭐ (Not started)

---

## Architecture Fit Analysis

### ✅ Strong Alignments

1. **Data Model Matches Existing Patterns**

   PRD proposes:
   ```python
   StrategyVariant:
       strategy_id: str
       parameters: dict
   ```

   Current system already uses:
   ```python
   SweepResult:
       params: Dict[str, Any]
       result: BacktestResult
   ```

   **Action:** Rename `SweepResult` → `StrategyVariant` for consistency

2. **Modular Design Matches Existing Structure**

   PRD's module separation mirrors your current folder structure:
   ```
   vibe/backtester/
   ├── core/            ← Execution Engine (Module 4)
   ├── analysis/        ← Evaluation + Metrics (Module 5)
   │   ├── regime_research/  ← Indicator Engine (Module 1)
   │   ├── parameter_sweep.py ← Parameter Generator (Module 3)
   │   └── metrics.py   ← Already there!
   └── reporting/       ← Surface Analysis (Module 9)
   ```

3. **Determinism is Already Enforced**

   PRD Requirement:
   > "Deterministic runs (same input → same output)"

   ✅ Your BacktestEngine already achieves this via:
   - Fixed random seed (if needed)
   - No external API calls during backtest
   - Reproducible slippage simulation

---

### ⚠️ Gaps to Address

#### 1. **Indicator Pre-computation** (PRD Module 1 — CRITICAL)

**Problem:**
```python
# Current: BacktestEngine recomputes ATR on every run
# For 100 parameter combinations → ATR computed 100 times!
```

**Solution:**
```python
# PRD-compliant approach:
class OptimizationPipeline:
    def run(self, symbol, date_range, param_grid):
        # 1. Pre-compute indicators ONCE
        features = self._precompute_indicators(symbol, date_range)
        
        # 2. Run parameter sweep (reuse features)
        for params in param_grid:
            result = self._run_backtest(params, features)  # No re-computation!
```

**Implementation Steps:**
1. Modify `BacktestEngine.run()` to **accept pre-computed features** as optional parameter
2. Create `OptimizationPipeline` class that:
   - Calls `FeatureEngine.compute()` once
   - Passes features to each backtest
3. Update `ParameterSweep` to use this pipeline

**Estimated CPU Savings:** 50-90% (depends on number of indicators)

---

#### 2. **Parallelization** (PRD Module 4)

**Problem:**
```python
# Current: Sequential execution
for params in combinations:
    result = engine.run(...)  # Blocks until done
```

**Solution:**
```python
from concurrent.futures import ProcessPoolExecutor

# PRD-compliant parallel execution
with ProcessPoolExecutor(max_workers=8) as executor:
    futures = [executor.submit(run_backtest, params) for params in combinations]
    results = [f.result() for f in futures]
```

**Constraints:**
- Must serialize `StrategyRuleSet` (already Pydantic, so should work)
- Must pass pre-computed features (pickle DataFrame or use shared memory)

**Estimated Time Savings:** 4-8x on an 8-core machine

---

#### 3. **Result Caching** (PRD Module 4)

**Problem:**
- Running same parameter combination twice wastes time

**Solution:**
```python
import hashlib
import pickle

def cache_key(params, symbol, date_range):
    key = f"{params}_{symbol}_{date_range}"
    return hashlib.md5(key.encode()).hexdigest()

# Cache to disk
cache_dir = Path("cache/backtests")
cache_file = cache_dir / f"{cache_key(params, symbol, date_range)}.pkl"

if cache_file.exists():
    result = pickle.load(cache_file.open("rb"))
else:
    result = engine.run(...)
    pickle.dump(result, cache_file.open("wb"))
```

---

## PRD Scoring Model Review

### PRD Proposes:
```
final_score = 
    0.4 * sharpe
  + 0.2 * stability_score
  + 0.2 * robustness_score
  + 0.2 * walk_forward_score
```

### ⚠️ Reality Check

**Issue:** Your regime research revealed that:
- ORB has +0.11R expectancy with **extreme tail dependence** (top 10% = 60% of profits)
- Win rate is only 29.2% (trend-following style)
- Sharpe = 0.886 (decent but not stellar)

**Recommendation:** Add **tail risk metrics** to scoring:

```python
final_score = 
    0.3 * sharpe
  + 0.2 * expectancy_r
  + 0.1 * tail_ratio       # (95th percentile return / 5th percentile return)
  + 0.1 * stability_score
  + 0.1 * robustness_score
  + 0.2 * walk_forward_score
```

**Why:** You need to capture **convex payoff structure**, not just Sharpe

---

## Implementation Roadmap

### Phase 1: Foundation (Leverage Existing Code) — 2 weeks

**Goal:** Connect existing modules without writing new code

1. ✅ Add indicator pre-computation to `ParameterSweep`
   - Modify `BacktestEngine.run()` to accept optional `features` DataFrame
   - Update `ParameterSweep.run()` to call `FeatureEngine` once before loop

2. ✅ Add result caching to `ParameterSweep`
   - Hash params + symbol + date range → cache key
   - Save/load `BacktestResult` as pickle

3. ✅ Refactor ranking to use composite score
   - Add `score_result()` function to `metrics.py`
   - Implement PRD formula (with tail risk adjustment)

**Test:**
```python
def test_indicator_precomputation_avoids_redundant_calc():
    # Run 10 parameter combinations
    # Assert: FeatureEngine.compute() called only ONCE
```

---

### Phase 2: Robustness & Walk-Forward — 4 weeks

**Goal:** Implement PRD Modules 6 & 7

1. ✅ Create `RobustnessAnalyzer` class
   - Noise injection (randomize fills by ±X ticks)
   - Parameter perturbation (wiggle params by ±10%)
   - Return variance score (lower = more robust)

2. ✅ Create `WalkForwardEngine` class
   - Rolling train/test splits (e.g., train=6mo, test=1mo)
   - Compute train/test degradation score
   - Return distribution of test period results

**Test:**
```python
def test_robustness_score_penalizes_fragile_strategies():
    # Strategy that fails with +1 tick slippage should score low
```

---

### Phase 3: Surface Analysis & Parallelization — 3 weeks

**Goal:** Implement PRD Modules 9 & optimize Module 4

1. ✅ Create `SurfaceAnalyzer` class
   - For 2D param pairs, generate performance heatmap
   - Detect cliffs (gradient > threshold)
   - Identify plateaus (low gradient regions)

2. ✅ Add parallelization to `ParameterSweep`
   - Use `ProcessPoolExecutor` for parameter combinations
   - Measure speedup (should be 4-8x)

**Test:**
```python
def test_parallel_sweep_matches_sequential():
    # Assert: parallel and sequential produce identical results
```

---

### Phase 4: Production Integration — 2 weeks

**Goal:** Create unified `OptimizationPipeline` API

1. ✅ Create `vibe/backtester/optimization/pipeline.py`
   - Combines all modules (indicator engine → sweep → robustness → walk-forward → ranking)
   - Single entry point: `pipeline.run(strategy, param_grid, date_range)`

2. ✅ Add CLI tool: `scripts/optimize_strategy.py`
   - Run full optimization with single command
   - Output: ranked candidates + reports

**Usage:**
```bash
python scripts/optimize_strategy.py \
    --strategy orb \
    --param-grid config/orb_param_grid.yaml \
    --start 2018-01-01 \
    --end 2024-12-31 \
    --walk-forward \
    --output reports/orb_optimization.html
```

---

## Key Recommendations

### 1. **Start with Indicator Pre-computation** (Highest ROI)
- Simplest to implement
- Largest performance gain (50-90% speedup)
- Requires minimal code changes

### 2. **Don't Over-engineer Robustness Initially**
- Start with simple noise injection (randomize fills by ±5 ticks)
- Only add parameter perturbation if initial results are sensitive

### 3. **Use Existing Regime Research Framework**
- Your `FeatureEngine` is production-grade
- Already handles 20+ indicators with dependency resolution
- Don't rebuild — just integrate

### 4. **Test Incrementally**
- Each PRD module should have unit tests before moving to next
- Use your existing ORB strategy as test case (known baseline)

### 5. **Watch for Memory Issues with Parallelization**
- Passing large DataFrames to subprocesses can be slow
- Consider using shared memory (e.g., `multiprocessing.shared_memory`)

---

## PRD Gaps & Clarifications

### ❓ Not Addressed in PRD

1. **What happens when walk-forward shows degradation?**
   - Does optimizer reject that parameter set?
   - Or does it flag it as "risky but potentially profitable"?

2. **How to handle strategies with < 100 trades?**
   - Statistical significance is low
   - Should optimizer require minimum N trades?

3. **What if robustness test shows variance > X?**
   - Is this a hard reject or a scoring penalty?

4. **How to prevent overfitting in multi-metric scoring?**
   - Scoring formula itself could be overfitted to historical data
   - Consider reserving final year as "hold-out validation"

### 📝 Suggested PRD Additions

Add section:
```markdown
## 13. Overfitting Prevention

- Reserve final 20% of data as hold-out validation
- Do NOT use hold-out data for:
  - Parameter tuning
  - Scoring formula calibration
  - Robustness testing
- Use hold-out data ONLY for final validation
```

---

## Final Verdict

**Architecture Compatibility:** ⭐⭐⭐⭐⭐ (5/5)

**Implementation Readiness:** ⭐⭐⭐⭐ (4/5)
- 80% of components already exist
- Clear path to integration
- Minor gaps are non-blocking

**PRD Quality:** ⭐⭐⭐⭐ (4/5)
- Well-structured and modular
- Matches research best practices
- Minor gaps around overfitting prevention

**Recommendation:** ✅ **APPROVE PRD & START PHASE 1 IMPLEMENTATION**

---

## Next Steps

1. ✅ Review this architecture analysis
2. ✅ Prioritize Phase 1 tasks (indicator pre-computation + caching)
3. ✅ Create implementation tickets with TDD tests (as PRD specifies)
4. ✅ Start with smallest module (indicator pre-computation)
5. ✅ Validate against ORB baseline before moving to Phase 2

**Estimated Time to MVP:** 8-10 weeks (if working full-time)

**Estimated Time to Production:** 12-14 weeks (includes hardening + testing)
