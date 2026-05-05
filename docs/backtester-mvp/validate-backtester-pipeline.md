# Backtester Validation Framework

The goal is **not to find alpha** — it's to prove the simulator behaves like reality.
A backtester that passes this framework is "trustworthy enough" to do research.

Tests are organized into phases. Each phase assumes the previous one passed.
Planned unit tests are listed at the end of each phase.

---

## Phase 0 — Ground-Truth Comparison (most powerful)

This is stronger than any synthetic sanity check because it validates signal logic,
fill prices, and exit timing simultaneously against a known-correct implementation.

### 0.1 Third-party baseline comparison

Run the same strategy on the same date range against QC/LEAN logs.

**Reference:** `data/QuantConnect/v0_ORB_logs_*.txt` (ORB on QQQ, full history)
**Script:** `scripts/compare_trades.py`

**Expectation:**
- Direction mismatches < 15% (we achieved 9% after fixes)
- Entry price gap consistent and explainable (our ~$5 gap = dividend adjustment, not a code bug)
- Stop exit alignment > 90%

**Red flags:**
- Direction mismatches > 20% → signal detection logic broken
- Entry prices vary randomly (not systematic offset) → fill price calculation broken
- Large fraction of "QC stopped, we held to EOD" → stop logic broken

**Current status:** Passing. 9% direction mismatch remaining; $5.24 avg entry gap explained
by raw vs dividend-adjusted data (Databento ITCH vs QC `DataNormalizationMode.ADJUSTED`).

**Planned unit test:** `vibe/tests/backtester/test_engine_qc_alignment.py`
- Replay a handful of known QC trade dates against synthetic bar data
- Assert signal direction, fill price (within tolerance), and exit reason match

---

## Phase 1 — Sanity Checks (must pass before any research)

### 1.1 No-trade test

Run the engine with a strategy that never generates a signal.

**Expectation:**
- Cash unchanged throughout
- Equity curve is perfectly flat
- Zero trades in trade history

**If it fails:** Hidden P&L accumulation, data loading bug, or equity calculation error.

**Planned unit test:** `vibe/tests/backtester/test_engine_sanity.py::test_no_trade_flat_equity`
```python
# Strategy: always returns signal=0
# Assert: equity_curve[-1][1] == initial_capital
# Assert: len(trade_history) == 0
# Assert: cash == initial_capital throughout
```

---

### 1.2 Buy-and-hold benchmark

Buy at the first bar, hold to the end of the period.

**⚠️ Data caveat:** Our Databento ITCH data is **raw** (no dividend adjustment). QQQ pays
~1.5–2% annually, so over 8 years the buy-and-hold return will appear ~12–15% lower than
published QQQ performance. This is expected, not a bug.

**Expectation (raw data):**
- Total return roughly tracks QQQ price appreciation (ex-dividends)
- Drawdown shape matches known QQQ bear periods (2020 COVID, 2022 rate hikes)
- No phantom losses or gains at EOD

**If it fails:** P&L accounting is wrong. Stop everything.

**Planned unit test:** `vibe/tests/backtester/test_engine_sanity.py::test_buy_hold_pnl`
```python
# Synthetic 3-bar data: open=100, close rises 100→105→110
# Buy at bar 1, hold through bar 3
# Assert: final equity = initial_capital + (110 - 100) * qty
# Assert: no intermediate exits triggered
```

---

### 1.3 Cash balance invariant

At every bar the accounting identity must hold:

```
equity = cash + sum(position_mark_to_market)
```

For shorts, mark-to-market is negative (offsets received proceeds).

**Expectation:** Invariant holds to floating-point precision at every timestep.

**If it fails:** Cash or equity accounting has a sign error. Past bugs: short open was
subtracting cash instead of adding; short MTM was positive instead of negative.

**Planned unit test:** `vibe/tests/backtester/test_engine_sanity.py::test_cash_invariant`
```python
# Run a full mini-backtest (long + short trades, stops, EOD exits)
# After every portfolio.update_equity() call, assert:
#   equity == cash + sum(mtm_positions)
# Check both long and short positions
```

---

### 1.4 Random-direction strategy

Always in a position, direction chosen randomly at each entry. Re-enters immediately
after exit. Use a fixed random seed for reproducibility.

**Tighter spec than "just random":** Enter at bar open price, exit at bar close.
Expected PnL = `-(slippage_per_side * 2 * num_trades)`.

**Expectation:**
- Expectancy ≈ 0 before costs
- After slippage: negative, proportional to number of trades
- No systematic positive bias

**If it fails (profitable):** Execution model is broken — fills are too favorable.

**Planned unit test:** `vibe/tests/backtester/test_engine_sanity.py::test_random_strategy_loses_to_costs`
```python
# 1000 random trades on synthetic flat price data (no drift)
# Assert: total_pnl < 0 (costs dominate)
# Assert: abs(total_pnl) ≈ slippage_per_trade * 2 * num_trades (within 10%)
```

---

## Phase 2 — Known-Strategy Validation

### 2.1 Opening Range Breakout on QQQ

Already our primary strategy. Validation criteria based on published ORB research.

**Expectation:**
- Win rate: 25–45%
- Expectancy: small positive or near zero (0.05–0.25R)
- Sensitive to slippage (see Phase 3.1)
- Not explosively profitable — if annualised Sharpe > 2.5 on raw data, investigate

**Red flags:**
- Equity curve perfectly smooth → unrealistic fills
- Win rate > 55% → look-ahead bias (future data leaking into signal)
- Insensitive to slippage changes → slippage not applied

**Current status:** 2007 trades, 34.4% win rate, 0.16R expectancy, $1.37M total on $100k
over 2018–2026. Within expected range.

---

### 2.2 Look-ahead bias check

Verify that ORB levels for a given bar use only data from *before* that bar.

The ORBCalculator filters by `trading_date` and the engine passes `df.loc[:ts]` as
context. The ORB window (9:30–9:35) must have fully closed before a breakout signal fires.

**Planned unit test:** `vibe/tests/backtester/test_engine_sanity.py::test_no_lookahead`
```python
# Construct synthetic bars: ORB window 9:30–9:35, breakout bar at 9:35
# Assert: signal at 9:35 uses ORB levels from 9:30–9:34 bars only
# Assert: the 9:35 bar's own OHLC does NOT affect the ORB high/low it's being
#          tested against
```

---

## Phase 3 — Execution Realism Tests

### 3.1 Slippage sensitivity

Run ORB with 0, 1, 2, and 5 slippage ticks. Performance must degrade monotonically.

**Expectation:**
- Total P&L decreases (or win rate decreases) at each slippage level
- No sudden cliff — smooth degradation
- At high slippage (5+ ticks) strategy should approach breakeven or go negative

**Red flags:**
- No change between 0 and 5 ticks → slippage not applied
- Massive collapse at 1 tick → edge is entirely slippage-dependent (fragile)

**Note:** With our current model, slippage is applied on top of the stop-market trigger
price (`OR_high + $0.01 + slippage`). Test should confirm this model, not the old
bar.close + slippage model.

**Planned unit test:** `vibe/tests/backtester/test_engine_execution.py::test_slippage_degrades_monotonically`
```python
# Run mini-backtest with slippage_ticks in [0, 1, 2, 5]
# Assert: pnl[0] >= pnl[1] >= pnl[2] >= pnl[5]
# Assert: entry prices differ by exactly (delta_ticks * 0.01) between runs
```

---

### 3.2 Intrabar vs close-based detection

Confirm that switching breakout detection mode changes results meaningfully.

**Expectation:**
- Intrabar detection fires earlier → different direction on reversal bars
- Close-based detection has fewer, later entries
- Results should differ on ~20–30% of trade dates (based on our QC comparison data)

**This test validates Fix #1.** If the two modes produce identical results,
the intrabar logic is not actually being used.

**Planned unit test:** `vibe/tests/backtester/test_engine_execution.py::test_intrabar_vs_close_detection`
```python
# Construct a bar: high crosses OR_high but close is below OR_low
# Intrabar mode → long signal
# Close-based mode → short signal
# Assert they differ
```

---

### 3.3 Stop fill price model

Validate that stops fill at `stop_price`, not `bar.close`.

**Expectation:**
- Long stop: `bar.low <= stop_price` → fill at `stop_price`
- Short stop: `bar.high >= stop_price` → fill at `stop_price`
- Intrabar wick below stop level on a bar that closes above → stop fires

**Planned unit test:** Already covered by `test_portfolio.py::test_check_exits_stop_hit`
and `test_check_exits_intrabar_wick_stops`. This phase just documents the intent.

---

## Phase 4 — Data Integrity

### 4.1 Bar count validation

A normal NYSE session has 390 1-minute bars (9:30–16:00). Resampled to 5-minute:
78 bars per day. Check for days with anomalous counts.

**Expectation:** No days with fewer than 60 bars (accounting for early closes, holidays).
No days with more than 390 bars (duplicate data).

**Planned script:** `scripts/validate_data.py --check bar-counts --symbol QQQ`

---

### 4.2 Gap detection

Flag trading days where consecutive bars are more than one interval apart.

**Expectation:** Gaps only occur at:
- Session boundaries (16:00 → 09:30 next day)
- Known early closes (day-before Thanksgiving, Christmas Eve, etc.)

**Planned script:** `scripts/validate_data.py --check gaps --symbol QQQ`

---

### 4.3 Spot-check against external source

Pick 10 random trading days and compare OHLCV against QC logs or TradingView.

**Expectation:** Prices match within rounding (raw vs adjusted differences expected
but should be systematic, not random).

**This is a manual one-time check**, not an automated test.

---

## Phase 5 — Stability Sanity

### 5.1 Parameter perturbation

Slightly change ORB parameters. Performance should shift gradually, not collapse.

| Parameter | Baseline | Perturbation |
|---|---|---|
| ORB window | 5 min | 3 min, 10 min |
| Take-profit multiplier | 2.0x | 1.5x, 2.5x |
| Entry cutoff | 15:00 | 14:00, 15:30 |

**Expectation:** Each ±1 parameter step changes total P&L by < 50% of baseline.

**Red flag:** Performance completely collapses with tiny changes → the result is noise,
not signal.

**Planned test:** Manual run via CLI, not automated. Document results in a parameter
sensitivity table when running research.

---

## Planned Unit Test Files

```
vibe/tests/backtester/
├── test_portfolio.py           ✅ exists — stop exits, cash accounting, short support
├── test_engine_sanity.py       📋 planned
│   ├── test_no_trade_flat_equity
│   ├── test_buy_hold_pnl
│   ├── test_cash_invariant
│   ├── test_random_strategy_loses_to_costs
│   └── test_no_lookahead
├── test_engine_execution.py    📋 planned
│   ├── test_slippage_degrades_monotonically
│   ├── test_intrabar_vs_close_detection
│   └── test_entry_price_at_breakout_level
└── test_engine_qc_alignment.py 📋 planned
    └── test_known_trade_dates_match_qc
```

---

## Minimal Checklist

Before treating backtest results as meaningful:

- [ ] **Phase 0:** QC comparison — direction mismatch < 15%, stop alignment > 90%
- [ ] **Phase 1.1:** No-trade → flat equity
- [ ] **Phase 1.2:** Buy & hold → tracks QQQ price appreciation (ex-dividends, raw data)
- [ ] **Phase 1.3:** Cash invariant holds at every bar
- [ ] **Phase 2.1:** ORB win rate 25–45%, expectancy 0.05–0.25R
- [ ] **Phase 3.1:** Slippage degrades performance monotonically
- [ ] **Phase 3.2:** Intrabar vs close-based detection produces different results on reversal bars

If all pass → the engine is trustworthy for research.

---

## Appendix — Original Spec (v1)

The original framework was sourced from ChatGPT and served as the starting point.
Preserved here for reference; the sections above supersede it.

**Phase 1 — Sanity checks**
1. Buy & hold benchmark — buy at first bar, hold to end; compare total return and drawdown shape to known QQQ performance
2. No-trade test — strategy never trades; equity must be flat
3. Always-in-market (random direction) — expectancy ≈ 0 before costs, negative after

**Phase 2 — Known-strategy validation**
4. Moving average crossover (20/50 MA) — slightly positive or flat on QQQ; suffers in chop; not explosive
5. Opening Range Breakout — low win rate (30–45%), small positive or near-zero expectancy, sensitive to costs
6. Mean reversion toy (buy when down 3 bars, exit next bar) — small edge, degrades fast with costs

**Phase 3 — Execution realism**
7. Slippage sensitivity — run same strategy at 0/1/2/5 ticks; performance degrades smoothly
8. Spread/friction test — add commission or fixed spread; strategies get worse
9. Intrabar consistency — switching close-based to high/low-based detection should change results meaningfully

**Phase 4 — Data integrity**
10. Bar count validation — 390 1-min bars/day (normal session)
11. Random day cross-check — compare OHLC on 10 random days vs external source
12. Gap detection — no missing bars within sessions

**Phase 5 — Stability sanity**
13. Parameter perturbation — OR window 5→6 min, MA 20→22; performance should shift slightly, not collapse

**Items not carried forward into v2:**
- Tests 4 and 6 (MA crossover, mean reversion) deferred — require implementing new strategies; low marginal value over the Phase 1 sanity checks
- Test 8 (commission/spread) merged into the slippage sensitivity section
- "Always profitable → simulator lying" framing clarified: random strategies must lose to costs, not just be near zero
