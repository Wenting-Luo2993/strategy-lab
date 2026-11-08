# ORB edge exploration: results

A living, high-level summary of what has been explored for the ORB strategy
and what was found. Add a row for each new experiment, and revise the overall
conclusion and "What is left" when the picture changes. Detailed tables and
decision rules stay in the research plan.

**Last updated:** 2026-11-07  
**Programme:** `orb_edge_2026` (100 trials counted, including 75 October legacy runs on the same data; see "Trial count" below)  
**Data:** exploration set only: QQQ, AMZN, GOOGL, MSFT and TSLA, 2018-05-01
to 2024-12-31. Confirmation stocks and the holdout are untouched.  
**Baseline rule:** `vibe/rulesets/orb_production.yaml`: 5-minute opening
range, both directions, stop at the opposite side of the range, no
take-profit, exit at 15:55. Entry is a market order at the next 5-minute
bar's open after a completed breakout bar (`AT_NEXT_BAR_OPEN`, +2 ticks).

Rows 3 to 11 are on the Supabase dashboard, each with a one-line conclusion.
The local DB that recorded rows 3 to 8 no longer exists; rows 9 to 11 and
later runs are recorded in `%LOCALAPPDATA%\strategy-lab\orb_edge_2026.sqlite3`.
Rows 0 to 2 (Step 0, the Step 2
baseline and the first condition pass) were run before publishing was wired
up and are **not yet in Supabase**; republishing them is an open action. Full
tables, cutoffs and decision rules are in
`2026-10-31-orb-edge-research-plan.md`, Steps 0 to 4.

## Overall conclusion

**For this rule (breakout of either side, market entry at the next bar's
open, five correlated large caps, 2018–2024), no exploratory experiment has
found an edge.** That is narrower than "ORB has no edge":

- The baseline averages −0.001R, CI [−0.046, +0.044]. One-sided, an average
  edge of +0.05R or more is excluded at 97.5%; +0.03 to +0.04R is **not**
  excluded. Power to detect a true +0.05R was only about 45–60%.
- The mean is set by a handful of trend days: median trade −0.81R, skew 3.7,
  and dropping the best 20 of 1,679 days takes the mean to −0.05R.
- The result depends on the entry order type. The same signals repriced on
  1-minute bars (panel review, 2026-11-03):

  | Entry fill | Mean R |
  | --- | --- |
  | Resting stop at the trigger, but with the completed-bar body filter (look-ahead) | +0.23 |
  | Gap-aware resting stop, no body filter, +2 ticks (lookahead-free) | +0.06, CI [+0.01, +0.11] |
  | Next 5-min open +2 ticks (published baseline) | −0.001 |
  | Next 5-min open +10 ticks | −0.08 |

  The old +0.44R came from the look-ahead row. A lookahead-free stop entry is
  the decisive next test.
- No market condition separates good sessions from bad ones, and the two
  "weak leads" did not replicate out of sample (see row 3).
- No exit change beats holding to 15:55; break-even clearly hurts.
- R and dollars rank variants differently, because 77% of orders are capped by
  buying power. All comparisons now need $/session alongside R.

**Update 2026-11-07 (rows 9 to 11):** the two entry tests are recorded. The
lookahead-free resting-stop entry is the only variant whose interval excludes
0: +0.062R [+0.010, +0.115], +$46 per trade. It is still tail-dependent
(+0.011R without the best 20 days) and rests on the stop-order fill model.
The Zarattini & Aziz first-candle ORB reproduces the paper under its own
near-zero costs but not under ours (+0.028R, CI includes 0). The open
question is now the real cost of stop-order fills, which only live (paper)
trading can measure. See "What is left".

## Experiments

R is profit divided by initial risk. Condition studies compare terciles
(low / mid / high) of a variable known at entry time. Exit and opening-range
studies are paired comparisons: variant minus baseline on the same 8,395
symbol-sessions (1,679 clustered sessions). Intervals are approximate 95%
confidence intervals. Rows 0 to 8 used execution model v6; rows 9 to 11 use v7
(minimum-risk floor of 5 bps, take-profit must trade through by 1 tick).

| # | Question | Experiment ID(s) | Trials | Conclusion |
| --- | --- | --- | --- | --- |
| 0 | Can the pipeline's statistics be trusted? (Step 0, tool checks) | V6 null: `01a1995d-81b6-7264-a192-55f462401160`<br>V7 planted edge: `01a19a1b-874c-7c9a-88db-09848d437ced` | 0 | **Yes.** On data with no edge, 0 of 200 runs gave a false positive and interval coverage was 95.5%. A planted +0.256R edge was recovered with 95.0% coverage. Detection was 28.5% against a predicted 40.6%, so treat power estimates as about 10 points optimistic. |
| 1 | Does the baseline ORB have an edge on realistic fills? (Step 2) | `01a19a24-ada7-744a-855c-bdf8dfd414bc` | 1 | **No.** 8,375 trades, mean net −0.001R, CI [−0.046, +0.044]. Every symbol's interval includes 0. Exits are bimodal: 51% at end of day (mean +1.01R) and 49% stopped (mean −1.04R), and they cancel. Costs are small (0.009R per trade). The earlier +0.44R to +0.78R (P5b) came from an entry fill that live trading cannot get and is superseded. |
| 2 | Do market conditions separate good sessions from bad ones? (Step 3, first pass) | `01a19a31-b0d9-7da3-a600-023183d2bd11` | 6 | **No significant condition.** Variables: `abs_gap_pct`, `or_width_atr`, `rel_open_volume`, `gap_pct`, `atr_pctile` and `prior_trend_20d_pct`. Every high − low interval includes 0, and no tercile reaches 0.05R. Two weak leads: gap size (+0.059R high − low) and opening-range width relative to ATR (+0.045R). Wider ranges doing better contradicts the "tight ranges break cleanly" mechanism. With five large symbols, high relative volume is just normal volume, not "stocks in play". |
| 3 | Does the overnight gap matter once direction and size are measured properly? (Step 3, second pass) | `01a19e08-dfd9-737e-8379-5185a2f7ab0c` | 2 | **No.** Gap direction (`gap_aligned_atr`) has no effect: breakouts with the gap (+0.023R) and against it (+0.020R) do the same, for longs and shorts alike. ATR-scaled gap size (`abs_gap_atr`) is +0.066R high − low, CI [−0.032, +0.163]. It holds in 6/7 years and 4/5 symbols, but those counts barely distinguish signal from noise (P ≈ 0.06 and 0.19 under the null). **Out of sample it does not replicate:** with terciles cut on 2018-05 to 2021-08 and applied to 2021-09 to 2024, high − low falls from +0.132 to +0.006R (`or_width_atr` +0.112 → −0.040, `abs_gap_pct` +0.107 → +0.001). Classic regression to the mean. Not a lead. |
| 4 | Do breakouts in the direction of the 20-day trend do better? (Step 3, third pass) | `01a1a253-6a62-7475-8ccf-8ff1d65c8202` | 1 | **No.** `trend_aligned_pct` terciles (against / mid / with): −0.029 / +0.050 / −0.017. With − against is +0.012R, CI [−0.090, +0.114]. Not monotonic, and out of sample the train-period gap (+0.073R) reverses (−0.083R). Longs and shorts show the same flat shape. This also deprioritises wiring the higher-timeframe trend filter (`mtf_validation`). Step 3 closes with no registered condition. |
| 5 | Does moving the stop to break-even after +1R help? (Step 4) | `01a1a263-8056-7def-8d7b-05e25fe56c81` | 1 | **No, it clearly hurts.** −0.041R per session, CI [−0.062, −0.020], negative in 7/7 years and 5/5 symbols. Breakouts often retest before running, and a break-even stop turns those retests into scratches. This is the only exit comparison whose interval excludes 0. The engine moves the trailing stop on the bar high before testing the bar low, which is slightly *pessimistic* here (1-minute repricing: −0.002R); it does not change the conclusion. In dollars it is also worse: −$15 per symbol-session, CI [−26, −5]. |
| 6 | Does closing earlier than 15:55 help? (Step 4) | Flat at 12:00: `01a1a274-c8eb-714f-bd89-f374269a8851`<br>Flat at 14:00: `01a1a284-c7db-70bb-8a93-037055ecbd27` | 2 | **Not supported (12:00 CI includes 0).** Flat at 12:00: −0.021R, CI [−0.047, +0.005], negative in 5/7 years and 5/5 symbols. Flat at 14:00: −0.007R, CI [−0.026, +0.012], mixed (3 up, 3 down, 2020 flat at 0.000). The earlier the exit, the worse the result: the afternoon is where the end-of-day winners make their money. **Confound:** both variants also move the entry cutoff to the exit time (8,320 trades at 12:00), so they test "fewer late entries + earlier exit" together. A clean test pairs each against a baseline with the same entry cutoff. |
| 7 | Does a take-profit help? (Step 4, after fixing the take-profit) | 1× OR range: `01a1a27c-dd1a-7a29-8d5c-b21e0c709dbc`<br>2× OR range: `01a1a28c-66e4-7139-92ee-17a9bb7f34c0`<br>3× OR range: `01a1a29c-0591-7b90-8b52-876f1f672660`<br>0.25× daily ATR: `01a1a27c-f364-74f2-ba85-9ee78bb7c280`<br>0.5× daily ATR: `01a1a28c-7bf4-783a-98ae-018502e9a90d`<br>1× daily ATR: `01a1a29b-dd42-719a-915b-177859107b30` | 6 | **No.** OR grid: −0.041, −0.030 and −0.009R (1× and 2× intervals exclude 0; 3× includes it). ATR grid: −0.040, −0.021 and −0.001R (0.25× excludes 0; 0.5× and 1× include it). In dollars 1× OR is −$25 per symbol-session, CI [−50, −1]. The two grids agree, and the cost shrinks smoothly as the target widens: capping winners loses more than it saves. Targets are anchored at the breakout level, not the fill, and entries fill past it, so a hit realises about half the nominal size (median 0.46R at 1× OR, 1.09R at 2× OR, 1.94R at 1× ATR). Targets filled on a touch with no slippage (8.7% of 1× OR fills only touched), so these results are if anything slightly flattering. The widest targets are hit too rarely (8–17% of trades) to matter. The take-profit was ignored by the backtester before commit `cc30292`. |
| 8 | Does a longer opening range help, i.e. waiting 15, 30 or 60 minutes and trading the breakout of the early move? (Step 4) | 15 min: `01a1a314-ed14-7d59-9f7b-c919c72f7603`<br>30 min: `01a1a31d-197d-7faf-aebd-d3f06d6e44e2`<br>60 min: `01a1a32e-1821-7c5b-a347-339fdd8c4c97` | 3 | **Not supported (every CI includes 0).** 15 min: +0.011R, CI [−0.020, +0.042]. 30 min: +0.009R, CI [−0.026, +0.043]. 60 min: +0.007R, CI [−0.032, +0.046]. 60 min is positive in 4/7 years and 4/5 symbols (only TSLA negative). A flat plateau at zero in R. **In dollars all three are worse than the baseline** (−$2 to −$5 per symbol-session, CIs ±$20–26), because a wider stop changes position size; the small R "gain" is not a dollar gain. The year pattern is mostly shared (up in 2019, 2020 and 2024, down in 2018, 2021 and 2022), except 2023, where 15 and 30 min are negative and 60 min is positive. A longer range widens the stop, so more trades are held to the close (85% at 60 minutes) and fewer trades fire (7,105 vs 8,375). Note this tests a breakout of either side, not "trade the direction of the first candle"; that literature variant (Zarattini & Aziz) is still open. |
| 9 | Does the baseline still show no edge under execution model v7? (re-baseline) | `01a1b8a6-43d6-7c52-a64f-4b52468d9112` | 1 | **No edge.** 8,375 trades, +0.003R, CI [−0.042, +0.048]; edges ≥ +0.049R excluded one-sided (≥ +0.059R after the Step 0 deflation). +$9 per trade, CI [−21, +39]. 65% of orders capped by buying power. Without the best 10 days −0.027R. Supersedes row 1 as the reference baseline. |
| 10 | Does a lookahead-free resting stop order at the range edge (gap-aware, 1-minute bars, +2 ticks, no completed-bar body filter) do better than a market order at the next bar's open? | `01a1b8a6-eefd-7c52-a22e-651d5a3c3fff` | 1 | **Small positive, not yet a usable edge.** 8,383 trades, +0.062R, CI [+0.010, +0.115], +$46 per trade, CI [+2, +91]. The only result whose interval excludes 0, but below the 0.05R bar with confidence (outcome "underpowered"). Tail-dependent: without the best 10 / 20 days +0.034 / +0.011R. It depends on stop orders filling as modelled; each extra tick of entry slippage costs about 0.01R, so roughly 6 extra ticks erase it. Needs live fill evidence before it counts. |
| 11 | Does the Zarattini & Aziz (2023) first-candle-direction ORB reproduce? (go with the first 5-minute candle at the next open, stop at its opposite extreme, target 10R, exit at the close) | `01a1b8ab-3665-7d69-a3be-669c16e08420` | 1 | **Reproduces the paper's costs, not ours.** Realistic costs, 5 symbols: +0.028R, CI [−0.031, +0.087], not supported. Near-frictionless QQQ 2018-05 to 2023-02 (unrecorded, scratch): +0.19R per trade, 24% wins, 75% stop exits; compounding $25k at 1% risk with up to 4× leverage gives 35%/yr, Sharpe 1.12, max drawdown 22% (paper: 33%/yr, 1.13, 22%). Under realistic costs QQQ falls to +0.087R and 16%/yr, Sharpe 0.63, about the same as buy-and-hold (13%/yr, Sharpe 0.61), and the best 10 days carry 97% of the R. Unlevered (1×): 4%/yr. |

## What the results say together

- **No average edge for this entry.** The baseline sits at 0R, and no
  condition tested finds a subset that reaches +0.05R and survives out of
  sample. Small edges (+0.03R) cannot be ruled out with this sample.
- **Locking in open profit costs more than it saves.** 40% of trades reach
  +1R of open profit, yet every way of locking it in (break-even, earlier
  exits, take-profits) does worse. The edge, if any, lives in a handful of
  trend days, and the returned profit is the price of letting them run.
- **Exit results are consistent.** Exit levers lose more the tighter they
  are, across two independent target definitions. That consistency is about
  the *exit* mechanism; it says nothing about the power of the condition
  studies, which had only about 50% power for a 0.10R tercile gap.
- **The entry is the open question.** The result swings from −0.08R to +0.06R
  across realistic entry fills. Timing levers (range duration, 20-day trend)
  do not help; the order type might. Row 10 confirms the resting stop at
  +0.06R, and row 11 shows the published first-candle edge is mostly a cost
  assumption. What decides it now is the real slippage of stop orders.

## Trial count

100 trials are counted (`python scripts/show_trial_ledger.py --programme
orb_edge_2026 --research-db %LOCALAPPDATA%\strategy-lab\orb_edge_2026.sqlite3`):

- 3 recorded in the new local DB (rows 9 to 11);
- 15 published in Supabase whose local DB was lost (rows 3 to 8);
- 7 from before publishing was wired up (rows 1 and 2);
- 75 October 2026 same-data legacy configurations (nested walk-forward,
  sensitivity sweep, EXP-073), counted by distinct configuration.

External entries are in `config/research/trial_ledger_orb_edge_2026.yaml`.
Any future lead must be judged against the full count.

## What is left

| Option | Status |
| --- | --- |
| **Gap-aware resting-stop entry on 1-minute bars** (`--entry-fill-policy resting_stop`, commit `e00bfd8`) | Done, recorded (row 10): +0.062R [+0.010, +0.115], tail-dependent |
| **First-candle-direction ORB** (Zarattini & Aziz 2023; `vibe/rulesets/orb_first_candle_za.yaml`, commit `e6f3d2d`) | Done, recorded (row 11): reproduces the paper under its costs, not supported under ours |
| **Measure real stop-order fills** (paper-trade the resting-stop entry, log slippage per fill) | **Next.** Row 10 survives up to about 6 extra ticks of slippage; only live fills can say which side of that it lands |
| Dollar, power and robustness reporting | Done (commit `4ba9762`); printed by every study and shown in the local viewer, not yet on the Supabase dashboard |
| Execution identity, dataset pin, member-run summaries, trial ledger (commit `4c942a5`) | Done in code. Ledger: 100 trials. Republishing rows 0–2 and backfilling run summaries for rows 3–8 is blocked: their local DB was lost |
| Execution model v7 (minimum-risk floor, take-profit must trade through; commit `7bd69a6`) | Done. Baseline rerun under v7 recorded (row 9) |
| Time-exit rerun paired against a same-cutoff baseline | Open |
| Opening-range duration sweep (15, 30 and 60 minutes vs the 5-minute baseline) | Done (row 8): flat at zero in R, worse in $ |
| Take-profit set from the entry price (entry + k × initial risk), to test true 1R/2R/3R | Optional, low priority |
| Prior-session intraday trend (a condition study, 3 trials) | Documented, not run. See the note below |
| Higher-timeframe trend filter (`mtf_validation`) | Deprioritised by row 4 |
| Stocks in play (relative volume across many stocks) | Needs a broader universe; not with the five exploration symbols |

### Note: prior-session intraday trend (option, not run)

The idea is the short-horizon version of row 4: does the trend of the last
0.5, 1 or 4 hours, rather than 20 days, line up with good breakouts?

- **What it would measure.** The data is regular hours only (09:30 to 16:00),
  and trades enter early (median 10 minutes after the open, 91% within 30
  minutes). So at entry, a 0.5h, 1h or 4h lookback is almost entirely the end
  of the previous session. The window should end at the previous close:
  - excluding the overnight gap, which row 3 already tested;
  - excluding today's opening minutes, which overlap with the breakout move
    and would make almost every trade look aligned.
- **How to run it.** As a condition study, not a filter. Terciles of
  trade side × prior-session return over the last 30, 60 and 240 minutes show
  the whole against/mid/with shape on the existing trades with no reruns. A
  filter is just "drop a tercile", so its effect can be read off the study. An
  EMA-based filter would add a free parameter and cut the trade count.
- **Why it was not run.** The mechanism is weak: nothing clearly links
  yesterday's afternoon to today's breakout. Its close relatives, the 20-day
  trend (row 4) and the overnight gap (row 3), both showed nothing. The
  published intraday-momentum effect (Gao, Han, Li and Zhou, 2018) is within
  one day: the first half-hour predicts the last half-hour. Each extra
  low-prior trial also raises the bar for anything confirmed later.
- **Higher timeframes collapse to multi-day trends.** On regular-hours bars,
  a 20-period EMA on 4-hour bars spans about 10 sessions, close to the 20-day
  trend already tested. On 1-hour bars it spans about 3 sessions, and on
  30-minute bars about 1.5.
- **"Wait 15, 30 or 60 minutes, then trade with the early move"** is close
  to, but not the same as, the opening-range duration sweep: row 8 trades a
  breakout of either side of the longer range, not the first candle's
  direction. The first-candle-direction rule is listed above as open.
