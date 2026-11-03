# ORB edge exploration: results

A living, high-level summary of what has been explored for the ORB strategy
and what was found. Add a row for each new experiment, and revise the overall
conclusion and "What is left" when the picture changes. Detailed tables and
decision rules stay in the research plan.

**Last updated:** 2026-11-03  
**Programme:** `orb_edge_2026` (22 trials)  
**Data:** exploration set only: QQQ, AMZN, GOOGL, MSFT and TSLA, 2018-05-01
to 2024-12-31. Confirmation stocks and the holdout are untouched.  
**Baseline rule:** `vibe/rulesets/orb_production.yaml`: 5-minute opening
range, both directions, stop at the opposite side of the range, no
take-profit, exit at 15:55.

Every experiment is in `tmp\research-viewer.sqlite3` and on the Supabase
dashboard, each with a one-line conclusion. Full tables, cutoffs and decision
rules are in `2026-10-31-orb-edge-research-plan.md`, Steps 0 to 4.

## Overall conclusion

**No exploratory experiment has found an edge.**

- On realistic fills, the 5-minute ORB averages about 0R per trade.
- No market condition separates good sessions from bad ones.
- No exit change beats holding to 15:55.
- The opening-range duration does not matter: 15, 30 and 60 minutes all sit
  within 0.01R of the baseline.
- Nothing reaches the +0.05R per trade minimum edge needed to justify a
  confirmation test.

Every planned lever has now been tried. Unless a new idea with a strong
mechanism comes up (see "What is left"), the honest conclusion is "this ORB
has no edge worth confirming".

## Experiments

R is profit divided by initial risk. Condition studies compare terciles
(low / mid / high) of a variable known at entry time. Exit and opening-range
studies are paired comparisons: variant minus baseline on the same 8,395
symbol-sessions (1,679 clustered sessions). Intervals are approximate 95%
confidence intervals.

| # | Question | Experiment ID(s) | Trials | Conclusion |
| --- | --- | --- | --- | --- |
| 0 | Can the pipeline's statistics be trusted? (Step 0, tool checks) | V6 null: `01a1995d-81b6-7264-a192-55f462401160`<br>V7 planted edge: `01a19a1b-874c-7c9a-88db-09848d437ced` | 0 | **Yes.** On data with no edge, 0 of 200 runs gave a false positive and interval coverage was 95.5%. A planted +0.256R edge was recovered with 95.0% coverage. Detection was 28.5% against a predicted 40.6%, so treat power estimates as about 10 points optimistic. |
| 1 | Does the baseline ORB have an edge on realistic fills? (Step 2) | `01a19a24-ada7-744a-855c-bdf8dfd414bc` | 1 | **No.** 8,375 trades, mean net −0.001R, CI [−0.046, +0.044]. Every symbol's interval includes 0. Exits are bimodal: 51% at end of day (mean +1.01R) and 49% stopped (mean −1.04R), and they cancel. Costs are small (0.009R per trade). The earlier +0.44R to +0.78R (P5b) came from an entry fill that live trading cannot get and is superseded. |
| 2 | Do market conditions separate good sessions from bad ones? (Step 3, first pass) | `01a19a31-b0d9-7da3-a600-023183d2bd11` | 6 | **No significant condition.** Variables: `abs_gap_pct`, `or_width_atr`, `rel_open_volume`, `gap_pct`, `atr_pctile` and `prior_trend_20d_pct`. Every high − low interval includes 0, and no tercile reaches 0.05R. Two weak leads: gap size (+0.059R high − low) and opening-range width relative to ATR (+0.045R). Wider ranges doing better contradicts the "tight ranges break cleanly" mechanism. With five large symbols, high relative volume is just normal volume, not "stocks in play". |
| 3 | Does the overnight gap matter once direction and size are measured properly? (Step 3, second pass) | `01a19e08-dfd9-737e-8379-5185a2f7ab0c` | 2 | **No.** Gap direction (`gap_aligned_atr`) has no effect: breakouts with the gap (+0.023R) and against it (+0.020R) do the same, for longs and shorts alike. ATR-scaled gap size (`abs_gap_atr`) is +0.066R high − low, CI [−0.032, +0.163]. It holds in 6/7 years and 4/5 symbols, so it is not just a TSLA effect, but it is not monotonic. The pattern is "quiet opens are bad" (low tercile −0.045R). Skipping them would leave only about +0.025R. Kept as a diagnostic note, not registered. |
| 4 | Do breakouts in the direction of the 20-day trend do better? (Step 3, third pass) | `01a1a253-6a62-7475-8ccf-8ff1d65c8202` | 1 | **No.** `trend_aligned_pct` terciles (against / mid / with): −0.029 / +0.050 / −0.017. With − against is +0.012R, CI [−0.090, +0.114]. Not monotonic, and the sign holds in only 3/7 years and 3/5 symbols. Longs and shorts show the same flat shape. This also deprioritises wiring the higher-timeframe trend filter (`mtf_validation`). Step 3 closes with no registered condition. |
| 5 | Does moving the stop to break-even after +1R help? (Step 4) | `01a1a263-8056-7def-8d7b-05e25fe56c81` | 1 | **No, it clearly hurts.** −0.041R per session, CI [−0.062, −0.020], negative in 7/7 years and 5/5 symbols. Breakouts often retest before running, and a break-even stop turns those retests into scratches. This holds even though the engine's trailing-stop update is slightly optimistic. |
| 6 | Does closing earlier than 15:55 help? (Step 4) | Flat at 12:00: `01a1a274-c8eb-714f-bd89-f374269a8851`<br>Flat at 14:00: `01a1a284-c7db-70bb-8a93-037055ecbd27` | 2 | **No.** Flat at 12:00: −0.021R, CI [−0.047, +0.005], negative in 5/7 years and 5/5 symbols. Flat at 14:00: −0.007R, CI [−0.026, +0.012], mixed. The earlier the exit, the worse the result: the afternoon is where the end-of-day winners make their money. |
| 7 | Does a take-profit help? (Step 4, after fixing the take-profit) | 1× OR range: `01a1a27c-dd1a-7a29-8d5c-b21e0c709dbc`<br>2× OR range: `01a1a28c-66e4-7139-92ee-17a9bb7f34c0`<br>3× OR range: `01a1a29c-0591-7b90-8b52-876f1f672660`<br>0.25× daily ATR: `01a1a27c-f364-74f2-ba85-9ee78bb7c280`<br>0.5× daily ATR: `01a1a28c-7bf4-783a-98ae-018502e9a90d`<br>1× daily ATR: `01a1a29b-dd42-719a-915b-177859107b30` | 6 | **No.** OR grid: −0.041, −0.030 and −0.009R. ATR grid: −0.040, −0.021 and −0.001R. The two grids agree, and the cost shrinks smoothly as the target widens: capping winners loses more than it saves. Targets are anchored at the breakout level and entries fill past it, so a hit realises about half the nominal size (median 0.46R at 1× OR, 1.09R at 2× OR, 1.94R at 1× ATR). The widest targets are hit too rarely (8–17% of trades) to matter. The take-profit was ignored by the backtester before commit `cc30292`. |
| 8 | Does a longer opening range help, i.e. waiting 15, 30 or 60 minutes and trading the breakout of the early move? (Step 4) | 15 min: `01a1a314-ed14-7d59-9f7b-c919c72f7603`<br>30 min: `01a1a31d-197d-7faf-aebd-d3f06d6e44e2`<br>60 min: `01a1a32e-1821-7c5b-a347-339fdd8c4c97` | 3 | **No.** 15 min: +0.011R, CI [−0.020, +0.042]. 30 min: +0.009R, CI [−0.026, +0.043]. 60 min: +0.007R, CI [−0.032, +0.046]. Each is positive in only 3/7 years and 3/5 symbols. A flat plateau at zero: the duration does not matter. All three gain and lose in the same years (up in 2019, 2020 and 2024, down in 2018, 2021 and 2022), so the year pattern is the market's, not the duration's. A longer range widens the stop, so more trades are held to the close (85% at 60 minutes) and fewer trades fire (7,105 vs 8,375). Even "trade the direction of the first hour and hold to the close" averages about 0R on these symbols. |

## What the results say together

- **There is no average edge to filter or improve.** The baseline sits at 0R.
  The conditions tested do not find a subset that reaches +0.05R.
- **The "winners give back" clue was a mirage.** 40% of trades reach +1R of
  open profit, yet every way of locking it in (break-even, earlier exits,
  take-profits) does worse. The returned profit is the price of letting the
  end-of-day tail run.
- **The results are consistent, not noisy.** Exit levers lose more the
  tighter they are, across two independent target definitions. That
  pattern makes it unlikely the null results are just low power.
- **Nothing about timing helps either.** Neither waiting longer to define the
  range nor trend measured over 20 days changes the result.

## What is left

| Option | Status |
| --- | --- |
| Opening-range duration sweep (15, 30 and 60 minutes vs the 5-minute baseline) | Done (row 8): flat at zero |
| Take-profit set from the entry price (entry + k × initial risk), to test true 1R/2R/3R | Optional, low priority; the smooth shape above makes a reversal unlikely |
| Prior-session intraday trend: return over the last 30, 60 and 240 minutes of the previous session, gap excluded, aligned with the trade side (a condition study, 3 trials) | Documented, not run. See the note below |
| Higher-timeframe trend filter (`mtf_validation`) | Deprioritised by row 4 |
| Stocks in play (relative volume across many stocks) | Needs a broader universe; not with the five exploration symbols |
| Stop and conclude "no edge worth confirming" | The default now that every planned lever is done |

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
- **"Wait 15, 30 or 60 minutes, then trade with the early move"** is the
  opening-range duration sweep: a breakout of a 30-minute range only fires in
  the direction the price already moved after the open. It was run (row 8) and
  found nothing.
