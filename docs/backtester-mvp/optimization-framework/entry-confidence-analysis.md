# Entry Signal Confidence - Implementation Analysis

**Date:** 2026-05-18  
**Difficulty:** 🟢 **LOW-MEDIUM** (2-3 hours for basic implementation)

---

## Executive Summary

Adding **entry signal confidence validation** as an optimizable parameter is **straightforward** with your current architecture. The optimization framework already supports it—you just need to:

1. Add confidence scoring logic to `ORBStrategy`
2. Add confidence threshold to YAML ruleset
3. Add parameter to sensitivity runner
4. Run optimization

**Estimated effort:** 2-3 hours for basic implementation, 4-6 hours for advanced multi-component scoring.

---

## Current System Analysis

### ✅ What You Already Have

Your system **already has** entry confidence components:

| Component | Location | Status | Purpose |
|-----------|----------|--------|---------|
| **Volume filter** | `ORBStrategy._check_volume_filter()` | ✅ Implemented | Filter low-volume breakouts |
| **Body % filter** | `ORBCalculator.orb_body_pct_filter` | ✅ Implemented | Filter weak breakout candles |
| **Pre-computed indicators** | `FeatureEngine` (ATR, ADX, slope, etc.) | ✅ Implemented | Available for confidence scoring |
| **MTF validation** | `MTFValidator` | ✅ Implemented (disabled) | Multi-timeframe alignment |

**Current filtering pattern:**
```python
# In ORBStrategy.generate_signal_incremental()
if self.config.use_volume_filter:
    if not self._check_volume_filter(daily_df, idx):
        continue  # Skip entry

# In ORBCalculator.is_long_breakout()
if self.body_pct_filter > 0.0:
    if body_pct < self.body_pct_filter:
        return False  # Reject weak breakout
```

**This is already confidence filtering!** You just need to unify it into a single **confidence score**.

---

## Implementation Approaches

### Approach 1: Simple Confidence Threshold (EASIEST)

**Concept:** Combine existing filters into a 0.0-1.0 confidence score, then optimize the threshold.

#### Step 1: Add Confidence Scorer to ORBStrategy

```python
# vibe/common/strategies/orb.py

def _calculate_entry_confidence(
    self, 
    current_bar: pd.Series,
    daily_df: pd.DataFrame,
    current_idx: int,
    orb_levels: ORBLevels,
) -> float:
    """
    Calculate entry confidence score (0.0 = low, 1.0 = high).
    
    Components:
    - Volume strength (0.0-1.0)
    - Body % (0.0-1.0)
    - Breakout distance (0.0-1.0)
    
    Returns:
        Confidence score (0.0-1.0)
    """
    confidence_components = []
    
    # Component 1: Volume strength (current vol vs avg vol)
    if current_idx > 0:
        lookback = min(20, current_idx)
        avg_volume = daily_df.iloc[current_idx - lookback : current_idx]["volume"].mean()
        current_volume = current_bar["volume"]
        
        # Normalize: 1.0x avg = 0.0, 2.0x avg = 1.0
        volume_score = min((current_volume / avg_volume - 1.0), 1.0)
        confidence_components.append(volume_score)
    
    # Component 2: Body % (strong candle body)
    body_pct = self._calculate_body_pct(
        current_bar["open"], 
        current_bar["high"], 
        current_bar["low"], 
        current_bar["close"]
    )
    confidence_components.append(body_pct)
    
    # Component 3: Breakout distance (how far beyond ORB level)
    orb_range = orb_levels.orb_high - orb_levels.orb_low
    if orb_range > 0:
        # For long breakout
        if current_bar["close"] > orb_levels.orb_high:
            breakout_distance = current_bar["close"] - orb_levels.orb_high
            breakout_score = min(breakout_distance / (0.5 * orb_range), 1.0)
            confidence_components.append(breakout_score)
        # For short breakout
        elif current_bar["close"] < orb_levels.orb_low:
            breakout_distance = orb_levels.orb_low - current_bar["close"]
            breakout_score = min(breakout_distance / (0.5 * orb_range), 1.0)
            confidence_components.append(breakout_score)
    
    # Average all components
    if not confidence_components:
        return 0.5  # Neutral if no components available
    
    return sum(confidence_components) / len(confidence_components)
```

#### Step 2: Add Threshold to Strategy Config

```python
# vibe/common/strategies/orb.py

class ORBStrategyConfig(StrategyConfig):
    """ORB Strategy configuration."""
    
    # ... existing fields ...
    
    entry_confidence_threshold: float = Field(
        default=0.0,
        description="Min entry confidence score (0.0-1.0, 0.0 = no filter)",
    )
```

#### Step 3: Apply Filter in Signal Generation

```python
# vibe/common/strategies/orb.py - generate_signal_incremental()

# Calculate confidence score
confidence = self._calculate_entry_confidence(
    current_bar=row,
    daily_df=daily_df,
    current_idx=idx,
    orb_levels=levels,
)

# Filter by threshold
if confidence < self.config.entry_confidence_threshold:
    return 0, {"reason": "low_confidence", "confidence": confidence}

# Accept entry
signal = 1 if is_long else -1
metadata["confidence"] = confidence
return signal, metadata
```

#### Step 4: Add to YAML Ruleset

```yaml
# vibe/rulesets/orb_production.yaml

strategy:
  type: orb
  orb_duration_minutes: 5
  entry_confidence_threshold: 0.0  # 0.0 = no filter, 0.5 = medium, 0.7 = high
```

#### Step 5: Add to Optimization Parameters

```python
# vibe/backtester/analysis/sensitivity_runner.py

ParameterDefinition(
    path="strategy.entry_confidence_threshold",
    values=[0.0, 0.3, 0.5, 0.7],
    base_value=0.0,
    name="Confidence_Threshold",
),
```

#### Step 6: Run Optimization

```bash
python scripts/optimize_strategy.py --strategy orb --mode full
```

**Difficulty:** 🟢 **LOW** (2-3 hours)  
**Benefits:**
- ✅ Simple to implement (reuses existing filters)
- ✅ Easy to interpret (single threshold parameter)
- ✅ Backward compatible (threshold=0.0 disables filter)

**Limitations:**
- ⚠️ Equal weighting of components may not be optimal
- ⚠️ Normalization is somewhat arbitrary

---

### Approach 2: Weighted Multi-Component Confidence (MEDIUM)

**Concept:** Allow optimizing **weights** for each confidence component.

#### Configuration

```yaml
# vibe/rulesets/orb_production.yaml

strategy:
  type: orb
  entry_confidence:
    enabled: true
    threshold: 0.5
    weights:
      volume: 0.4
      body_pct: 0.3
      breakout_distance: 0.3
```

#### Optimization

```python
# Optimize component weights
ParameterDefinition("strategy.entry_confidence.threshold", [0.0, 0.5, 0.7], 0.5, "Confidence_Threshold"),
ParameterDefinition("strategy.entry_confidence.weights.volume", [0.2, 0.4, 0.6], 0.4, "Volume_Weight"),
ParameterDefinition("strategy.entry_confidence.weights.body_pct", [0.2, 0.3, 0.4], 0.3, "Body_Weight"),
```

**Difficulty:** 🟡 **MEDIUM** (4-6 hours)  
**Benefits:**
- ✅ More flexible (optimizes component importance)
- ✅ Can discover which filters matter most

**Limitations:**
- ⚠️ More parameters = higher overfitting risk
- ⚠️ Requires more backtests (3 × 3 × 3 = 27 combinations)

---

### Approach 3: Indicator-Based Confidence (ADVANCED)

**Concept:** Use pre-computed indicators (ATR percentile, ADX, slope) for confidence.

#### Implementation

```python
def _calculate_entry_confidence(
    self, 
    current_bar: pd.Series,
    ...
) -> float:
    """Enhanced confidence with regime indicators."""
    
    # Component 1: Volume (as before)
    volume_score = ...
    
    # Component 2: Body % (as before)
    body_score = ...
    
    # Component 3: ATR percentile (avoid extreme volatility)
    # Lower ATR percentile = more confidence (stable conditions)
    atr_pctile = current_bar.get("atr_percentile_60d", 0.5)
    atr_score = 1.0 - min(atr_pctile, 1.0)  # Invert: low vol = high score
    
    # Component 4: Trend strength (ADX)
    # Higher ADX = stronger trend = more confidence
    adx = current_bar.get("ADX_14", 0)
    adx_score = min(adx / 40.0, 1.0)  # Normalize: ADX 40+ = 1.0
    
    # Component 5: Trend direction alignment (slope)
    slope = current_bar.get("slope_20", 0)
    # For long: positive slope = high confidence
    # For short: negative slope = high confidence
    if signal_direction == 1:  # Long
        slope_score = min(max(slope, 0) / 0.02, 1.0)
    else:  # Short
        slope_score = min(max(-slope, 0) / 0.02, 1.0)
    
    # Weighted average
    confidence = (
        0.25 * volume_score +
        0.20 * body_score +
        0.20 * atr_score +
        0.20 * adx_score +
        0.15 * slope_score
    )
    
    return confidence
```

**Difficulty:** 🟡 **MEDIUM** (4-6 hours, indicators already available)  
**Benefits:**
- ✅ Uses regime research indicators (ATR, ADX, slope)
- ✅ Captures market context (volatility, trend strength)
- ✅ Can align with regime filters

**Limitations:**
- ⚠️ More complex to interpret
- ⚠️ May overfit to specific market regimes

---

## Recommended Implementation Path

### Phase 1: Start Simple (Week 1)

1. **Implement Approach 1** (simple threshold)
   - Combine volume + body % + breakout distance
   - Single threshold parameter (0.0-1.0)
   - Optimize on 2018-2024 data

2. **Validate performance**
   - Does confidence filter improve expectancy?
   - What threshold works best?
   - Walk-forward validation

### Phase 2: Add Indicators (Week 2-3, if Phase 1 works)

3. **Add ATR percentile component**
   - Your research showed `atr_pctile < 0.80` improves performance
   - Filter out extreme volatility days

4. **Add trend alignment (optional)**
   - ADX for trend strength
   - Slope for direction alignment

5. **Optimize component weights**
   - Find which components matter most

### Phase 3: Production (Week 4, if validated)

6. **Deploy best configuration**
   - Add to `orb_production_no_tp.yaml`
   - Monitor in paper trading

---

## Integration with Existing System

### ✅ Already Compatible

Your optimization framework **already supports** this out-of-the-box:

1. **YAML-based parameters:** Any field in ruleset YAML can be optimized
2. **Pre-computed indicators:** FeatureEngine provides ATR, ADX, slope—just reference them in `current_bar`
3. **Dot-notation paths:** `strategy.entry_confidence_threshold` works immediately
4. **Backward compatibility:** `threshold=0.0` disables filter (no behavior change)

### Changes Required

| Component | File | Change Required | Difficulty |
|-----------|------|-----------------|------------|
| Strategy config | `vibe/common/strategies/orb.py` | Add `entry_confidence_threshold` field | 🟢 Trivial |
| Confidence scorer | `vibe/common/strategies/orb.py` | Add `_calculate_entry_confidence()` method | 🟢 Low |
| Signal generation | `vibe/common/strategies/orb.py` | Call scorer and filter by threshold | 🟢 Low |
| YAML ruleset | `vibe/rulesets/orb_production.yaml` | Add `entry_confidence_threshold: 0.0` | 🟢 Trivial |
| Ruleset model | `vibe/common/ruleset/models.py` | Add `entry_confidence_threshold` to `ORBStrategyParams` | 🟢 Trivial |
| Sensitivity runner | `vibe/backtester/analysis/sensitivity_runner.py` | Add parameter definition | 🟢 Trivial |

**Total LOC estimate:** ~150 lines for Approach 1, ~250 lines for Approach 2

---

## Expected Performance Impact

Based on your regime research findings:

### Hypothesis

**Entry confidence should improve edge by:**
- Filtering low-quality breakouts (weak volume, small body)
- Reducing drawdowns (fewer losing trades)
- Potentially improving Sharpe ratio

### Expected Outcomes

| Metric | Current (No TP) | With Confidence Filter (Estimate) |
|--------|-----------------|-----------------------------------|
| Expectancy | +0.11R | +0.13R to +0.15R (↑15-30%) |
| Win Rate | 29.2% | 32-35% (↑10-20%) |
| # Trades | 1,678 | 1,200-1,400 (↓15-30%) |
| Sharpe | 0.886 | 0.95-1.05 (↑5-15%) |

**Why it should work:**
- Your research showed quality matters (EOD exits = 90.9% win rate)
- Top 10% of trades = 60% of profits → need to identify high-quality entries
- H3 filter (ATR percentile) already improved performance (+18%)

### Risks

- ⚠️ **Overfitting:** Too many confidence components = curve-fitting
- ⚠️ **Trade count reduction:** May filter too aggressively (need ≥500 trades for statistical validity)
- ⚠️ **Regime dependence:** Optimal threshold may vary by regime

---

## Testing Strategy

### Backtest Validation (Before Production)

1. **In-sample optimization (2018-2023):**
   - Find best threshold (0.0, 0.3, 0.5, 0.7)
   
2. **Out-of-sample validation (2024-2025):**
   - Test if filter still works on unseen data
   
3. **Walk-forward analysis:**
   - 6-month train, 1-month test, rolling
   
4. **Robustness check:**
   - Does it survive 10-tick slippage?
   - Does it work across regimes?

5. **Trade count check:**
   - Ensure ≥500 trades remain (statistical validity)
   - If <500 trades, threshold is too aggressive

### Paper Trading Validation (Before Live)

6. **3-month paper trading** with confidence filter enabled
7. **Compare to baseline** (no filter)
8. **Monitor false negatives** (did we skip big winners?)

---

## Code Example: Full Implementation

```python
# vibe/common/strategies/orb.py

class ORBStrategyConfig(StrategyConfig):
    """ORB Strategy configuration."""
    
    # ... existing fields ...
    
    entry_confidence_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum entry confidence score (0.0 = no filter, 1.0 = max filter)",
    )


class ORBStrategy(StrategyBase):
    """Opening Range Breakout strategy with entry confidence filtering."""
    
    def _calculate_entry_confidence(
        self,
        current_bar: pd.Series,
        daily_df: pd.DataFrame,
        current_idx: int,
        orb_levels: ORBLevels,
        signal_direction: int,  # 1 = long, -1 = short
    ) -> float:
        """
        Calculate entry confidence score (0.0-1.0).
        
        Returns:
            Confidence score (higher = more confident)
        """
        scores = []
        
        # 1. Volume strength
        if current_idx > 0:
            lookback = min(20, current_idx)
            avg_vol = daily_df.iloc[current_idx - lookback : current_idx]["volume"].mean()
            current_vol = current_bar["volume"]
            # 1.0x avg = 0.0, 2.0x avg = 1.0
            vol_score = min(max((current_vol / avg_vol - 1.0), 0.0), 1.0)
            scores.append(vol_score)
        
        # 2. Body percentage
        body_pct = self._calculate_body_pct(
            current_bar["open"],
            current_bar["high"],
            current_bar["low"],
            current_bar["close"],
        )
        scores.append(body_pct)
        
        # 3. Breakout distance
        orb_range = orb_levels.orb_high - orb_levels.orb_low
        if orb_range > 0:
            if signal_direction == 1:  # Long
                distance = current_bar["close"] - orb_levels.orb_high
            else:  # Short
                distance = orb_levels.orb_low - current_bar["close"]
            
            # Normalize: 50% of ORB range = max score
            breakout_score = min(max(distance / (0.5 * orb_range), 0.0), 1.0)
            scores.append(breakout_score)
        
        # Average all components
        return sum(scores) / len(scores) if scores else 0.5
    
    def generate_signal_incremental(
        self,
        symbol: str,
        current_bar: Dict[str, float],
        df_context: pd.DataFrame,
    ) -> Tuple[int, Dict[str, Any]]:
        """Generate signal with confidence filtering."""
        
        # ... existing logic to determine signal direction ...
        
        # NEW: Calculate confidence
        confidence = self._calculate_entry_confidence(
            current_bar=row,
            daily_df=daily_df,
            current_idx=idx,
            orb_levels=levels,
            signal_direction=signal,  # 1 or -1
        )
        
        # NEW: Filter by threshold
        if confidence < self.config.entry_confidence_threshold:
            return 0, {
                "reason": "confidence_too_low",
                "confidence": confidence,
                "threshold": self.config.entry_confidence_threshold,
            }
        
        # Accept entry
        metadata["confidence"] = confidence
        return signal, metadata
```

```yaml
# vibe/rulesets/orb_production.yaml

strategy:
  type: orb
  orb_duration_minutes: 5
  entry_cutoff_time: "15:00"
  entry_confidence_threshold: 0.0  # Start with 0.0 (disabled), optimize later
```

```python
# vibe/backtester/analysis/sensitivity_runner.py

def get_orb_parameters(mode: str = "quick") -> list:
    if mode == "quick":
        return [
            ParameterDefinition("strategy.orb_duration_minutes", [5, 10, 15], 5, "ORB_Duration"),
            ParameterDefinition("exit.take_profit.multiplier", [0.0], 0.0, "TP_Multiplier"),
            ParameterDefinition("position_size.value", [0.005, 0.01, 0.02], 0.01, "Risk_Pct"),
            
            # NEW: Optimize confidence threshold
            ParameterDefinition(
                path="strategy.entry_confidence_threshold",
                values=[0.0, 0.3, 0.5, 0.7],
                base_value=0.0,
                name="Confidence_Threshold",
            ),
        ]
```

---

## Summary

**Question:** How difficult is it to add entry signal confidence validation as an optimizable parameter?

**Answer:** 🟢 **LOW-MEDIUM difficulty** (2-3 hours)

**Why it's easy:**
1. ✅ Architecture already supports it (YAML parameters, pre-computed indicators)
2. ✅ Similar patterns already exist (volume filter, body filter)
3. ✅ Optimization framework handles it automatically
4. ✅ No framework changes required

**Implementation checklist:**
- [ ] Add `_calculate_entry_confidence()` to `ORBStrategy` (~50 LOC)
- [ ] Add `entry_confidence_threshold` to `ORBStrategyConfig` (1 line)
- [ ] Add threshold check to `generate_signal_incremental()` (~5 LOC)
- [ ] Add field to `ORBStrategyParams` Pydantic model (1 line)
- [ ] Add to YAML ruleset (1 line)
- [ ] Add to sensitivity runner (5 lines)
- [ ] Run optimization (`python scripts/optimize_strategy.py --strategy orb`)

**Expected timeline:**
- Basic implementation: 2-3 hours
- Testing & validation: 4-6 hours
- Walk-forward analysis: 2 hours
- **Total:** 1-2 days

**Recommendation:** Start with **Approach 1** (simple threshold), validate with walk-forward, then decide if more complexity is needed.
