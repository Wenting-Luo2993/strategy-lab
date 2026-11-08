# ADR-018: Research Pipeline Contracts and Run Identity

**Date**: 2026-09-09

**Status**: ✅ Accepted

## Context

An independent design review of the backtest research pipeline found that
existing results cannot be trusted for reasons unrelated to strategy quality:

- **Identity is under-specified.** `ParameterSweep._cache_key` hashes the
  ruleset *filename* plus a few scalars. Editing a non-swept field of a ruleset
  returns a cached result computed under the old rules, silently.
- **Runs complete without validation.** A run producing impossible metrics
  wrote a `completed` record indistinguishable from a good one.
- **Metrics disagree with themselves.** Winners are `r > 0` in
  `performance.py` while losers are `r < 0` in `parameter_sweep.py`, so
  exactly-zero-R trades vanish. Max drawdown is a fraction rendered as dollars.
- **Splits use calendar arithmetic** (`months * 30`), so nominally equal folds
  contain different numbers of trading sessions.
- **The headline correctness check was tautological.** `equity == cash + Σ mtm`
  cannot fail, because `Portfolio.update_equity` computes equity that way.

These need a shared vocabulary before any of the fixes can proceed in parallel.

## Decision

Introduce `vibe/research_pipeline/` as a contracts-only package (increment P0):
canonical hashing, run lifecycle, core contract models, `RunFingerprint`,
OneDrive-safe database paths, and a `ResearchStore` protocol. No execution
logic.

Key rules encoded structurally rather than by convention:

| Rule | Mechanism |
|---|---|
| A run cannot skip validation | No `RUNNING -> COMPLETED` edge exists |
| Known-wrong results cannot be laundered | No `VALIDATION_FAILED -> COMPLETED` edge |
| Caches cannot go stale | `cache_key() == fingerprint`, which covers ruleset content, code commit, feature/execution/metric versions, data snapshot, split and universe hashes |
| Causal features cannot look ahead | `FeatureDeclaration` rejects `CAUSAL` with `lookahead_bars > 0` |
| Survivorship bias cannot be implicit | `UniverseSpec` forces `static_declared` to declare `survivorship_bias=present` |
| Money cannot appear from nowhere | `RunEvidence` asserts `gross - costs == net` and `Δequity == net` |
| Identity cannot vary by machine | Naive datetimes, non-finite floats, and OS-specific path separators are rejected at encode time |

## Alternatives Considered

- **Extend `vibe/research_journal` models in place** — Rejected. The journal
  records *what was tried*; this package constrains *what may be believed*.
  Mixing them would make the YAML-to-SQLite migration harder.
- **Start with the SQLite store (P7) first** — Rejected. Storage without an
  identity definition just persists ambiguity faster.
- **Skip the contracts layer, fix defects directly** — Rejected. Five
  workstreams need the same vocabulary; without it they cannot run in parallel.
- **Reuse `ExperimentStatus`** — Rejected. It lacks the states that carry the
  guarantee (`VALIDATING`, `REVIEW_REQUIRED`, `VALIDATION_FAILED`,
  `INCONCLUSIVE`).

## Reasoning

Declaring contracts up front is what makes the remaining increments
parallelizable. The state machine and validators turn documented rules into
failures at construction time, which is the only form of rule that survives
contact with a deadline.

## Consequences

- ✅ Stale-cache and skipped-validation classes of bug become unexpressible
- ✅ Independent workstreams (splits, execution realism, storage, dashboard)
  can proceed against fixed shapes
- ✅ Cross-device reproducibility: identity is machine-independent by construction
- ⚠️ Existing runs predate the fingerprint and are stamped
  `methodology_version = "legacy-uncontrolled"`
- ⚠️ P1/P2 will change existing metric values; a golden file is frozen first
- ⚠️ `point_in_time_screened` universes are rejected until delisted history exists

## Related Files

- `vibe/research_pipeline/` (hashing, lifecycle, contracts, identity, paths, store)
- `tests/unit/research_pipeline/` (102 tests)
- `docs/backtest-research-summaries/2026-09-09-backtest-pipeline-implementation-plan.md`
- `memory-bank/features/portfolio-simulation-constraint.md`
