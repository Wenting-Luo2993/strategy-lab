# Snapshot Testing TODO Tracking

This document centralizes implementation tasks referenced by `snapshot_testing_requirements.md`. Each item has an ID for cross-referencing in commits and PRs.

## Legend

- Status: NOT STARTED | IN PROGRESS | BLOCKED | DONE
- Pytest CLI Flags: `--update-snapshots` (regenerate), `--auto-create-snapshots` (create missing), `--snapshot-visualize` (HTML diff), `--snapshot-prune` (stale detection).

## Core Tasks

1. Requirements doc (DONE) – initial specification established.
2. Data extraction script – implement `extract_fixture_data.py` using `resolve_workspace_path`.
3. Finalize snapshot file formats – confirm column set & naming convention (signals/trades per test context).
4. Identify strategies to cover – enumerate initial strategy list (orb-strategy + others) & map to fixtures.
5. Design orchestrator snapshot tests – define end-to-end test flow & parametrization.
6. Implement fixture generator – actual code for extraction script with metadata.json.
7. Implement pytest snapshot flags – register CLI options & hook into fixture (`--update-snapshots`, `--auto-create-snapshots`, `--snapshot-visualize`, `--snapshot-prune`).
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
22. Auto-create missing snapshots – implement logic under `--auto-create-snapshots` flag in fixture.
23. Stale snapshot detection – list (and optional prune with confirmation) under `--snapshot-prune` flag.
24. Workspace path enforcement – audit scripts to ensure all path resolution uses `resolve_workspace_path`.
25. Guard rails for future test generation – guidelines & checks preventing inadvertent snapshot bloat or misuse.
26. Architecture diagram maintenance – keep diagram in sync when flow changes.

## Extended / Future Tasks

- Parquet support for very large datasets (defer until size pressure observed).
- Portfolio-level aggregated snapshots.
- Metrics snapshots (performance KPIs) once signal/trade layer is stable.
- CHANGELOG integration for snapshot format version bumps.

## Proposed Implementation Order

Replaced by structured stages below.

## Staged Execution Plan

Each stage produces a testable deliverable with explicit verification steps. Progression depends on successful verification of prior stage.

### Stage 1 – Data & Determinism Foundation

Scope Tasks: 2 (extraction script), 16 (metadata schema), 19 (sorting utilities), 18 (tolerance settings), 15 (performance considerations baseline), 24 (path enforcement audit partial).
Deliverables:

- `scripts/extract_fixture_data.py` generating deterministic fixture directories with `metadata.json`.
- Utility functions: `normalize_numeric(df)`, `sort_signals(df)`, `sort_trades(df)`.
- Defined tolerance constants (e.g. `NUMERIC_ABS_TOL=1e-6`).
  Verification:

1. Run extraction twice on same inputs; directory diff is empty (excluding timestamp fields which must match identical value).
2. Metadata file includes all required keys; schema validated by a lightweight test.
3. Sorting utilities produce stable ordering (hash before/after identical).
4. Numeric normalization eliminates float drift (rounding applied).
5. Performance: fixture generation completes under target threshold (e.g. <2s for 2 tickers 1 day).
   Exit Criteria: All above pass in a dedicated `test_fixture_generation.py`.

### Stage 2 – Snapshot Core & Diff Logic

Scope Tasks: 3 (formats), 11 (config hashing), 8 (comparison helpers), 17 (diff reporter).
Deliverables:

- Column & filename spec finalized and documented.
- `hash_config(config_dicts...)` stable under key reordering.
- `compare_snapshots(expected_df, actual_df)` returns structured diff object.
- Text diff reporter limited to first N mismatches; includes regeneration hint.
  Verification:

1. Hash invariance test: same logical config with shuffled keys yields identical hash.
2. Intentional single-value change surfaces in diff with correct counts.
3. Empty expected snapshot case handled (zero signals scenario).
4. Performance: comparison time < 100ms for typical snapshot size.
   Exit Criteria: New tests `test_config_hashing.py`, `test_snapshot_diff.py` pass.

### Stage 3 – Pytest Integration & Flags

Scope Tasks: 7 (CLI flags), 27 (assert_snapshot fixture), 22 (auto-create), 18 (reuse tolerances), 24 (path enforcement completion).
Deliverables:

- Pytest CLI options registered.
- `assert_snapshot` fixture performing normalization + compare + auto-create.
- Auto-create only writes when file missing and flag present; update flag overwrites.
  Verification:

1. Running `pytest --auto-create-snapshots` creates baseline files; second run without flags passes.
2. Altering code triggers failure without flags; running with `--update-snapshots` resolves.
3. No unintended writes when flags absent (checked via filesystem timestamp assertions).
   Exit Criteria: `test_snapshot_workflow.py` passes scenarios.

### Stage 4 – Strategy & Orchestrator Integration

Scope Tasks: 4 (strategies list), 5 (orchestrator tests), 9 (integrate into existing tests), 10 (risk-managed order checks).
Deliverables:

- Parameterized tests covering target strategies and risk profiles.
- Trade snapshot generation & comparison integrated.
  Verification:

1. Full test suite green with baseline snapshots.
2. Introduce controlled strategy parameter change → targeted snapshot diffs only.
3. Risk sizing edge case (e.g. fractional share rounding) captured in snapshot.
   Exit Criteria: `pytest` full run passes; diff tests behave as expected.

### Stage 5 – Visualization & Governance

Scope Tasks: 21 (HTML visualization), 23 (pruning), 12 (CI & pre-commit), 25 (guard rails), 20 (security/privacy review).
Deliverables:

- HTML diff artifact generator.
- Pruning mechanism listing stale snapshots (`--snapshot-prune`).
- Pre-commit & CI rules documented and implemented.
- Guard rail checks (e.g. max snapshot size, forbid secrets in metadata).
  Verification:

1. Force mismatch; HTML artifact produced with summary + row detail.
2. Create dummy unused snapshot; prune flag lists it; confirm no deletion without explicit confirmation.
3. Pre-commit rejects modification generating diff without update flag.
4. Security scan test ensures no credential patterns present.
   Exit Criteria: Governance tests `test_snapshot_governance.py` pass; CI pipeline updated.

### Stage 6 – Maintenance & Extensions

Scope Tasks: 14 (edge cases), 26 (diagram maintenance), 28 (optional plugin layer), future backlog items.
Deliverables:

- Edge case tests (holidays, partial sessions, zero signals).
- Optional pytest plugin extraction evaluated / implemented.
  Verification:

1. Edge case test matrix passes.
2. Plugin layer (if built) replicates existing behavior with identical test outcomes.
   Exit Criteria: Edge case suite green; decision recorded on plugin extraction.

## Stage Verification Summary Table (to append as progress updates)

| Stage | Status      | Key Pending Tests                       |
| ----- | ----------- | --------------------------------------- |
| 1     | VALIDATED   | test_fixture_generation                 |
| 2     | NOT STARTED | test_config_hashing, test_snapshot_diff |
| 3     | NOT STARTED | test_snapshot_workflow                  |
| 4     | NOT STARTED | existing strategy tests integrated      |
| 5     | NOT STARTED | test_snapshot_governance                |
| 6     | NOT STARTED | edge case matrix                        |

## Acceptance Gates Per Stage

Stage 1 – Data & Determinism Foundation:

- Running extraction twice on identical params yields byte-for-byte identical fixture (no unintended drift).
- OCHLV (or OHLCV) and required columns exist and contain no None/NaN values.
- All columns present in source data_cache files are preserved in fixture output (completeness).
- Metadata schema validates (all required keys present, types correct).
- Sorting utilities produce deterministic ordering (hash of ordered index stable across runs).
- Numeric normalization applied (values rounded to defined precision) eliminating float jitter.
- Performance target met (<= 2s for sample fixture: 2 tickers, 1 day).
- All paths resolved via resolve_workspace_path (audit passes).

Stage 2 – Snapshot Core & Diff Logic:

- Snapshot filename & column specification documented and enforced.
- Config hash invariant under key reordering and stable across processes.
- Single-cell intentional modification surfaces correctly in diff (row count change, value diff recorded).
- Zero-signal / empty snapshot case handled gracefully (no exception; passes comparison when expected empty).
- Diff reporter outputs limited mismatches (first N) and includes regeneration hint text.
- Comparison runtime acceptable (< 100ms typical snapshot) on baseline hardware.

Stage 3 – Pytest Integration & Flags:

- CLI flags registered: --auto-create-snapshots, --update-snapshots, --snapshot-visualize, --snapshot-prune.
- No snapshot files written/modified when flags absent (verified by timestamps).
- Auto-create writes only missing snapshots; existing ones untouched.
- Update rewrites only targeted existing snapshots; after run tests pass without flags.
- Visualization artifacts generated only with --snapshot-visualize when diffs exist.
- Workflow tests (create → validate → fail → update → pass) succeed end-to-end.

Stage 4 – Strategy & Orchestrator Integration:

- Parameterized strategy and orchestrator tests pass with baseline snapshots.
- Controlled strategy parameter change triggers ONLY related snapshot diffs (no cascade elsewhere).
- Risk-managed order snapshots include sizing & stops; discrepancies detected if logic altered.
- Trade and signal order sorting deterministic (timestamp,ticker,order_id or signal_type as specified).

Stage 5 – Visualization & Governance:

- HTML diff report generated exclusively under --snapshot-visualize; contains summary + first N row diffs.
- --snapshot-prune lists all stale snapshots; no deletions occur without explicit confirmation flow.
- Guard rails enforced: snapshot size thresholds, secret/credential pattern scan returns clean.
- Pre-commit hook prevents committing diffs unless run with update flag (or skipping hook intentionally documented).
- CI pipeline fails when unapproved diffs detected; passes after legitimate update.
- Security/privacy review confirms no sensitive data stored in fixtures/metadata.

Stage 6 – Maintenance & Extensions:

- Edge case matrix (holiday, partial session, high precision prices, zero signals) all pass.
- Optional plugin layer (if implemented) reproduces identical results vs fixture-based approach (parity test).
- Architectural diagram updated post significant structural changes.
- Future extension placeholders (metrics/portfolio/parquet) documented without blocking current tests.

## Open Dependencies

- Strategy configuration object shape (needed for hashing) – confirm canonical serialization.
- Risk management configuration availability in tests – ensure accessible fixture or factory.

## Pytest CLI Flags

- `--update-snapshots` – overwrite existing snapshots.
- `--auto-create-snapshots` – create missing snapshots.
- `--snapshot-visualize` – produce HTML diff artifacts for mismatches.
- `--snapshot-prune` – detect (and optionally prune) stale snapshots.

## Governance Notes

- Any snapshot format change must bump `generator_version` and update README.
- Automatic creation is silent but logged; creation events should be reviewed in PR diff.
- Pruning never deletes by default unless explicitly flagged to avoid accidental data loss.

## Risk Mitigations

- Floating point differences: enforce rounding & tolerance compare.
- Hidden randomness: seed all random or pseudo-random sources at test start.
- Performance degradation: monitor runtime; fallback to smaller fixture set if threshold exceeded.

---

This TODO tracking file complements the requirements and is the single source of truth for implementation progress.
