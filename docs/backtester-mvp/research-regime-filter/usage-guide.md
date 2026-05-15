# Usage Guide — Regime Research Framework

## Purpose of this document

This guide walks through using the framework to answer diagnostic questions like:

> **"Why does the ORB strategy work well in 2022 but not in 2020?"**

It covers the full research workflow: generating trade data, running the pipeline, interpreting outputs, and forming testable filter hypotheses — without overfitting.

---

## Prerequisites

Before running any analysis you need:

1. **OHLCV parquet data** for your symbol in `data/parquet/`.  
   If you haven't converted Databento data yet:
   ```bash
   python scripts/convert_databento.py --symbol QQQ
   ```

2. **Trade CSV files** — one per date range you want to compare.  
   Generate them with the backtester:
   ```bash
   # 2020 trades
   python scripts/run_backtest.py \
     --ruleset orb_production --symbol QQQ \
     --start 2020-01-01 --end 2020-12-31 \
     --capital 100000 \
     --trades-csv reports/orb_trades_2020.csv \
     --output reports/orb_2020.html

   # 2022 trades
   python scripts/run_backtest.py \
     --ruleset orb_production --symbol QQQ \
     --start 2022-01-01 --end 2022-12-31 \
     --capital 100000 \
     --trades-csv reports/orb_trades_2022.csv \
     --output reports/orb_2022.html
   ```

   The trades CSV must have at minimum these columns:
   - `entry_time` — datetime of trade entry
   - `pnl_r` — R-multiple (profit/loss normalized by initial risk)

3. **Virtual environment active** with dependencies installed.

---

## Step 1 — Establish a baseline understanding

Before touching the framework, open the HTML reports from the backtester and note:

| Question | Where to look |
|----------|--------------|
| What was the overall expectancy in each year? | Equity curve summary |
| Were losses concentrated in specific months? | Monthly return table |
| Did the strategy take many trades or few? | Trade log |

You are looking for **when** the strategy hurt you — not just **that** it did. This is the prior knowledge the PRD requires before filter evaluation.

For the 2020 vs 2022 case, likely observations:
- 2020 Q1: severe drawdown (COVID crash, extreme intraday volatility)
- 2020 Q2–Q3: partial recovery but choppy
- 2022: more consistent performance despite the bear market

These observations inform which features to investigate first.

---

## Step 2 — Run the full pipeline

Run `analyze_regimes.py` with the combined trade history (all years you want to study):

```bash
# Option A: full feature set, no filter yet — diagnostic mode
python scripts/analyze_regimes.py \
  --trades-csv reports/orb_trades_2020.csv \
  --data-dir   data/parquet \
  --symbol     QQQ \
  --features   atr_pctile,gap_pct,slope_20d,adx_14,realized_vol,vol_pctile,dist_ma20_pct \
  --output     reports/regime_2020_diagnostic

# Then 2022
python scripts/analyze_regimes.py \
  --trades-csv reports/orb_trades_2022.csv \
  --data-dir   data/parquet \
  --symbol     QQQ \
  --features   atr_pctile,gap_pct,slope_20d,adx_14,realized_vol,vol_pctile,dist_ma20_pct \
  --output     reports/regime_2022_diagnostic
```

Or combine both years into a single analysis by concatenating the CSV files first (gives you year-by-year stability tables automatically):

```bash
# Combine trade files
python -c "
import pandas as pd
df = pd.concat([
    pd.read_csv('reports/orb_trades_2020.csv'),
    pd.read_csv('reports/orb_trades_2022.csv'),
])
df.to_csv('reports/orb_trades_combined.csv', index=False)
"

python scripts/analyze_regimes.py \
  --trades-csv reports/orb_trades_combined.csv \
  --data-dir   data/parquet \
  --symbol     QQQ \
  --features   atr_pctile,gap_pct,slope_20d,adx_14,realized_vol,vol_pctile,dist_ma20_pct \
  --output     reports/regime_combined_diagnostic
```

---

## Step 3 — Read the diagnostic output

Open `reports/regime_combined_diagnostic/regime_analysis.md`.

### What to look for in the year-by-year table

```
## Year-by-Year Performance

| Year | Trades | Expectancy | Win Rate | Sharpe |
|------|--------|-----------|----------|--------|
| 2020 | 247    | -0.12      | 41.3%    | -0.34  |
| 2022 | 251    | +0.21      | 49.4%    | +0.61  |
```

This confirms the problem is real. Now look at the **Regime Label Distribution** section.

### What to look for in the regime table

```
## Performance by Regime

| Regime              | Trades | Expectancy | Win Rate | Sharpe |
|---------------------|--------|-----------|----------|--------|
| ranging_high_vol    | 143    | -0.34      | 36%      | -1.1   |
| ranging             | 198    |  0.05      | 47%      |  0.2   |
| trending_up         |  87    |  0.29      | 54%      |  0.8   |
| trending_down       | 70     |  0.31      | 52%      |  0.9   |
```

The key signal: **ORB tends to fail in ranging + high volatility conditions.**

This makes mechanical sense: when markets are chopping without directional conviction AND volatility is elevated (as in COVID Q1 2020), ORB breakouts trigger but immediately reverse. The strategy's stop-distance is based on OR size, which is inflated by the volatility, so the risk/reward deteriorates.

### Why 2022 was different

2022 was a bear market with **persistent directional trends** (rate hike regime). Even though absolute prices were falling:
- ADX was high — markets trended, they didn't chop
- ORB breakouts in the direction of the trend (usually short) continued
- Stop distances were moderate — ATR percentile stayed in the 50–75th range, not extreme

---

## Step 4 — Form hypotheses and test filters

Based on the diagnostic output, write down 2–3 candidate hypotheses **before** running any filter. This is the critical anti-overfitting discipline.

**Example hypotheses from the 2020 analysis:**

| # | Hypothesis | Observable feature | Testable filter |
|---|------------|-------------------|----------------|
| 1 | ORB fails during extreme volatility spikes | `atr_pctile` | `atr_pctile < 0.80` |
| 2 | ORB fails when market has no directional bias | `adx_14`, `slope_20d` | `regime != 'ranging'` |
| 3 | ORB fails on large overnight gaps that reverse | `gap_pct` | `gap_pct.abs() < 1.0` |

Now test them:

```bash
# Hypothesis 1: skip extreme volatility days
python scripts/analyze_regimes.py \
  --trades-csv reports/orb_trades_combined.csv \
  --data-dir   data/parquet \
  --symbol     QQQ \
  --features   atr_pctile,gap_pct,slope_20d,adx_14 \
  --filter     "atr_pctile < 0.80" \
  --output     reports/filter_h1_low_vol

# Hypothesis 2: skip ranging days
python scripts/analyze_regimes.py \
  --trades-csv reports/orb_trades_combined.csv \
  --data-dir   data/parquet \
  --symbol     QQQ \
  --features   atr_pctile,gap_pct,slope_20d,adx_14 \
  --filter     "regime != 'ranging_high_vol'" \
  --output     reports/filter_h2_no_ranging_hv
```

---

## Step 5 — Evaluate the filter output

Open `reports/filter_h1_low_vol/filter_comparison.md`.

### Side-by-side comparison

```
| Metric          |   Baseline |   Filtered |
|-----------------|-----------|-----------|
| Trade count     |        498 |        372 |
| Expectancy (R)  |     +0.045 |     +0.18  |
| Win rate        |      45.4% |      49.1% |
| Sharpe          |      +0.12 |      +0.51 |
| Max drawdown    |      -8.4R |      -4.2R |
| Profit factor   |       1.09 |       1.38 |
```

### Yearly stability table

This is the most important output. A filter that only works in one year is overfitting.

```
### Year-by-Year (filtered)

| Year | Trades | Expectancy | Sharpe |
|------|--------|-----------|--------|
| 2020 | 168    |    +0.09   |  +0.21 |
| 2022 | 204    |    +0.25   |  +0.72 |
```

Both years improve. The filter is worth investigating further.

### Warnings to take seriously

If the report contains any of these warnings, stop and reconsider:

```
[TINY_SAMPLE] Filter leaves only 22 trades — metrics unreliable
[NARROW_THRESHOLD] Filter covers only 1.2% of atr_pctile range
[SINGLE_YEAR_STABILITY] Positive expectancy in only 1/2 years
```

A warning does not mean discard — it means **do not promote to production** until you understand why the sample is small or why stability is limited.

---

## Step 6 — Understand the mechanism before promoting a filter

Before applying any filter to live trading, you must be able to answer:

**"Why does this feature predict ORB performance?"**

Bad answer: "The backtest improved when I filtered on this."

Good answers:
- "High ATR percentile days mean OR size is very large. Our stop is based on OR level, so the initial risk per trade grows while the probability of a continuation breakout does not. R/R deteriorates mechanically."
- "On ranging days (low ADX, flat slope), ORB breakouts fire but the market has no directional conviction — mean reversion dominates over momentum continuation."
- "Large gap days tend to exhaust early buyers/sellers, increasing the probability of reversal before the ORB target is hit."

If you cannot construct a causal story, the feature may be noise.

---

## Step 7 — Python API (for custom analysis)

If the CLI doesn't fit your workflow, use the modules directly:

```python
import pandas as pd
from pathlib import Path

from vibe.backtester.analysis.regime_research.features import FeatureEngine
from vibe.backtester.analysis.regime_research.attribution import TradeAttributor
from vibe.backtester.analysis.regime_research.labeler import DayRegimeLabeler, LabelerConfig
from vibe.backtester.analysis.regime_research.filter_evaluator import FilterEvaluator
from vibe.backtester.analysis.regime_research.reporting import ReportGenerator

# 1. Load data
ohlcv = pd.read_parquet("data/parquet/QQQ.parquet")
ohlcv.columns = [c.lower() for c in ohlcv.columns]
trades = pd.read_csv("reports/orb_trades_combined.csv")
trades["entry_time"] = pd.to_datetime(trades["entry_time"])

# 2. Features
engine = FeatureEngine()
features = engine.compute(ohlcv, ["atr_14", "atr_pctile", "adx_14", "slope_20d", "gap_pct"])

# 3. Attribution
attributor = TradeAttributor()
enriched = attributor.enrich(trades, features)

# 4. Day labels
labeler = DayRegimeLabeler()
cfg = LabelerConfig(adx_trend_threshold=25.0, high_vol_pctile=0.80)
labels = labeler.label(features, cfg)

# Attach labels to enriched trades
label_df = labels.rename("regime").to_frame().reset_index()
label_df.columns = ["date", "regime"]
label_df["date"] = pd.to_datetime(label_df["date"]).dt.normalize()
enriched["_date"] = pd.to_datetime(enriched["entry_time"]).dt.normalize()
enriched = enriched.merge(label_df, left_on="_date", right_on="date", how="left").drop(
    columns=["_date", "date"]
)

# 5. Year-by-year breakdown without any filter
evaluator = FilterEvaluator()
baseline_report = evaluator.evaluate(enriched, "")
for ym in baseline_report.yearly_baseline:
    print(f"{ym.year}: expectancy={ym.metrics.expectancy:.3f}, "
          f"trades={ym.metrics.trade_count}")

# 6. Test a filter
filter_report = evaluator.evaluate(enriched, "regime != 'ranging_high_vol'")
print(filter_report.summary())

# 7. Generate reports
reporter = ReportGenerator()
reporter.generate(enriched, [filter_report], Path("reports/custom_analysis"))
```

---

## Anti-patterns to avoid

These are the most common ways to misuse this framework:

### Do not interpret individual feature spikes as regime signals

If `atr_pctile` shows good expectancy in the 0.55–0.65 bucket but poor elsewhere, that is almost certainly noise. The framework is looking for **monotonic relationships** — performance improving consistently as a feature rises or falls — not isolated sweet spots.

### Do not promote filters that only improved one year

If `atr_pctile < 0.80` worked in 2022 but made 2020 worse, the filter is not a regime insight — it is a coincidence. The year-by-year stability table in the filter comparison report catches this.

### Do not combine more than 2–3 features in a single filter

Every additional condition in a filter reduces the sample size and increases the risk of overfitting. Start with a single feature, understand it, then consider adding a second condition only if the causal story demands it.

### Do not use year labels as regime features

The framework's `DayRegimeLabeler` only uses forward-observable features (ATR percentile, ADX, slope). Never add a "year=2020" dummy variable — that is the definition of lookahead overfitting.

---

## Checklist before promoting a filter

- [ ] Filter improves expectancy in **both** the bad year (2020) and the good year (2022)
- [ ] Improvement is present in ≥ 3 years if multi-year data is available
- [ ] No `TINY_SAMPLE` or `NARROW_THRESHOLD` warning in the report
- [ ] You can articulate a **causal mechanism** for why the feature predicts performance
- [ ] Trade count reduction is acceptable (rule of thumb: filter removes at most 40% of trades)
- [ ] Sharpe, drawdown, and profit factor all improve — not just expectancy
- [ ] Filter uses a broad threshold (e.g., "top 20% of ATR percentile"), not a precise one

---

## Relevant files

| File | Purpose |
|------|---------|
| `vibe/common/indicators/batch.py` | Vectorized indicators (ATR, ADX, slope, percentile rank) |
| `vibe/backtester/analysis/regime_research/features.py` | `FeatureEngine` — 18 features |
| `vibe/backtester/analysis/regime_research/attribution.py` | `TradeAttributor` — as-of join |
| `vibe/backtester/analysis/regime_research/labeler.py` | `DayRegimeLabeler` + `LabelerConfig` |
| `vibe/backtester/analysis/regime_research/filter_evaluator.py` | `FilterEvaluator` — metrics + guardrails |
| `vibe/backtester/analysis/regime_research/reporting.py` | `ReportGenerator` — markdown + JSON |
| `scripts/analyze_regimes.py` | CLI entry point |
| `docs/backtester-mvp/research-regime-filter/prd.md` | Full design intent |
| `docs/backtester-mvp/research-regime-filter/execution-plan.md` | Implementation plan |
