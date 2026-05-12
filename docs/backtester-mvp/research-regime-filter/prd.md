# PRD — Reusable Regime Filter Research Framework

## Goal

Build a reusable regime analysis and optimization framework on top of the existing backtester infrastructure.

The purpose of this system is NOT to optimize profit directly, but to:

1. Explain why a strategy works in some periods and fails in others
2. Identify observable market-state features correlated with strategy performance
3. Discover candidate regime filters that may improve robustness
4. Reuse the framework across multiple future strategies

The framework should support:

* ORB
* breakout systems
* mean reversion systems
* future custom strategies

The framework should prioritize:

* causality
* interpretability
* robustness
  over raw optimization.

---

# Core Design Principles

## 1. Regime analysis is diagnostic first

This framework should NOT blindly optimize filters.

Instead:

* identify market conditions associated with good/bad performance
* quantify relationships
* surface interpretable insights

The framework should help answer:

* “Why did 2020 fail?”
* “What market structure benefits this strategy?”
* “What observable conditions predict edge deterioration?”

---

## 2. Use only forward-observable features

All regime features must be computable using information available BEFORE trade entry.

Examples:

* ATR percentile
* overnight gap %
* distance from moving average
* VIX level
* opening range size
* trend slope

Examples NOT allowed:

* year labels
* future volatility
* hindsight bull/bear labels

---

## 3. Avoid overfitting

The framework must discourage:

* brute force threshold optimization
* combinatorial filter explosions
* highly specific parameter tuning

Favor:

* broad stable regions
* monotonic relationships
* interpretable effects

---

# System Architecture

## Modules

### 1. Feature Engine

Responsible for computing reusable market-state features.

Input:

* OHLCV parquet dataset

Output:

* daily/intraday feature table keyed by timestamp/date

Features should be modular and extensible.

---

### 2. Trade Attribution Engine

Joins executed trades with regime features available at trade entry time.

Output:

* enriched trade table

Example:
| trade_id | entry_time | pnl_r | atr_pctile | gap_pct | trend_slope | ... |

---

### 3. Regime Analyzer

Analyzes strategy performance conditioned on features.

Responsibilities:

* bucket analysis
* conditional expectancy
* conditional Sharpe
* win rate by feature bucket
* trade distribution analysis

---

### 4. Filter Evaluator

Tests candidate filters for robustness.

Example:

* ATR percentile < 80
* OR size percentile between 20–70
* trend slope > 0

Responsibilities:

* compare filtered vs unfiltered performance
* measure trade reduction
* evaluate stability across years

---

### 5. Report Generator

Outputs:

* markdown reports
* JSON summaries
* charts/tables

Goal:

* human-readable research artifacts
* reusable for future strategy evaluation pipeline

---

# Phase 1 — Feature Engine

## Initial Feature Set

### Volatility Features

* ATR(14)
* ATR percentile
* opening range size %
* realized volatility
* overnight gap %
* rolling volatility percentile

### Trend Features

* distance from 20D MA
* distance from 50D MA
* 20D slope
* 50D slope
* ADX-like trend strength metric

### Opening Behavior Features

* opening volume percentile
* opening range expansion
* gap continuation direction

### Market Context Features

* prior day range
* prior day trend
* previous day close location
* inside/outside day classification

### Optional External Features

Design system to optionally support:

* VIX
* macro indicators
* breadth indicators

without tightly coupling architecture to them.

---

# Phase 2 — Trade Attribution

For every trade:

* attach regime features available immediately before entry

Requirements:

* no future leakage
* timestamp alignment validation
* feature snapshotting

Output:
`enriched_trades.parquet`

---

# Phase 3 — Regime Diagnostics

## Required Analyses

### 1. Feature Bucket Analysis

Example:

* ATR percentile deciles
* gap size buckets

Metrics per bucket:

* expectancy
* Sharpe
* win rate
* avg win/loss
* trade count

---

### 2. Monotonicity Detection

Detect whether:

* performance improves consistently as feature rises/falls

This is preferred over isolated spikes.

---

### 3. Stability Across Years

For each feature relationship:

* compare consistency across years

Goal:

* identify robust effects
* reject regime relationships that only appear in one period

---

### 4. Trade Distribution Analysis

Analyze:

* skewness
* tail dependence
* concentration of profits

Goal:

* identify whether filters improve convexity or merely remove trades.

---

# Phase 4 — Candidate Filter Evaluation


## Candidate Filter Requirements

Filters must:

* use observable features only
* reduce trade count reasonably
* improve robustness, not only CAGR

Evaluate:

* expectancy
* Sharpe
* drawdown
* PF
* convexity metrics
* stability across years

---

## Important Anti-Overfitting Rules

Reject filters that:

* depend on narrow thresholds
* dramatically reduce sample size
* only improve one year
* create unstable parameter sensitivity

Prefer:

* broad threshold ranges
* simple interpretable rules

---

# Reporting Requirements

## Generate:

* feature importance tables
* conditional performance tables
* yearly stability tables
* filter comparison reports

---

## Final Summary Report

Must answer:

* What market conditions help this strategy?
* What conditions hurt it?
* Is the relationship stable?
* Are candidate filters robust?
* What is the tradeoff between selectivity and opportunity?

Do NOT output:

* “predicted future return”
* “guaranteed profitable filter”

---

# CLI Interface

Examples:

```bash
python analyze_regimes.py --strategy orb

python analyze_regimes.py \
  --strategy orb \
  --features atr,gap,trend \
  --output reports/orb_regime_analysis
```

---

# Technical Requirements

* Python only
* Modular architecture
* Pandas + NumPy preferred
* Plotly or Matplotlib acceptable
* Parquet-first workflow
* Reusable feature registry system
* Config-driven feature selection preferred

Avoid:

* heavy frameworks
* over-engineering
* hidden state

---

# Deliverables

## Initial Deliverables

1. Feature engine
2. Trade attribution engine
3. Regime diagnostics module
4. Candidate filter evaluator
5. Markdown + JSON reporting
6. Example ORB regime analysis

---

# Success Criteria

The framework should help answer questions like:

* Why did ORB fail in 2020 but succeed in 2022?
* Does ORB require trend persistence?
* Does ORB fail during extreme opening volatility?
* Are filters stable across years?
* Does filtering improve robustness or merely reduce trades?

The final result should function as a reusable research layer for future strategy development, not a one-off optimization script.
