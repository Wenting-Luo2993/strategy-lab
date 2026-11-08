# Portfolio Paper Trading Plan

**Status:** Approved 2026-11-08, in implementation
**Goal:** Paper-trade the row-10 resting-stop ORB entry on five symbols with
account-level risk, so that live fills can be compared trade by trade with the
backtest.

## Why

Paper trading measures **execution**, not edge.

- Row 10 of `docs/backtest-research-summaries/orb-exploration-results.md`
  (gap-aware resting stop, +0.062R, CI [+0.010, +0.115]) survives only about
  6 extra ticks of entry slippage.
- Only real stop-order fills can say which side of that threshold we are on.

Paper trading cannot confirm the edge itself. A standard error of about
0.023R over 1,679 days means about 4 years of forward data would be needed.

The bot currently trades QQQ only, about 1 fill per day, so 100 fills would
take about 20 weeks. Five symbols (QQQ, AMZN, GOOGL, MSFT, TSLA, the
exploration set) bring that down to about 4–6 weeks.

Questions paper trading must answer:

1. **Implementation parity:** on the same days, does the live bot take the
   same trades as the backtest (signals, times, sides, stops, R)?
2. **Stop-order slippage:** how many ticks beyond the trigger level do entry
   and protective-stop fills land, compared with the modelled 2 ticks?
3. **Operational reliability:** daily gateway restart, reconnects, warm-up,
   no missed sessions.

## Gaps found in the current bot

| Area | Today | Problem |
| --- | --- | --- |
| Live ruleset | `orb_exp073_paper_burn_in`: on a completed 5m breakout bar, a stop at bar high/low ±10 ticks, 3-bar expiry, body filter, BE trailing at 3R, 50% cap | Not the row-10 strategy, so the paper results would not test it |
| Position cap | `SimpleRiskManager.pre_trade_check` receives only the current symbol's position | `max_positions` never counts other symbols |
| Drawdown | `max_drawdown_pct` is stored but never checked | No daily loss limit |
| Protective stop | Client-side: `_check_exits` compares the latest bar price and closes with a market order | Does not match the backtest's stop fill; no broker-side protection if the bot dies |
| Linked orders | `interactive_brokers.py` builds Market/Limit/Stop orders only | No OCA group, so the two resting entry sides cannot cancel each other |
| Sizing basis | Live sizes from current `account.equity` and IBKR `BuyingPower` (about 4× equity on margin) | IBKR buying power allows leverage the backtest does not |
| Sizing log | `[SIZING]` estimate in `orchestrator.py` uses `config.trading.initial_capital` | Duplicated logic that disagrees with the real sizer |

Already in place and reused:

- Per-symbol state: ORB levels, entry-attempt counts and pending entries are
  keyed by symbol.
- The `active_symbols` loop.
- Slippage capture (`expected_price`, `slippage_bps`, `slippage_valid`).
- The backtester's shared-capital `PortfolioBacktestEngine` /
  `PortfolioSimulationConfig` (`max_symbol_exposure_pct`,
  `max_open_positions`, `symbol_priority`).

## Confirmed decisions (user)

1. **One shared ruleset with per-symbol runtime state.** Not one config per
   symbol.
2. **Session-start equity snapshot.** Take account equity (IBKR
   NetLiquidation) once at warm-up. It is the basis for the whole day, so caps
   do not drift intraday.
3. **Risk per trade:** 1% of the session-start equity.
4. **Per-position cap:** 20% (configurable) of the session-start equity, in
   notional.
5. **Never exceed account equity (no leverage).** Before every entry, check
   that gross open notional + reserved pending-entry notional + the new order
   ≤ *current* NetLiquidation.
6. **Account-level limits:** maximum open positions and a daily loss limit.
7. **Implement full portfolio support now.** It is needed eventually anyway.

## Sizing rule (live and backtest identical)

```
E0        = NetLiquidation snapshot at warm-up (session-start equity)
risk_qty  = floor(0.01 * E0 / |entry - stop|)
cap_qty   = floor(max_symbol_exposure_pct * E0 / entry)              # 20%
headroom  = NLV_now - gross_open_notional - reserved_pending_notional
room_qty  = floor(max_gross_leverage * headroom / entry)             # leverage 1.0
broker_qty= floor(IBKR AvailableFunds / entry)                       # live-only safety net
qty       = min(risk_qty, cap_qty, room_qty, broker_qty)
```

- `gross_open_notional` sums |qty| × entry price over all open positions,
  long and short.
- A resting OCA pair reserves the **larger** of its two sides once, because
  only one side can fill.
- Every clamp records `capped_by`
  (`max_symbol_exposure | equity_headroom | broker_available_funds`). The
  parity report can then explain a size difference instead of flagging it.
- `qty < 1`: skip the entry and log it.
- If the warm-up snapshot fails, block entries for the session and send a
  Discord alert. Never fall back to a guessed value.

## Ruleset changes

Add a `portfolio:` block to the ruleset schema (`vibe/common/ruleset/models.py`).
Mirror `PortfolioSimulationConfig` names where they exist:

```yaml
portfolio:
  sizing_equity_basis: session_start   # session_start | current
  max_symbol_exposure_pct: 0.20
  max_gross_leverage: 1.0
  max_open_positions: 5
  daily_loss_limit_pct: 0.03           # of session-start equity, realized + unrealized
  symbol_priority: [QQQ, AMZN, GOOGL, MSFT, TSLA]
```

Rulesets without the block keep today's behaviour, so existing research
results stay reproducible.

## Stages

Each stage lands as its own commit(s) with tests. Stages run in this order.

### Stage 1: Account-level risk manager (live)

- Replace `SimpleRiskManager` with a `PortfolioRiskManager` (or extend it).
  It sees **all** broker positions plus pending entries.
- Enforce:
  - `max_open_positions` across symbols, counting pending entries;
  - one position per symbol;
  - the equity-headroom rule;
  - the daily loss limit.
- Daily loss limit: when realized + unrealized P&L ≤ −`daily_loss_limit_pct`
  × E0:
  - cancel all pending entries;
  - flatten all positions;
  - block new entries until the next session;
  - send a Discord alert (payload + formatter, following the CLAUDE.md
    pattern).
- Warm-up phase takes the E0 snapshot (`WarmupPhaseManager`). Store it on the
  orchestrator and publish it in the account snapshot.
- Tests:
  - the cross-symbol position cap (a regression test for the current bug);
  - headroom with long + short + pending;
  - daily loss trigger and reset next session;
  - snapshot failure blocks entries.

### Stage 2: Shared sizing, backtest parity

- One sizing function in `vibe/common/risk/` used by both the live
  `TradeExecutor` and the backtester (`engine.py` / `portfolio_engine.py`). It
  implements the rule above and returns `capped_by`.
- Backtester:
  - add the `session_start` equity basis (equity at the first bar of each
    session);
  - honour `max_gross_leverage` and `daily_loss_limit_pct` from the
    `portfolio:` block.
  - Default behaviour without the block is unchanged; a test asserts an
    existing ruleset's results are byte-identical.
- Remove the orchestrator's duplicated `[SIZING]` estimate. Log the real
  sizer result instead.
- Tests:
  - a live/backtest sizing table test, with the same inputs giving the same
    qty and `capped_by`;
  - the backtester unchanged without the block.

### Stage 3: Resting-stop entry with broker-side protection (row 10 live)

New ruleset `vibe/rulesets/orb_resting_stop_paper.yaml`:

- `orb_production.yaml` semantics: 5m OR from 09:30, symmetric, stop at the
  opposite OR edge, no take-profit, EOD 15:55, entry cutoff 15:00, one
  attempt per day.
- The body filter does not apply (that is part of the resting-stop
  definition; see `resting_stop_conflicts`).
- The `portfolio:` block above. Starts with `symbols: [QQQ]`.

New `entry_order.type: resting_stop`, matching
`vibe/backtester/core/resting_stop.py`:

- At OR end (09:35 ET), place a **buy stop at OR high** and a **sell stop at
  OR low** in one IBKR OCA group (`ocaGroup`, `ocaType=1`).
  - Each is sized separately by the stage 2 rule; the pair reserves the max
    once.
- Each entry carries an **attached protective stop** (bracket child:
  `parentId`, `transmit` on the last leg) at the opposite OR edge, so
  protection is at the broker from the moment of fill.
- If price is already beyond a level when the order is placed, IBKR triggers
  it immediately. This is the backtest's gap fill (`max(level, open)`); flag
  it `gap_fill=true`.
- Cancel unfilled entries at `entry_cutoff_time`.
- At EOD:
  - cancel protective stops;
  - flatten with a market order (as today);
  - cover the race where the stop fills at the same moment.
- `_check_exits` must not double-exit a position whose broker stop has
  already filled. Broker fills are the source of truth.
- Restart recovery: after a mid-session bot restart, rebuild OCA pairs,
  brackets and positions from IBKR open orders/positions
  (`ib_exchange.py` restore path). Never place duplicate orders.
- Slippage record per fill:
  - benchmark = trigger level (entry) or stop level (protective exit);
  - store signed slippage in ticks plus `gap_fill`, `order_role`
    (`entry|protective_stop|eod`).
- Tests (mock exchange and IB broker unit tests):
  - OCA linkage; bracket construction;
  - gap fill; cutoff cancel; EOD race;
  - restart recovery; no client-side double exit.

### Stage 4: Daily parity and slippage report

- At cooldown, for each symbol traded today:
  - fetch the session's 1-minute bars from IBKR;
  - run the backtest resting-stop logic (`SessionMinutes.first_trigger` and
    the engine path) with the same ruleset and E0;
  - diff against live trades: taken/not taken, side, entry minute, entry
    price, qty, exit reason, R.
- Summarise per day:
  - matched trades; mismatches with reason;
  - mean/median entry slippage in ticks vs the modelled 2;
  - gap fills.
- Send it as a Discord embed and persist it in the dashboard store (synced
  to Supabase).
- Also provide `scripts/live_backtest_parity.py --start --end` to recompute
  the report offline over a date range from the published live trades.
- Tests: parity on a synthetic day (match), and one injected mismatch of
  each kind.

### Stage 5: Five symbols

- Ruleset `symbols: [QQQ, AMZN, GOOGL, MSFT, TSLA]`.
- Verify:
  - IBKR market data and real-time bars for all five;
  - the per-cycle data loop completes well within one bar with five symbols
    (log timings);
  - the VM memory footprint (956 MiB host): measure RSS with 5 symbols in a
    mock/paper dry run;
  - warm-up, ORB Discord notification and dashboard show all five.
- Tests: multi-symbol orchestrator test with simultaneous triggers that
  exhaust headroom (later symbols clamped in `symbol_priority` order).

## Rollout

1. Deploy with `bash deploy/ibkr-docker/deploy.sh bot` outside market hours,
   with `ACTIVE_RULESET=orb_resting_stop_paper` (QQQ only).
2. Shakedown, 1 week. Daily parity report clean; no missed sessions; restart
   recovery observed at least once (daily 04:00 UTC gateway restart).
3. Switch the ruleset to five symbols (a compose/ruleset change on main, then
   `deploy.sh bot`).
4. Measurement, 4–6 weeks or ≥100 entry fills, whichever is later.
5. Decision: compare the measured entry slippage with the ~6-tick threshold
   from row 10. IBKR paper fills are simulated, so treat paper slippage as a
   **lower bound**. A small live position is the real test.

## Estimate

| Stage | Estimate |
| --- | --- |
| 1 Risk manager | 1.5 d |
| 2 Shared sizing + backtest parity | 1.5 d |
| 3 Resting stop + broker-side protection + recovery | 2.5–3 d |
| 4 Parity and slippage report | 1.5 d |
| 5 Five symbols | 1 d |
| **Total** | **8–8.5 d**, plus 1 week of shakedown |

## Non-goals

- No change to research conclusions or published experiments.
- No live (real-money) trading.
- No new strategies.
