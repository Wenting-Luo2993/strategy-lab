# ORB edge exploration: results so far

**Updated:** 2026-11-03  
**Programme:** `orb_edge_2026` (19 trials)  
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
- Nothing reaches the +0.05R per trade minimum edge needed to justify a
  confirmation test.

The opening-range duration sweep is the only planned lever not yet tried. If
it also finds nothing, the honest conclusion is "this ORB has no edge worth
confirming".

## Experiments

R is profit divided by initial risk. Condition studies compare terciles
(low / mid / high) of a variable known at entry time. Exit studies are paired
comparisons: variant minus baseline on the same 8,395 symbol-sessions (1,679
clustered sessions). Intervals are approximate 95% confidence intervals.

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

## What is left

| Option | Status |
| --- | --- |
| Opening-range duration sweep (5, 10, 15 and 30 minutes) | Next: the last planned lever |
| Take-profit set from the entry price (entry + k × initial risk), to test true 1R/2R/3R | Optional, low priority; the smooth shape above makes a reversal unlikely |
| Higher-timeframe trend filter (`mtf_validation`) | Deprioritised by row 4 |
| Stocks in play (relative volume across many stocks) | Needs a broader universe; not with the five exploration symbols |
| Stop and conclude "no edge worth confirming" | Valid if the duration sweep also finds nothing |
