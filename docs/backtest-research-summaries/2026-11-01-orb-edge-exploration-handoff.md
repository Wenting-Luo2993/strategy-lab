<!-- taskboard
{"id":"orb-old-handoff","title":"ORB exploration handoff (historical)","projects":["orb-research"],"status":"historical","kind":"reference","reviewed":"2026-11-14","superseded_by":["orb-results","orb-roadmap"]}
-->
# ORB edge exploration handoff

> Historical continuation notes. Some trial-count and next-step statements
> below predate the latest results. Use the [living summary](orb-exploration-results.md)
> and [research roadmap](../taskboard/orb-research.md) for the reviewed work index.

**Updated:** 2026-11-03  
**Branch:** `wenting-luo2993-backtest-implementation-review`  
**Researcher:** Bloom  
**Current focus:** the research pipeline is working on real data. The next
work is research, not platform building: explore the ORB strategy on the
exploration set to find a condition or change that produces an edge worth
confirming.

This supersedes the "next step" sections of `2026-10-18-backtest-handoff.md`.
That document stays as the record of the platform build.

## Read first

1. This handoff.
2. `orb-exploration-results.md`: the living summary, with one table of every
   exploratory experiment (full IDs and conclusions). Add a row for each new
   experiment.
3. `2026-10-31-orb-edge-research-plan.md`: Steps 0 to 11, with the results of
   Steps 0 to 4 recorded inline.
4. `2026-10-31-backtest-research-north-star.md`: what a finished experiment
   report should show, and why.

## Where things stand

| Step | Status | Experiment ID | Result |
| --- | --- | --- | --- |
| 0. Trust the tool | Done | V6 `01a1995d-81b6-7264-a192-55f462401160`, V7 `01a19a1b-874c-7c9a-88db-09848d437ced` | V6 null: 0/200 false positives, coverage 95.5%. V7 planted edge: truth +0.256R, coverage 95.0%, detection 28.5% vs predicted 40.6% (conservative; treat power estimates as ~10 points optimistic) |
| 1. Freeze evidence map | Done | none (metadata only) | 25 confirmation stocks in `config/research/universes/orb-confirmation-universe-v1.json`, all locked in `config/final_holdout.yaml` |
| 2. Baseline | Done | `01a19a24-ada7-744a-855c-bdf8dfd414bc` | 8,375 trades, mean net −0.001R, 95% CI [−0.046, +0.044]R. No average edge on honest fills |
| 3. Conditions, first pass | Done | `01a19a31-b0d9-7da3-a600-023183d2bd11` | No condition is significant. Weak leads: `abs_gap_pct` (+0.059R high − low) and `or_width_atr` (+0.045R) |
| 3. Conditions, gap second pass | Done (2026-11-02) | `01a19e08-dfd9-737e-8379-5185a2f7ab0c` | Gap direction (`gap_aligned_atr`) has no effect. ATR-scaled gap size (`abs_gap_atr`, +0.066R high − low) holds in 6/7 years and 4/5 symbols but fails the rule; quiet opens are the weak spot |
| 3. Conditions, trend direction-aware | Done (2026-11-03) | `01a1a253-6a62-7475-8ccf-8ff1d65c8202` | Not supported: with − against +0.012R [−0.090, +0.114], not monotonic; out of sample the train-period gap reverses. Step 3 closes with no registered condition. The earlier "weak leads" (`abs_gap_atr`, `or_width_atr`, `abs_gap_pct`) also fail a train/test split (see `orb-exploration-results.md`, row 3) |
| 4. Exit levers | Done (2026-11-03) | 9 experiments, listed in the research plan, Step 4 | Contradicted: break-even −0.041R, time exits −0.021R (12:00) and −0.007R (14:00), and take-profit −0.041R to −0.001R per session. Tighter exits hurt more; nothing beats holding to 15:55 |
| 4. Opening-range duration | Done (2026-11-03) | `01a1a314-ed14-7d59-9f7b-c919c72f7603` (15 min), `01a1a31d-197d-7faf-aebd-d3f06d6e44e2` (30 min), `01a1a32e-1821-7c5b-a347-339fdd8c4c97` (60 min) | Not supported: +0.011R, +0.009R and +0.007R per session, all intervals include 0 (60 min: 4/7 years, 4/5 symbols). Flat at zero in R and worse than the baseline in dollars. Step 4 closes with no enhancement |
| Panel review | Done (2026-11-03) | none | Numbers reproduce. "No edge" is overstated: it holds for next-bar-open market entry, but a lookahead-free resting-stop entry gives +0.06R [+0.01, +0.11]. See `orb-exploration-results.md` |
| Entry tests (2026-11-07) | Done | `01a1b8a6-43d6-7c52-a64f-4b52468d9112` (v7 baseline), `01a1b8a6-eefd-7c52-a22e-651d5a3c3fff` (resting stop), `01a1b8ab-3665-7d69-a3be-669c16e08420` (Zarattini & Aziz) | v7 baseline +0.003R [−0.042, +0.048]. Resting-stop entry +0.062R [+0.010, +0.115], tail-dependent. First-candle ORB +0.028R [−0.031, +0.087]; reproduces the paper only under near-zero costs. See `orb-exploration-results.md`, rows 9 to 11 |
| Next | Decision pending | | Measure real stop-order slippage by paper-trading the resting-stop entry. Confirmation data stays untouched until hypotheses are registered |

Trial ledger (`orb_edge_2026`): 100 trials, including 75 October legacy
configurations; see `orb-exploration-results.md`, "Trial count". The
breakdown below is the original 22 programme trials:

- 1 baseline;
- 6 conditions in the first pass;
- 2 gap conditions (2026-11-02);
- 1 trend condition (2026-11-03);
- 9 exit variants (2026-11-03): break-even, two time exits and six
  take-profits;
- 3 opening-range durations (2026-11-03): 15, 30 and 60 minutes.

The local research DB was rebuilt on 2026-11-02, so `show_trial_ledger.py`
misses the earlier 7 trials. They are recorded here and in the research plan,
but they (and the V6/V7 checks) are **not in Supabase yet**; Supabase
`n_trials` sums to 15. The October 2026 nested walk-forward (172 runs),
sensitivity sweep (65) and EXP-073 used the same data and are not counted.
Every variable or variant you try adds to the ledger.

Every experiment now has a one-line conclusion (`research_experiment_conclusions`,
schema v18), shown in the local viewer and the remote dashboard. Set one with
`python scripts/publish_research.py --db <db> conclusion set <id> "<text>" --publish`.

### What the baseline tells us

- **Exits are bimodal.** 51% of trades exit at end of day (mean +1.01R) and 49%
  are stopped out (mean −1.04R). They cancel.
- **Winners give back.** 40% of trades reach +1R of favourable excursion and 18%
  reach +2R, yet the average trade is about 0R. Some open profit is being
  returned before 15:55. This looked like the strongest clue for exit levers,
  but the 2026-11-03 exit study showed that capturing it costs more than it
  saves (see B).
- **Costs are small.** Commission is 0.009R per trade, so costs are not what
  kills the edge.
- **P5b's +0.44R to +0.78R is superseded.** It came from an idealised entry fill
  that live trading cannot get (see the research plan, Step 2).

## Next research steps

Work only on the exploration set: QQQ, AMZN, GOOGL, MSFT and TSLA, 2018-05-01
to 2024-12-31 (always pass `--end 2024-12-31`).

A to D are done or deprioritised. Nothing has reached the 0.05R minimum edge,
but the panel review showed the conclusion depends on the entry order type,
so the next steps are a lookahead-free resting-stop entry and the
first-candle-direction ORB before any "no edge" conclusion. Options not yet run,
including the prior-session intraday trend, are listed in
`orb-exploration-results.md`, "What is left".

### A. Direction-aware conditions (Step 3, second pass): done

Both gap and trend were contradicted (see the status table). Neither direction
nor trend alignment separates outcomes.

**Question:** does ORB work better when the breakout goes *with* the overnight
gap or the prior trend?

The first pass used signed gap and trend, which mixes longs and shorts. A long
breakout after a gap up and a short breakout after a gap up land in the same
tercile, which can hide a real effect.

- **Code (done 2026-11-02):** `vibe/research_pipeline/conditional.py` has
  `TRADE_RELATIVE_VARIABLES`, which multiply a session feature by the trade
  side (+1 long, −1 short): `gap_aligned_pct`, `gap_aligned_atr` and
  `trend_aligned_pct`. `session_features.py` adds `gap_atr` and `abs_gap_atr`.
  Tercile output now includes a `by_side` (long/short) breakdown, shown in the
  viewer. The gap half is done (see the status table); trend remains.
- **Run:** `run_fixed_rule_study.py` with the two new `--condition` flags,
  `--trial-family condition --trial-label step3_direction_aware`.
- **Read:** a monotonic increase from "against" to "with" that holds in most
  years and most symbols. The first pass's high − low intervals were about
  ±0.10R wide, so a difference smaller than about 0.14R between the extreme
  terciles is unlikely to be detected reliably (80% power).

### B. Exit levers (Step 4, first): done

All nine variants were at or below the baseline. Results and reading are in the
research plan, Step 4, first pass. Points to carry forward:

- **The take-profit was broken and is fixed** (commit `cc30292`). It now
  honours `orb_range_multiple` or `atr_multiple` (daily ATR(14) known at the
  open).
- **The target is anchored at the breakout level, not the entry.** Entries
  fill past the trigger, so a hit target realises about half of its nominal
  size: 1× OR ≈ 0.46R, 2× OR ≈ 1.09R, 1× daily ATR ≈ 1.94R.
- **Optional follow-up, low priority:** a true R-multiple target (entry +
  k × initial risk). It is not implemented, and the smooth "tighter is worse"
  shape makes a reversal unlikely.

The original plan for B is kept below for the record.

**Question:** can exits keep more of the +1R moves that 40% of trades reach?

Run each lever as a paired comparison against `orb_production.yaml` (same
sessions, per-session difference):

1. Take-profit at 1R, 2R and 3R (`exit.take_profit`). HYP-003 removed the
   take-profit under the old fill model, so it needs a rerun.
2. Break-even stop after +1R (HYP-004).
3. Time exits: flat at 12:00 and 14:00 instead of 15:55.

Use the MAE/MFE panel to explain any change, not just the mean. Count each
variant as a trial.

### C. Opening-range duration (Step 4): done

Ran 15, 30 and 60 minutes against the 5-minute baseline (the 10-minute point
was dropped in favour of 60 to cover the "wait an hour" idea). All three are
within 0.01R of the baseline: a flat plateau at zero. Results are in the
research plan, Step 4, second pass. The original plan for C is kept below.

**Question:** is 5 minutes special, or is there a broad plateau?

Sweep 5, 10, 15 and 30 minutes as paired comparisons against the 5-minute
baseline. Look for a plateau of similar results, not a single best value. A
single sharp peak is a sign of noise.

### D. Trend filter (needs engineering): deprioritised

`trend_aligned_pct` was contradicted on 2026-11-03, so this filter is unlikely
to help. Keep it in the backlog.

The ruleset has an `mtf_validation` block (30-minute trend alignment), but
**the backtester ignores it**. Only `trade_filter.volume_confirmation` is wired
(`vibe/backtester/runner.py`, around line 55). The EMA trend rule exists in
`vibe/common/validation/rules/trend_alignment.py`. Wire it in, add an
integrity test, then run it as a paired comparison. Do A first: if
`trend_aligned_pct` shows nothing, this filter is unlikely to help.

### E. Later: stocks in play

The relative-volume effect in Zarattini, Barbon and Aziz (2024) ranks many
stocks each morning and trades the most active ones. With five large symbols,
the top `rel_open_volume` tercile is ordinary volume. Testing this properly
needs a broader universe. Do not use confirmation stocks for it.

### When to stop exploring

Aim for **at most two conditions and one bundled enhancement**, each with a
mechanism. Then go to Step 5 (register hypotheses, compute the minimum
detectable edge on the confirmation set) before touching confirmation data. If
nothing reaches the 0.05R minimum edge on exploration, state the scoped
result: which entry rule, which symbols and years, which edge size is
excluded, and the power to detect it. That is a valid result.

## Rules that still apply

- Exploration results are labelled "cannot confirm" in the viewer. Do not
  treat them as proof.
- Every variable and variant is a trial. Use `--programme orb_edge_2026` and
  one research DB, so `scripts/show_trial_ledger.py` counts everything.
- Confirmation stocks and the 2025-01-01 to 2026-04-27 holdout stay locked
  until Step 5 is committed. The holdout is read exactly once (Step 10).

## How to run

Local state that the branch does not carry:

- Research DB: `%LOCALAPPDATA%\strategy-lab\orb_edge_2026.sqlite3` (pass
  `--research-db`; from 2026-11-07, rows 9 onward). The DB that recorded rows
  3 to 8 was lost; those experiments exist only in Supabase and are counted
  through `config/research/trial_ledger_orb_edge_2026.yaml`. Rows 0 to 2 are
  in `tmp\research-viewer.sqlite3` of the `reimagined-giggle` worktree.
- Exploration data: `D:\development\strategy-lab\vibe\data\parquet\` (pass
  `--data-dir`).
- Confirmation raw data: `D:\development\strategy-lab\vibe\data\core-universe`.
  Do not convert or run it yet.

Commands are in the research plan, section "How to run Steps 0, 2, 3 and 4".
Start the viewer with:

```powershell
node tools\backtest-research-viewer\server.mjs --db $env:LOCALAPPDATA\strategy-lab\orb_edge_2026.sqlite3 --port 4180
```

Open http://127.0.0.1:4180/, open an experiment, and read its study panel.
"Guide: on" turns on the reading coachmarks. Linked runs are collapsed at the
bottom of each study panel.

## Engineering backlog (not blocking research)

| Item | Why | Where |
| --- | --- | --- |
| Parallel workers for fixed-rule and paired runners | Studies run serially (Step 2 took 4.3 min; sweeps multiply this) | `scripts/run_fixed_rule_study.py`, `scripts/run_paired_comparison.py`; V6/V7 already use a worker pool |
| Engine history scan | `engine.py` passes `df.loc[:ts]` on every bar and `orb_levels.py` rescans history, so long runs slow down | `vibe/backtester/core/engine.py`, ORB level calculation |
| Frozen tercile cutoffs in confirmation | `tercile_breakdown(cutoffs=...)` exists but the confirmation runner does not pass exploration cutoffs. Needed by Step 7 | `vibe/research_pipeline/conditional.py` and the confirmation runner |
| `mtf_validation` wiring | Needed for lever D | `vibe/backtester/runner.py` |
| V6 viewer badge shows "Review" | Its rule is two-sided; the study passed | `tools/backtest-research-viewer/public/experiment-kinds.mjs`, `validationBadge` |
