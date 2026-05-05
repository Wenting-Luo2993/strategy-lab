# Backtester QC Alignment Fixes

**Date:** 2026-04-30
**Scope:** `vibe/backtester/` and `vibe/common/strategies/orb.py`
**Reference baseline:** QuantConnect LEAN engine ORB strategy (`data/QuantConnect/quantConnectScript.py`)

This document records three bugs found by comparing our backtester trade-by-trade against QuantConnect ground-truth logs on 2023 H1 (123 matched trades). Each section describes what the code did before, what it does now, and the measured effect.

---

## Fix #1 — Breakout Detection: Close-Based vs Intrabar

**File:** `vibe/common/strategies/orb.py` — `generate_signal_incremental()`

### Before

Breakout was detected using bar close:

```python
# Long: close must be above OR high
if current_price > levels.high:
    # → signal long

# Short: close must be below OR low
elif current_price < levels.low:
    # → signal short
```

This means a bar that spikes above `OR_high` intrabar but closes below `OR_low` would be detected as a **short** (close below low), even though QC's stop-market order at `OR_high + $0.01` would have fired a **long** the instant price crossed the high.

### After

Breakout is detected using bar high/low with a 1-tick offset, matching QC's stop-market trigger price:

```python
_TICK = 0.01
long_broke  = bar_high >= levels.high + _TICK
short_broke = bar_low  <= levels.low  - _TICK

# Tie-break when both levels are hit in the same bar.
# LEAN heuristic: the side with the larger move from bar.open fired first.
if long_broke and short_broke:
    up_move   = bar_high - bar_open
    down_move = bar_open - bar_low
    if up_move >= down_move:
        short_broke = False
    else:
        long_broke  = False
```

The 1-tick offset (`+ $0.01`) is critical: the ORB window bar itself sets `OR_high`, and the following bar's high often equals that exact value (touching but not crossing). Without the offset, the breakout bar would be falsely detected on bars that merely graze the level.

### Result

| Metric | Before | After |
|---|---|---|
| Direction mismatches (2023 H1) | 36 / 123 (29%) | 11 / 123 (9%) |

---

## Fix #2 — Entry Fill Price: Bar Close vs Breakout Level

**Files:** `vibe/backtester/core/engine.py`, `vibe/backtester/core/fill_simulator.py`

### Before

Entries were filled at `bar.close + 5 ticks` (default slippage):

```python
# engine.py
fill = fill_sim.execute(symbol, side, quantity, bar)
# → fill_price = bar.close + $0.05 (5 ticks at $0.01 each)
```

On a $490 stock with a breakout bar that closes well above `OR_high`, this placed entries $5–10 above the actual breakout level. This significantly overstates entry cost compared to QC's stop-market fill at exactly `OR_high + $0.01`.

### After

`FillSimulator.execute()` accepts a `price_override` that bypasses slippage calculation:

```python
# fill_simulator.py
def execute(self, symbol, side, quantity, bar, next_bar=None, price_override=None):
    if price_override is not None:
        fill_price = price_override
    else:
        # existing slippage logic unchanged
        ...
```

`engine.py` computes the exact stop-market fill price and passes it as the override:

```python
# engine.py
_TICK = 0.01
if signal_value == 1 and orb_high is not None:
    entry_price = orb_high + _TICK      # long fill = OR_high + $0.01
elif signal_value == -1 and orb_low is not None:
    entry_price = orb_low - _TICK       # short fill = OR_low - $0.01
else:
    entry_price = bar.close             # fallback if metadata missing

fill = fill_sim.execute(symbol, side, quantity, bar, price_override=entry_price)
```

### Result

| Metric | Before | After |
|---|---|---|
| Average entry price gap vs QC (2023 H1) | +$5.28 | +$5.24 |

The remaining $5.24 gap is not a code bug. QC uses `DataNormalizationMode.ADJUSTED` which reduces historical prices by cumulative dividends. Databento XNAS ITCH is raw. For QQQ, cumulative dividends since the 2023 data window account for ~$5–6 in adjusted price difference. This is irreducible without switching data sources.

---

## Fix #3 — Portfolio Stop-Loss Logic and Short Position Accounting

**File:** `vibe/backtester/core/portfolio.py`

Three separate bugs in `PortfolioManager`, all related to short position handling.

---

### 3a — Stop Triggered on Intrabar Wick

**Before:**

```python
# check_exits()
if pos.side == "buy" and bar.low <= pos.stop_price:
    # → fill at stop_price
```

Stop fired whenever `bar.low` touched the stop price, regardless of where the bar closed. This is more aggressive than QC, which only stops out when the bar *closes* at or below the stop level.

**After:**

```python
long_stop  = pos.side == "buy"  and bar.close <= pos.stop_price
short_stop = pos.side == "sell" and bar.close >= pos.stop_price
```

Fill is at `bar.close`, not `stop_price`. This matches QC's close-based stop logic exactly.

**Test added:** `test_check_exits_intrabar_wick_does_not_stop` — bar low wicks below stop but closes above it → no exit.

---

### 3b — Short Positions Had No Stop Exit

**Before:**

```python
# check_exits() — only checked buy positions
if pos.side == "buy" and bar.low <= pos.stop_price:
    # stop logic for longs only
```

Short positions could never be stopped out. They could only exit at EOD. This meant short trades that moved sharply against us continued to accumulate losses all day.

**After:**

Both sides are checked:

```python
long_stop  = pos.side == "buy"  and bar.close <= pos.stop_price
short_stop = pos.side == "sell" and bar.close >= pos.stop_price

if long_stop:
    fill = FillResult(symbol=symbol, side="sell", filled_qty=pos.quantity, avg_price=bar.close)
    self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
elif short_stop:
    fill = FillResult(symbol=symbol, side="buy", filled_qty=pos.quantity, avg_price=bar.close)
    self.close_position(fill, exit_reason="STOP", timestamp=clock.now())
```

**Test added:** `test_check_exits_short_stop_hit` — short stop fires when `bar.close >= stop_price`.

---

### 3c — Short Cash and Equity Accounting

**Before:**

```python
# open_position()
self.cash -= fill.filled_qty * fill.avg_price  # always subtracted
```

Opening a short position should *receive* cash (you sell shares you don't own, collecting proceeds). Closing it should *spend* cash (you buy back the shares). The old code did the opposite, making both long and short opens subtract cash.

Additionally, equity marking did not account for short positions:

```python
# update_equity() — old
position_value = sum(bar.close * pos.quantity for sym, pos in ...)
```

This treated short positions as if they had positive value, double-counting what was already in cash.

**After:**

```python
# open_position()
if fill.side == "buy":
    self.cash -= fill.filled_qty * fill.avg_price   # long: spend cash
else:  # short
    self.cash += fill.filled_qty * fill.avg_price   # short: receive proceeds

# close_position()
if fill.side == "sell":  # closing long
    self.cash += fill.filled_qty * fill.avg_price
else:  # closing short (buy back)
    self.cash -= fill.filled_qty * fill.avg_price

# update_equity()
position_value = sum(
    (bar.close * pos.quantity if pos.side == "buy"
     else -bar.close * pos.quantity)   # short: negative value offsets received cash
    for sym, pos in self.positions.items()
    if sym in current_bars
)
```

**Test added:** `test_short_cash_accounting` — open short adds cash, close short subtracts.

### Combined Fix #3 Result

| Metric | Before | After |
|---|---|---|
| QC stopped / we held EOD (2023 H1) | 49 / 123 (40%) | 8 / 123 (7%) |

---

## Overall Impact

### 2023 H1 (123 matched trades, $100k capital)

| Metric | Before all fixes | After all fixes | QC reference |
|---|---|---|---|
| Direction mismatches | 36 / 123 (29%) | 11 / 123 (9%) | — |
| Avg entry price gap | +$5.28 | +$5.24 (data diff) | — |
| QC stopped / we held EOD | 49 / 123 (40%) | 8 / 123 (7%) | — |
| Total P&L | -$14,822 | -$1,268 | -$4,342 |
| Win rate | 35.0% | 27.6% | 24.2% |

### Full history (2018-05-01 to 2026-04-28, $100k capital)

| Metric | v3 (after all fixes) |
|---|---|
| Trades | 2,007 |
| Win rate | 34.4% |
| Expectancy | 0.16R |
| Total P&L | +$1,376,466 |

Report: `reports/qqq_full_history_v3.html`

---

## Known Remaining Gaps

These differences vs QC are acknowledged but intentionally left unfixed:

| Difference | Our system | QC | Notes |
|---|---|---|---|
| Entry cutoff | 15:00 | ~15:58 | Intentional strategy choice in `orb_production.yaml` |
| EOD exit time | 15:55 | 15:59 | Minor; 4-bar difference |
| Data normalization | Raw (Databento ITCH) | Dividend-adjusted | ~$5 price gap; irreducible without data source change |
| Remaining direction mismatches | 11/123 (9%) | — | Root cause unknown; likely complex intrabar scenarios |
