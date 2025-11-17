# Snapshot Testing TODO Tracking

This document centralizes implementation tasks referenced by `snapshot_testing_requirements.md`. Each item has an ID for cross-referencing in commits and PRs.

## Legend

- Status: NOT STARTED | IN PROGRESS | BLOCKED | DONE
- Env Flags: `UPDATE_SNAPSHOTS=1` (regenerate), `SNAPSHOT_VISUALIZE=1` (generate HTML diff), future flags TBD.

## Core Tasks

1. Requirements doc (DONE) – initial specification established.
2. Data extraction script – implement `extract_fixture_data.py` using `resolve_workspace_path`.
3. Finalize snapshot file formats – confirm column set & naming convention (signals/trades per test context).
4. Identify strategies to cover – enumerate initial strategy list (orb-strategy + others) & map to fixtures.
5. Design orchestrator snapshot tests – define end-to-end test flow & parametrization.
6. Implement fixture generator – actual code for extraction script with metadata.json.
7. Implement snapshot generation utility – `generate_snapshots.py` with validate vs update mode.
8. Snapshot comparison helpers – utilities for DataFrame vs CSV diff with tolerances.
9. Integrate snapshots into existing tests – modify `test_orb_indicator.py`, `test_orb_strategy.py`, add orchestrator test.
10. Risk-managed order checks – include position sizing & stop logic in trade snapshot comparison.
11. Config hashing function – deterministic SHA256 over canonical JSON of configs.
12. CI governance integration – include snapshot validation in pre-commit + full CI pipeline.
13. Document workflow in README – add contributor instructions for snapshot lifecycle.
14. Edge case handling – missing days, partial sessions, zero-signal scenarios, high precision prices.
15. Performance considerations – keep fixtures minimal & caching strategy for reused computations.
16. Metadata schema – finalize & enforce keys (version, commit, created_at, generator, config_hash, tickers, dates).
17. Diff reporter – produce concise textual diff + limited row detail (first N mismatches).
18. Tolerance settings – central constants for numeric comparisons & allowable timestamp drift (likely zero).
19. Deterministic sorting utilities – shared function to sort outputs before snapshot write / compare.
20. Security & privacy review – ensure no secrets or creds leak into fixtures or metadata.
21. Snapshot diff visualization (HTML) – initial implementation guarded by `SNAPSHOT_VISUALIZE` flag.
22. Auto-create missing snapshots – if fixture exists but snapshot absent, create automatically (unless disabled by flag).
23. Stale snapshot detection – flag & optionally delete snapshots not referenced by any test (Jest-like behavior, gated by `SNAPSHOT_PRUNE=1`).
24. Workspace path enforcement – audit scripts to ensure all path resolution uses `resolve_workspace_path`.
25. Guard rails for future test generation – guidelines & checks preventing inadvertent snapshot bloat or misuse.
26. Architecture diagram maintenance – keep diagram in sync when flow changes.

## Extended / Future Tasks

- Parquet support for very large datasets (defer until size pressure observed).
- Portfolio-level aggregated snapshots.
- Metrics snapshots (performance KPIs) once signal/trade layer is stable.
- CHANGELOG integration for snapshot format version bumps.

## Proposed Implementation Order

1 -> 2 -> 3 -> (shared foundation) -> 6 -> 11 -> 19 -> 7 -> 8 -> 9 -> 10
Parallel: 14, 18 early; 12 after 7–9; 21–23 after baseline stability.

## Acceptance Gates Per Task

- 2: Script generates deterministic identical fixture twice.
- 7: Update mode writes files; validate mode passes unchanged.
- 8: Failing diff shows limited mismatch set & helpful regeneration hint.
- 11: Hash changes only when config content changes; invariant to key ordering.
- 21: HTML report generated only with flag; includes summary + per-row diffs.
- 23: Running prune mode lists stale snapshots & exit code signals if any found.

## Open Dependencies

- Strategy configuration object shape (needed for hashing) – confirm canonical serialization.
- Risk management configuration availability in tests – ensure accessible fixture or factory.

## Flags & Environment Variables

- `UPDATE_SNAPSHOTS=1` – allows regeneration.
- `SNAPSHOT_VISUALIZE=1` – produce HTML diff artifacts.
- `SNAPSHOT_PRUNE=1` – enable stale snapshot pruning.

## Governance Notes

- Any snapshot format change must bump `generator_version` and update README.
- Automatic creation is silent but logged; creation events should be reviewed in PR diff.
- Pruning never deletes by default unless explicitly flagged to avoid accidental data loss.

## Risk Mitigations

- Floating point differences: enforce rounding & tolerance compare.
- Hidden randomness: seed all random or pseudo-random sources at test start.
- Performance degradation: monitor runtime; fallback to smaller fixture set if threshold exceeded.

## Tracking Status Template (To Be Updated During Implementation)

| ID  | Title                            | Status      | Owner | Notes |
| --- | -------------------------------- | ----------- | ----- | ----- |
| 2   | Data extraction script           | NOT STARTED |       |       |
| 3   | Snapshot formats                 | NOT STARTED |       |       |
| 6   | Fixture generator                | NOT STARTED |       |       |
| 7   | Snapshot generator               | NOT STARTED |       |       |
| 8   | Comparison helpers               | NOT STARTED |       |       |
| 11  | Config hashing                   | NOT STARTED |       |       |
| 19  | Sorting utilities                | NOT STARTED |       |       |
| 21  | HTML diff visualization          | NOT STARTED |       |       |
| 22  | Auto-create missing snapshots    | NOT STARTED |       |       |
| 23  | Stale snapshot detection         | NOT STARTED |       |       |
| 24  | Path enforcement audit           | NOT STARTED |       |       |
| 25  | Guard rails for future tests     | NOT STARTED |       |       |
| 26  | Architecture diagram maintenance | NOT STARTED |       |       |

Update this table as tasks progress.

---

This TODO tracking file complements the requirements and is the single source of truth for implementation progress.
