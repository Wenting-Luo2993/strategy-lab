# Target Architecture Gap Audit

**Date:** 2026-10-14
**Scope:** Section 3 ("Proposed Target Architecture") of
`2026-09-09-backtest-pipeline-implementation-plan.md`, audited step by step
against the code actually on disk.
**Trigger:** A walk-forward result in the research viewer showed train folds at
+10.2% against test folds at +0.6%. Reading the code to explain the gap showed
the comparison does not mean what its labels claim.

---

## 1. The headline finding

**Every component of the target architecture exists and is correct. Almost none
of them are reachable from anything a person runs.**

This is the same failure that P15a found and fixed for run recording: the
capability was built, tested, and then never wired to a caller. The audit found
it again, one layer up, and this time the unwired component is the one that
decides whether a strategy works.

Concretely, there are **two** walk-forward implementations:

| | `vibe/research_pipeline/walk_forward.py` | `vibe/backtester/analysis/walk_forward.py` |
| --- | --- | --- |
| Per-fold optimization | Yes | **No** |
| Dedicated validation window | Yes | **No** |
| Sessions from an exchange calendar | Yes | **No** — `timedelta(days=months * 30)` |
| Purge / embargo | Yes | **No** |
| Role contamination audit | Yes (`audit_session_separation`) | **No** |
| Fold-count hard invariant | Yes | **No** |
| Survivorship-safe stitching | Yes | **No** — reports a mean over folds |
| **Production callers** | **Zero** | `OptimizationPipeline` → `scripts/optimize_strategy.py` |

The correct one is referenced only by its own module and its unit test. The
broken one is what produced the numbers in the research store.

---

## 2. What the legacy path actually does

`WalkForwardEngine.analyze()` holds `self.ruleset` fixed and calls `engine.run()`
twice per fold — once on the train window, once on the test window
(`walk_forward.py:170-190`). There is no optimizer, no refit, and no parameter
selection inside the training window. The "train" half is simply the earlier
window run with the same parameters as the "test" half.

Where those parameters come from makes it worse (`pipeline.py:342`):

```python
best_ruleset = sweep._create_modified_ruleset(best_params)
```

`best_params` is the winner of a sweep over the **entire** `start_date..end_date`
range (`pipeline.py:255-266`), which contains every fold's test window.

So the two labels are both wrong:

- **train** — nothing was trained here. It is just the earlier window.
- **test** — not out-of-sample. The parameters were selected using data that
  includes this window.

Three consequences worth stating plainly:

1. **The train/test gap is not evidence of overfitting from parameter fitting**,
   because no per-fold fitting happened. It measures regime sensitivity plus a
   window-length artifact (6 months vs 1 month, 120+ trades vs ~20).
2. **`degradation = test_exp / train_exp`** (`walk_forward.py:46`) explodes when
   a fold's train expectancy is near zero, and the reported mean is then
   dominated by that artifact. This is the source of the nonsensical
   "Avg degradation: 4357.2%".
3. **`timedelta(days=months * 30)`** drifts against the real calendar, so folds
   slide over time and windows do not align to sessions. Section 8 of the plan
   explicitly forbids this: "never in calendar days or approximate 30-day
   months."

The plan already predicted this exact defect at §10:

> The current `WalkForwardEngine` must be upgraded because it evaluates one
> fixed ruleset across train and test periods rather than re-optimizing within
> each training fold.

It was diagnosed and then never acted on. The replacement was written —
`NestedWalkForwardDriver` — but the old path was left as the default and
nothing pointed at the new one.

---

## 3. Step-by-step audit of the technical execution architecture

Each node of the §3 flowchart, with what exists and what is missing.

| # | Architecture node | Implementation | Status |
| --- | --- | --- | --- |
| A | `BacktestRunRequest` | none | **Missing (cosmetic).** Scripts pass ad-hoc kwargs. No typed request object binds a run to its split plan. Low severity: the fingerprint already captures identity. |
| B | `TemporalSplitPlanner` | `splits/planner.py` | **Complete, unreachable.** Session-based, derives purge/embargo, rejects folds that touch the holdout, emits a per-fold `SplitManifest`. No CLI calls it. |
| C | `SplitManifest` | `contracts.py` | **Complete.** Immutable, hashed, rejects `applied < derived`. |
| D | `FeatureAvailabilityValidator` | `features/leakage.py::audit_feature_availability` | **Complete** under a different name, plus `run_leakage_suite` covering truncation equivalence, prefix invariance, future perturbation, ORB boundary, and split contamination. |
| E | `Optimizer (train)` | `optimization.py::Optimizer` | **Complete, unreachable.** Never receives validation or test data. Refuses to report a partial grid (`OptimizationIncompleteError`). |
| F | `Selector (validation)` | `optimization.py::Selector` | **Complete, unreachable.** Deterministic medoid of a connected validation plateau — not a bare argmax. Records `n_candidates_evaluated` and `selection_margin`, exactly as §10 "Selection discipline" requires. |
| G | `FrozenCandidate` | `SelectionResult.selected` | **Present in substance**, absent in name. The selected candidate is frozen by value before the test segment runs. |
| H | `BacktestEngine (test / OOS)` | `optimization.py::RulesetSegmentExecutor` + `segment_runner.py::run_segment` | **Complete, unreachable.** Builds a fresh engine per invocation so no state crosses candidates/roles/folds; warmup primes indicators but is excluded from grading, and `assert_warmup_excluded` verifies the suppression held. Defaults to realistic execution rather than inheriting the optimistic legacy default. |
| I | `MetricValidator` | `validation.py::MetricValidator` | **Complete.** Collects all findings before deciding a terminal state; gates plausibility/acceptance by scope. |
| J | `ValidationReport` → `COMPLETED` / `VALIDATION_FAILED` | `validation.py`, `lifecycle.py` | **Complete.** |
| M | Local SQLite + publish outbox | `storage/`, `publishing.py` | **Complete and wired** (P12, P15a). |
| N | Remote research store | `publishing.py` | **Complete.** |
| O | Research dashboard | `tools/backtest-research-viewer`, `apps/operational-metrics-dashboard` | **Complete for local**; `/research` still renders fixtures (P15c). |

### The composition gap

B through J exist and are individually correct. They are composed in exactly one
place — `NestedWalkForwardDriver.run()` — and that function has no production
caller. The seam between "correct components" and "a result a person can
produce" is the entire gap.

### Research lifecycle audit (the first §3 diagram)

| Lifecycle stage | Status |
| --- | --- |
| Define universe | `universe.py` exists; liquidity/price screens implemented |
| Baseline strategy + initial evidence | Reachable (`run_backtest.py`), recorded since P15a |
| Hypothesis generation | Registry supports hypotheses; manual |
| Parameter / rule exploration | Reachable (`ParameterSweep`) |
| Parameter sensitivity | Reachable (`sensitivity_runner.py`), recorded since P15a |
| **Freeze methodology** | **Not enforced.** No mechanism marks a lineage frozen before validation begins. |
| **Walk-forward validation** | **Broken in the reachable path** (§2 above) |
| True out-of-sample | Guard implemented (`holdout.py`): config-locked range, loader refuses past `dev_end`, append-only access log. Promotion blocking deferred to P11. |
| Regime / robustness | Reachable (`robustness.py`) |
| Paper → live → monitoring | Out of scope here |

---

## 4. Severity-ordered gaps

| # | Gap | Severity | Why it matters |
| --- | --- | --- | --- |
| G1 | Nested walk-forward has no entry point; the broken engine is the default | **Critical** | Every walk-forward number produced to date mislabels in-sample results as out-of-sample. |
| G2 | Legacy `WalkForwardEngine` presents its output as train/test | **Critical** | The labels are the defect. Anything reading them is misled, including the research store and the viewer. |
| G3 | Stored runs from the legacy path are labelled `SegmentRole.TRAIN` / `TEST` | **High** | P15a faithfully recorded the legacy engine's roles, so the store now contains rows asserting out-of-sample status that was never established. |
| G4 | `degradation` ratio is unstable near zero | Medium | Produces meaningless headline numbers like 4357.2%. |
| G5 | `timedelta(days=months * 30)` instead of sessions | Medium | Violates §8 explicitly; folds drift. |
| G6 | "Freeze methodology" is not a mechanism | Medium | §3 requires freezing before validation; nothing enforces or records it. |
| G7 | No `BacktestRunRequest` type | Low | Cosmetic; identity is already captured by the fingerprint. |
| G8 | Swept parameters produce byte-identical results | **Resolved — see §6** | Measured, not assumed. One real defect (`EOD_Time` was never read), one misinformation defect (`Volume_Threshold` is gated off), one false alarm (`Entry_Cutoff` is wired and genuinely non-binding). |

---

## 5. Execution order

1. **G1** — add the missing entry point so nested walk-forward is runnable.
   This is wiring, not new capability.
2. **G2** — make the legacy engine refuse to present itself as OOS evidence.
3. **G3** — mark the affected stored runs so existing rows stop asserting
   out-of-sample status.
4. **G4/G5** — fold into G2 rather than polishing a deprecated path.
5. **G6, G7** — follow-ups, tracked but not in this pass.
6. **G8** — resolved in this pass; see §6.

The guiding rule for this work, which the audit itself demonstrates: *a
capability that nothing calls is indistinguishable from a capability that does
not exist.* Each fix below therefore ends at a command a person can run, not at
a class that passes a unit test.

---

## 6. G8 resolved — three identical symptoms, three different causes

The reported symptom was one line in the sensitivity output: `Entry_Cutoff`,
`EOD_Time` and `Volume_Threshold` each produced byte-identical rows at every
swept value. It would have been easy to file that as a single "parameters not
wired" defect. Measuring each axis separately showed three unrelated causes,
only one of which was a wiring bug.

The method was the same in each case, and it is the part worth keeping: **run
the axis at a value so extreme that an inert result is impossible**, and
include a positive control so the harness itself is proven able to detect
change. Sweeping only the configured values cannot distinguish "this knob does
nothing" from "this knob is never read."

QQQ 5m, 2022-01-03 .. 2022-06-30, `orb_production.yaml`:

| Probe | Trades | Net P&L | Verdict |
|---|---|---|---|
| `entry_cutoff` 14:00 / 15:00 / 15:30 | 124 / 124 / 124 | identical | suspicious |
| `entry_cutoff` **09:36** (extreme) | 51 | 2,935.92 | **wired** |
| `eod_time` 15:50 / 15:55 / 16:00 | 124 / 124 / 124 | identical | suspicious |
| `eod_time` **10:30** (extreme) | 124 | identical | **defect** |
| `volume_threshold` 1.0 / 2.0 / 5.0 | 124 / 124 / 124 | identical | suspicious |
| `volume_confirmation=true`, threshold **99** | 0 | 0.00 | **wired, but gated off** |
| `orb_duration` 30 (positive control) | 123 | 3,266.01 | harness can detect change |

### 6.1 `EOD_Time` — a real defect

`PortfolioManager.check_exits` compared against a module-level constant,
`_EOD_CUTOFF = time(15, 55)`. `exit.eod_time` was validated by the ruleset
model, threaded into `ORBStrategy` as `market_close_time`, swept by the
sensitivity runner — and then never consulted on the exit path. Setting it to
10:30 changed nothing, while 41 of 124 trades still exited with reason `EOD`.

This is the most dangerous shape of bug in a research pipeline: the
configuration is *accepted*, so nothing errors, and the sweep dutifully
reports that the parameter has no effect. A reader concludes the exit time
does not matter. It matters a great deal — at a 10:30 cutoff the same strategy
returns 5,608.86 instead of 10,935.10.

The cutoff is now a constructor argument on `PortfolioManager`
(`eod_cutoff`, `eod_enabled`), supplied from `exit.eod_time` / `exit.eod` by
both `engine.py` and `portfolio_engine.py`.

**Goldens are unaffected.** The former constant equals the `ExitConfig`
default, so the production ruleset reproduces byte-identically (124 trades,
10,935.10, STOP 83 / EOD 41). Only configurations that previously had *no*
effect now behave as written — 15:50 moves to 11,216.10, where it was
previously indistinguishable from 15:55.

`portfolio_engine._close_at_session_end` is deliberately left alone. It flattens
at each session's final bar and exists to handle early closes, which is a
correctness backstop rather than a configured exit rule.

### 6.2 `Volume_Threshold` — misinformation, not a bug

The threshold is correctly wired, but it is only read when
`trade_filter.volume_confirmation` is true, and `orb_production.yaml` sets it
to `false`. Every swept value was therefore identical *by construction*.

Nothing here is broken, yet the sweep output was still wrong in the way that
matters: it presented "no sensitivity" for a parameter that was never
consulted. That is indistinguishable, to a reader, from the genuine finding
that a parameter does not matter — and it is false.

`ParameterDefinition` gained a `requires` field naming the gating flag, and
`drop_inert_parameters()` removes such axes from the sweep with an explicit
message saying why. The axis is now **absent and explained** rather than
**present and misleading**.

### 6.3 `Entry_Cutoff` — a false alarm, and a legitimate result

The cutoff is wired (09:36 cuts trades from 124 to 51). The swept values
(14:00, 15:00, 15:30) are simply later than every ORB entry this strategy
takes, because an opening-range breakout fires in the morning or not at all.

This one is left exactly as it is. "The entry cutoff does not bind at any
plausible value" is a true and useful finding about the strategy, and it is
the kind of answer a sensitivity sweep exists to produce. The distinction
from §6.2 is the whole point: there, the pipeline could not have detected an
effect; here, it could have, and correctly reports there is none.

### 6.4 What this generalises to

Three axes, one symptom, three causes — and the symptom alone could not tell
them apart. A sweep that reports "no sensitivity" is making one of three very
different claims, and it currently has no way to say which:

1. the parameter was never read (a bug),
2. the parameter is disabled by a flag (a configuration fact), or
3. the parameter is read and genuinely does not bind (a result).

Only the third is a research finding. The `requires` mechanism separates (2)
from (3); the regression tests in `test_portfolio.py` keep (1) from recurring
for this parameter. A general guard — asserting that at least one extreme
value of every live axis moves the result — would be the stronger fix, and is
worth considering if more inert axes appear.

---

## 7. Turning validation on, and the three defects it found

Section 6 ended with the sweep axes fixed. The next gap was that **no
recorded run had ever left `VALIDATING`**: the whole validation layer existed,
was unit-tested, and was never reachable from a study runner. Wiring it up was
a two-line change in principle. What it exposed was not.

The honest summary is that *switching on the validator immediately found three
real defects that every previous backtest had silently carried*. That is the
system working: each one had been producing wrong numbers for as long as the
pipeline has existed, and none of them announced itself.

### 7.1 The validator could never have completed a run

`MetricValidator` skips the plausibility and research-acceptance gates unless
the scope is `CANDIDATE` or `PROMOTION`, and records the reduced set in
`checked_categories`. But `SqliteResearchStore._assert_completed_report_is_valid`
refuses a `COMPLETED` report that omitted *any* category. A `RUN`- or
`SWEEP_ROW`-scoped report therefore could not finish a run under any
circumstances — the two halves of the contract disagreed.

The store is right to be strict, so the fix is on the calling side:
`build_study_validator` now uses `ValidationScope.CANDIDATE`. That is also the
correct description of what a sweep point *is*. Each point is a candidate
parameterization, and the gates that ask "is this result too good to believe?"
are exactly the ones worth applying before one can be selected.

### 7.2 `--end` silently dropped the final trading session

`datetime.strptime(args.end, "%Y-%m-%d")` yields midnight, so the session the
user named on the command line contributed no bars. The exchange calendar's
`sessions_between` is inclusive at both ends, so the run and the calendar
disagreed by exactly one session — every recorded run failed with a blocking
`DATA-001`.

This had been wrong in every sweep ever run. It was invisible because nothing
previously compared the run's session count against an independent source;
the run agreed with itself. `DATA-007` deliberately takes `observed_sessions`
from the metric layer rather than from the bars for precisely this reason, and
that choice is what made the bug visible the moment validation was enabled.

### 7.3 A zero-trade run with a moving equity curve

One sweep point (`EOD_Time = 16:00`) reported **0 trades and +$882 of equity
change**. The accounting check rejected it: *money was created or destroyed
outside the trade ledger*.

The check was right, and the cause is the most interesting finding in this
document. Making `eod_time` configurable (§6.1) removed an accident that had
been load-bearing: while the cutoff was hardcoded to 15:55, an intraday
position was *always* closed before the session ended. With the cutoff set to
the closing bell, the EOD rule never fires, and `engine.py` had no other way
to close a position — unlike `portfolio_engine.py`, which has always had a
session-end backstop.

So the position survived overnight. It then blocked every subsequent entry,
because the engine only enters when flat, and it never reached the trade
ledger. The result: a run claiming the strategy never traded, with an equity
curve that moved anyway.

`PortfolioManager.close_open_positions_at_session_end` is the fix, called from
the engine on each session's final bar. It is a correctness backstop, not a
strategy rule — exactly parallel to `portfolio_engine._close_at_session_end`.

The production baseline is **byte-identical** (124 trades, `STOP` 83 / `EOD`
41, over 2022-01-03..2022-06-30), because at 15:55 the EOD rule still fires
first and the backstop is a no-op. Only configurations that were already
silently broken changed.

### 7.4 The recorded window was the trade window

A zero-trade run also could not be *recorded* at all: the run fingerprint took
its first and last session from `result.overall.first_date` / `last_date`,
which are **trade** dates. With no trades there are no dates, and the sink
refused to guess.

Refusing to guess was correct; asking the wrong source was not. Two sweep
points over identical data also described themselves as covering different
windows whenever their first trade fell on different days — so the same
configuration over the same data could produce two distinct runs for a reason
having nothing to do with the configuration.

`BacktestResult.start_date` / `end_date` are the *graded* window the engine
reports after excluding warmup, which is what the split manifest is meant to
pin. The trade dates remain a fallback, and the original refusal still fires
when neither is available.

### 7.5 Result

A 13-point quick sweep over QQQ now records **13 runs, all reaching
`COMPLETED`** — the first validated runs this pipeline has ever produced. The
nested walk-forward script runs per-fold optimization end to end (`TRAIN grid:
2/2 evaluated`, selection margin, plateau size, unseen TEST result) against the
shared evidence module rather than its own copy of the leakage suite.

The 52 legacy rows left at `validating` predate the validator being reachable
and are expected; they cannot be retro-validated because no leakage report was
ever measured for them.

### 7.6 What this generalises to

Every one of §7.1–§7.4 was a defect that produced plausible-looking numbers.
None of them crashed, and none would have been found by reading the code —
§7.3 in particular was *created* by a correct fix removing an accidental
guarantee. They were found because an independent check was finally allowed to
disagree with the thing it was checking.

The corollary is uncomfortable but worth stating: results produced before this
point should be treated as unvalidated, because the mechanism that would have
caught these problems was never switched on.

---

## 8. Re-freezing the ORB golden, and proving the new baseline is the right one

The session-end backstop (§7.3) moved the QQQ ORB golden. A moved golden is
exactly the signal that fixture exists to raise, so the question was never
"make it green" but "is the new number the correct one?". A stale baseline and
a real regression look identical until you look.

### 8.1 What moved

```
n_trades                  1257 -> 1258
exit_reason_counts.EOD     478 -> 480
exit_reason_counts.STOP    779 -> 778
gap_through_exits            3 -> 1
trade_ledger_sha256     62bb5a1b... -> c56f47b8...
equity_curve_sha256     10fc520d... -> 14545b34...
```

Two more EOD exits, two fewer gap-through exits, one extra trade. Over five
years and 1258 trades this is a very small, very specific change -- which is
itself informative. A metric-definition error or an accounting error would
move many trades, not three.

### 8.2 The prediction, and the check that could have refuted it

If the backstop is correct, the extra EOD closes must land only on sessions
where the configured cutoff *never occurs*. `orb_production` sets
`eod_time: 15:55`, so the prediction was specific and falsifiable: the new
exits must fall on sessions whose last bar precedes 15:55, and nowhere else.
Had they appeared on ordinary sessions, the backstop would be firing when the
EOD rule should have, and the fix would be wrong.

Grouping the run's EOD exits by wall-clock time:

```
15:15  x1
15:45  x2
15:55  x477
```

Exactly three exits at a non-standard time:

```
2019-07-03 15:45
2019-12-24 15:15
2020-12-24 15:47  (reported in the 15:45 bucket)
```

Day before Independence Day, and two Christmas Eves -- all NYSE early-close
sessions. Reading the last bar actually present for each session in the
parquet confirms it:

```
2019-07-03  last bar 15:46
2019-12-24  last bar 15:15
2020-12-24  last bar 15:47
every ordinary session  last bar 15:59
```

Those three sessions end *before* the 15:55 cutoff. The EOD rule could never
have fired on them.

### 8.3 Why the old number was the wrong one

On those three days the ORB position was previously carried overnight. That is
not a strategy choice -- the ruleset says flatten at 15:55 -- it is the
simulator failing to honour the rule because the clock ran out first. The
carried position then blocked every subsequent entry, because both engines only
open when flat.

`gap_through_exits` falling from 3 to 1 is the confirming detail, and it is the
part that makes this more than bookkeeping. Two of those overnight carries were
being exited the following morning *through* their stop, at whatever gap the
open presented. Those two fills were pure artifact: trades the strategy had
already ordered closed, priced against a gap it should never have been exposed
to. Removing them removes a small, silent, systematically-wrong-signed source
of P&L.

So the new baseline is not merely different, it is the one that matches the
ruleset. Re-frozen via `GOLDEN_UPDATE=1`, and green on a clean re-run.

### 8.4 What this says about half-days generally

Any rule expressed as a wall-clock time carries the same latent assumption:
that the clock will be reached. Early closes break it roughly nine times a
year, and the failure is silent because the position simply persists. The
backstop is the general answer -- it is a correctness guard keyed to the
session's own final bar rather than to a configured time, so it holds whatever
`eod_time` is set to, including when the EOD rule is disabled entirely.

---

## 9. Candidate gates at fold scope: why the nested walk-forward can almost
never complete

With per-fold optimization working (§7, §8), the nested walk-forward now runs
end to end and stops at `inconclusive`. That is a gate doing its job rather
than a crash -- but the reason it fires is worth stating precisely, because as
currently composed the run is close to unable to succeed.

### 9.1 Making the fold explain itself

The runner printed only the generic reason:

```
Reason: Validation produced non-selectable candidate outcomes ['review_required']
-- Fold 4 [inconclusive]
   no selection: ...
```

The findings existed on `FoldResult.validation_evaluations` and
`FoldResult.test_validation`, but the reporter only walked the TRAIN batch, so
the gate that actually fired was invisible. A code alone is not triageable
either -- whether a plausibility gate is a real signal or a small-sample
artifact depends entirely on the observed value and the sample behind it. The
reporter now prints both:

```
-- Fold 4 [inconclusive]
   ! VALIDATION {'tp': 2.0} -> review_required: PLAUS-001(review)
       PLAUS-001: win_rate=0.65 expected '<= 0.55 unless payoff asymmetry explains it'
```

### 9.2 The gate is correct; the scope it is applied at is not

PLAUS-001 asks "is this win rate too good to believe, given the payoff
profile?" That is a sound question of a full run. Fold 4's VALIDATION window is
**20 sessions**, and the comparable TEST windows in the same run produced 20
trades each.

Over 20 trades, a *true* 55% strategy produces an observed win rate of 65% or
better about a quarter of the time:

```
n=20: P(win_rate >= 0.65 | true 0.55) = 0.252
n=25: P(win_rate >= 0.65 | true 0.55) = 0.242
n=30: P(win_rate >= 0.65 | true 0.55) = 0.135
```

So at fold scope the gate has almost no discriminating power: 0.65 and 0.55 are
not distinguishable from 20 observations. It is not detecting an implausible
result, it is detecting a small sample.

### 9.3 Why that composes into a structural problem

A single non-selectable fold makes the whole walk-forward inconclusive -- which
is the correct rule, since stitching only the folds that happened to pass is
survivorship selection. But combined with a per-fold false-positive rate near
25%, the probability that *no* fold trips the gate by chance is:

```
over  5 folds: 0.2373
over 14 folds: 0.0178
```

A 14-fold run completes under 2% of the time **even if the strategy is exactly
as good as the gate's own null hypothesis**. The pipeline would then be
reporting "inconclusive" as its normal output, and an operator would quickly
learn to ignore it -- which is the worst possible end state for a safety gate.

### 9.4 The fix this points to, and what it is not

The tempting fix is to raise `high_win_rate` or drop the gate at fold scope.
Both are wrong: the first hides genuinely implausible full-run results, and the
second removes the check exactly where overfitting is most likely.

The principled fix is to make the gate measure what it claims to. PLAUS-001
currently compares a point estimate against a threshold with no reference to
sample size, so it cannot distinguish "implausibly high" from "imprecisely
estimated". It should fire only when the observed rate is *statistically
distinguishable* from the threshold at the observed `n_trades` -- i.e. when the
sample can actually support the claim. That is a tightening of the gate's
meaning, not a loosening of its threshold: a 95%-win-rate fold over 20 trades
would still fire, while 0.65-over-20 would not.

Note that `ValidationProfile` already carries the threshold, and the runner
already has a `--min-trades` eligibility floor, so the machinery to express a
sample-aware precondition exists.

### 9.4 Resolved: the gate now measures evidence, not a ratio

This was originally left unimplemented and deferred to the user as a
research-policy decision. **Running the pipeline reframed it as a defect.**

A recorded nested walk-forward on QQQ sent *every* fold inconclusive on
`PLAUS-001: win_rate=0.6428... expected '<= 0.55 unless payoff asymmetry
explains it'` -- nine wins in fourteen trades, which is a thing a coin does
regularly. No fold ever reached a TEST evaluation, so `WHERE segment_role =
'test'` returned nothing. A gate that compares a ratio to a threshold with no
regard for sample size cannot distinguish a suspicious result from noise, it
blocks the entire pipeline, and it trains people to ignore it. That is not a
threshold anyone would want to tune; it is a question asked wrongly.

The fix leaves `high_win_rate` exactly where it is and adds a *second*
condition: the observed wins must also be hard to explain by chance. A
one-sided binomial tail p-value is computed against the threshold as the null
rate, and the finding fires only when that p-value falls below a new
`ValidationProfile.plausibility_alpha` (default `0.05`).

```
 9 / 14   -> p = 0.3373   quiet (noise)
15 / 20   -> p = 0.0553   quiet (just short)
320 / 500 -> p = 0.000027 fires (same 64% rate, real evidence)
550 / 1000-> p = 0.5131   quiet (55% is the null)
```

Note the second and third rows: the win *rate* is effectively unchanged, only
the weight of evidence behind it moves. That is the behaviour the gate always
claimed to have. Payoff asymmetry still excuses a high win rate, so the new
condition tightened the gate's meaning without loosening its threshold.

The tail is summed in log space via `math.lgamma` above a few hundred trades
and falls back to a normal approximation with continuity correction beyond
`n = 20_000`. The naive `(1 - p) ** n` form underflows around `n ~ 550` at
`p = 0.55`, which would have made every long run look *impossible* -- the
opposite failure, and a much quieter one.

**User decision:** retain `plausibility_alpha = 0.05`. It remains an explicit
research-policy knob, not an implicit consequence of a bare threshold.

**Expanded credibility checks.** Calculation correctness now independently
reconciles mean net-R expectancy and session-based Sharpe, and rejects invalid
R/equity populations at every scope (`CROSS-005` through `CROSS-008`).
Candidate/promotion plausibility also reviews expectancy outside `[-5, 5]` R,
absolute Sharpe above 5, one-session absolute-return concentration above 50%,
and near-zero variability with nonzero mean (`PLAUS-004` through `PLAUS-007`).
Return-distribution reviews require at least 20 returns. These configurable
review defaults are broad diagnostic envelopes, not proof of invalidity.
Concentration reports include leave-one-out Sharpe; findings retain their
observed values, bounds and diagnostic context, now inspectable in the local
viewer's Validation tab. Existing persisted runs are not silently revalidated.
Metric version 2 and its first-session exclusion are unchanged.


## 10. Legacy inventory: what should be deprecated or cleaned up

Prompted by the question "is there things we implemented before that should be
deprecated/cleaned up?", this section replaces guesswork with a measured
reachability map. Every module under `vibe/backtester` and
`vibe/research_pipeline` was classified by walking the import graph from two
root sets: `scripts/` (can a person run it?) and the test suites (is it
covered?). Package `__init__` modules are treated as implicitly imported, so
they do not show up as false orphans.

Result over 84 owned modules: **76 reachable from a script, 7 test-only, 1
`__main__`**. The repo is in better shape than the recurring
"built, tested, never wired" theme suggested — but the exceptions matter, and
they are not all the same kind of problem.

### 10.1 Live but wrong — the real cleanup target

`vibe/backtester/analysis/walk_forward.py` is still reachable in production via
`vibe/backtester/optimization/pipeline.py` → `scripts/optimize_strategy.py`. A
previous pass already gave it an accurate module docstring, a runtime
`DeprecationWarning`, honest log lines, and `is_out_of_sample: False` in its
exported dict. Two user-facing surfaces were still lying, and both are fixed
here:

1. **The summary report.** `OptimizationResult.summary()` printed
   `Walk-Forward Score`, `Avg Test Expectancy`, and `Avg Degradation`. That is
   the artifact a person actually reads; the corrected docstring is not in
   front of them when they read it. It now prints
   `Regime-Stability Score (IN-SAMPLE, not walk-forward)`,
   `Avg Later-Window Expectancy`, `Avg Cross-Window Drift`, and an explicit
   pointer to `scripts/run_nested_walk_forward.py`.

2. **The research store — the more serious one.** `_record_fold` recorded the
   second half of every fold as `SegmentRole.TEST`. Its docstring argued this
   prevented confusion. It did the opposite. Parameters reaching this engine
   are chosen by a sweep over the *entire* date range (`pipeline.py:255-266`,
   `pipeline.py:342`), so the later window is in-sample as well. Writing it as
   `TEST` meant the store — and therefore the local viewer being built for
   evaluating runs — would present an in-sample number as out-of-sample
   evidence. Both halves are now recorded as `SegmentRole.TRAIN`, which is
   what they are. The fingerprints stay distinct because the halves span
   different windows and so hash to different `split_manifest_hash` values.

   There are **12 such rows already in the local research store** (6 folds ×
   2 halves), 6 of which were written under the `TEST` role. They predate this
   fix and cannot be relabelled in place without changing their fingerprints.
   They are identifiable by `notes LIKE 'Walk-forward fold%'`.

`vibe/tests/backtester/test_walk_forward.py::test_recorded_folds_are_never_labelled_out_of_sample`
pins the invariant. It was mutation-checked: restoring `SegmentRole.TEST`
fails it.

**Not deleted, deliberately.** `scripts/optimize_strategy.py` is a live entry
point — it was edited during this audit for the `--end` window fix — so
deleting the engine underneath it would break a path someone uses. What the
module measures (one fixed parameter set across different market regimes) is a
legitimate question; only its *name* and its *labels* were wrong. Removal
should wait until `run_nested_walk_forward.py` demonstrably covers the use
case.

### 10.2 The legacy journal is a facade, not a second system of record

**This section originally claimed `vibe/research_journal/` was a live parallel
recording system writing to its own store. That claim is wrong, and it was
about to justify deleting fifteen files. The correction matters more than the
cleanup.**

`vibe/research_journal/` (10 modules) is the predecessor of
`vibe/research_pipeline/`. P11 did not merely migrate the data across -- it
**converted the journal into a facade over the same store**.
`ResearchRegistry.__init__` constructs a `SqliteResearchStore`
(`registry.py:29,61`), and every read and write goes through it:
`create_registry_record`, `update_registry_record`, `get_registry_record`,
`query_registry_records`, `allocate_registry_id`. There is no YAML writer and
no second schema left behind it.

The one place a separate file can appear is `_compatibility_db_path`
(`registry.py:460`), and it is deliberate: an **explicit** `research_root`
outside a git tree gets an isolated side database, which is what tests, tools
and imports want. With `research_root=None` -- the production path, and what
`optimize_strategy.py` passes -- it resolves to `research_db_path()`, the main
research DB.

Measured on this machine:

```
research databases on disk : exactly 1 (AppData/Local/strategy-lab/research.sqlite3)
registry_records           : 0 rows
```

So there is **one** system of record, and the `--journal` flag has never
actually been used. Both halves of the original worry are empty.

**User decision: retain the journal feature.** Keep `vibe/research_journal/`,
its trackers/examples and the `register_in_research_journal` flag as the
SQLite-backed notebook facade. Zero local usage is not grounds to remove a
working user-facing feature. This is not competing persistence.

The lesson generalises: "module X is still imported" establishes reachability,
not duplication. This audit reached "two systems of record" from an import
graph without opening the module or querying the store, and both checks
refuted it in minutes.

### 10.3 Test-only modules — three different dispositions

Being test-only is not by itself a defect. These split into three groups:

| Module | Lines | Disposition |
|---|---|---|
| `vibe/research_pipeline/analysis.py` | 712 | **Wire it, do not delete.** This is the adapter-neutral analysis layer over validated run summaries, mirroring the dashboard's Supabase contract. It is exactly what the local viewer and remote dashboard work needs. Built, tested, never wired — the familiar pattern. |
| `vibe/research_pipeline/universe.py` | 369 | **Keep.** Planned P5b cross-sectional capability, awaiting an entry point. |
| `vibe/backtester/core/portfolio_engine.py` | 1008 | **Keep.** Planned shared-capital multi-symbol simulation, awaiting an entry point. |
| `vibe/backtester/integration/experiment_tracker.py` | 158 | **Retain with 10.2 by user decision.** SQLite-backed journal facade, not duplicate storage. New run evidence uses `research_sink`. |
| `vibe/backtester/integration/sweep_tracker.py` | 284 | **Retain with 10.2 by user decision.** Same journal-facade contract. |
| `vibe/backtester/analysis/regime.py` | 71 | **Removed.** Standalone ADX classifier superseded by the reachable `vibe/backtester/analysis/regime_research/` package (`labeler`, `features`, `filter_evaluator`, `attribution`, `reporting`). Deleted with its test; nothing else imported it and it was not re-exported from the package `__init__`. |

The two trackers are also demonstrated by `examples/backtest_experiment_tracking.py`
and `examples/parameter_sweep_experiment_tracking.py`, which must be removed in
the same change or they will import a deleted module.

### 10.4 Deliberate hard-failure shims — keep

`SqliteResearchStore.enqueue_publication` raises `StoreError` by design. It
looks like dead code to a caller search, but it is a load-bearing guard that
stops legacy callers supplying arbitrary publication payloads; the real path is
`_enqueue_run_publication_tx`, called atomically from `finalize_validation()`.
Do not "clean this up".

### 10.5 A gap this survey exposed, relevant to the dashboard work

`segment_role` is accepted by `ResearchRunSink.record()` and flows into
`recording.py:290` — but **only to compute `split_manifest_hash`. It is never
persisted as a queryable column.** The `runs` table has no role column, and no
table in the store does.

Two consequences:

- The store cannot answer "show me only out-of-sample runs." For a dashboard
  whose entire purpose is judging whether results are trustworthy, that is the
  single most important filter, and it is currently impossible without parsing
  the free-text `notes` field.
- `segment_role` defaults to `SegmentRole.TEST` in both `recording.py:248` and
  `recording.py:549`. Every ordinary `record()` call that does not pass a role
  is fingerprinted as a test-role run. The default should be the conservative
  one.

Promoting `segment_role` to a first-class indexed column is a schema migration
(v7) and new capability rather than cleanup, so it is recorded here as a
prerequisite for the viewer/dashboard work rather than executed in this pass.

**Update — implemented as schema v10.** `runs.segment_role` is now a persisted,
indexed column, so a viewer can filter out-of-sample runs with a `WHERE`
clause instead of parsing notes. Two details were load-bearing:

- **Existing rows backfill to NULL, not to a guess.** NULL means "recorded
  before the role was persisted". Defaulting them to `test` would manufacture
  out-of-sample evidence that was never established.
- **The fingerprint default is unchanged.** `record()` still hashes an
  undeclared run as `SegmentRole.TEST` so identities already in the store
  continue to hash the same way, but it now writes NULL to the *column*. The
  two were deliberately decoupled: changing the hash default would have
  orphaned every existing run from its own future re-runs, while keeping the
  column default would have handed the viewer every exploratory backtest as
  out-of-sample evidence.

Verified against a copy of the live database: v9 → v10 applies cleanly, all
117 runs preserved, index created, re-migration idempotent. Pinned by
`test_segment_role_is_queryable_not_buried_in_the_fingerprint`,
`test_persisting_the_role_did_not_move_any_fingerprint`, and
`test_an_undeclared_run_is_not_recorded_as_out_of_sample`; the first was
mutation-checked by coercing NULL to `test`.

**Cleanup decision.** The user permits deleting old experimental runs instead
of quarantining them. No deletion is required to enable the new checks, and
this credibility-check update leaves the existing database intact. NULL roles
remain unknown, not evidence of corrupt results; viewing a run does not
revalidate or rewrite its historical report.

**Update 2 — the role is now analysable, not just filterable.** Persisting the
column and exposing it in the list view still left the *analysis* layer blind:
`SqliteAnalysisAdapter.field_catalog()` reported 77 fields and none of them was
the role, so every grouped query silently pooled train, validation and test
into one population. A dashboard that can filter by role in one view and
average across roles in another is worse than one that cannot do either,
because the second view looks authoritative.

`dataset.segment_role` is now projected as a dimension. Because the catalog is
*inferred from rows* rather than declared as a fixed UI list, the field becomes
groupable, filterable and screenable everywhere at once, with no frontend
change.

NULL maps to the string `"unlabelled"` rather than being left as None, for the
same reason the backfill chose NULL: an unnamed group reads as missing data,
when what it actually means is "provenance unknown". The spelling matches the
vocabulary the viewer filter already uses.

Verified against the live 151-run database through the full Node → bridge →
adapter path. Grouping by role gives:

| role | mean expectancy | runs |
| --- | --- | --- |
| train | +0.005R | 15 |
| validation | **+0.179R** | 15 |
| test | **−0.036R** | 4 |

That spread is the entire argument for the dimension. Validation looks like a
working strategy; the genuinely unseen test window is negative — the textbook
overfitting signature. Pooled, these 147 runs average to a single number that
means nothing. Pinned by `test_segment_role_is_analysable`,
`test_unlabelled_runs_group_under_a_named_bucket`, and
`test_analyses_can_isolate_out_of_sample_runs`, each mutation-checked.

### 10.6 The inversion that matters most for the viewer

Having made the role queryable, the obvious next question is what the store
would actually return. The answer is the sharpest finding in this section:

| Path | Correct methodology? | Writes to the research store? |
|---|---|---|
| `vibe/backtester/analysis/walk_forward.py` (legacy) | No | **Yes** — 12 rows |
| `vibe/research_pipeline/walk_forward.py` (genuine) | Yes | **No — nothing at all** |

`NestedWalkForwardDriver` and `scripts/run_nested_walk_forward.py` contain no
reference to a run sink, a store, or `record()`. The nested walk-forward —
the only path that performs per-fold optimization, holds out a real test
window, and enforces purge/embargo — leaves no trace in the research store.

So every row in the store that was ever labelled out-of-sample came from the
path that could not produce out-of-sample evidence, and every genuinely
out-of-sample result produced by this repo exists only in console output.

This is not cleanup; it is the missing wiring that the viewer work depends on,
and it should be the next increment. Recording a fold properly means one run
per evaluation with its true `SegmentRole` (TRAIN / VALIDATION / TEST), which
is now expressible because the column exists.

**Update — wired.** `scripts/run_nested_walk_forward.py` gained `--record-runs`
(and `--record-test-only` for large grids). The approach matters as much as
the result: `NestedWalkForwardDriver` stays pure, performing no I/O. Instead
`vibe/research_pipeline/recording_executor.py` decorates the
`CandidateExecutor` the driver was already given.

That placement is what makes the labels trustworthy. Every evaluation a
walk-forward performs — each training candidate, each validation candidate,
and the one frozen test run — passes through the executor exactly once, and
`SessionSegment` already carries its own `SegmentRole`. The role is therefore
*read off the segment being evaluated* rather than asserted by the caller, so
a run cannot be filed under a role it was not run on. That is precisely the
failure mode of §2, reintroduced structurally impossible.

Supporting changes:

- `RulesetSegmentExecutor.effective_ruleset()` was extracted from `__call__`
  and made public, so a recorded run pins the configuration that actually
  executed. Rebuilding it at the call site would let the recorder and the
  engine drift into disagreeing about what ran; recording the *base* ruleset
  instead would file every swept point under identical settings. An executor
  that cannot supply it is refused at construction rather than after a
  multi-hour walk-forward.
- A store that is unreachable or mid-migration cannot alter a research result:
  recording failures are logged and collected, and the inner result is
  returned untouched. An infrastructure fact must not become a research
  finding.

Pinned by `tests/unit/research_pipeline/test_recording_executor.py`;
`test_each_segment_is_recorded_under_the_role_it_actually_ran_on` was
mutation-checked by hard-coding `SegmentRole.TEST`, which fails it.

Still open: the 12 legacy rows remain in the store (now correctly labelled
TRAIN, see §10.5), and no recorded nested walk-forward exists yet because
this worktree has no materialized parquet.

## 11. Experiment-first local inspection

The user identified a presentation and provenance gap: a controlled question
such as "ORB duration 5 / 15 / 30" appeared as many unrelated train/validation/test
rows. The local viewer now makes the **experiment** the primary review object.
Each experiment declares its axes, candidate grid, objective and split plan;
each fold aligns training evidence, validation comparison/selection and the
frozen held-out test. Segment charts and trades remain drill-downs.

Schema v11 persists `research_experiments` and explicit
`research_experiment_runs` membership, outside the run fingerprint. A rerun
gets a new experiment ID while identical segment runs may be reused. The
recording decorator attaches run IDs during evaluation; the composition root
persists final fold outcomes and selector rationale. No persistence was added
to the pure driver. Test-only retention preserves comparison summaries while
truthfully reporting unavailable training/validation run details.

The UI explicitly states the current policy: training scores every candidate
but does not performance-shortlist; validation selects a plateau medoid; test
evaluates only the frozen setting. Study grading and individual recorded-run
lifecycle validation are separate facts. Missing run validation no longer
produces a green "No blocking findings" badge.

No experiment membership or past selection rationale is inferred for historical
runs. Interrupted or failed studies, recording errors, untested settings and
missing/oversized reports remain visible rather than becoming successful-looking
aggregates. Local synthetic preview data is isolated from the real research DB
and explicitly labelled; it does not establish strategy performance.
