# ORB Edge-Finding Playbook (Manual Process)

This document defines a **manual, repeatable workflow** for discovering and validating trading edge, using a simple **Opening Range Breakout (ORB) on SPY** as the training ground.

The goal is **not** to make ORB perfect.
The goal is to train a **generic edge-finding process** you can reuse across strategies.

---

## 1. Behavioral Hypothesis (Before Any Optimization)

Write this in your own words. A concrete starting point:

> Overnight information (earnings, macro news, geopolitical events) creates an imbalance in positioning. At the cash open, institutions and large participants are forced to adjust inventory to new information, creating an opening auction with elevated order flow. When price breaks the initial 5-minute range with confirming participation, the probability of short-term continuation in the direction of the break is higher than random.

Checklist:

- Describe **who** is pressured to act (e.g., funds, market makers, systematic flows).
- Describe **what** they are likely forced to do (buy, sell, hedge, unwind).
- Describe **when** this pressure is expressed (around the open).
- Describe **how** ORB is supposed to capture this behavior (breakout from initial range).

If you cannot articulate this clearly, **do not move on**.

Document your final one-paragraph hypothesis here.

---

## 2. Define the Baseline ORB Spec (No Optimization)

You need **one fixed, dumb, transparent rule set**. This is your **raw signal test**.

Baseline for SPY:

- Instrument: SPY (regular session, 9:30–16:00 US/Eastern).
- Bar timeframe: Use data granular enough to define a 5-minute range and execute (e.g., 1-min bars, but ORB range defined on the 9:30–9:35 bar).
- Opening range definition:
  - Opening Range High (ORH) = high of the 9:30–9:35 bar.
  - Opening Range Low (ORL) = low of the 9:30–9:35 bar.
- Entry rules (one trade per day maximum):
  - Long: Place a buy stop at ORH as soon as the 9:35 bar closes. If price trades through ORH before 11:00, go long at breakout price.
  - Short: Place a sell stop at ORL as soon as the 9:35 bar closes. If price trades through ORL before 11:00, go short at breakout price.
  - If both sides would trigger on the same bar, define a deterministic rule (e.g., "first touch in time wins" or "skip that day"). Pick one and freeze it.
- Exit rules:
  - Initial stop: 1R, where R = ORH − ORL (range size) for that day.
    - Long: stop = entry − R.
    - Short: stop = entry + R.
  - Target: 2R profit target.
  - Time stop: If neither stop nor target hit by 11:00, exit at market at 11:00.
- Position sizing:
  - Fixed risk per trade (e.g., 0.5% of account equity per trade), but for edge finding, focus on **R-multiples**; position sizing is secondary.
- Friction assumptions (for now):
  - Realistic commission and slippage settings, but **constant**, not optimized.

Key point: **Every parameter above is chosen once and then frozen.**
No grids. No sweeps. No searching.

---

## 3. How to "Remove All Optimization" (Conceptually)

In practice, this means:

- Do **not** run any parameter sweeps or optimizers.
- Choose **one** value for each of the following and hard-code/fix them:
  - Opening range length (here: 5 minutes).
  - Target multiple (here: 2R).
  - Stop multiple (here: 1R).
  - Trading window (here: from 9:35 until 11:00 only).
  - One instrument (here: SPY only).
- Remove / disable:
  - Any loops over parameters (e.g., `for range in [5, 10, 15]: ...`).
  - Any optimizer configuration in your backtest platform.
  - Any regime filter that was tuned via optimization.

You are deliberately **throwing away** all the cleverness.
You want a **clean, dumb baseline** that answers a single question:

> "Does a basic ORB breakout on SPY have positive expectancy at all over the last 3+ years, before any conditioning?"

---

## 4. Backtest Configuration for the Baseline Test

For the first baseline run (e.g., on QuantConnect or another platform):

- Universe: SPY only.
- Date range: At least the last 3–5 years (the longer, the better, as long as data quality is stable).
- Resolution: Intraday bars sufficient to implement the 5-minute ORB and precise entries/exits.
- Order type: Use market or stop-market orders consistent with how you would realistically trade.
- Costs:
  - Use realistic but **fixed** commission and slippage assumptions.
  - Do **not** try to fine-tune costs to make results look better.

Run a single backtest with these fixed settings.

---

## 5. Export a Trade Log for Analysis

For each trade, you need at least:

- Trade date.
- Side: long or short.
- Entry time, entry price.
- Stop price at entry, target price at entry.
- Exit time, exit price.
- Gross P&L in currency.
- P&L in R-multiples ( (exit_price − entry_price) / R for longs, sign-adjusted for shorts ).
- Any platform-reported commission and slippage.

If possible, also capture (for later segmentation):

- Gap%: ((open_today − close_yesterday) / close_yesterday) × 100.
- Daily ATR percentile (e.g., 14-day ATR vs 1-year history) at the open.
- VIX level or VIX regime tag (e.g., low/medium/high) on that day.

You can compute some of these after the fact in a spreadsheet or Python notebook.
The key is: **you end up with one row per trade** and enough columns to slice by conditions later.

---

## 6. Analyze Expectancy by Clusters (Manual Workflow)

With the trade log in a spreadsheet or notebook:

1. Compute basic metrics for the **entire sample**:
   - Win rate.
   - Average win (in R).
   - Average loss (in R).
   - Expectancy per trade: (Win% × Avg Win) − (Loss% × Avg Loss).
2. Then create **simple buckets** for:
   - Gap size (e.g., negative gap, small positive, large positive).
   - ATR percentile (e.g., low-vol, mid-vol, high-vol days).
   - VIX regime (e.g., VIX < 15, 15–25, >25).
3. For each bucket, recompute expectancy.

Your goal is to see if there are **clusters of conditions** where expectancy is:

- Clearly positive and meaningfully better than the full-sample average.
- Stable across time (not just a handful of trades).

Those clusters become **candidates for edge.**

---

## 7. Deciding the Next Move After Baseline

Once you have the baseline and segmented results, you can:

- If baseline expectancy is **strongly negative everywhere**:
  - Likely no ORB edge on SPY in this form → move on or rethink hypothesis.
- If baseline expectancy is **weakly positive but unstable**:
  - Consider it "raw bias" and look for conditions where it strengthens.
- If certain clusters (e.g., large positive gap + high VIX) show strong expectancy:
  - Formulate a **refined behavioral hypothesis** specific to that cluster.
  - Design a second-generation test that **only trades those conditions**, still without heavy optimization.

You then repeat the same pattern: hypothesis → minimal rules → baseline test → segmentation → stress.

---

## 8. Your Very Next Concrete Step

Given your current situation (optimized ORB that underperforms SPY):

1. **Write your one-paragraph behavioral hypothesis** for SPY ORB at the open in this document (section 1).
2. **Commit to the baseline spec above** (section 2) or adjust it slightly and freeze it.
3. **De-optimise your existing ORB implementation** so it matches that baseline:
   - Remove all parameter sweeps.
   - Hard-code the chosen values.
   - Run a single 3–5 year backtest on SPY only.
4. **Export the full trade list** and compute expectancy both overall and by a few simple clusters.

Once you’ve done steps 1–4, we can:

- Interpret your actual numbers.
- Decide whether there is any conditional edge worth pursuing.
- Design the next iteration (filters and regime definition) **without slipping back into blind optimization.**
