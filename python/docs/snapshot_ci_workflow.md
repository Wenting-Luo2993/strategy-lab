# Snapshot Testing CI/CD Workflow

This document describes how snapshot testing integrates with CI/CD pipelines and pre-commit hooks to ensure code quality and prevent regressions.

## Overview

The snapshot testing framework enforces deterministic behavior through:

- **Pre-commit hooks**: Validate snapshots locally before commits
- **CI pipeline**: Verify snapshot stability on every push/PR
- **Governance checks**: Enforce size limits and security policies

## Pre-Commit Hook

### Configuration

The `.pre-commit-config.yaml` runs `pytest` without snapshot update flags:

```yaml
- repo: local
  hooks:
    - id: pytest
      name: Run pytest
      entry: pytest
      language: system
      types: [python]
      pass_filenames: false
      stages: [pre-commit]
```

### How It Works

1. **Developer makes changes** to strategy logic
2. **Pre-commit hook runs** `pytest` automatically
3. **If snapshots don't match**: Commit is blocked with diff output
4. **Developer reviews diff**: Determine if changes are intentional
5. **Update snapshots if needed**: Run `pytest --update-snapshots -k <test>`
6. **Commit updated snapshots**: Include both code and snapshot changes

### Installation

```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

### Bypassing (Use Sparingly)

If you need to commit without running tests (NOT RECOMMENDED):

```bash
git commit --no-verify -m "Your message"
```

## CI Pipeline

### GitHub Actions Workflow

Location: `.github/workflows/snapshot-tests.yml`

The CI pipeline runs two jobs:

#### 1. Test Job

- Runs full test suite including snapshot tests
- **Critical**: Never runs with `--update-snapshots` or `--auto-create-snapshots`
- Fails if any snapshot mismatches are detected
- Validates that snapshots in the repo are current

#### 2. Snapshot Governance Job

- Checks snapshot file sizes
- Scans for secrets/credentials
- Validates snapshot count limits
- Ensures security compliance

### Environment Variables

The CI explicitly sets:

```yaml
env:
  SNAPSHOT_UPDATE: "0"
  SNAPSHOT_AUTO_CREATE: "0"
```

This ensures snapshots cannot be accidentally modified in CI.

### Pull Request Workflow

1. **Developer pushes changes** to feature branch
2. **CI runs automatically** on PR
3. **If snapshots fail**: PR is blocked
   - Review the CI output for diffs
   - Update snapshots locally if intentional
   - Push updated snapshots to the PR branch
4. **Green CI**: PR can be merged

## Snapshot Update Workflow

### When Logic Changes Are Intentional

```bash
# 1. Make code changes
# Edit strategy files...

# 2. Run tests to see diffs
pytest -k orb_strategy -vv

# 3. Review the differences carefully
# Ensure changes are expected

# 4. Update snapshots
pytest --update-snapshots -k orb_strategy

# 5. Verify tests pass
pytest -k orb_strategy

# 6. Commit both code and snapshots
git add python/src/strategies/ python/tests/__snapshots__/
git commit -m "Update ORB strategy logic and snapshots"
```

### Bulk Snapshot Updates

If you need to regenerate all snapshots (rare, only after major refactoring):

```bash
# Update all snapshots
pytest --update-snapshots

# Verify all tests pass
pytest

# Review the changes carefully before committing
git diff python/tests/__snapshots__/
```

## Snapshot Pruning

### Detecting Stale Snapshots

Run tests with pruning flag to list unused snapshots:

```bash
pytest --snapshot-prune
```

Output:

```
[Snapshot Prune] Stale snapshot files (not touched this run):
  - signals__old_test.snapshot.csv
  - trades__deprecated.snapshot.csv
```

### Deleting Stale Snapshots

#### Interactive Mode

```bash
# Prompts for confirmation
pytest --snapshot-prune
# > Delete these 2 stale snapshot(s)? [yes/no]: yes
```

#### Automated Mode (CI/Scripts)

```bash
# Auto-confirm deletion
SNAPSHOT_PRUNE_DELETE=1 pytest --snapshot-prune
```

### When to Prune

- After removing or renaming tests
- During major refactoring
- As part of quarterly maintenance

## Governance Checks

### Size Limits

- **Single snapshot**: 10 MB maximum
- **Single fixture**: 50 MB maximum
- **Total snapshots**: Warning at 100 files

### Security Checks

Automatically scans for:

- API keys
- AWS credentials
- Passwords and secrets
- JWT tokens
- Private keys

### Running Governance Tests

```bash
# Run governance validation
pytest tests/snapshot/test_snapshot_governance.py -v

# Check actual snapshot directory
pytest tests/snapshot/test_snapshot_governance.py::test_actual_snapshot_directory_compliance -v
```

## Troubleshooting

### Snapshot Mismatch in CI but Passes Locally

**Cause**: Different Python versions or floating-point precision

**Solution**:

1. Regenerate snapshots with `--update-snapshots`
2. Ensure consistent Python version (use 3.11)
3. Check `NUMERIC_PRECISION` setting in `snapshot_utils.py`

### Pre-Commit Hook Fails

**Cause**: Snapshot out of sync with code changes

**Solution**:

1. Run `pytest -k <failing_test> -vv` to see diff
2. Update snapshots if changes are intentional
3. Try commit again

### Stale Snapshots Not Detected

**Cause**: Tests still reference the snapshots

**Solution**:

1. Verify test is actually removed/renamed
2. Check for parameterized tests that might still use it
3. Search codebase for snapshot name references

### Governance Check Fails in CI

**Cause**: Snapshot file too large or contains secrets

**Solution**:

1. Check which file failed: `pytest tests/snapshot/test_snapshot_governance.py -v`
2. For size issues: Reduce fixture data or use smaller date ranges
3. For secrets: Remove sensitive data from fixtures/metadata

## Best Practices

### Do's

✅ Update snapshots when logic changes are intentional
✅ Review snapshot diffs carefully before updating
✅ Include snapshot changes in the same commit as code changes
✅ Run full test suite before pushing
✅ Keep fixtures minimal (1-2 days of data)
✅ Document why snapshots were updated in commit messages

### Don'ts

❌ Don't use `--update-snapshots` in CI or pre-commit
❌ Don't commit without running tests first
❌ Don't ignore snapshot failures without investigation
❌ Don't include real credentials in fixtures
❌ Don't create massive snapshot files
❌ Don't bypass pre-commit hooks routinely

## Environment Variable Reference

| Variable                | Values | Purpose                       |
| ----------------------- | ------ | ----------------------------- |
| `SNAPSHOT_UPDATE`       | 0, 1   | Enable snapshot regeneration  |
| `SNAPSHOT_AUTO_CREATE`  | 0, 1   | Auto-create missing snapshots |
| `SNAPSHOT_VISUALIZE`    | 0, 1   | Generate HTML diff artifacts  |
| `SNAPSHOT_PRUNE`        | 0, 1   | List stale snapshots          |
| `SNAPSHOT_PRUNE_DELETE` | 0, 1   | Auto-delete stale snapshots   |
| `SNAPSHOT_ROOT`         | path   | Override snapshot directory   |
| `SCENARIOS_ROOT`        | path   | Override fixture directory    |

## CLI Flag Reference

| Flag                      | Purpose                       | Use Case                        |
| ------------------------- | ----------------------------- | ------------------------------- |
| `--auto-create-snapshots` | Create missing snapshots      | Initial baseline creation       |
| `--update-snapshots`      | Regenerate existing snapshots | After intentional logic changes |
| `--snapshot-visualize`    | Generate HTML diff reports    | Debugging complex diffs         |
| `--snapshot-prune`        | List/delete stale snapshots   | Cleanup after refactoring       |

## Example Scenarios

### Scenario 1: Adding New Test with Snapshot

```bash
# 1. Write new test that calls assert_snapshot
# 2. Run with auto-create
pytest tests/orb/test_new_feature.py --auto-create-snapshots

# 3. Verify snapshot was created
ls python/tests/__snapshots__/signals__*

# 4. Run again to verify it passes
pytest tests/orb/test_new_feature.py

# 5. Commit test and snapshot together
git add python/tests/orb/test_new_feature.py python/tests/__snapshots__/
git commit -m "Add new feature test with snapshots"
```

### Scenario 2: Fixing Bug That Changes Behavior

```bash
# 1. Fix the bug in code
# 2. Run tests - expect failure
pytest -k affected_test -vv

# 3. Review diff - ensure fix is correct
# 4. Update snapshots
pytest --update-snapshots -k affected_test

# 5. Verify fix
pytest -k affected_test

# 6. Commit with explanation
git add src/ python/tests/__snapshots__/
git commit -m "Fix bug in signal generation

Updated snapshots reflect corrected behavior where exit signals
now properly trigger on RSI crossover."
```

### Scenario 3: Major Refactoring

```bash
# 1. Make refactoring changes
# 2. Update all affected snapshots
pytest --update-snapshots

# 3. Verify full suite passes
pytest

# 4. Check for stale snapshots
pytest --snapshot-prune

# 5. Review all changes
git diff python/tests/__snapshots__/

# 6. Commit with detailed message
git commit -am "Refactor strategy signal generation

- Moved signal logic to separate module
- Updated all snapshots to reflect identical behavior
- Removed 3 deprecated test snapshots"
```

## Monitoring and Maintenance

### Weekly

- Review snapshot count trends
- Check for oversized snapshots
- Run governance tests

### Monthly

- Run pruning to remove stale snapshots
- Review fixture data for relevance
- Update CI workflows if needed

### Quarterly

- Audit all snapshots for continued relevance
- Review and update documentation
- Validate security scanning patterns

## Support and Questions

For issues with snapshot testing:

1. Check this documentation first
2. Run governance tests: `pytest tests/snapshot/test_snapshot_governance.py`
3. Review snapshot requirements: `python/docs/snapshot_testing_requirements.md`
4. Check TODO list: `python/docs/snapshot_testing_todo.md`
