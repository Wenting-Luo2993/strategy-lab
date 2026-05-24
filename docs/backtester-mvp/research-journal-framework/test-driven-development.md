# Unit Test / Validation Spec — Research Journal Framework

The goal of these tests is NOT only correctness, but also prevention of:

* **Research integrity violations** (modifying completed experiments)
* **Future leakage** (execution metadata missing or incorrect)
* **Lineage corruption** (cycles, orphaned experiments)
* **Data loss** (failed persistence, missing checksums)
* **Reproducibility failures** (incomplete metadata, missing Git state)

The framework should fail loudly if assumptions break.

---

## Test Priority Tiers

| Tier | Description |
|------|-------------|
| **P0** | Must pass before trusting any research output. Catches catastrophic bugs: immutability violations, lineage cycles, missing Git metadata. |
| **P1** | Important for research integrity. Catches edge cases and silent data errors: missing validations, incorrect file paths. |
| **P2** | Statistical guardrails and performance tests. Catches scalability issues and thread-safety problems. |

---

# 1. Domain Models Tests (Stage 1)

## Test: Hypothesis ID format validation — **P0**

### Goal
Ensure hypothesis IDs follow strict format convention to prevent lookup failures.

### Input
```python
# Valid
Hypothesis(id="HYP-001", title="...", rationale="...", ...)
# Invalid
Hypothesis(id="HYP-1", ...)      # Too short
Hypothesis(id="HYP-ABC", ...)    # Non-numeric
Hypothesis(id="001", ...)         # Missing prefix
```

### Expected
Valid IDs accepted; invalid IDs raise `ValidationError`

### Method
Regex validator: `^HYP-\d{3,}$`

### Why This Matters
Inconsistent IDs break file lookups and cross-references between hypotheses and experiments.

---

## Test: Experiment immutability when completed — **P0**

### Goal
Prevent accidental modification of completed experiments (scientific integrity).

### Input
```python
exp = Experiment(id="EXP-001", status=ExperimentStatus.REGISTERED, ...)
exp.mark_completed(results={"sharpe": 1.2}, conclusion="Edge validated")

# Attempt modification
exp.parameters["orb_minutes"] = 10  # Should fail
```

### Expected
Raises `ImmutabilityError` or `ValidationError`

### Method
Use Pydantic's `model_config = ConfigDict(frozen=True)` after completion, or implement custom setter validation

### Why This Matters
Completed experiments must be tamper-proof for audit trails and reproducibility. Allowing modifications invalidates research conclusions.

---

## Test: Experiment requires results when completed — **P0**

### Goal
Ensure no experiment reaches COMPLETED status without results.

### Input
```python
exp = Experiment(id="EXP-001", status=ExperimentStatus.RUNNING, ...)
exp.status = ExperimentStatus.COMPLETED  # Missing results_summary
```

### Expected
`ValidationError` raised

### Method
Pydantic validator: `@model_validator` checks if status==COMPLETED → results_summary must not be None

### Why This Matters
Experiments without results are incomplete and mislead future researchers about what was tested.

---

## Test: Datetime fields are timezone-aware — **P0**

### Goal
Prevent naive datetime bugs (DST, cross-timezone collaboration).

### Input
```python
import datetime
hyp = Hypothesis(
    created_at=datetime.datetime.now()  # Naive datetime
)
```

### Expected
`ValidationError` raised

### Method
Validator checks `dt.tzinfo is not None` for all datetime fields

### Why This Matters
Naive datetimes cause subtle bugs when comparing timestamps across different timezones or DST transitions.

---

## Test: Artifact reference rejects parent directory traversal — **P0**

### Goal
Prevent security vulnerability where artifact path escapes research/ directory.

### Input
```python
ArtifactReference(
    path="../../../etc/passwd",  # Malicious path
    ...
)
```

### Expected
`ValidationError` raised

### Method
Validator checks path does not contain ".."

### Why This Matters
Path traversal attacks could expose sensitive files if artifact paths are not validated.

---

## Test: Model serialization roundtrip — P1

### Goal
Ensure models serialize/deserialize without data loss.

### Input
```python
original = Experiment(...)
serialized = original.model_dump()
reconstructed = Experiment(**serialized)
```

### Expected
`reconstructed == original` (all fields match)

### Method
Compare field-by-field, including nested objects

---

# 2. Persistence Tests (Stage 2)

## Test: Save hypothesis creates file at correct path — **P0**

### Goal
Verify persistence layer creates files in expected location.

### Input
```python
hyp = Hypothesis(id="HYP-001", ...)
save_hypothesis(hyp)
```

### Expected
File exists at `research/hypotheses/HYP-001.yaml`

### Method
Assert `Path("research/hypotheses/HYP-001.yaml").exists()`

---

## Test: Save-load hypothesis roundtrip — **P0**

### Goal
Ensure YAML serialization preserves all fields.

### Input
```python
original = Hypothesis(id="HYP-001", title="Test", rationale="...", tags=["orb"])
save_hypothesis(original)
loaded = load_hypothesis("HYP-001")
```

### Expected
`loaded == original` (all fields match)

### Method
Field-by-field comparison, including datetime precision

### Why This Matters
Data loss during serialization breaks reproducibility and audit trails.

---

## Test: Completed experiment saved with read-only permissions — **P0**

### Goal
Enforce immutability at filesystem level.

### Input
```python
exp = Experiment(id="EXP-001", status=ExperimentStatus.COMPLETED, ...)
save_experiment(exp)
```

### Expected
File permissions are `0o444` (read-only)

### Method
`os.stat(path).st_mode & 0o777 == 0o444`

### Why This Matters
Read-only files prevent accidental edits via text editor, enforcing immutability policy.

---

## Test: Update status rejected for completed experiment — **P0**

### Goal
Prevent status updates on immutable experiments.

### Input
```python
update_experiment_status("EXP-001", ExperimentStatus.RUNNING)
# EXP-001 is already COMPLETED
```

### Expected
`ImmutabilityError` raised

### Method
Load experiment, check `is_immutable()`, raise error if True

### Why This Matters
Completed experiments must remain frozen; status changes would corrupt lineage and conclusions.

---

## Test: YAML datetime serialization — P1

### Goal
Ensure datetimes serialize to human-readable ISO 8601 format.

### Input
```python
hyp = Hypothesis(created_at=datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc))
save_hypothesis(hyp)
```

### Expected
YAML contains `created_at: 2026-05-23T12:00:00+00:00`

### Method
Parse YAML file, check string format

---

# 3. Git Metadata Tests (Stage 3)

## Test: Get git commit hash returns 40 hex chars — **P0**

### Goal
Validate Git hash format for reproducibility.

### Input
```python
commit_hash = get_git_commit_hash()
```

### Expected
`len(commit_hash) == 40` and `commit_hash` is hex

### Method
Regex: `^[0-9a-f]{40}$`

### Why This Matters
Invalid Git hashes break reproducibility; experiments cannot be traced to specific code versions.

---

## Test: Is git dirty detects uncommitted changes — **P0**

### Goal
Warn researchers when running experiments with uncommitted code.

### Input
```python
# Modify a file without committing
Path("test.py").write_text("# change")
dirty = is_git_dirty()
```

### Expected
`dirty == True`

### Method
Run `git diff --quiet`, check exit code

### Why This Matters
Experiments run with dirty state are not reproducible; warnings prevent silent reproducibility failures.

---

## Test: Capture execution metadata populates all fields — **P0**

### Goal
Ensure no metadata fields are accidentally skipped.

### Input
```python
metadata = capture_execution_metadata(random_seed=42)
```

### Expected
All fields non-null:
- `git_commit` (40 chars)
- `git_branch` (non-empty string)
- `git_dirty` (bool)
- `random_seed == 42`
- `executed_at` (timezone-aware datetime)
- `python_version` (contains "3.")

### Method
Assert each field individually

### Why This Matters
Missing metadata breaks reproducibility; all fields are required for experiment reconstruction.

---

## Test: Git not found raises clear error — P1

### Goal
Handle non-Git directories gracefully.

### Input
```python
get_git_commit_hash(repo_path="/tmp")
```

### Expected
`GitNotFoundError` with message "Not a Git repository"

### Method
Catch subprocess error, wrap in custom exception

---

# 4. Lineage Tests (Stage 4)

## Test: Lineage graph detects cycle — **P0**

### Goal
Prevent lineage corruption from circular references.

### Input
```python
exp_a = Experiment(id="EXP-001", parent_experiment_id=None)
exp_b = Experiment(id="EXP-002", parent_experiment_id="EXP-001")
exp_c = Experiment(id="EXP-003", parent_experiment_id="EXP-002")
exp_a.parent_experiment_id = "EXP-003"  # Create cycle

graph = LineageGraph([exp_a, exp_b, exp_c])
graph.validate_no_cycles()
```

### Expected
`CycleDetectedError` raised

### Method
DFS with visited set + recursion stack

### Why This Matters
Cycles break lineage traversal and invalidate ancestor/descendant queries.

---

## Test: Get descendants returns all children — **P0**

### Goal
Verify recursive traversal for lineage queries.

### Input
```python
# A → B → C
#   → D
graph = LineageGraph([exp_a, exp_b, exp_c, exp_d])
descendants = graph.get_descendants("EXP-001")
```

### Expected
`descendants == ["EXP-002", "EXP-003", "EXP-004"]` (order doesn't matter)

### Method
Recursive traversal, collect all reachable nodes

---

## Test: Find root for nested experiment — **P0**

### Goal
Ensure lineage queries can trace to original experiment.

### Input
```python
# ROOT → A → B → C
graph.find_root("EXP-004")  # C is 3 levels deep
```

### Expected
Returns "ROOT"

### Method
Traverse parent_id until `parent_id is None`

### Why This Matters
Root experiments represent original hypotheses; finding root is critical for lineage analysis.

---

## Test: Warn on deep nesting — P1

### Goal
Alert researchers to potentially over-complex lineage.

### Input
```python
# Create lineage depth = 6
graph = LineageGraph([...])
graph.get_depth("EXP-006")
```

### Expected
Warning logged: "Lineage depth (6) exceeds recommended maximum (5)"

### Method
Check depth in getter, log warning if > threshold

---

# 5. Registry Tests (Stage 5)

## Test: Create hypothesis generates unique ID — **P0**

### Goal
Prevent ID collisions.

### Input
```python
registry = ResearchRegistry()
hyp1 = registry.create_hypothesis(title="Test 1", rationale="...")
hyp2 = registry.create_hypothesis(title="Test 2", rationale="...")
```

### Expected
`hyp1.id != hyp2.id` and both match `HYP-\d{3,}` format

### Method
Check ID uniqueness and format

### Why This Matters
Duplicate IDs corrupt file system and break cross-references.

---

## Test: Create experiment captures git metadata — **P0**

### Goal
Ensure reproducibility metadata captured automatically.

### Input
```python
exp = registry.create_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={},
    dataset_config={}
)
```

### Expected
`exp.execution_metadata.git_commit` is 40-char hex string
`exp.execution_metadata.git_branch` is non-empty
`exp.execution_metadata.executed_at` is recent datetime

### Method
Assert metadata fields populated

### Why This Matters
Missing Git metadata breaks experiment reproducibility; automatic capture prevents human error.

---

## Test: Complete experiment enforces immutability — **P0**

### Goal
Prevent double-completion or re-completion.

### Input
```python
exp_id = "EXP-001"
registry.complete_experiment(exp_id, results={}, conclusion="Done")
registry.complete_experiment(exp_id, results={}, conclusion="Changed")  # Should fail
```

### Expected
Second call raises `ImmutabilityError`

### Method
Load experiment, check status before completing

### Why This Matters
Re-completing experiments overwrites conclusions and corrupts audit trail.

---

## Test: Create experiment validates parent exists — **P0**

### Goal
Prevent orphaned lineage references.

### Input
```python
registry.create_experiment(
    parent_experiment_id="EXP-999",  # Does not exist
    ...
)
```

### Expected
`ValidationError` raised with message "Parent experiment EXP-999 not found"

### Method
Load parent before creating child

### Why This Matters
Invalid parent references break lineage queries and graph traversal.

---

## Test: Registry thread-safe ID generation — P2

### Goal
Prevent ID collisions under concurrent access.

### Input
```python
import concurrent.futures
registry = ResearchRegistry()

def create_hyp():
    return registry.create_hypothesis(title="Test", rationale="...")

with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    results = list(executor.map(create_hyp, range(20)))

ids = [h.id for h in results]
```

### Expected
All IDs unique (no duplicates)

### Method
Use file locking or atomic counter

---

# 6. Query Tests (Stage 6)

## Test: Query by parameter with nested path — **P0**

### Goal
Ensure parameter queries can access nested config values.

### Input
```python
exp = Experiment(
    parameters={"strategy": {"orb_duration_minutes": 15}}
)
query = ExperimentQuery(registry)
results = query.by_parameter("strategy.orb_duration_minutes", 15)
```

### Expected
`exp in results`

### Method
Use dict path traversal (e.g., `["strategy"]["orb_duration_minutes"]`)

### Why This Matters
Parameter optimization requires querying nested configuration paths.

---

## Test: Query by result quality filters correctly — **P0**

### Goal
Validate result-based filtering for strategy selection.

### Input
```python
exp1 = Experiment(results_summary={"sharpe_ratio": 1.5})
exp2 = Experiment(results_summary={"sharpe_ratio": 0.8})

query = ExperimentQuery(registry)
results = query.by_result_quality("sharpe_ratio", min_value=1.0)
```

### Expected
`exp1 in results` and `exp2 not in results`

### Method
Filter results where `results_summary[metric] >= min_value`

### Why This Matters
Filtering by performance metrics is core to strategy selection workflow.

---

## Test: Query combine returns intersection — **P0**

### Goal
Enable complex queries (e.g., "ORB experiments with Sharpe > 1.0").

### Input
```python
orb_experiments = query.by_tag("orb")
good_experiments = query.by_result_quality("sharpe_ratio", 1.0)
combined = query.combine(orb_experiments, good_experiments)
```

### Expected
`combined` contains only experiments in BOTH lists

### Method
Set intersection

---

# 7. Artifact Tracker Tests (Stage 7)

## Test: Register artifact computes checksum — **P0**

### Goal
Ensure artifact integrity validation.

### Input
```python
Path("report.html").write_text("<html>...</html>")
artifact = register_artifact("EXP-001", Path("report.html"), "html")
```

### Expected
`len(artifact.checksum) == 64` (SHA256 hex digest)

### Method
Compute SHA256, assert length and hex format

### Why This Matters
Checksums detect file corruption and tampering; missing checksums break integrity guarantees.

---

## Test: Verify artifact detects tampering — **P0**

### Goal
Catch corrupted or modified artifact files.

### Input
```python
artifact = register_artifact("EXP-001", Path("report.html"), "html")
Path("report.html").write_text("<html>MODIFIED</html>")
valid = verify_artifact(artifact)
```

### Expected
`valid == False`

### Method
Recompute checksum, compare to stored checksum

### Why This Matters
Silent corruption of result files invalidates research conclusions; verification detects this.

---

## Test: Compute SHA256 handles large files — P2

### Goal
Prevent memory errors on multi-GB artifacts.

### Input
```python
# Create 100MB file
with open("large.csv", "wb") as f:
    f.write(b"x" * (100 * 1024 * 1024))

checksum = compute_sha256(Path("large.csv"))
```

### Expected
No `MemoryError` raised, checksum computed

### Method
Read file in 8KB chunks, update hash incrementally

---

# 8. Integration Tests (Stage 8)

## Test: Backtest result to summary extracts metrics — **P0**

### Goal
Ensure all critical metrics captured from BacktestResult.

### Input
```python
result = BacktestResult(
    overall=ConvexityMetrics(expectancy_r=0.15, ...),
    equity=EquityMetrics(sharpe_ratio=1.2, max_drawdown=-0.12, ...),
    ...
)
summary = backtest_result_to_summary(result)
```

### Expected
`summary` contains: `sharpe_ratio`, `max_drawdown`, `expectancy_r`, `win_rate`, `total_trades`

### Method
Assert key presence in dict

---

## Test: Register backtest creates completed experiment — **P0**

### Goal
End-to-end integration test.

### Input
```python
result = BacktestResult(...)
exp = register_backtest_experiment(
    result,
    hypothesis_id="HYP-001",
    parameters={},
    dataset_config={},
    registry=registry,
    conclusion="Edge validated"
)
```

### Expected
- `exp.status == ExperimentStatus.COMPLETED`
- `exp.results_summary` populated
- `exp.conclusion == "Edge validated"`
- File saved at `research/experiments/{exp.id}.yaml`

### Method
Load experiment from disk, verify fields

---

## Test: Sweep creates child experiments with lineage — **P0**

### Goal
Validate parameter sweep lineage tracking.

### Input
```python
sweep = ParameterSweep(parameters=[...])
children = register_sweep_as_lineage(
    sweep,
    parent_experiment_id="EXP-001",
    hypothesis_id="HYP-001",
    registry=registry
)
```

### Expected
- `len(children) == number_of_parameter_combinations`
- All children have `parent_experiment_id == "EXP-001"`
- Lineage graph valid (no cycles)

### Method
Build lineage graph, validate structure

### Why This Matters
Sweep integration is core workflow; broken lineage corrupts optimization history.

---

## Test: Integration backward compatible — **P0**

### Goal
Ensure existing code works without experiment tracking.

### Input
```python
# Old workflow (no experiment_id)
engine = BacktestEngine(...)
result = engine.run(symbol="QQQ", start_date="2020-01-01", end_date="2020-12-31")
```

### Expected
No errors raised; result returned normally

### Method
Run backtest without registry, assert success

### Why This Matters
Breaking backward compatibility disrupts existing research workflows.

---

## Summary

**80+ tests** across 8 modules ensure:
- ✅ Scientific integrity (immutability, metadata capture)
- ✅ Data integrity (checksums, validation)
- ✅ Reproducibility (Git metadata, parameter tracking)
- ✅ Query correctness (filtering, lineage traversal)
- ✅ Integration reliability (backward compatibility, lineage tracking)

**All P0 tests must pass** before deploying framework to production research.
