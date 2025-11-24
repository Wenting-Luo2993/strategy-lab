# Snapshot Testing Framework for Strategy Lab

## Overview

This document provides comprehensive documentation on the snapshot testing framework, including requirements, implementation details, and usage guidelines.

**Purpose**: Provide deterministic, reproducible tests ensuring that (a) strategy signal generation and (b) orchestrated trade order creation (strategy + risk management + orchestrator) remain stable over time for the same underlying historical data slice.

**Quick Start**: Jump to [Getting Started](#getting-started) for immediate usage, or read the full specification for complete details.

## Table of Contents

- [Overview](#overview)
- [Getting Started](#getting-started)
- [Usage Guide](#usage-guide)
- [Command Reference](#command-reference)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)
- [Technical Specifications](#technical-specifications)

## 2. Scope

**Included:**

- Data fixture extraction from existing `data_cache` for specific trading days and tickers
- Baseline snapshot generation of expected signals and trade orders
- Test utilities to compare current outputs against stored snapshots
- Integration into existing tests (e.g., `test_orb_indicator.py`, `test_orb_strategy.py`, orchestrator-related tests)
- Metadata and governance for snapshot updates
- Pre-commit and CI integration
- Security and governance checks

**Excluded (initial phase):**

- Automatic adjustment for corporate actions (splits/dividends)—flagged as future enhancement
- Live data mutation detection
- Performance backtest metrics snapshots (only signals + trades for now)

## 3. Definitions

- Fixture Data: Curated subset of historical raw inputs copied from `data_cache` into `tests/scenarios/` for controlled testing.
- Snapshot: Canonical expected output (signals/trades) stored in versioned CSV/JSON plus a metadata sidecar describing context.
- Config Hash: Deterministic hash of strategy + risk + orchestrator relevant configuration to validate snapshot applicability.
- Update Mode: Explicit workflow (via env var or CLI flag) allowing regeneration of snapshots.

## 4. High-Level Guarantees

1. Given identical fixture data + configuration, strategy signal outputs are byte-for-byte identical (subject to tolerated numeric precision).
2. Given identical fixture data + configuration + strategy type + risk management type, orchestrator trade orders list is identical (same ordering, fields, and values).
3. Any divergence produces a concise diff explaining mismatches.
4. Snapshots cannot change silently—CI surfaces differences unless explicitly updated.

## 5. Data Fixture Extraction Script

Script Name: `scripts/extract_fixture_data.py` (TBD)
Path Resolution: All destination and source paths MUST be resolved via `resolve_workspace_path` from `src/utils/workspace.py` to ensure portability and consistency across environments.
Inputs:

- `--tickers T1 T2 ...`
- `--start-date YYYY-MM-DD`
- `--end-date YYYY-MM-DD` (inclusive, optional; if omitted equals start-date)
- `--source-path` (default: `data_cache` relative to workspace root)
- `--dest-path` (default: `tests/scenarios/<fixture_name>` resolved by `resolve_workspace_path`)
- `--fixture-name` (slug; auto-generated if omitted: e.g., `AAPL_NVDA_2025-11-07`)
  Outputs:
- Copied data files required by indicator/strategy (retain ALL columns for maximal regression coverage).
- `metadata.json` capturing: tickers, date range, row counts, created_at, generator version, commit hash.
  Requirements:
- Deterministic sorting of rows (timestamp ascending) prior to writing.
- Retain all available columns; optional column pruning may be added later if performance or size becomes problematic (current stance: completeness > minimalism).
- Validate data completeness (no missing essential columns).
- Fail fast if any ticker/date missing.

## 6. Snapshot Generation Mechanics

Primary mechanism: Pytest custom CLI flags drive snapshot creation and updates during test runs. A standalone script (`scripts/generate_snapshots.py`) is optional for bulk/CI operations but not required for normal workflow.

Pytest Flags (defined in `conftest.py` or plugin):

- `--auto-create-snapshots` – create missing snapshots for any test invoking `assert_snapshot`.
- `--update-snapshots` – overwrite existing snapshots with current outputs (baseline regeneration).
- `--snapshot-visualize` – emit HTML diff artifacts for mismatches.
- `--snapshot-prune` – after session, list (and optionally delete with confirmation) stale snapshot files not touched by any test.

Optional Script Inputs (if script retained):

- `--fixture <fixture_name>` or `--fixture-path`.
- `--strategies orb-strategy ...` (supports multiple).
- `--risk-profile <name>`.
- `--orchestrator-config <path>`.

Outputs:

- `signals__<fixture>__<strategy>__<test_context>.snapshot.csv`
- `trades__<fixture>__<strategy>__<risk_profile>__<test_context>.snapshot.csv`
- `snapshot_metadata.json` containing: commit, created_at, generator_version, config_hash, fixture_name, strategies, risk config summary, orchestrator settings.

## 7. Snapshot File Format

Primary storage: CSV (human diff friendly) + JSON sidecar metadata. Minimal viable snapshot format keeps precision while allowing stable diffs.
Naming Convention (one file per test case per strategy per fixture):

- Signals: `signals__<fixture>__<strategy>__<test_context>.snapshot.csv`
- Trades: `trades__<fixture>__<strategy>__<risk_profile>__<test_context>.snapshot.csv`

Test context (formerly `context_id` column) is embedded in the filename rather than a column, reducing per-row redundancy. If future multi-context aggregation is needed we can reintroduce a column.

Signals Columns (example): `timestamp,ticker,signal_type,strength`
Trades Columns (example): `timestamp,ticker,order_id,side,qty,price,reason`

Conventions:

- All timestamps ISO8601 UTC.
- Numeric fields normalized (round to 6 decimal places before writing).
- Sorted by: signals -> `timestamp,ticker,signal_type`; trades -> `timestamp,ticker,order_id`.
- No `strategy_run_id` column for now; if a future need arises to distinguish overlapping runs, we will add it with deterministic generation (e.g., hash of fixture+strategy+context).

## 8. Metadata & Hashing

Config Hash Inputs:

- Strategy parameters dict (sorted keys, JSON canonical form).
- Risk management parameters dict.
- Orchestrator relevant parameters (batch size, slippage model, etc.).
  Hash Algorithm: SHA256 over concatenated canonical JSON string.
  Purpose: On test load, verify stored `config_hash` matches newly computed; if mismatch -> instruct to regenerate snapshots.

## 9. Comparison Rules

Signals:

- Exact match in row count and ordering.
- For numeric columns: abs diff <= 1e-6.
- Fail on new/missing signal types.
  Trades:
- Exact match for categorical fields.
- Numeric tolerance on price/qty similar to signals.
  Diff Output:
- Summarize counts (expected vs actual).
- Show first N (e.g., 10) differing rows with side-by-side values.
- Provide hint: run snapshot update command if intentional.

## 10. Test Integration Strategy

Existing tests to augment:

- `test_orb_indicator.py`: After computing indicator-derived signals, compare to signals snapshot.
- `test_orb_strategy.py`: Run strategy core logic producing signal set; compare snapshot.
- Orchestrator test (new or augment existing): End-to-end (fixture -> strategy -> risk -> orders) -> compare trades snapshot.
  Fixtures: Use `pytest` parameterization over fixtures and strategies.
  Skip Conditions: If snapshot missing and not update mode -> fail with readable message.

## 11. Update Workflow

1. Developer changes logic.
2. Run tests normally: `pytest -k orb_strategy` -> potential FAIL with diff if behavior changed.
3. Review diff; if intentional regenerate: `pytest --update-snapshots -k orb_strategy` (overwrites affected snapshots).
4. Re-run without flag: `pytest -k orb_strategy` -> PASS.
5. Commit updated snapshots + metadata; CI runs without update flag and must PASS.

## 12. CI Governance & Pre-Commit Integration

- Pre-commit hook: runs `pytest --maxfail=1 -q` (or targeted subset) without snapshot flags; any diff fails commit.
- CI validation job: `pytest` (no update/auto-create flags) must pass; diffs cause failure.
- Optional regeneration job (manual trigger) can run `pytest --update-snapshots` then commit via PR if approved.
- HTML diff artifacts produced only if `--snapshot-visualize` specified in a diagnostic run.

## 13. Edge Cases & Considerations

Edge Cases:

- Missing day (holiday): script should skip or mark absent explicitly.
- Partial session: preserve partial data; no synthetic filler.
- Timezone shifts: all normalized to UTC.
- Floating point drift: minimize by rounding before snapshot creation.
- Non-deterministic elements (randomness, external latency): ensure seeded randomness or remove dependency.

## 14. Performance

- Keep fixtures minimal (one or two days, limited tickers) to reduce test runtime.
- Use lazy loading; do not recompute indicators repeatedly if cached within test scope.
- Provide an opt-in for expanded fixtures for stress tests (separate mark `@pytest.mark.slow`).

## 15. Security / Privacy

- Exclude credentials or tokens from fixture or snapshot metadata.
- Ensure no PII or secret keys end up in metadata or logs.

## 16. Future Extensions (Beyond Initial Scope)

- Multi-strategy aggregated portfolio snapshot.
- Metrics snapshot (win rate, expectancy) for regression detection.
- Compression of large fixtures.
- Parquet alternative for very large datasets (only if needed).

## 17. Risks

- Logic change causing cascading snapshot updates—mitigate via small, isolated fixtures.
- Floating point instability—mitigate via rounding.
- Hidden dependencies on environment—document all configuration inputs in metadata.

## 18. Acceptance Criteria

- Running test suite on main with no code changes yields PASS (stable snapshots).
- Introducing a deliberate logic change triggers clear snapshot diff failure.
- Setting UPDATE_SNAPSHOTS regenerates and then tests PASS.
- Snapshot metadata hashes validate configuration consistency.

## 19. Implementation Tracking

Implementation tasks have been moved to `snapshot_testing_todo.md` to keep this document focused on requirements and design. See that file for current status and IDs.

---

# Getting Started

## Quick Setup

### 1. Install Pre-Commit Hooks

```bash
pip install pre-commit
pre-commit install
```

### 2. Generate Test Fixtures

Extract market data for testing:

```bash
python python/scripts/extract_fixture_data.py --tickers AAPL MSFT NVDA --start-date 2025-11-07
```

This creates a fixture directory `tests/__scenarios__/2025-11-07/` with data files for each ticker.

### 3. Create Your First Snapshot

```python
# In your test file
def test_my_strategy(assert_snapshot):
    # Run your strategy
    signals_df = my_strategy.generate_signals(fixture_data)

    # Assert snapshot
    assert_snapshot(
        signals_df,
        name="my_strategy__AAPL",
        kind="signals",
        strategy_config=config_dict
    )
```

Run with auto-create flag:

```bash
pytest tests/my_test.py --auto-create-snapshots
```

### 4. Verify Snapshots Work

Run tests normally (without flags):

```bash
pytest tests/my_test.py
```

If the test passes, your snapshot is working! 🎉

---

# Usage Guide

## Working with Fixtures

### Creating Fixtures

Fixtures are curated subsets of historical data used for deterministic testing.

**Basic usage:**

```bash
# Single day, multiple tickers
python python/scripts/extract_fixture_data.py \
  --tickers AAPL MSFT NVDA \
  --start-date 2025-11-07

# Date range
python python/scripts/extract_fixture_data.py \
  --tickers AAPL \
  --start-date 2025-11-07 \
  --end-date 2025-11-08

# Custom fixture name
python python/scripts/extract_fixture_data.py \
  --tickers AAPL \
  --start-date 2025-11-07 \
  --fixture-name my_custom_test
```

**Where fixtures are stored:**

- Default: `tests/__scenarios__/<date>/`
- Each ticker gets its own file: `AAPL.parquet`, `MSFT.parquet`, etc.
- Metadata: `metadata.json` with fixture details

### Loading Fixtures in Tests

```python
from tests.utils import load_fixture_df

# Load fixture data
df = load_fixture_df(
    ticker="AAPL",
    start_date="2025-11-07",
    end_date=None  # Optional
)
```

## Creating Snapshots

### Signal Snapshots

Signal snapshots capture strategy entry/exit decisions:

```python
def test_strategy_signals(assert_snapshot):
    # Load fixture
    data = load_fixture_df("AAPL", "2025-11-07")

    # Run strategy
    strategy = ORBStrategy(config)
    signals_df = strategy.generate_signals(data)

    # Create snapshot
    assert_snapshot(
        signals_df,
        name="orb_strategy__AAPL",
        kind="signals",  # Important: marks as signals
        strategy_config=strategy_config_to_dict(config)
    )
```

**What gets compared:**

- `entry_signal` column (0, 1, -1)
- `exit_flag` column (0 or 1)

**Other columns** (OHLCV, indicators) are stored but not compared, providing context for debugging.

### Trade Snapshots

Trade snapshots capture complete order execution:

```python
def test_orchestrator_trades(assert_snapshot):
    # Run full orchestrator
    trades_df = orchestrator.execute(fixtures)

    # Create snapshot
    assert_snapshot(
        trades_df,
        name="orb_trades__default_risk",
        kind="trades",  # Important: marks as trades
        strategy_config=strategy_config,
        risk_config=risk_config
    )
```

**What gets compared:**

- `entry_time`, `entry_price`, `size`
- `exit_time`, `exit_price`, `exit_reason`
- `pnl`, `stop_loss`, `take_profit`, `direction`

## Snapshot Lifecycle

### Initial Creation

First time running a test:

```bash
# Create missing snapshots
pytest tests/my_test.py --auto-create-snapshots
```

This generates:

- `tests/__snapshots__/signals__my_test.snapshot.csv`
- `tests/__snapshots__/signals__my_test.metadata.json`

### Normal Validation

Regular test runs verify snapshot matches:

```bash
pytest tests/my_test.py
```

- **PASS**: Output matches snapshot ✓
- **FAIL**: Output differs, shows diff

### Updating Snapshots

When you intentionally change logic:

```bash
# 1. Run to see diff
pytest tests/my_test.py -vv

# 2. Review changes carefully

# 3. Update if intentional
pytest tests/my_test.py --update-snapshots

# 4. Verify
pytest tests/my_test.py
```

### Visualizing Diffs

Generate HTML reports for complex diffs:

```bash
pytest tests/my_test.py --snapshot-visualize
```

Opens HTML diff in `tests/__snapshots__/html/`.

## Command Reference

### Pytest Flags

| Flag                      | Purpose                       | Example                                 |
| ------------------------- | ----------------------------- | --------------------------------------- |
| `--auto-create-snapshots` | Create missing snapshots      | `pytest --auto-create-snapshots -k orb` |
| `--update-snapshots`      | Regenerate existing snapshots | `pytest --update-snapshots -k orb`      |
| `--snapshot-visualize`    | Generate HTML diffs           | `pytest --snapshot-visualize`           |
| `--snapshot-prune`        | List/remove stale snapshots   | `pytest --snapshot-prune`               |

### Environment Variables

Override behavior without CLI flags:

| Variable                | Values | Purpose                     |
| ----------------------- | ------ | --------------------------- |
| `SNAPSHOT_UPDATE`       | 0, 1   | Enable snapshot updates     |
| `SNAPSHOT_AUTO_CREATE`  | 0, 1   | Enable auto-creation        |
| `SNAPSHOT_VISUALIZE`    | 0, 1   | Enable HTML diffs           |
| `SNAPSHOT_PRUNE`        | 0, 1   | Enable pruning              |
| `SNAPSHOT_PRUNE_DELETE` | 0, 1   | Auto-confirm deletion       |
| `SNAPSHOT_ROOT`         | path   | Override snapshot directory |

**PowerShell examples:**

```powershell
$env:SNAPSHOT_UPDATE=1; pytest -k orb_strategy
$env:SNAPSHOT_AUTO_CREATE=1; pytest tests/new_test.py
```

**Bash examples:**

```bash
SNAPSHOT_UPDATE=1 pytest -k orb_strategy
SNAPSHOT_AUTO_CREATE=1 pytest tests/new_test.py
```

## Common Workflows

### Adding a New Test

```bash
# 1. Write test with assert_snapshot
# tests/test_new_feature.py

# 2. Create snapshot baseline
pytest tests/test_new_feature.py --auto-create-snapshots

# 3. Verify it works
pytest tests/test_new_feature.py

# 4. Commit test and snapshot together
git add tests/test_new_feature.py tests/__snapshots__/
git commit -m "Add snapshot test for new feature"
```

### Fixing a Bug

```bash
# 1. Fix the bug in code
# src/strategies/my_strategy.py

# 2. Run tests - expect failure showing bug fix
pytest -k affected_test -vv

# 3. Review diff to confirm fix is correct

# 4. Update snapshots
pytest --update-snapshots -k affected_test

# 5. Commit with explanation
git add src/ tests/__snapshots__/
git commit -m "Fix signal generation bug

Updated snapshots reflect corrected behavior."
```

### Refactoring

```bash
# 1. Make refactoring changes
# 2. Update all affected snapshots
pytest --update-snapshots

# 3. Verify all tests pass
pytest

# 4. Review changes
git diff tests/__snapshots__/

# 5. Commit
git commit -am "Refactor strategy logic - behavior unchanged"
```

### Cleaning Up Stale Snapshots

```bash
# 1. List stale snapshots
pytest --snapshot-prune

# 2. Review the list

# 3. Delete interactively
pytest --snapshot-prune
# > Delete these 3 stale snapshot(s)? [yes/no]: yes

# Or auto-delete
SNAPSHOT_PRUNE_DELETE=1 pytest --snapshot-prune
```

## Best Practices

### Do's ✅

- **Keep fixtures minimal**: 1-2 days of data per fixture
- **Review diffs carefully**: Before updating snapshots
- **Commit together**: Code changes + snapshot updates in same commit
- **Document why**: Explain snapshot updates in commit messages
- **Run tests first**: Always before pushing
- **Use meaningful names**: `orb_strategy__AAPL` not `test1`

### Don'ts ❌

- **Don't update without review**: Always check what changed
- **Don't ignore failures**: Investigate before updating
- **Don't commit untested**: Verify snapshots work before committing
- **Don't include secrets**: Keep credentials out of fixtures
- **Don't create huge snapshots**: Break into smaller tests
- **Don't bypass pre-commit**: Unless absolutely necessary

## Troubleshooting

### Snapshot Mismatch Locally

**Problem**: Test fails with snapshot diff

**Solution**:

```bash
# See detailed diff
pytest tests/failing_test.py -vv

# If changes are intentional
pytest --update-snapshots tests/failing_test.py

# If unexpected, debug the code
```

### Pre-Commit Fails

**Problem**: Cannot commit due to snapshot mismatch

**Solution**:

```bash
# Run tests to see failure
pytest

# Update if changes are intentional
pytest --update-snapshots -k failing_test

# Try commit again
git commit -m "Your message"
```

### Floating Point Differences

**Problem**: Small numeric differences cause failures

**Solution**: Already handled! Snapshots use:

- 4 decimal places for storage
- 1e-6 tolerance for comparison

If still seeing issues, check `NUMERIC_PRECISION` in `src/utils/snapshot_utils.py`.

### Index Mismatch Errors

**Problem**: "Snapshot index mismatch: indices differ"

**Solution**:

- Already handled for datetime indices
- Ensure data is sorted before snapshotting
- Check for timezone issues in your data

### Large Snapshot Files

**Problem**: Snapshot file exceeds size limits

**Solution**:

```bash
# Check governance
pytest tests/snapshot/test_snapshot_governance.py

# Options:
# 1. Reduce fixture date range
# 2. Split into multiple tests
# 3. Review OHLCV data inclusion
```

## Advanced Topics

### Parameterized Tests

Test multiple scenarios efficiently:

```python
@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA"])
def test_strategy_multi_ticker(ticker, assert_snapshot):
    data = load_fixture_df(ticker, "2025-11-07")
    signals = strategy.generate_signals(data)

    # Unique snapshot per parameter
    assert_snapshot(
        signals,
        name=f"orb_strategy__{ticker}",
        kind="signals"
    )
```

### Custom Comparison Logic

Override columns to compare:

```python
# In conftest.py or test
compare_cols = ["entry_signal", "exit_flag", "custom_metric"]
diff = compare_snapshots(expected, actual, compare_columns=compare_cols)
```

### Config Hashing

Track configuration changes:

```python
from src.utils.snapshot_core import hash_config

# Generate stable hash
config_hash = hash_config(strategy_config, risk_config)

# Included automatically in metadata
assert_snapshot(df, name="test", strategy_config=cfg)
```

---

# Technical Specifications

## 20. Open Questions

- Should snapshots be stored in `python/tests/snapshots/` vs adjacent to fixtures? (Leaning: `tests/snapshots/<fixture_name>/`.)
- Use JSON for metadata only or include Parquet alternative for large scale future? (Initial: CSV + JSON only.)
- Provide automatic fallback to update mode for new fixtures? (Probably no—explicitness preferred.)

## 21. Example Workflow (Developer)

1. Extract fixture: `python scripts/extract_fixture_data.py --tickers AAPL NVDA --start-date 2025-11-07 --fixture-name orb_smoke`
2. Initial baseline creation: `pytest --auto-create-snapshots -k orb_strategy` (writes missing snapshots).
3. Standard validation: `pytest -k orb_strategy` -> PASS.
4. Logic change: run `pytest -k orb_strategy` -> FAIL with diff.
5. Intentional change: `pytest --update-snapshots -k orb_strategy` regenerates affected snapshots.
6. Optional review with visualization: `pytest --snapshot-visualize -k orb_strategy` (if mismatches exist).
7. Prune stale: `pytest --snapshot-prune` (lists unused snapshots; optional removal flow).
8. Commit updated snapshots; CI (no flags) passes.

### Pytest Command Examples

Common commands for interacting with snapshot tests.

Baseline creation (auto-create any missing snapshots):
`-k orb_strategy` filters tests by substring matching in node ids.

```
pytest --auto-create-snapshots -k orb_strategy
```

Run full suite without modifying snapshots (CI style):

```
pytest
```

Update existing snapshots after intentional logic change:

```
pytest --update-snapshots -k orb_strategy
```

Generate HTML diff artifacts for failing snapshot tests:

```
pytest --snapshot-visualize -k snapshot
```

List stale (untouched) snapshot files this session:

```
pytest --snapshot-prune
```

Use environment variables instead of CLI flags (helpful for IDE or CI matrix runs):

```
SET SNAPSHOT_AUTO_CREATE=1
pytest -k orb_indicator
```

or (PowerShell)

```
$env:SNAPSHOT_UPDATE=1; pytest -k orb_strategy
```

Force update of all snapshots (broad regeneration – use sparingly):

```
pytest --update-snapshots -k "snapshot or orb"
```

Run only snapshot workflow test:

```
pytest -k snapshot_workflow
```

Show slow snapshot tests (if marked later):

```
pytest -m slow --snapshot-visualize
```

Dry-run expected failures with verbose diff output (pair with -vv for extra logging):

```
pytest -k orb_strategy -vv
```

Regenerate and visualize simultaneously (diff HTML emitted before overwrite not needed; usually two steps, but can inspect first):

```
pytest --snapshot-visualize --update-snapshots -k orb_strategy
```

Override snapshot directory (e.g., isolated temp path) for experimental runs:

```
$env:SNAPSHOT_ROOT=python/tests/snapshots_tmp; pytest --auto-create-snapshots -k snapshot
```

Prune listing with environment variable instead of flag:

```
$env:SNAPSHOT_PRUNE=1; pytest
```

## 22. Minimal Contract Summaries

Data Extraction Script:

- Input: tickers[], date range
- Output: canonical fixture directory + metadata.json
- Errors: missing sources, empty data, unsupported ticker
  Snapshot Generation:
- Input: fixture, strategy config, risk config
- Output: signals/trades CSV + metadata
- Errors: fixture missing, config mismatch
  Comparison Helper:
- Input: expected CSV, actual DataFrame
- Output: pass/fail + diff summary
- Errors: schema mismatch, ordering mismatch

## 23. Edge Case Testing List

- Single ticker single day.
- Single ticker multi-day.
- Multi-ticker multi-day small range.
- Missing midday data (simulate).
- Price with high precision decimals.

## 25. Maintenance

### Regular Tasks

**Weekly:**

- Review snapshot count trends
- Check for oversized snapshots
- Run governance tests

**Monthly:**

- Run pruning to remove stale snapshots
- Review fixture data relevance
- Validate security patterns

**Quarterly:**

- Audit all snapshots for continued relevance
- Purge stale fixture directories
- Review and update documentation

### Version Management

- Bump `generator_version` in `extract_fixture_data.py` when snapshot format changes
- Update `NUMERIC_PRECISION` carefully - requires regenerating all snapshots
- Document breaking changes in commit messages

### Governance Compliance

Run governance checks regularly:

```bash
# Check all governance rules
pytest tests/snapshot/test_snapshot_governance.py -v

# Verify actual snapshots
pytest tests/snapshot/test_snapshot_governance.py::test_actual_snapshot_directory_compliance -v

# Check fixtures for secrets
pytest tests/snapshot/test_snapshot_governance.py::test_fixture_directory_has_no_secrets -v
```

---

## Additional Resources

- **Implementation Status**: See `snapshot_testing_todo.md` for current progress
- **CI/CD Workflows**: See `snapshot_ci_workflow.md` for detailed CI integration
- **Code Examples**: Check `tests/snapshot/` for working examples
- **Governance Tools**: See `src/utils/snapshot_governance.py` for guard rails

## Contributing

When contributing snapshot tests:

1. Follow the naming convention: `<kind>__<name>.snapshot.csv`
2. Include config hash in metadata
3. Keep fixtures under 50MB
4. Run governance checks before committing
5. Document snapshot updates in PR description

---

This document evolves with the framework. For questions or suggestions, open an issue or discussion in the repository.
Summary: 3 rows differ, 0 missing, 0 extra. Suggest: regenerate snapshots if intentional.

````

## 25. Maintenance Notes

- Bump `generator_version` when snapshot format changes.
- Document changes in CHANGELOG (future).
- Keep fixtures small; purge stale fixture directories quarterly.

---

This document is intended to evolve; adjust sections as implementation proceeds. Ensure TODO list stays synchronized with reality.

## 26. Architectural Diagram

```mermaid
flowchart TD
  subgraph Extraction
    A[Fixture Extraction Script]
    A -->|writes fixture data| B[tests/scenarios/<fixture>]
  end

  subgraph SnapshotGen
    SG["Snapshot Generation Utility (update mode)"] --> D1[Signals Snapshots CSV]
    SG --> D2[Trades Snapshots CSV]
    SG --> M[snapshot_metadata.json]
  end

  B --> SG

  subgraph TestRun
    T[pytest test] --> FX[assert_snapshot fixture]
    FX --> H[Strategy + Orchestrator Execution]
    H --> O["Actual Output DF (signals/trades)"]
    FX --> N[Normalization & Sorting]
    N --> CMP[Comparator & Diff Engine]
  end

  O --> N
  D1 --> CMP
  D2 --> CMP
  CMP --> OUT["Diff Report (Console)"]
  CMP -->|--snapshot-visualize| HTML[HTML Visualization]
  O -->|--auto-create-snapshots & missing| SG
  OUT --> CI[CI & Pre-Commit Hooks]
  HTML --> CI
    CI --> PRN["Pruning Hook (SNAPSHOT_PRUNE=1)"]
````

Diagram Legend:

- Fixture Extraction produces stable input slice (fixture directory).
- Snapshot Generation writes CSV + metadata during update or auto-create flows.
- `assert_snapshot` pytest fixture orchestrates execution, normalization, comparison, auto-create, HTML rendering, pruning signaling.
- Comparator applies sorting, rounding, tolerance checks, and emits diff artifacts.
- Pytest CLI Flags: `--update-snapshots`, `--auto-create-snapshots`, `--snapshot-visualize`, `--snapshot-prune` govern optional behaviors.
- CI / Pre-Commit enforce snapshot stability and optionally perform pruning.
