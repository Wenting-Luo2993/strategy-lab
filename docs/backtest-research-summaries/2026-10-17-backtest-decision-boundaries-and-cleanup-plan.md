# Backtest decision boundaries and package cleanup plan

**Date:** 2026-10-17  
**Status:** Plan only; no implementation is authorized by this document  
**Scope:** Engine-integrity ownership, research-reliability ownership, package
boundaries, and reconciliation of remaining work from the September 9 plan and
October 14 architecture audit

## 1. Decision

The backtest system needs two separate deterministic decisions:

1. **Did the engine run correctly?**
   - Owned by `vibe/backtester`.
   - Judges arithmetic, accounting, execution mechanics, and simulator
     invariants.
   - Produces an `EngineIntegrityReport`.

2. **Is the result reliable and stable?**
   - Owned by `vibe/research_pipeline`.
   - Judges methodology, data and split validity, sufficiency, out-of-sample
     evidence, robustness, stability, and pre-registered research acceptance.
   - Produces a `StudyReliabilityReport`.

`vibe/research_journal` remains a notebook and registry facade over the shared
SQLite research store. It does not execute simulations and does not decide
either verdict.

This separation is necessary because a correctly executed simulation can
produce evidence that rejects the hypothesis. The current real ORB-duration
experiment is the reference example:

- engine and persisted run validation: passed;
- procedure execution: completed;
- stitched held-out expectancy: approximately `-0.0245R` over 80 trades;
- research conclusion: the evidence does not support promotion.

`completed` must therefore never be used as a synonym for `reliable`.

```mermaid
flowchart LR
    A[Research question and pre-registered policy] --> B[research_pipeline]
    B --> C[backtester]
    C --> D{Engine integrity}
    D -->|Fail| E[Invalid simulation]
    D -->|Pass| F[Metrics and execution evidence]
    F --> B
    B --> G{Research reliability}
    G -->|Reliable| H[Eligible for next stage]
    G -->|Rejected| I[Hypothesis rejected]
    G -->|Inconclusive| J[More evidence required]
    G -->|Review required| K[Human investigation]
```

## 2. Target package responsibilities

### `vibe/backtester`

Keep the deterministic simulation kernel here:

- market-data loading needed by simulation;
- signal evaluation and order generation;
- order queues, fills, volume, impact, commissions, and slippage;
- single-symbol and shared-capital portfolio execution;
- positions, cash, buying power, exposure, and equity;
- trade, equity, and normalized metric calculation;
- accounting reconciliation;
- execution and metric invariants;
- the structured engine-integrity verdict.

The backtester must not know about SQLite research storage, Supabase,
experiment registries, publication outboxes, hypotheses, or dashboard state.

### `vibe/research_pipeline`

Keep research methodology and orchestration here:

- study requests and immutable research-policy identity;
- temporal split planning, purge, embargo, and warmup;
- feature availability and leakage checks;
- optimizer, selector, and frozen-candidate evaluation;
- nested walk-forward and stitched held-out evidence;
- data-integrity and dataset-identity validation;
- sufficiency and plausibility diagnostics;
- sensitivity, robustness, regime, and portfolio study orchestration;
- final-holdout authorization and pre-registered acceptance;
- the structured study-reliability verdict;
- run/experiment persistence, publication, and analysis adapters.

The research pipeline must require a passing engine-integrity report before it
uses a run for selection, aggregation, or reliability evaluation. It must not
reimplement accounting rules owned by the backtester.

### `vibe/research_journal`

Keep the user-facing notebook facade:

- hypotheses;
- experiment notes and conclusions;
- rejected ideas;
- narrative observations;
- lineage and artifact references.

It continues to use the same SQLite research store as
`vibe/research_pipeline`; it is not a second system of record.

## 3. Decision block 1: engine integrity

### 3.1 Existing foundation

The core behavior already exists:

- independently observed cash ledger entries;
- final-equity versus realized-P&L reconciliation;
- per-fill cash-delta reconciliation;
- entry/exit/open-quantity parity;
- gross P&L, costs, and net P&L reconciliation;
- cash and leverage limits;
- execution-model version evidence;
- trade census and R-sample checks;
- expectancy and session-Sharpe recomputation;
- equity-curve structural checks;
- single-symbol and portfolio execution diagnostics.

The latest nested-run lifecycle fix also makes these checks reachable from real
persisted nested studies.

### 3.2 Remaining gap

Ownership is currently wrong and diagnostic detail is incomplete:

- several engine-level checks live in
  `vibe/research_pipeline/validation.py`;
- `BacktestResult.execution_diagnostics` retains only aggregate reconciliation
  counts rather than each reconciliation identity, residual, and explanation;
- the research pipeline must reconstruct the engine-correctness verdict from
  loosely related result fields;
- `RunState.COMPLETED` currently mixes execution validity with broader research
  validation terminology.

### 3.3 Planned contract

Add an immutable `EngineIntegrityReport` to every current backtest result:

```text
EngineIntegrityReport
├── status: PASS | FAIL
├── engine_version
├── metric_calculation_version
├── checked_categories
└── findings[]
    ├── check_id
    ├── category
    ├── observed
    ├── expected
    ├── residual
    ├── applicable
    └── context
```

Backtester-owned categories:

- metric mathematics;
- cross-metric consistency;
- equity-series consistency;
- accounting;
- execution mechanics;
- cash, leverage, and position invariants;
- required session-end state;
- deterministic execution-model identity.

An absent report is not a pass. New research execution must fail closed when
engine-integrity evidence is missing.

## 4. Decision block 2: research reliability

### 4.1 Existing foundation

The pipeline already produces much of the required evidence:

- exchange-session train/validation/test splits;
- purge and embargo;
- warmup exclusion;
- full-grid training evaluation;
- connected-plateau validation selection;
- frozen test evaluation;
- all-fold, non-overlapping stitched OOS evidence;
- data-integrity checks;
- six required leakage checks;
- feature-registry identity;
- minimum-trade and minimum-session checks;
- plausibility diagnostics;
- locked final holdout and append-only access log;
- a pre-registered final-holdout acceptance rule.

### 4.2 Remaining gap

There is no deterministic study-level reliability decision:

- a study becomes `completed` when every fold executes and its individual run
  validation passes;
- stitched OOS evidence is displayed but is not judged against a registered
  reliability policy;
- fold consistency and validation-to-test degradation are not gated;
- parameter-selection stability is recorded but not judged;
- generic acceptance findings are advisory and cannot reject a study;
- the final-holdout acceptance evaluator exists but has no supported execution
  command that applies and persists the verdict;
- robustness is not part of nested-study completion;
- parameter perturbation and random subsampling remain unimplemented in the
  legacy robustness analyzer.

### 4.3 Planned contract

Add an immutable, versioned `ReliabilityPolicy` before a study begins. It may
declare:

- minimum stitched OOS trades and sessions;
- minimum stitched OOS expectancy;
- optional Sharpe, drawdown, payoff, or cost constraints;
- minimum acceptable fold consistency;
- maximum validation-to-test degradation;
- acceptable parameter-selection movement across folds;
- plateau width and selection-margin requirements;
- required cost/slippage stress survival;
- required parameter-neighborhood robustness;
- regime coverage and return-concentration bounds;
- whether shared-capital portfolio evidence is required;
- which findings require review rather than rejection.

The evaluator returns:

```text
StudyReliabilityReport
├── verdict: RELIABLE | REJECTED | INCONCLUSIVE | REVIEW_REQUIRED
├── policy_hash
├── engine_integrity_complete
├── stitched_oos_summary
├── fold_stability_summary
├── robustness_summary
├── holdout_summary
└── criteria[]
    ├── criterion_id
    ├── outcome
    ├── observed
    ├── expected
    └── evidence references
```

Human interpretation of why a hypothesis failed remains outside the
deterministic evaluator. The dashboard supplies the evidence; the researcher
and assistant form or revise the hypothesis.

## 5. Methodology freeze and final holdout

The October 14 audit's methodology-freeze gap remains open.

Current immutable fingerprints and experiment definitions make changes
detectable, but there is no explicit action that says:

> This methodology and reliability policy are frozen; unseen evaluation may
> now begin.

Plan:

1. Introduce a frozen study-definition identity containing:
   - strategy/ruleset identity;
   - universe and dataset IDs;
   - parameter axes;
   - split and warmup policy;
   - execution and metric versions;
   - leakage requirements;
   - reliability policy;
   - final-holdout lock hash.
2. Persist a `frozen_at`, `frozen_by`, and immutable definition hash.
3. Refuse final-holdout execution for an unfrozen definition.
4. Add a dedicated final-holdout command requiring an attributed unlock token.
5. Apply the committed acceptance rule exactly once.
6. Persist `PASSED`, `FAILED`, or `INCONCLUSIVE` separately from ordinary run
   completion.
7. Preserve the existing lineage touch-count promotion block.

## 6. Robustness completion

Replace the current disconnected robustness score with reproducible evidence
owned by `research_pipeline`.

Required slices:

1. **Execution-cost stress**
   - declared slippage and commission envelope;
   - pass/fail against conservative cost assumptions.
2. **Parameter-neighborhood robustness**
   - deterministic adjacent values;
   - cliff and sign-change detection;
   - reuse selector plateau topology.
3. **Temporal stability**
   - positive/negative fold balance;
   - worst-fold behavior;
   - validation-to-test degradation;
   - dependence on a single period.
4. **Regime stability**
   - predeclared regime labels;
   - coverage and concentration;
   - no ex-post regime selection.
5. **Seeded subsampling or bootstrap diagnostics**
   - deterministic seed and sample identity;
   - advisory unless a justified policy is pre-registered.
6. **Multiple-testing diagnostics**
   - retain `n_candidates_evaluated`;
   - add deflated Sharpe or probability-of-backtest-overfitting only when its
     policy and interpretation are explicitly adopted.

## 7. Portfolio integration

Shared-capital portfolio simulation exists and is tested, but it has no
standard research entry point in `scripts/` and is not composed into nested
study reliability.

Plan:

- add a portfolio segment executor implementing the same research-pipeline
  candidate contract;
- persist pooled and per-symbol evidence under one experiment;
- require shared-capital evidence for claims scoped to multiple symbols;
- include concentration, contention, leverage, incomplete sessions, and
  rejected signals in the reliability policy;
- keep single-symbol execution available for explicitly single-symbol
  hypotheses;
- do not mistake independent per-symbol capital resets for a portfolio.

## 8. Package cleanup

### 8.1 Keep in `vibe/backtester`

- `core/engine.py`
- `core/portfolio_engine.py`
- `core/portfolio.py`
- `core/reconciliation.py`
- `core/execution/`
- execution realism, commissions, slippage, and fill simulation;
- simulator-facing data loading;
- metric and performance calculation;
- the new integrity contracts and checks.

### 8.2 Move to `vibe/research_pipeline`

Research-specific modules currently under `vibe/backtester`:

- `analysis/research_sink.py`;
- `analysis/research_evidence.py`;
- parameter-sweep orchestration;
- sensitivity orchestration;
- robustness orchestration;
- regime-research orchestration;
- optimization scoring and parameter-surface analysis;
- research-window helpers that are not engine primitives.

Moves must preserve import compatibility for one release when an external
caller may exist. New code must import the new locations.

### 8.3 Delete after migration

- deprecated fixed-parameter `analysis/walk_forward.py`;
- obsolete `OptimizationPipeline` integration with that module;
- superseded wrappers and duplicate command-line entry points;
- compatibility exports after their announced removal window.

The fixed-parameter cross-period comparison is legitimate only if retained
under a truthful regime-comparison name. It must not continue under
“walk-forward” terminology.

### 8.4 Retain or relocate

- keep `research_journal` as the SQLite-backed notebook facade;
- move its backtester integration trackers to the journal or pipeline adapter
  layer if they remain supported;
- retain hard-failure compatibility shims that prevent unsafe publication.

### 8.5 Dependency rule

Add an architecture test enforcing:

```text
research_pipeline -> backtester
research_journal -> research_pipeline storage
backtester -X-> research_pipeline
```

The current reverse imports from backtester research adapters must disappear.

## 9. Persistence and dashboard

Use a forward-only migration after schema v11.

Persist separately:

- engine-integrity reports keyed by run;
- study-reliability reports keyed by experiment;
- reliability-policy identity and hash;
- structured criterion outcomes;
- robustness evidence and provenance;
- methodology-freeze identity.

Historical records are not automatically reclassified. Where evidence was not
captured, display `Not assessed`.

Dashboard presentation:

- **Engine integrity:** Passed / Failed / Not assessed.
- **Procedure execution:** Completed / Failed / Interrupted.
- **Research reliability:** Reliable / Rejected / Inconclusive / Review.
- detailed accounting identities and residuals for engine failures;
- failed reliability criteria, folds, and robustness evidence for rejected
  studies;
- no green “completed” presentation that implies economic validation.

## 10. Reconciliation of the September 9 implementation plan

### Implemented or superseded

The following original gaps are implemented in the current branch:

- SQLite research storage, migrations, leases, evidence, retention, and
  publication outbox;
- metric normalization;
- execution realism, costs, buying power, and accounting reconciliation;
- exchange-session split planning, warmup, purge, and embargo;
- feature declarations and leakage harness;
- cross-sectional universe analysis;
- run validation lifecycle;
- genuine nested walk-forward entry point;
- segment-role persistence;
- explicit parent experiments and fold/run membership;
- final-holdout lock and repeated-touch promotion block;
- shared-capital portfolio engine;
- local SQLite viewer and experiment-first inspection;
- remote dashboard adapters and flexible analyzer implementation;
- approved-dataset manifest, verification, cache, and synchronization tooling;
- artifact staging, fenced outbox, reconciliation, and safe preview contracts;
- legacy YAML archival and active-tree removal.

### Still open in code or composition

1. The two decision reports and their ownership boundary.
2. Study-level reliability evaluation.
3. Explicit methodology freeze.
4. Supported final-holdout execution and persisted acceptance verdict.
5. Full robustness composition and missing perturbation/subsample methods.
6. Portfolio research entry point and nested reliability integration.
7. Removal or truthful renaming of the deprecated fixed-parameter
   walk-forward path.
8. Package relocation and one-way dependency enforcement.
9. Detailed engine-integrity persistence and dashboard presentation.
10. A typed study/run request remains absent. This is low priority and should
    be introduced as part of the frozen study-definition contract rather than
    as a cosmetic wrapper.
11. The local retention pruning policy for diagnostic/full trade and equity
    rows remains a product-policy decision.
12. Point-in-time universe membership, delistings, and corporate-action
    history remain incomplete. Static declared universes must retain their
    survivorship-bias badge.
13. Slippage calibration against live fills remains a data limitation.
14. Repeatable checked-in browser coverage for the nested experiment/run
    dialog remains desirable.

### External activation gates

These are not missing deterministic core code, but the system is not fully
operational across devices until they are completed:

- confirm Databento private multi-device redistribution entitlement;
- record owner approval for dataset publication;
- deploy and independently review Supabase schemas, RLS, RPCs, and private
  buckets;
- provision dedicated server-side credentials;
- deploy and review the signed-preview Edge Function;
- backfill and reconcile terminal research publications;
- validate anonymous multi-page dashboard reads;
- load-test cardinality, response size, timeouts, and query plans;
- test interrupted dataset/artifact upload and reconciliation recovery;
- verify dashboard CSP origins;
- activate the production `/research` data source rather than fixture mode.

The concrete remote artifact backend exists in commit `48b785d` on repository
history, with a later equivalent implementation on
`wenting-luo2993-remote-artifact-storage`; deployment and security gates still
remain. No licensed data or artifact bytes should be uploaded before those
gates pass.

## 11. Reconciliation of the October 14 architecture audit

| Audit item | Current status |
| --- | --- |
| G1 genuine nested walk-forward unreachable | Resolved |
| G2 legacy walk-forward presented as OOS | Labels corrected and deprecated; deletion/renaming remains |
| G3 legacy rows asserted TEST provenance | New writes fixed; historical data remains historical |
| G4 unstable degradation ratio | Guarded in legacy path; disappears when legacy path is removed |
| G5 approximate calendar windows | Genuine path resolved; remains only in deprecated path |
| G6 methodology freeze | Still open |
| G7 typed run request | Still open, low priority; fold into frozen study definition |
| G8 inert sweep axes | Resolved for known gated axes |
| Validator reachability | Resolved, including persisted nested segment finalization |
| Queryable/analysable segment role | Resolved |
| Genuine nested recording | Resolved |
| Explicit parent experiments | Resolved |
| Experiment-first local viewer | Resolved |
| Analysis adapter was test-only | Resolved; local and remote analyzer surfaces use it |
| Universe capability awaiting use | Core capability implemented; broader point-in-time universe remains open |
| Portfolio engine awaiting use | Engine implemented; supported research entry point remains open |
| Journal duplication concern | Resolved as a false alarm; retain the facade |

## 12. Implementation sequence

1. **Contracts and terminology**
   - Define engine-integrity and study-reliability reports.
   - Define frozen study identity and reliability policy.
   - Pin lifecycle vocabulary with tests.

2. **Backtester integrity ownership**
   - Move engine-level checks.
   - Preserve structured reconciliation findings.
   - Attach the report to single-symbol and portfolio results.

3. **Research-pipeline reliability**
   - Require engine-integrity pass.
   - Evaluate stitched OOS and fold stability.
   - Persist the study verdict.

4. **Robustness completion**
   - Cost stress, parameter neighborhoods, temporal/regime stability, and
     deterministic subsampling.

5. **Methodology freeze and final holdout**
   - Freeze command, authorized final runner, acceptance verdict, and
     one-touch enforcement.

6. **Portfolio composition**
   - Research entry point, experiment persistence, and reliability criteria.

7. **Storage and dashboard**
   - Forward-only migration and separate integrity/reliability presentation.

8. **Package cleanup**
   - Move research adapters and orchestrators.
   - Remove the deprecated walk-forward path.
   - Enforce one-way imports.

9. **Remote activation lane**
   - Licensing, deployment, credentials, reconciliation, load testing, and
     recovery drills.

## 13. Required acceptance tests

- Mutating each accounting identity causes engine-integrity failure.
- Missing engine-integrity evidence fails closed in the research pipeline.
- Single-symbol and portfolio engines implement the same integrity contract.
- A failed engine result cannot enter optimization or selection.
- Reliability thresholds cannot change after study freeze.
- Negative stitched OOS evidence produces `REJECTED`, not `RELIABLE`.
- Too little evidence produces `INCONCLUSIVE`.
- Suspicious but non-dispositive evidence produces `REVIEW_REQUIRED`.
- A study satisfying every registered criterion produces `RELIABLE`.
- The current ORB-duration fixture yields:
  - engine integrity `PASS`;
  - procedure `COMPLETED`;
  - reliability `REJECTED`.
- Holdout access and acceptance are recorded exactly once.
- Multi-symbol claims require shared-capital evidence.
- Historical records without new reports remain `Not assessed`.
- `vibe/backtester` cannot import `vibe.research_pipeline`.
- Existing production golden results remain unchanged unless an intentional
  execution-model version change is approved.

## 14. Explicit non-goals

- Automatically explaining the economic reason a hypothesis failed.
- Automatically rewriting or tuning the strategy after rejection.
- Treating one profitable fold as reliable evidence.
- Treating procedure completion as strategy validation.
- Using the final holdout repeatedly during development.
- Uploading licensed market data before entitlement and security approval.
- Deleting `vibe/backtester`; it remains the execution kernel.

