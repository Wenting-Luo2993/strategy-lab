# Realistic Order Execution Simulator — Implementation Plan

## Project Scope: Medium (14 tasks across 3 phases)

---

## Phase 1: Core Foundation (Tasks 1–6)

Delivers: Order/Fill models, pluggable slippage/volume/impact protocols with default implementations, `ExecutionSimulator` orchestrator. All backward-compatible — existing tests pass unchanged.

### Task 1: Data Models (`execution/models.py`)

**Implementation:**
- Create `Order` dataclass: `id`, `symbol`, `side`, `size`, `order_type`, `limit_price`, `timestamp`, `signal_bar_index`, `price_override`
- Create `Fill` dataclass: `order_id`, `symbol`, `side`, `price`, `qty`, `timestamp`, `slippage`, `impact`
- Side validation (`buy`/`sell`), order_type validation (`market`/`limit`)
- Limit order validation in `__post_init__`: limit orders must have `limit_price`, market orders must not
- `Order.remaining(filled_qty) -> Order` method for partial fill remainder:
  - Returns new Order with same ID, reduced size (`self.size - filled_qty`)
  - Preserves all other fields (same order, just partial fill)
  - Raises `ValueError` if `filled_qty > self.size`

**Verification:**
- All fields serialize/deserialize correctly
- Invalid side/order_type raises `ValueError`
- Limit order without limit_price raises `ValueError`
- Market order with limit_price raises `ValueError`
- `remaining()` returns order with reduced size
- `remaining()` raises when filled_qty exceeds size

**Unit Tests:**
- `test_order_creation_with_valid_fields`
- `test_order_rejects_invalid_side`
- `test_order_rejects_invalid_order_type`
- `test_limit_order_without_limit_price_raises`
- `test_market_order_with_limit_price_raises`
- `test_order_with_price_override`
- `test_fill_creation`
- `test_order_remaining_reduces_size`
- `test_order_remaining_preserves_other_fields`
- `test_order_remaining_raises_when_filled_exceeds_size`

---

### Task 2: Slippage Models (`execution/slippage.py`)

**Implementation:**
- Define `SlippageModel` Protocol: `calculate(base_price, side, order_size, bar) -> float`
- `FixedTickSlippage(ticks, tick_size=0.01)`: returns `base_price ± ticks * tick_size` (replicates current behavior)
- `SqrtVolumeSlippage(k=0.1, max_slippage_pct=0.05)`: 
  - Returns `base_price * (1 ± k * sqrt(order_size / bar.volume))`
  - Handles zero volume: returns `float('inf')` (unfillable - will be caught by VolumeModel)
  - Caps slippage at `max_slippage_pct` (default 5%) to prevent absurd values on low-volume bars

**Verification:**
- `FixedTickSlippage` produces identical results to current `FillSimulator`
- `SqrtVolumeSlippage` increases with order size, decreases with volume
- Buy slippage increases price, sell slippage decreases price
- Zero volume handled gracefully (returns inf or capped value)

**Unit Tests:**
- `test_fixed_tick_buy_adds_slippage`
- `test_fixed_tick_sell_subtracts_slippage`
- `test_fixed_tick_matches_legacy_fill_simulator`
- `test_sqrt_slippage_increases_with_order_size`
- `test_sqrt_slippage_decreases_with_higher_volume`
- `test_sqrt_slippage_buy_vs_sell_direction`
- `test_sqrt_slippage_zero_volume_returns_inf`
- `test_sqrt_slippage_capped_at_max_pct`

---

### Task 3: Volume Models (`execution/volume.py`)

**Implementation:**
- Define `VolumeModel` Protocol: `max_fill_qty(order_size, bar_volume) -> float`
- `UnlimitedVolume()`: returns `order_size` (current behavior)
- `ParticipationRateVolume(rate=0.10)`: returns `min(order_size, rate * bar_volume)`

**Verification:**
- `UnlimitedVolume` always returns full order size
- `ParticipationRateVolume` caps at `rate * volume`
- Zero volume bar returns 0 fill quantity

**Unit Tests:**
- `test_unlimited_returns_full_size`
- `test_participation_caps_at_rate_times_volume`
- `test_participation_fills_full_when_volume_sufficient`
- `test_participation_zero_volume_returns_zero`
- `test_participation_custom_rate`

---

### Task 4: Impact Models (`execution/impact.py`)

**Implementation:**
- Define `ImpactModel` Protocol: `price_impact(order_size, bar_volume, side, adv=None) -> float`
- `NoImpact()`: returns `0.0` (current behavior)
- `SqrtImpact(k=0.1, max_impact_pct=0.05)`: 
  - Returns `k * sqrt(order_size / adv)` (uses ADV if provided, else bar_volume)
  - Handles zero ADV: returns `float('inf')` (unfillable)
  - Caps impact at `max_impact_pct` (default 5%) to prevent absurd values

**Verification:**
- `NoImpact` always returns 0
- `SqrtImpact` increases with order size, decreases with volume
- Impact is always positive (caller applies direction)
- Zero ADV/volume handled gracefully

**Unit Tests:**
- `test_no_impact_returns_zero`
- `test_sqrt_impact_increases_with_order_size`
- `test_sqrt_impact_decreases_with_higher_adv`
- `test_sqrt_impact_uses_bar_volume_when_no_adv`
- `test_sqrt_impact_zero_adv_returns_inf`
- `test_sqrt_impact_capped_at_max_pct`

---

### Task 5: Execution Config (`execution/config.py`)

**Implementation:**
- `ExecutionConfig` dataclass:
  - `slippage_model: SlippageModel` (default `FixedTickSlippage(5)`)
  - `volume_model: VolumeModel` (default `UnlimitedVolume()`)
  - `impact_model: ImpactModel` (default `NoImpact()`)
  - `latency_bars: int` (default `0`)
  - `adv_window: int` (default `20` — days for ADV calculation)
- Factory: `ExecutionConfig.legacy(slippage_ticks=5)` — creates config matching current behavior
- Factory: `ExecutionConfig.realistic(...)` — creates config with all models enabled

**Verification:**
- `legacy()` config produces behavior identical to current `FillSimulator`
- `realistic()` config enables all three models
- All defaults are sane

**Unit Tests:**
- `test_legacy_config_defaults`
- `test_realistic_config_has_all_models`
- `test_config_custom_overrides`

---

### Task 6: ExecutionSimulator (`execution/simulator.py`)

**Implementation:**
- `ExecutionSimulator(config: ExecutionConfig)`:
  - `execute_market_order(order, bar, adv=None) -> Fill | None`
    1. If `order.price_override` is set: use that price directly, skip slippage/impact (for ORB entries)
    2. `max_qty = volume_model.max_fill_qty(order.size, bar.volume)`
    3. If `max_qty <= 0`, return `None`
    4. `base_price = bar.close` (or `bar.open` for next-bar mode)
    5. `fill_price = slippage_model.calculate(base_price, order.side, max_qty, bar)`
    6. `impact = impact_model.price_impact(max_qty, bar.volume, order.side, adv)`
    7. Apply impact directionally (buy: +impact, sell: -impact)
    8. Return `Fill(slippage=fill_price-base_price, impact=impact, ...)`
  - `execute_order(order, bar, adv=None) -> Fill | None` — dispatches to market/limit handler

**Verification:**
- Market order with legacy config produces identical fills to current `FillSimulator`
- Volume constraint limits fill quantity
- Slippage + impact both applied to final price
- Zero-volume bar returns no fill
- `price_override` skips slippage and impact models

**Unit Tests:**
- `test_market_order_full_fill_legacy_config`
- `test_market_order_partial_fill_volume_constrained`
- `test_market_order_slippage_applied`
- `test_market_order_impact_applied`
- `test_market_order_combined_slippage_and_impact`
- `test_market_order_zero_volume_no_fill`
- `test_market_order_buy_vs_sell_direction`
- `test_market_order_price_override_skips_slippage_and_impact`

**Functional Test — Phase 1 Integration:**
```
Given: ExecutionConfig.legacy(slippage_ticks=5), bar(close=100, volume=1M)
When:  execute_market_order(buy, size=100)
Then:  Fill(price=100.05, qty=100)  ← identical to current FillSimulator
```

---

## Phase 2: Engine Integration (Tasks 7–10)

Delivers: Engine uses `ExecutionSimulator`, latency support via pending order queue, limit order fills. Existing backtest results are deterministically identical when using legacy config.

### Task 7: Pending Order Queue in Engine

**Implementation:**
- Add `pending_orders: list[Order]` to `BacktestEngine`
- Add `bar_index: int` counter to event loop
- On signal: create `Order` with `signal_bar_index = bar_index`
- Each bar: check pending orders where `signal_bar_index + latency_bars <= bar_index`
  - Eligible orders → `execution_simulator.execute_order(order, bar)`
  - Non-eligible orders → remain in queue
  - Orders older than 1 day → expire and discard
- When `latency_bars=0` (default): order is eligible immediately (same bar) — preserves current behavior

**Verification:**
- `latency_bars=0`: fills on same bar as signal (identical to current)
- `latency_bars=1`: fills on next bar
- `latency_bars=2`: fills 2 bars later
- Orders expire at EOD if not filled

**Unit Tests:**
- `test_zero_latency_fills_same_bar`
- `test_latency_1_fills_next_bar`
- `test_latency_2_fills_two_bars_later`
- `test_pending_order_expires_at_eod`
- `test_multiple_pending_orders_processed_in_order`

---

### Task 8: Engine ExecutionConfig Integration

**Implementation:**
- Add `execution_config: ExecutionConfig | None = None` parameter to `BacktestEngine.__init__`
- If `None`, create `ExecutionConfig.legacy(slippage_ticks=self.slippage_ticks)` (backward compatible)
- Replace direct `FillSimulator` usage with `ExecutionSimulator` in the event loop
- **Compute rolling ADV efficiently (CRITICAL):**
  - Pre-compute once before event loop starts (not per-bar)
  - Resample bar data to daily volumes: `daily_volumes = df.resample('1D').agg({'volume': 'sum'})`
  - Apply rolling window: `adv = daily_volumes.rolling(window=20).mean()`
  - Lookup in event loop: `current_adv = adv.loc[current_date]` (O(1))
  - This is O(n) vs O(n × 20) if computed per-bar — 20x faster
- Preserve `price_override` path for ORB entries (skip slippage/impact)

**Verification:**
- Without `execution_config`: produces identical results to current engine
- With `ExecutionConfig.realistic(...)`: fills show volume constraints, dynamic slippage, impact
- ADV computed once before loop, not per-bar (check timing/profiling)

**Unit Tests:**
- `test_engine_default_config_matches_legacy`
- `test_engine_realistic_config_changes_fills`
- `test_engine_adv_computed_once_before_loop`
- `test_engine_adv_uses_trailing_window`

**Functional Test — Backward Compatibility:**
```
Given: Same ruleset, same data, BacktestEngine(slippage_ticks=5) (no execution_config)
When:  engine.run("QQQ", start, end)
Then:  BacktestResult identical to current engine output (same trades, same prices, same P&L)
```

---

### Task 9: Limit Order Support in Simulator

**Implementation:**
- `ExecutionSimulator.execute_limit_order(order, bar, adv=None) -> Fill | None`:
  - Buy limit: fills only if `bar.low <= order.limit_price`
  - Sell limit: fills only if `bar.high >= order.limit_price`
  - Fill price = `order.limit_price` (limit orders get their price or better)
  - Volume constraint still applies
- Dispatch from `execute_order()` based on `order.order_type`

**Verification:**
- Limit buy not filled when bar low > limit price
- Limit buy filled when bar low <= limit price
- Fill price equals limit price (not bar close)
- Volume constraint applies to limit orders

**Unit Tests:**
- `test_limit_buy_not_filled_price_not_reached`
- `test_limit_buy_filled_when_price_crossed`
- `test_limit_sell_not_filled_price_not_reached`
- `test_limit_sell_filled_when_price_crossed`
- `test_limit_order_fills_at_limit_price`
- `test_limit_order_respects_volume_constraint`

---

### Task 10: Portfolio Partial Fill Handling

**Implementation:**
- Modify `PortfolioManager.open_position()` to accept partial fills (position opened with partial qty)
- Add `PortfolioManager.add_to_position(fill)` for accumulating partial fills into existing position
  - Weighted average entry price: `(old_qty * old_price + new_qty * new_price) / total_qty`
  - Quantity accumulates
  - Stop/TP remain from original signal
- Engine tracks unfilled remainder in pending queue

**Verification:**
- Single full fill: identical to current behavior
- Two partial fills: correct weighted average entry, accumulated quantity
- Unfilled remainder discarded at EOD

**Unit Tests:**
- `test_full_fill_identical_to_current`
- `test_partial_fill_opens_position_with_partial_qty`
- `test_add_to_position_weighted_average_price`
- `test_add_to_position_accumulates_quantity`
- `test_unfilled_remainder_does_not_create_position`

---

## Phase 3: Validation & Polish (Tasks 11–14)

Delivers: Determinism guarantee, degradation validation, legacy wrapper preservation, comparison tooling.

### Task 11: Determinism Tests

**Implementation:**
- Run same backtest 3x with same config → assert identical `BacktestResult`
- Run with legacy config → assert matches old `FillSimulator` output
- Run with realistic config → assert deterministic (no random seed issues)

**Verification:**
- All runs produce byte-identical results
- No floating point drift across runs

**Unit Tests:**
- `test_determinism_legacy_config_3_runs`
- `test_determinism_realistic_config_3_runs`
- `test_legacy_config_matches_old_fill_simulator`

---

### Task 12: Degradation Validation

**Implementation:**
- Script/test that runs the same strategy with:
  1. Legacy config (current behavior)
  2. Realistic config (volume + slippage + impact)
- Asserts that realistic config produces **worse** performance (lower P&L, higher slippage costs)
- If realistic config produces *better* results than legacy, the models are likely misconfigured

**Verification:**
- Realistic fills show measurable P&L degradation vs legacy
- Slippage cost per trade is trackable in `Fill.slippage` + `Fill.impact`

**Functional Test:**
```
Given: ORB strategy, QQQ 2024 data
When:  Run with legacy config → P&L_legacy
       Run with realistic config → P&L_realistic
Then:  P&L_realistic < P&L_legacy (fills are worse)
       avg(Fill.slippage) > 0 (slippage is non-zero)
       Some trades have qty < order_size (volume constrained)
```

---

### Task 13: Legacy FillSimulator Wrapper

**Implementation:**
- Preserve `FillSimulator` class and its public API
- Internally delegate to `ExecutionSimulator(ExecutionConfig.legacy(slippage_ticks))`
- All existing `test_fill_simulator.py` tests pass without modification

**Verification:**
- `FillSimulator(slippage_ticks=5).execute(...)` returns identical `FillResult` to current code
- No test in `test_fill_simulator.py` changes

**Unit Tests:**
- All existing tests in `test_fill_simulator.py` pass unchanged (regression)

---

### Task 14: A/B Comparison Report

**Implementation:**
- Add `compare_execution_modes()` function to `analysis/performance.py` (or new file)
- Takes two `BacktestResult` objects (legacy vs realistic)
- Outputs: trade count diff, avg fill price diff, P&L diff, slippage cost breakdown
- Markdown-formatted output for analysis reports

**Verification:**
- Report shows non-zero differences between legacy and realistic modes
- Report is human-readable and matches expected format

**Unit Tests:**
- `test_comparison_report_with_identical_results_shows_zero_diff`
- `test_comparison_report_with_different_results_shows_diffs`

---

## Testing Strategy

### Unit Tests
- **Coverage target**: 95%+ for `execution/` module
- **Location**: `vibe/tests/backtester/execution/`
- **Pattern**: One test file per module (`test_slippage.py`, `test_volume.py`, etc.)

### Integration Tests
- **Backward compatibility**: Legacy config produces identical results to current engine
- **Location**: `vibe/tests/backtester/test_engine_execution.py` (existing file, extend)

### Functional Tests
- **Degradation test**: Realistic fills produce worse P&L than legacy (proves models work)
- **Determinism test**: Same inputs → same outputs across runs
- **Location**: `vibe/tests/backtester/test_execution_integration.py` (new file)

---

## Verification Checklist

### Phase 1 — Core Foundation
- [ ] All Phase 1 unit tests pass (models + simulator)
- [ ] `price_override` field added to Order dataclass with tests
- [ ] Limit order validation (must have limit_price) in Order.__post_init__
- [ ] Order.remaining() raises ValueError when filled_qty > size
- [ ] Zero volume/ADV returns infinity or capped slippage
- [ ] Max slippage/impact caps configured (default 5%)
- [ ] `ExecutionSimulator` with legacy config matches `FillSimulator` output exactly
- [ ] `price_override` skips slippage and impact models

### Phase 2 — Engine Integration
- [ ] All existing `test_fill_simulator.py` tests pass unchanged
- [ ] Engine with no `execution_config` produces identical backtest results to current code
- [ ] Engine with `execution_config=legacy` produces identical results to no config
- [ ] ADV pre-computed once before event loop (not per-bar)
- [ ] Latency queue correctly delays fills by N bars
- [ ] Limit orders fill only when price crosses limit
- [ ] Volume participation caps fill quantity correctly
- [ ] Sqrt slippage increases with order size / decreases with volume
- [ ] Market impact increases fill cost for large orders
- [ ] Partial fills tracked correctly in portfolio (weighted avg price)
- [ ] One-signal-per-symbol policy enforced (no multi-entry accumulation)

### Phase 3 — Validation
- [ ] Realistic config produces measurably worse P&L than legacy (degradation test)
- [ ] 3x determinism test passes for both legacy and realistic configs
- [ ] A/B comparison report generates correctly
- [ ] No existing backtester test is broken
