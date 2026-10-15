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
