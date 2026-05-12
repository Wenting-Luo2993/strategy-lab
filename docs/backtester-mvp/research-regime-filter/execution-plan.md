# Execution Plan — Regime Research Framework

Each stage delivers one vertical slice: functional code + the tests that validate it.
No stage is "done" until its tests pass.

**Source tree for all new code:**
```
vibe/common/indicators/
    batch.py             # Stage 1 — batch/vectorized indicators (shared with live bot)

vibe/backtester/analysis/regime_research/
    __init__.py
    features.py          # Stage 1 — feature table builder
    attribution.py       # Stage 2
    labeler.py           # Stage 3 — day-level regime labeler
    filter_evaluator.py  # Stage 4
    reporting.py         # Stage 5

vibe/tests/backtester/regime_research/
    __init__.py
    test_features.py     # Stage 1
    test_attribution.py  # Stage 2
    test_labeler.py      # Stage 3
    test_filter.py       # Stage 4
    test_reporting.py    # Stage 5

scripts/
    analyze_regimes.py   # Stage 5 CLI
```

---

## Stage 1 — Feature Engine

**Delivers:** A module that takes an OHLCV Parquet DataFrame and returns a feature table
keyed by date/timestamp. All features are forward-observable (no future leakage).

### Functional work

**Batch indicators** (`vibe/common/indicators/batch.py`) — new file, shared with live bot

`vibe/common/indicators/engine.py` already has incremental (stateful) implementations for
the live trading bot. For historical/research use we need batch vectorized equivalents that
operate on a full DataFrame at once. Any indicator not already available as a batch function
must be created here — **not** inlined in `features.py` — so both the backtester and live bot
can reuse the same logic.

Functions to add to `batch.py`:
- `atr_series(df, length=14) -> pd.Series` — True Range EMA; mirrors `engine.py::_update_atr`
- `sma_series(df, length) -> pd.Series` — simple moving average of close
- `adx_series(df, length=14) -> pd.Series` — batch ADX (directional movement index)
- `linear_slope(series, window) -> pd.Series` — rolling OLS slope of a price series
- `rolling_percentile_rank(series, window) -> pd.Series` — rank of current value within rolling window; output in [0.0, 1.0]

All functions must:
- Accept a DataFrame with columns `open`, `high`, `low`, `close`, `volume`
- Return a `pd.Series` aligned to the input index
- Produce `NaN` for the warmup period (first `length` rows), never fill silently

**Volatility features** (`features.py`)
- `atr_14` — `batch.atr_series(df, 14)`
- `atr_pctile` — `batch.rolling_percentile_rank(atr_14, 252)`
- `or_size_pct` — `(OR_high - OR_low) / prev_close * 100` (daily; requires OR levels per day)
- `realized_vol` — rolling std of log-returns, window=20
- `gap_pct` — `(open - prev_close) / prev_close * 100` (daily)
- `vol_pctile` — `batch.rolling_percentile_rank(realized_vol, 252)`

**Trend features** (`features.py`)
- `dist_ma20_pct` — `(close - sma20) / sma20 * 100`; `sma20 = batch.sma_series(df, 20)`
- `dist_ma50_pct` — same for 50
- `slope_20d` — `batch.linear_slope(daily_close, 20)`
- `slope_50d` — same for 50
- `adx_14` — `batch.adx_series(df, 14)`

**Opening behavior features** (`features.py`)
- `open_vol_pctile` — `batch.rolling_percentile_rank(first_bar_volume, 252)`
- `or_expansion` — `or_size / prev_day_atr` (normalized OR width)
- `gap_continuation` — `sign(gap_pct) == sign(prev_day_trend)` → 1 or -1

**Market context features** (`features.py`)
- `prev_day_range` — `prev_high - prev_low`
- `prev_day_trend_pct` — `(prev_close - prev_open) / prev_open * 100`
- `prev_close_location` — `(prev_close - prev_low) / (prev_high - prev_low)`; 0=bottom, 1=top
- `inside_day` — `1` if `high < prev_high and low > prev_low`, else `0`

**Feature registry** (`features.py`)
- `FeatureEngine` class: `compute(df: pd.DataFrame, features: list[str]) -> pd.DataFrame`
- Features selected by name; unrecognized names raise `ValueError`
- Each feature is a thin wrapper calling the appropriate `batch.*` function
- Percentile-based features clamp to [0.0, 1.0]

### Validation tests (`test_features.py`)

| Test | Tier | What it checks |
|------|------|---------------|
| `test_atr_series_matches_incremental_engine` | **P0** | `batch.atr_series` output matches `IncrementalIndicatorEngine` ATR on same data |
| `test_no_future_leakage_rolling` | **P0** | Spike at row 50: rows <50 unchanged, feature changes only at/after 50 |
| `test_feature_row_count_preserved` | P1 | Output df has same index length as input |
| `test_gap_pct_correctness` | P1 | prev_close=100, open=102 → gap_pct=0.02 |
| `test_trend_slope_positive_on_uptrend` | P1 | Monotonic up series → positive slope |
| `test_trend_slope_negative_on_downtrend` | P1 | Monotonic down series → negative slope |
| `test_percentile_features_bounded` | P1 | ATR percentile, vol percentile always in [0.0, 1.0] |
| `test_warmup_rows_are_nan` | P1 | First `length` rows of ATR, slope, percentile features are `NaN` |
| `test_unknown_feature_raises` | P1 | `FeatureEngine.compute(df, ["not_a_feature"])` raises `ValueError` |

`test_atr_series_matches_incremental_engine` is the key cross-validation: it confirms `batch.py`
and `engine.py` agree on the same underlying math, so the live bot and research use identical logic.

**Exit gate:** All P0 and P1 tests in `test_features.py` pass. `pytest vibe/tests/backtester/regime_research/test_features.py`

---

## Stage 2 — Trade Attribution Engine

**Delivers:** A function/class that joins a trade log (output of `BacktestEngine`) with the
feature table (output of Stage 1), producing an enriched trade DataFrame with no future
leakage. Every trade gets only features from bars at or before its entry timestamp.

### Functional work

**`attribution.py`**
- `TradeAttributor` class: `enrich(trades: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame`
- Merges on `entry_time` using an **as-of join** (`pd.merge_asof`) so each trade gets the
  most recent feature snapshot with `feature_time <= entry_time`
- Hard assert after merge: `assert (result["feature_time"] <= result["entry_time"]).all()`
- Missing features (e.g., MA50 during first 50 days): assign `NaN` explicitly, never forward-fill silently; log a count of NaN-attributed trades
- Trade count check: `len(enriched) == len(original_trades)` — raise if not equal

**Output columns added:** one column per feature name from the feature table, plus `feature_snapshot_time` (the bar from which features were taken).

### Validation tests (`test_attribution.py`)

| Test | Tier | What it checks |
|------|------|---------------|
| `test_no_future_leakage_attribution` | **P0** | Modify feature at T+1; trade at T is unchanged |
| `test_correct_timestamp_alignment` | **P0** | Trade entry at 09:45 → features only from ≤09:45 bars |
| `test_timestamp_causality_hard_assert` | **P0** | `feature_snapshot_time <= entry_time` holds for every row |
| `test_trade_count_preserved` | **P0** | `len(enriched) == len(original_trades)` always |
| `test_missing_feature_is_nan` | P1 | MA50 unavailable on day 10 → `NaN`, not filled |
| `test_no_silent_fill_on_missing` | P1 | No ffill/bfill applied to NaN features |

**Exit gate:** All P0 and P1 tests in `test_attribution.py` pass.

---

## Stage 3 — Day Regime Labeler

**Delivers:** A module that takes the feature table (Stage 1) and assigns each trading day
exactly one regime label based on rule-based thresholds on known metrics.

**MVP scope:** one label per *day*, computed from features available at the *prior day's close*
(no intraday data, no lookahead). A future iteration can extend to per-bar labels, but the
day-level interface must be defined now so Stage 4 can consume it.

### Functional work

**`labeler.py`**
- `DayRegimeLabeler` class: `label(features: pd.DataFrame, config: LabelerConfig) -> pd.Series`
  - Input: date-indexed feature table from Stage 1
  - Output: date-indexed `pd.Series` of categorical regime strings (or `NaN` during warmup)
- `LabelerConfig` dataclass with configurable thresholds (defaults shown):
  ```python
  @dataclass
  class LabelerConfig:
      adx_trend_threshold: float = 25.0   # ADX > this → trending
      slope_flat_band: float = 0.05       # |slope_20d| < this % → flat/ranging
      high_vol_pctile: float = 0.80       # atr_pctile > this → high_vol overlay
      low_vol_pctile: float = 0.20        # atr_pctile < this → low_vol overlay
  ```
- **MVP labeling logic** (applied to *prior day's* feature values, not today's):
  - If `adx_14 > adx_trend_threshold` and `slope_20d > slope_flat_band`: `"trending_up"`
  - If `adx_14 > adx_trend_threshold` and `slope_20d < -slope_flat_band`: `"trending_down"`
  - Otherwise: `"ranging"`
  - Overlay (append suffix): if `atr_pctile > high_vol_pctile` → append `"_high_vol"`, e.g. `"ranging_high_vol"`
  - Days where any required feature is `NaN` (warmup period): return `NaN`, never a fabricated label
- **No lookahead:** shift features by 1 day before labeling (`features.shift(1)`); the shift is
  done inside `DayRegimeLabeler.label()`, not by the caller
- Output attached to enriched trades by Stage 4 via a date join on `entry_date`

**Future extension (not in MVP):** `BarRegimeLabeler` class consuming intraday bars, same
interface (`label(features) -> pd.Series`), same `LabelerConfig`. Design `labeler.py` with
this in mind — the day labeler should be a thin specialization of a shared base, not a dead end.

### Validation tests (`test_labeler.py`)

| Test | Tier | What it checks |
|------|------|---------------|
| `test_every_day_gets_a_label_or_nan` | **P0** | No day is silently skipped; warmup days are `NaN` |
| `test_labels_mutually_exclusive` | **P0** | Each day has exactly one non-null label (no multi-label rows) |
| `test_no_lookahead_shift_applied` | **P0** | Modifying today's features doesn't change today's label (label uses yesterday's features) |
| `test_trending_up_labeled_correctly` | P1 | Synthetic: ADX=30, slope=+0.2% → `"trending_up"` |
| `test_trending_down_labeled_correctly` | P1 | Synthetic: ADX=30, slope=-0.2% → `"trending_down"` |
| `test_ranging_labeled_correctly` | P1 | Synthetic: ADX=15, slope=+0.01% → `"ranging"` |
| `test_high_vol_suffix_applied` | P1 | ADX=15, atr_pctile=0.9 → `"ranging_high_vol"` |
| `test_warmup_days_return_nan` | P1 | First 50 rows (before ADX warms up) → `NaN`, not a label |
| `test_custom_thresholds_change_labels` | P2 | Lower `adx_trend_threshold` → more days labeled trending |
| `test_label_distribution_not_degenerate` | P2 | On real ORB data, no single label exceeds 80% of days |

**Exit gate:** All P0 and P1 tests pass. `pytest vibe/tests/backtester/regime_research/test_labeler.py`

---

## Stage 4 — Filter Evaluator

**Delivers:** A module that applies a candidate filter to the enriched trade set,
recomputes all strategy metrics on filtered trades, and flags overfitting signals
(narrow threshold, tiny sample, single-year stability).

### Functional work

**`filter_evaluator.py`**
- `FilterEvaluator` class: `evaluate(enriched_trades: pd.DataFrame, filter_expr: str) -> FilterReport`
- `filter_expr` is a pandas query string on any column in the enriched trade table, including
  regime labels from Stage 3, e.g. `"regime == 'trending_up'"` or `"atr_pctile < 0.8"`
- **Baseline comparison:** always compute unfiltered metrics first; `FilterReport` includes side-by-side unfiltered vs filtered for: expectancy, win rate, Sharpe, max drawdown, profit factor, trade count, convexity (skewness of R-multiples)
- **Stability check per year:** re-run filtered metrics per year; flag if filter only improves ≤1 year
- **Overfitting guardrails:**
  - Narrow threshold warning: if filter is a range and `(upper - lower) / range_of_feature < 0.1`, emit warning
  - Tiny sample warning: if filtered trade count < 30, emit warning
  - Single-year stability: if Sharpe improves in only 1 of N years, emit warning
- Filtered trade count must be ≤ original count — assert at runtime
- Empty filter (no expression) must reproduce original metrics exactly

### Validation tests (`test_filter.py`)

| Test | Tier | What it checks |
|------|------|---------------|
| `test_filter_removes_correct_trades` | **P0** | `ATR_pctile < 0.5` → only qualifying trades remain |
| `test_regime_filter_works` | **P0** | `regime == 'trending_up'` → only trades on trending_up days remain |
| `test_no_filter_baseline_equivalence` | **P0** | Empty filter → identical expectancy, trade count, Sharpe |
| `test_filter_cannot_increase_trade_count` | P1 | Filtered count ≤ original always |
| `test_extreme_filter_returns_zero_trades` | P1 | `ATR_pctile > 2` → 0 trades, no crash |
| `test_filter_metrics_recomputed_from_filtered` | P1 | No stale cache: metrics reflect filtered set only |
| `test_random_strategy_no_regime_edge` | P1 | Random R-multiples — regime filter produces no consistent improvement |
| `test_shuffled_outcomes_weaken_regime_effect` | P1 | Shuffle trade outcomes → regime-filtered metrics converge toward baseline |
| `test_narrow_threshold_warning_emitted` | P2 | `atr_pctile between 0.423 and 0.427` → warning in report |
| `test_tiny_sample_warning_emitted` | P2 | Filter leaving 12 trades → insufficient sample warning |
| `test_single_year_stability_warning_emitted` | P2 | Filter improves only 1 of 5 years → stability warning |

**Exit gate:** All P0 and P1 tests pass. P2 tests pass before any filter is promoted to production use.

---

## Stage 5 — Reporting & CLI

**Delivers:** Human-readable markdown + JSON reports from the full pipeline, plus a CLI
entry point that runs the entire analysis from a single command.

### Functional work

**`reporting.py`**
- `ReportGenerator` class: `generate(analyzer_output, filter_reports, output_dir: Path) -> None`
- Outputs:
  - `regime_analysis.md` — feature bucket tables, monotonicity summary, year-by-year stability tables
  - `filter_comparison.md` — side-by-side filtered vs unfiltered for each candidate filter
  - `summary.json` — machine-readable version of the above (no NaN values; use `None` or omit)
  - Optional Plotly charts: bucket expectancy bar chart, yearly stability heatmap
- Handles edge cases cleanly: no profitable filters found, zero trades after filtering, features all NaN
- Final summary section in markdown must answer the five questions from the PRD:
  - What conditions help this strategy?
  - What conditions hurt it?
  - Is the relationship stable?
  - Are candidate filters robust?
  - What is the selectivity/opportunity tradeoff?

**`scripts/analyze_regimes.py`** (CLI)
```
python scripts/analyze_regimes.py \
  --strategy orb \
  --trades-csv reports/our_trades.csv \
  --data-dir data/parquet \
  --features atr_pctile,gap_pct,slope_20d \
  --filter "regime == 'trending_up'" \
  --output reports/orb_regime_analysis
```
- Pipeline: Feature Engine → Trade Attribution → Day Regime Labeler → Filter Evaluator → Report Generator
- `--features all` computes the full feature set
- `--filter` is optional; omit to run analysis only (no filter comparison)

### Validation tests (`test_reporting.py`)

| Test | Tier | What it checks |
|------|------|---------------|
| `test_report_reproducibility` | P2 | Same config + same seed → byte-identical JSON output |
| `test_json_no_nan_values` | P2 | All JSON outputs serialize cleanly; no NaN corruption |
| `test_markdown_generated_with_no_trades` | P2 | Empty filtered set → report generated, no crash |
| `test_markdown_generated_with_no_profitable_filters` | P2 | No filter beats baseline → report generated, no crash |

**Exit gate:** All P2 tests pass. Run CLI end-to-end on real ORB data and confirm report renders.

---

## Stage 6 — Hardening (P2 guardrails)

**Delivers:** Final integrity tests that must pass before trusting any research conclusion.
These catch bugs that produce believable-but-false results — the most dangerous failure mode.

### Tests to add across existing test files

| Test | File | Tier | What it checks |
|------|------|------|---------------|
| `test_intentional_future_feature_flagged` | `test_attribution.py` | P2 | Feature with `_t+1` suffix in name → `ValueError` or requires explicit override |
| `test_large_dataset_no_memory_explosion` | `test_features.py` | P2 | 8-year 1-min dataset completes without OOM; runtime < 60s |
| `test_feature_cache_consistent` | `test_features.py` | P2 | Cached and freshly-computed features are identical |

**Exit gate:** All three pass on a full-history run before publishing any regime research findings.

---

## Summary: Test Tier Distribution by Stage

| Stage | P0 | P1 | P2 | Total |
|-------|----|----|-----|-------|
| 1 — Feature Engine | 2 | 7 | 0 | 9 |
| 2 — Trade Attribution | 4 | 2 | 0 | 6 |
| 3 — Day Regime Labeler | 3 | 5 | 2 | 10 |
| 4 — Filter Evaluator | 3 | 5 | 3 | 11 |
| 5 — Reporting & CLI | 0 | 0 | 4 | 4 |
| 6 — Hardening | 0 | 0 | 3 | 3 |
| **Total** | **12** | **19** | **12** | **43** |

The P0 tests are the go/no-go gate. If any P0 test fails, do not trust any output from that stage
or any downstream stage.
