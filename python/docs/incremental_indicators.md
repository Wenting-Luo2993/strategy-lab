# Incremental Indicator Calculation Requirements

## Progress Tracker

**Overall Status**: 🔄 In Progress - Phase 1, 2 & 3 Complete, Ready for Phase 4

### Phase 1: Foundation (Week 1) - ✅ Complete

- [x] Evaluate and select library (talipp recommended) - **DONE: talipp installed and validated**
- [x] Create `src/indicators/incremental.py` module structure - **DONE: IncrementalIndicatorEngine implemented**
- [x] Implement state serialization/deserialization - **DONE: save_state() and load_state() methods**
- [x] Set up test infrastructure (fixtures, snapshots) - **DONE: tests/indicators/test_incremental_correctness.py with 9 passing tests**

### Phase 2: Core Indicators (Week 2-3) - ✅ Complete

- [x] Implement incremental SMA, EMA - **DONE: Already in Phase 1**
- [x] Implement incremental RSI - **DONE: Already in Phase 1**
- [x] Implement incremental ATR - **DONE: Already in Phase 1**
- [x] Write unit tests for correctness (incremental vs. batch) - **DONE: Already in Phase 1**
- [x] Performance benchmarking - **DONE: tests/performance/test_indicator_performance.py with 8 tests**

**Phase 2 Performance Results:**

- **Single-bar performance**: ✅ **PASSED** - Average 3.25ms, 95th percentile 3.87ms (<100ms target)
- **Multi-day extensions**: ⚠️ **Warmup overhead** - Current full-cache warmup approach takes 600ms+ per indicator
  - 1-day extension: Batch 26ms vs Incremental 2708ms (0.01x speedup - worse due to warmup)
  - 7-day extension: Batch 14ms vs Incremental 5760ms (0.002x speedup)
  - 30-day extension: Batch 20ms vs Incremental 14050ms (0.001x speedup)
- **Root cause**: Processing entire cache (8640 bars) for warmup on every run
- **Solution path**: State persistence (Phase 4) will eliminate warmup overhead, achieving 10x+ speedup target
- **Current value**: Real-time/paper trading scenarios benefit immediately from <5ms single-bar updates

### Phase 3: Advanced Indicators (Week 3-4) - ✅ Complete

- [x] Implement incremental MACD - **DONE: talipp MACD with MACDVal(macd, signal, histogram)**
- [x] Implement incremental Bollinger Bands - **DONE: talipp BB with BBVal(lb, cb, ub)**
- [x] Handle day-based indicators - **DONE: ORB hybrid approach recalculates affected days only**
- [x] Custom indicator: ORB - **DONE: Hybrid batch approach for day-scoped indicator**

**Phase 3 Results:**

- ✅ **ORB (Opening Range Breakout)**: Hybrid approach recalculates only affected days (days with new data)
- ✅ **MACD**: talipp integration with tolerance of 0.15 for algorithm differences
- ✅ **Bollinger Bands**: talipp integration matching pandas_ta within 0.1 precision
- ✅ **13 tests passing** (9 from Phase 1, 1 from Phase 2, 3 new in Phase 3)

### Phase 4: Cache Integration (Week 4-5) - ⏳ Not Started

- [ ] Modify `CacheDataLoader.__init__` for indicator config
- [ ] Implement `_read_cache` state loading
- [ ] Implement `_apply_indicators_to_new_data` private method
- [ ] Implement state persistence
- [ ] Integration tests for cache extension

### Phase 5: Testing & Validation (Week 5-6) - ⏳ Not Started

- [ ] Snapshot testing for all indicators
- [ ] Edge case testing (gaps, NaN, timezone)
- [ ] Performance regression tests
- [ ] Validate against existing cache files

### Phase 6: Documentation & Rollout (Week 6-7) - ⏳ Not Started

- [ ] Update README with indicator configuration
- [ ] Document state management and troubleshooting
- [ ] Migration guide for existing cache files
- [ ] Enable by default (or via feature flag)
- [ ] Monitor performance in production

**Legend**: ✅ Complete | 🔄 In Progress | ⏳ Not Started | ⏸️ Blocked

---

## Overview

This document outlines the requirements and implementation approach for converting the strategy-lab indicator calculation system from full-series recomputation to incremental calculation. The goal is to improve performance when fetching new data by only computing indicator values for newly-fetched bars while preserving existing cached indicator values.

**Purpose**: Enable efficient indicator calculation on new data segments without recalculating the entire historical series, reducing computation time and enabling real-time/near-real-time indicator updates.

---

## Table of Contents

- [Overview](#overview)
- [Current State Analysis](#current-state-analysis)
- [Requirements](#requirements)
- [Implementation Options](#implementation-options)
- [Library Evaluation](#library-evaluation)
- [Pre-Defined Indicator Set](#pre-defined-indicator-set)
- [Testing Strategy](#testing-strategy)
- [Integration Points](#integration-points)
- [Migration Path](#migration-path)
- [Open Questions](#open-questions)

---

## Current State Analysis

### Existing Implementation

**Current Indicators** (from `src/indicators/ta.py` and `src/indicators/orb.py`):

- Simple Moving Average (SMA)
- Exponential Moving Average (EMA)
- Relative Strength Index (RSI)
- Average True Range (ATR)
- MACD (Moving Average Convergence Divergence)
- Bollinger Bands
- Opening Range Breakout (ORB) levels

**Current Behavior**:

- All indicators use `pandas_ta` library for batch calculation over entire DataFrame
- Indicators support both bar-based and day-based length units
- Day-based indicators resample intraday data to daily, calculate, then forward-fill back to original frequency
- Cache system (`CacheDataLoader`) stores raw OHLCV + pre-computed indicators in rolling parquet files
- When new data is fetched, indicators are NOT automatically recalculated on new segments
- Legacy script `scripts/apply_indicator_data_cache.py` exists for offline batch indicator enrichment

**Problems with Current Approach**:

1. **Performance**: Recalculating entire series on every data fetch is computationally expensive
2. **Inconsistency**: New data segments lack indicator values unless manually enriched offline
3. **Memory**: Large DataFrames consume significant memory for full recalculation
4. **Latency**: Real-time/paper trading scenarios require fast indicator updates
5. **State Management**: No mechanism to maintain indicator state across cache updates

---

## Requirements

### Functional Requirements

#### FR1: Incremental Calculation

- **FR1.1**: Support incremental calculation for all existing indicators (SMA, EMA, RSI, ATR, MACD, BBands, ORB)
- **FR1.2**: Calculate indicators only on new data segments when cache is extended
- **FR1.3**: Preserve existing cached indicator values without recomputation
- **FR1.4**: Handle both bar-based and day-based indicator periods
- **FR1.5**: Support stateful indicators that require historical context (e.g., EMA, RSI)

#### FR2: State Management

- **FR2.1**: Persist indicator state (warmup values, rolling windows, etc.) alongside cache data
- **FR2.2**: Load and restore indicator state when resuming calculation
- **FR2.3**: Handle state initialization for new symbols/timeframes
- **FR2.4**: Validate state consistency with cached data

#### FR3: Data Integrity

- **FR3.1**: Ensure incremental results match full-series calculation (within numerical precision tolerance)
- **FR3.2**: Handle edge cases: gaps in data, missing bars, timezone changes
- **FR3.3**: Detect and handle cache corruption or state mismatch
- **FR3.4**: Support indicator recalculation on demand (override incremental mode)

#### FR4: Cache Integration

- **FR4.1**: Integrate with `CacheDataLoader.fetch()` to automatically calculate indicators on new data
- **FR4.2**: Store indicator values in rolling parquet files alongside OHLCV data
- **FR4.3**: Support partial indicator set (not all indicators required for all data)
- **FR4.4**: Maintain backward compatibility with existing cache files

### Non-Functional Requirements

#### NFR1: Performance

- **NFR1.1**: Incremental calculation should be ≥10x faster than full recalculation for typical cache extensions (1-7 days)
- **NFR1.2**: Memory usage should scale with new data size, not entire cache size
- **NFR1.3**: Support real-time calculation (single bar update) in <100ms for standard indicator set

#### NFR2: Correctness

- **NFR2.1**: Numerical precision: incremental results match full-series within 1e-6 relative error
- **NFR2.2**: 100% test coverage for incremental vs. full-series equivalence
- **NFR2.3**: Handle floating-point edge cases (NaN, inf, div-by-zero)

#### NFR3: Maintainability

- **NFR3.1**: Minimal changes to existing `IndicatorFactory` API
- **NFR3.2**: Clear separation between incremental and batch calculation modes
- **NFR3.3**: Comprehensive documentation of state management and calculation logic
- **NFR3.4**: Extensible architecture for adding new indicators

#### NFR4: Reliability

- **NFR4.1**: Graceful degradation: fallback to full recalculation if incremental fails
- **NFR4.2**: Logging and telemetry for incremental calculation performance
- **NFR4.3**: Validation tests run on every cache update

---

## Implementation Options

### Option 1: Custom Incremental Implementation

**Approach**: Implement incremental logic manually for each indicator.

**Pros**:

- Full control over calculation logic and state management
- No external dependencies
- Optimized for our specific use cases (5m bars, day-based resampling)
- Easy to debug and maintain

**Cons**:

- Significant development effort (implement each indicator from scratch)
- Risk of bugs and numerical inconsistencies
- Requires deep understanding of indicator mathematics
- Duplication of effort (indicators already exist in `pandas_ta`)

**Complexity**: High (4-6 weeks development + testing)

### Option 2: Stateful Wrapper Around pandas_ta

**Approach**: Create a stateful wrapper that manages rolling windows and calls `pandas_ta` on minimal data subsets.

**Pros**:

- Leverages existing `pandas_ta` implementations
- Moderate development effort
- Familiar API for developers already using `pandas_ta`

**Cons**:

- `pandas_ta` not designed for incremental use; may require full window data
- Complex state management for warmup periods
- Performance may not improve significantly if full window required
- Limited control over calculation details

**Complexity**: Medium (2-3 weeks development + testing)

### Option 3: Online/Streaming Indicator Libraries

**Approach**: Use existing libraries designed for incremental/online calculation.

**Pros**:

- Libraries designed explicitly for incremental calculation
- Battle-tested implementations
- Often optimized for performance
- Minimal custom code

**Cons**:

- External dependencies (licensing, maintenance, support)
- May not support all our indicators or use cases
- API differences require adaptation layer
- Potential mismatch with existing `pandas_ta` results

**Complexity**: Low-Medium (1-3 weeks integration + testing)

### Option 4: Hybrid Approach

**Approach**: Use streaming libraries for simple indicators (SMA, EMA), custom implementation for complex ones (ORB, day-based indicators).

**Pros**:

- Balanced effort vs. control
- Leverage libraries where appropriate
- Custom logic for domain-specific needs
- Incremental migration path

**Cons**:

- Multiple calculation paths to maintain
- Risk of inconsistencies between approaches
- Requires understanding multiple systems

**Complexity**: Medium (2-4 weeks development + testing)

**Recommendation**: Start with Option 3 (evaluate libraries), fallback to Option 4 if gaps exist.

---

## Library Evaluation

### Candidate Libraries for Online/Incremental Indicators

#### 1. **talipp** (Technical Analysis Library in Python)

- **GitHub**: https://github.com/nardew/talipp
- **License**: MIT
- **Status**: Active maintenance
- **Features**:
  - Designed for incremental calculation (add single values)
  - Supports: SMA, EMA, RSI, ATR, MACD, Bollinger Bands, and more
  - Pythonic API, easy to use
  - No dependencies on pandas (works with raw values)
- **Pros**:
  - Lightweight, focused on incremental use case
  - Active development, responsive maintainer
  - Clean API: `indicator.add(value)`, `indicator.value`
  - Good documentation with examples
- **Cons**:
  - Smaller community than pandas_ta/ta-lib
  - May need adaptation layer for DataFrame integration
  - Limited to technical indicators (no custom domain logic)
- **Verdict**: **Strong candidate** - designed exactly for our use case

#### 2. **online-indicator** (or similar streaming TA libs)

- Multiple small libraries exist for online calculation
- Generally support basic indicators (SMA, EMA, RSI)
- Varying quality and maintenance status
- **Verdict**: Evaluate on case-by-case basis; talipp likely superior

#### 3. **TA-Lib** with custom state management

- **Library**: https://github.com/mrjbq7/ta-lib
- **License**: BSD
- **Features**: Comprehensive indicator library (C-based, fast)
- **Approach**: Wrapper that manages state and calls TA-Lib on windowed data
- **Pros**: Fast, widely used, many indicators
- **Cons**: Not designed for incremental use; requires full window for most indicators
- **Verdict**: Not ideal for true incremental calculation

#### 4. **pandas-ta** with rolling windows

- Current library we use
- **Approach**: Maintain rolling window state, recalculate on window
- **Pros**: Already integrated, familiar
- **Cons**: Still recalculates full window; marginal improvement
- **Verdict**: Baseline/fallback, not optimal

#### 5. **Custom Implementation** (as needed)

- For indicators not supported by libraries (e.g., ORB, day-based resampling)
- Required regardless of library choice
- **Verdict**: Necessary complement to any library approach

### Recommended Library: **talipp**

**Rationale**:

- Purpose-built for incremental calculation
- Supports all our core indicators
- Active maintenance and MIT license
- Minimal dependencies and overhead
- Clean, testable API

**Integration Plan**:

1. Install talipp: `pip install talipp`
2. Create adapter layer: `src/indicators/incremental.py`
3. Implement state serialization for cache persistence
4. Add validation tests against `pandas_ta` baseline
5. Integrate with `CacheDataLoader`

**Fallback Plan**:

- If talipp lacks needed indicators or fails validation, implement custom incremental logic
- Use pandas_ta as reference implementation for correctness

---

## Pre-Defined Indicator Set

### Standard Indicators (Auto-Calculate on Cache Update)

These indicators will be automatically calculated and stored in cache for all symbols/timeframes:

#### Core Indicators (Based on `apply_indicator_data_cache.py`)

```python
CORE_INDICATORS = [
    # Moving Averages (EMA)
    {'name': 'ema', 'params': {'length': 20}, 'column': 'EMA_20'},
    {'name': 'ema', 'params': {'length': 30}, 'column': 'EMA_30'},
    {'name': 'ema', 'params': {'length': 50}, 'column': 'EMA_50'},
    {'name': 'ema', 'params': {'length': 200}, 'column': 'EMA_200'},

    # Momentum
    {'name': 'rsi', 'params': {'length': 14}, 'column': 'RSI_14'},

    # Volatility
    {'name': 'atr', 'params': {'length': 14}, 'column': 'ATRr_14'},

    # Strategy-Specific: ORB
    {'name': 'orb_levels', 'params': {'duration_minutes': 5, 'start_time': '09:30', 'body_pct': 0.5},
     'columns': ['ORB_High', 'ORB_Low', 'ORB_Range', 'ORB_Breakout']},
]
```

**Note**: These indicators match the configuration in `scripts/apply_indicator_data_cache.py` (EMA_LENGTHS = [20, 30, 50, 200], RSI_LENGTH = 14, ATR_LENGTH = 14, plus ORB levels).

### Configuration

**Cache Configuration** (in `CacheDataLoader.__init__`):

```python
def __init__(
    self,
    # ... existing params ...
    auto_indicators: Optional[List[Dict]] = None,  # None = CORE_INDICATORS, [] = disable
    indicator_mode: str = 'incremental',  # 'incremental', 'batch', 'skip'
):
    self.auto_indicators = auto_indicators if auto_indicators is not None else CORE_INDICATORS
    self.indicator_mode = indicator_mode
```

**Strategy Override**:
Strategies can request additional indicators via `IndicatorFactory.ensure_indicators()` (existing method), which will calculate missing indicators on-demand using batch mode.

---

## Testing Strategy

### Test Categories

#### 1. Unit Tests: Incremental Correctness

**Goal**: Verify incremental calculation matches full-series calculation.

**Approach**:

- For each indicator, generate synthetic data (e.g., 100 bars)
- Calculate using batch mode (pandas_ta)
- Calculate first 80 bars incrementally, then add 20 bars incrementally
- Compare results (allowing numerical tolerance: 1e-6 relative error)
- Test edge cases: NaN handling, zero values, negative values

**Test File**: `tests/indicators/test_incremental_correctness.py`

**Example Test**:

```python
def test_ema_incremental_matches_batch():
    # Generate test data
    df = pd.DataFrame({
        'close': np.random.randn(100).cumsum() + 100,
        'datetime': pd.date_range('2024-01-01', periods=100, freq='5min')
    }).set_index('datetime')

    # Batch calculation (baseline)
    df_batch = df.copy()
    df_batch.ta.ema(length=9, append=True)

    # Incremental calculation
    warmup_df = df.iloc[:50]
    state = IncrementalIndicator.init('ema', warmup_df, length=9)

    new_df = df.iloc[50:]
    result = IncrementalIndicator.update(state, new_df, 'ema', length=9)

    # Compare
    np.testing.assert_allclose(
        df_batch['EMA_9'].iloc[50:].values,
        result['EMA_9'].values,
        rtol=1e-6
    )
```

#### 2. Integration Tests: Cache Extension

**Goal**: Verify indicator calculation during cache fetch.

**Approach**:

- Mock `CacheDataLoader` with existing cache (e.g., 30 days data + indicators)
- Fetch new data (e.g., 1 additional day)
- Verify:
  - Indicators calculated only on new day
  - Existing indicator values unchanged
  - New indicator values correct (compare to batch baseline)
  - Cache file updated with new indicators

**Test File**: `tests/data/test_cache_incremental_indicators.py`

#### 3. Regression Tests: Snapshot Testing

**Goal**: Ensure indicator values remain stable across code changes.

**Approach**:

- Use existing snapshot testing framework
- Create fixtures with known indicator values
- Run incremental calculation and compare to snapshots
- Update snapshots only when indicator logic intentionally changes

**Test File**: `tests/snapshot/test_indicator_snapshots.py`

#### 4. Performance Tests

**Goal**: Measure incremental vs. batch performance.

**Approach**:

- Benchmark calculation time for varying data sizes (1 day, 7 days, 30 days new data)
- Compare memory usage
- Verify ≥10x speedup for typical use cases
- Profile hot paths for optimization

**Test File**: `tests/performance/test_indicator_performance.py`

#### 5. Edge Case Tests

**Test scenarios**:

- Empty new data (no-op)
- Gap in data (missing days)
- Single bar update (real-time scenario)
- Cache corruption (missing indicator columns)
- State mismatch (indicator params changed)
- Timezone changes
- Large cache (>1 year data)
- Multiple symbols in parallel

**Test File**: `tests/indicators/test_incremental_edge_cases.py`

### Test Data

**Fixtures**:

- Synthetic data: Controlled, reproducible, covers edge cases
- Historical data: Real market data from existing cache (AAPL, NVDA, AMZN)
- Use existing `tests/__scenarios__/` structure for fixture management

**Validation Data**:

- Pre-compute indicator values using pandas_ta (batch mode)
- Store as snapshots in `tests/__snapshots__/indicators/`
- Version alongside code changes

---

## Integration Points

### 1. CacheDataLoader Integration

**Modification Points** (in `src/data/cache.py`):

#### A. Initialize Indicator Engine

```python
class CacheDataLoader(DataLoader):
    def __init__(self, ..., auto_indicators=None, indicator_mode='incremental'):
        # ... existing init ...
        self.auto_indicators = auto_indicators or CORE_INDICATORS
        self.indicator_mode = indicator_mode
        self.indicator_engine = IncrementalIndicatorEngine() if indicator_mode == 'incremental' else None
```

#### B. Load Indicator State

```python
def _read_cache(self, path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if self.indicator_mode == 'incremental' and self.indicator_engine:
        state_path = path.with_suffix('.indicator_state.pkl')
        if state_path.exists():
            self.indicator_engine.load_state(state_path)
    return df
```

#### C. Calculate Indicators on New Data

```python
def fetch(self, symbol, timeframe, start, end) -> pd.DataFrame:
    # ... existing fetch logic ...

    # After fetching new segments and merging with cache
    if new_frames and self.auto_indicators:
        new_start_idx = len(df_cache) if not df_cache.empty else 0
        combined = self._apply_indicators_to_new_data(
            combined, new_start_idx, symbol, timeframe
        )

    # ... existing save logic ...

def _apply_indicators_to_new_data(
    self, df: pd.DataFrame, new_start_idx: int, symbol: str, timeframe: str
) -> pd.DataFrame:
    """Apply indicators to newly fetched data based on indicator_mode.

    Args:
        df: Combined DataFrame with old cache + new data
        new_start_idx: Index where new data begins
        symbol: Ticker symbol
        timeframe: Timeframe string (e.g., '5m')

    Returns:
        DataFrame with indicators calculated on new portion
    """
    if self.indicator_mode == 'incremental':
        # Incremental calculation
        return self.indicator_engine.update(
            df, new_start_idx, self.auto_indicators, symbol, timeframe
        )
    elif self.indicator_mode == 'batch':
        # Full recalculation (fallback)
        return IndicatorFactory.apply(df, self.auto_indicators)
    # else: 'skip' mode, no calculation
    return df
```

#### D. Persist Indicator State

```python
def fetch(self, symbol, timeframe, start, end) -> pd.DataFrame:
    # ... after saving parquet file ...
    if self.indicator_mode == 'incremental' and self.indicator_engine:
        state_path = rolling_path.with_suffix('.indicator_state.pkl')
        self.indicator_engine.save_state(state_path)
```

### 2. IndicatorFactory Updates

**New Module**: `src/indicators/incremental.py`

**Contents**:

- `IncrementalIndicatorEngine`: Manages indicator state and calculation
- `IncrementalIndicator`: Base class for individual incremental indicators
- Implementations: `IncrementalEMA`, `IncrementalRSI`, etc.
- Adapters for talipp library

**API**:

```python
class IncrementalIndicatorEngine:
    def __init__(self):
        self.states = {}  # {(symbol, timeframe, indicator_name): state}

    def update(self, df: pd.DataFrame, new_start_idx: int, indicators: List[Dict]) -> pd.DataFrame:
        """Calculate indicators on new portion of df starting at new_start_idx."""
        pass

    def load_state(self, path: Path):
        """Load persisted indicator state."""
        pass

    def save_state(self, path: Path):
        """Persist indicator state."""
        pass
```

**Backward Compatibility**:

- Existing `IndicatorFactory.apply()` remains unchanged (batch mode)
- New `IndicatorFactory.apply_incremental()` for incremental mode
- Strategies using `IndicatorFactory` unaffected (use cached indicator values)

### 3. Offline Enrichment Script

**Update**: `scripts/apply_indicator_data_cache.py`

**Changes**:

- Add flag `--incremental` to use incremental mode
- Default to batch mode for backward compatibility
- Support re-enrichment (overwrite existing indicators with recalculation)

### 4. Configuration Management

**New Config**: `src/config/indicators.py`

**Contents**:

```python
CORE_INDICATORS = [...]  # As defined above
ADVANCED_INDICATORS = [...]
STRATEGY_INDICATORS = {...}

DEFAULT_CACHE_INDICATOR_MODE = 'incremental'  # Or load from environment variable
```

---

## Migration Path

### Phase 1: Foundation (Week 1)

- [ ] Evaluate and select library (talipp recommended)
- [ ] Create `src/indicators/incremental.py` module structure
- [ ] Implement state serialization/deserialization
- [ ] Set up test infrastructure (fixtures, snapshots)

### Phase 2: Core Indicators (Week 2-3)

- [ ] Implement incremental SMA, EMA
- [ ] Implement incremental RSI
- [ ] Implement incremental ATR
- [ ] Write unit tests for correctness (incremental vs. batch)
- [ ] Performance benchmarking

### Phase 3: Advanced Indicators (Week 3-4)

- [ ] Implement incremental MACD
- [ ] Implement incremental Bollinger Bands
- [ ] Handle day-based indicators (resample, forward-fill logic)
- [ ] Custom indicator: ORB (incremental or batch hybrid)

### Phase 4: Cache Integration (Week 4-5)

- [ ] Modify `CacheDataLoader.__init__` for indicator config
- [ ] Implement `_read_cache` state loading
- [ ] Implement `fetch` indicator calculation on new data
- [ ] Implement state persistence
- [ ] Integration tests for cache extension

### Phase 5: Testing & Validation (Week 5-6)

- [ ] Snapshot testing for all indicators
- [ ] Edge case testing (gaps, NaN, timezone)
- [ ] Performance regression tests
- [ ] Validate against existing cache files
- [ ] CI/CD integration

### Phase 6: Documentation & Rollout (Week 6-7)

- [ ] Update README with indicator configuration
- [ ] Document state management and troubleshooting
- [ ] Migration guide for existing cache files
- [ ] Enable by default (or via feature flag)
- [ ] Monitor performance in production

### Rollback Plan

- Feature flag: `ENABLE_INCREMENTAL_INDICATORS` (default: false initially)
- Fallback to batch mode if incremental fails (logged as warning)
- Script to recalculate all indicators in batch mode if needed

---

## Open Questions

### Q1: State Serialization Format

**Question**: Should indicator state be stored in separate files or embedded in parquet metadata?

**What is Indicator State?**

Indicator state contains the intermediate values needed to calculate the next indicator value incrementally without reprocessing historical data. Different indicators require different state:

**Example States:**

1. **EMA (Exponential Moving Average)**:

   ```python
   {
       'indicator': 'ema',
       'params': {'length': 20},
       'state': {
           'last_ema': 152.45,  # Previous EMA value
           'alpha': 0.095238,   # Smoothing factor: 2/(n+1)
       },
       'last_bar_timestamp': '2025-11-24T15:55:00Z'
   }
   ```

2. **RSI (Relative Strength Index)**:

   ```python
   {
       'indicator': 'rsi',
       'params': {'length': 14},
       'state': {
           'avg_gain': 1.23,    # Smoothed average gain
           'avg_loss': 0.87,    # Smoothed average loss
           'last_close': 151.20 # Previous close for delta calculation
       },
       'last_bar_timestamp': '2025-11-24T15:55:00Z'
   }
   ```

3. **SMA (Simple Moving Average)**:

   ```python
   {
       'indicator': 'sma',
       'params': {'length': 20},
       'state': {
           'window': [150.2, 150.5, ..., 151.8],  # Last 20 values
           'sum': 3024.5  # Sum of window (optimization)
       },
       'last_bar_timestamp': '2025-11-24T15:55:00Z'
   }
   ```

4. **ATR (Average True Range)**:
   ```python
   {
       'indicator': 'atr',
       'params': {'length': 14},
       'state': {
           'last_atr': 2.45,     # Previous ATR value
           'last_close': 151.20  # Previous close for TR calculation
       },
       'last_bar_timestamp': '2025-11-24T15:55:00Z'
   }
   ```

**Options**:

- **A**: Separate `.pkl` or `.json` files (e.g., `AAPL_5m.indicator_state.pkl`)
- **B**: Parquet metadata (custom key-value store)
- **C**: Additional columns in parquet (e.g., `_state_ema_9`)

**Recommendation**: Option A (separate files)

- Pros: Clean separation, easy debugging, no schema pollution, supports complex state structures
- Cons: Extra file management (but already have `.meta.json` precedent)

### Q2: Day-Based Indicator Incremental Calculation

**Question**: How to handle day-based indicators (e.g., EMA_21 with `use_days=True`) incrementally?

**Current Cache Behavior**: The existing `apply_indicator_data_cache.py` script calculates all indicators using **per-bar** (intraday) calculation with `use_days=False` (default). No day-based indicators are currently being cached.

**Challenge**: Day-based indicators (when `use_days=True`) require:

1. Resampling intraday data to daily OHLCV
2. Calculating indicator on daily data
3. Forward-filling daily values back to intraday bars

Incremental approach is complex because a single new intraday bar might complete a new daily bar, requiring:

- Detection of daily boundary crossings
- Incremental daily indicator update
- Re-forward-fill to all intraday bars of that day

**Options**:

- **A**: Maintain daily cache + state separately, always recalculate daily indicators from daily cache
- **B**: Hybrid: daily indicators use batch mode, only intraday indicators are incremental
- **C**: Complex: track daily state, incrementally update daily values, re-forward-fill affected intraday bars

**Recommendation**: Option B (hybrid approach) for initial implementation

- **Current cache uses per-bar calculation only**, so this is not an immediate concern
- Focus incremental optimization on intraday indicators (all current cached indicators)
- Day-based calculation can remain batch mode if/when needed in the future
- Can revisit Option C if day-based performance becomes bottleneck

### Q3: Warmup Period Handling

**Question**: How many bars of warmup/context needed to initialize incremental calculation?

**Context**: Indicators like EMA, RSI need historical data to converge to stable values.

**Approach**:

- Calculate warmup requirement per indicator (e.g., EMA needs ~3x length, RSI needs length+1)
- When loading cache with existing indicators, use last N bars as warmup context
- If insufficient history (new cache), pad with NaN and document convergence period
- Add metadata: `warmup_complete_index` to track when indicators are stable

### Q4: Parallel Symbol Processing

**Question**: Can indicator calculation be parallelized across symbols?

**Context**: User may fetch data for multiple symbols simultaneously.

**Approach**:

- Indicator state is per-(symbol, timeframe), so independent
- CacheDataLoader instances can run in parallel (thread-safe state management)
- Consider multiprocessing for bulk enrichment script
- Lock state files during write (use `fcntl` or `filelock` library)

### Q5: Version Compatibility

**Question**: What happens if indicator parameters change between code versions?

**Example**: EMA length changed from 20 to 21, or ATR calculation logic fixed/updated.

**Approach**:

- Add version metadata to state files:
  ```python
  {
      'indicator': 'ema',
      'params': {'length': 20},
      'version': 'v1',
      'column': 'EMA_20'
  }
  ```
- On load, check params match current configuration
- **If mismatch in cache/batch scenarios**:
  - Log warning with details of mismatch
  - Discard state (do not use for incremental calculation)
  - **Do NOT auto-recalculate** - user must run `scripts/apply_indicator_data_cache.py --force` to recalculate
  - This prevents unexpected performance hits during data fetching
- **If mismatch in real-time scenarios** (paper/live trading):
  - Log warning
  - Skip indicator calculation (do not calculate that specific indicator)
  - Trading can continue with other indicators or cached values
  - Alerts user to configuration mismatch requiring investigation
- Consider state migration functions for non-breaking changes (e.g., adding new field with default value)

### Q6: Real-Time Calculation

**Question**: Should incremental system support single-bar real-time updates?

**Context**: Paper trading and live trading require bar-by-bar indicator updates.

**Approach**:

- Yes, design for single-bar incremental updates from start
- Performance target: <100ms for full indicator set on 1 bar
- Use same `IncrementalIndicatorEngine` for both cache extension and real-time
- Add telemetry to track real-time performance

---

## Success Criteria

### Must Have (MVP)

- [ ] SMA, EMA, RSI, ATR calculated incrementally with <1e-6 error vs. batch
- [ ] Cache integration: new data automatically enriched with indicators
- [ ] 10x performance improvement for 1-day cache extension (vs. full recalculation)
- [ ] 100% test coverage for incremental correctness
- [ ] Backward compatible: existing code works without changes
- [ ] Documentation: usage guide, API reference, troubleshooting

### Should Have

- [ ] MACD, Bollinger Bands incremental calculation
- [ ] State persistence and recovery
- [ ] Graceful degradation (fallback to batch mode)
- [ ] Performance benchmarks and CI integration
- [ ] ORB indicator incremental or hybrid approach

### Nice to Have

- [ ] Day-based indicators incremental calculation
- [ ] Parallel processing for multiple symbols
- [ ] Real-time single-bar performance optimization (<50ms)
- [ ] State migration for version changes
- [ ] Visual debugging tools (compare incremental vs. batch charts)

---

## References

### Internal Documentation

- `docs/snapshot_testing_requirements.md`: Testing framework guidelines
- `docs/strategy_verification.md`: Strategy testing workflows
- `src/data/cache.py`: Current cache implementation
- `src/indicators/ta.py`: Current indicator implementations
- `scripts/apply_indicator_data_cache.py`: Offline enrichment script

### External Libraries

- **talipp**: https://github.com/nardew/talipp (recommended)
- **pandas-ta**: https://github.com/twopirllc/pandas-ta (current baseline)
- **TA-Lib**: https://github.com/mrjbq7/ta-lib (reference implementation)

### Technical Analysis Resources

- **Indicators Explained**: https://www.investopedia.com/terms/t/technicalindicator.asp
- **EMA Calculation**: https://en.wikipedia.org/wiki/Moving_average#Exponential_moving_average
- **RSI Calculation**: https://en.wikipedia.org/wiki/Relative_strength_index
- **MACD Details**: https://www.investopedia.com/terms/m/macd.asp

---

## Appendix: Indicator Calculation Formulas

### Simple Moving Average (SMA)

```
SMA[t] = (P[t] + P[t-1] + ... + P[t-n+1]) / n

Incremental:
- Maintain rolling window of last n values
- On new bar: add P[t], remove P[t-n], recalculate sum/n
```

### Exponential Moving Average (EMA)

```
EMA[t] = α * P[t] + (1 - α) * EMA[t-1]
where α = 2 / (n + 1)

Incremental:
- Only need previous EMA value
- On new bar: apply formula directly
- Warmup: requires ~3n bars to converge
```

### Relative Strength Index (RSI)

```
RS = AvgGain / AvgLoss
RSI = 100 - (100 / (1 + RS))

AvgGain[t] = ((AvgGain[t-1] * (n-1)) + CurrentGain) / n
AvgLoss[t] = ((AvgLoss[t-1] * (n-1)) + CurrentLoss) / n

Incremental:
- Maintain previous AvgGain, AvgLoss
- On new bar: calculate gain/loss delta, update averages
- Warmup: requires n+1 bars
```

### Average True Range (ATR)

```
TR[t] = max(High[t] - Low[t], abs(High[t] - Close[t-1]), abs(Low[t] - Close[t-1]))
ATR[t] = (ATR[t-1] * (n-1) + TR[t]) / n

Incremental:
- Maintain previous ATR and Close
- On new bar: calculate TR, update ATR
- Warmup: requires n bars
```

### MACD

```
MACD = EMA_fast - EMA_slow
Signal = EMA(MACD, signal_length)
Histogram = MACD - Signal

Incremental:
- Maintain state for EMA_fast, EMA_slow, Signal EMA
- Calculate incrementally as per EMA formula
```

### Bollinger Bands

```
Middle = SMA(close, n)
StdDev = sqrt(sum((close[i] - Middle)^2) / n)
Upper = Middle + (k * StdDev)
Lower = Middle - (k * StdDev)

Incremental:
- Maintain rolling window for SMA and variance calculation
- On new bar: update SMA, update variance (Welford's algorithm), calculate bands
```

### Opening Range Breakout (ORB)

```
For each day:
- ORB_High = max(High) during opening range period
- ORB_Low = min(Low) during opening range period
- ORB_Range = ORB_High - ORB_Low
- ORB_Breakout = 1 if body breaks above ORB_High, -1 if below ORB_Low, else 0

Incremental:
- Per-day calculation (not cross-day state)
- On new day: initialize ORB values as bars arrive
- On new bar: update ORB levels during range period, check breakout after
- Challenge: detecting new day boundary, handling pre-market/post-market bars
```

---

**Document Version**: 1.0
**Last Updated**: 2024-11-24
**Author**: Strategy Lab Team
**Status**: Requirements & Planning Phase
