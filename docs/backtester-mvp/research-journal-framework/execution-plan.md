# Execution Plan — Research Journal Framework

Each stage delivers one vertical slice: functional code + the tests that validate it.
No stage is "done" until its tests pass.

**Source tree for all new code:**
```
vibe/research_journal/
    __init__.py
    models.py                 # Stage 1 — Domain models
    persistence.py            # Stage 2 — YAML read/write
    git_metadata.py           # Stage 3 — Git integration
    lineage.py                # Stage 4 — Lineage graph
    registry.py               # Stage 5 — Experiment registry
    query.py                  # Stage 6 — Query API
    artifact_tracker.py       # Stage 7 — Artifact management

vibe/research_journal/integration/
    __init__.py
    backtest_adapter.py       # Stage 8 — BacktestResult → Experiment
    sweep_adapter.py          # Stage 8 — ParameterSweep integration

vibe/tests/research_journal/
    __init__.py
    test_models.py            # Stage 1
    test_persistence.py       # Stage 2
    test_git_metadata.py      # Stage 3
    test_lineage.py           # Stage 4
    test_registry.py          # Stage 5
    test_query.py             # Stage 6
    test_artifact_tracker.py  # Stage 7
    test_integration.py       # Stage 8

research/                     # Git-tracked research metadata (created by Stage 2)
    hypotheses/
    experiments/
    notes/
    rejected/
    artifacts/
        .gitkeep
        registry.yaml
```

---

## Stage 1 — Domain Models

**Delivers:** Pydantic models for Hypothesis, Experiment, ResearchNote, RejectedIdea, and ArtifactReference. Models enforce validation, immutability (when completed), and type safety.

### Functional work

**Domain models** (`vibe/research_journal/models.py`) — new file

Enums to implement:
- `HypothesisStatus` — enum: `PROPOSED`, `ACTIVE`, `VALIDATED`, `INVALIDATED`, `ARCHIVED`
- `ExperimentStatus` — enum: `REGISTERED`, `RUNNING`, `COMPLETED`, `FAILED`, `ARCHIVED`

Models to implement:
- `Hypothesis` (Pydantic BaseModel):
  - Fields:
    - `id: str` — format: "HYP-NNN" (e.g., "HYP-001")
    - `title: str` — concise hypothesis statement
    - `rationale: str` — why we believe this hypothesis
    - `status: HypothesisStatus` — lifecycle state
    - `tags: List[str]` — categorization (e.g., ["orb", "volume", "momentum"])
    - `created_at: datetime` — UTC timestamp
    - `updated_at: datetime` — UTC timestamp
  - Validators:
    - ID must match pattern `HYP-\d{3,}`
    - Title max 200 chars
    - Rationale min 10 chars

- `ExecutionMetadata` (Pydantic BaseModel):
  - Fields:
    - `git_commit: str` — full commit hash
    - `git_branch: str` — branch name
    - `git_dirty: bool` — uncommitted changes flag
    - `random_seed: Optional[int]` — for reproducibility
    - `executed_at: datetime` — UTC timestamp
    - `python_version: str` — e.g., "3.12.0"

- `Experiment` (Pydantic BaseModel):
  - Fields:
    - `id: str` — format: "EXP-NNN"
    - `hypothesis_id: Optional[str]` — link to hypothesis
    - `parent_experiment_id: Optional[str]` — for lineage
    - `strategy_name: str` — e.g., "ORBStrategy"
    - `strategy_version: str` — e.g., "1.4.2"
    - `parameters: Dict[str, Any]` — parameter configuration
    - `dataset_config: Dict[str, Any]` — symbol, date range, etc.
    - `execution_metadata: ExecutionMetadata`
    - `status: ExperimentStatus`
    - `results_summary: Optional[Dict[str, Any]]` — metrics (Sharpe, expectancy, etc.)
    - `conclusion: Optional[str]` — human conclusion
    - `artifacts: List[str]` — artifact IDs
    - `tags: List[str]`
    - `created_at: datetime`
    - `completed_at: Optional[datetime]`
  - Validators:
    - ID must match pattern `EXP-\d{3,}`
    - If hypothesis_id provided, must match `HYP-\d{3,}` pattern
    - If parent_experiment_id provided, must match `EXP-\d{3,}` pattern
    - If status is COMPLETED, results_summary must not be None
  - Methods:
    - `mark_completed(results: dict, conclusion: str) -> None` — transition to COMPLETED
    - `is_immutable() -> bool` — returns True if status is COMPLETED/FAILED

- `ResearchNote` (Pydantic BaseModel):
  - Fields:
    - `id: str` — format: "NOTE-NNN"
    - `content: str` — freeform observation
    - `related_experiment_id: Optional[str]`
    - `tags: List[str]`
    - `created_at: datetime`
  - Validators:
    - ID must match pattern `NOTE-\d{3,}`
    - Content min 10 chars

- `RejectedIdea` (Pydantic BaseModel):
  - Fields:
    - `id: str` — format: "RJ-NNN"
    - `idea: str` — what was tested
    - `reason_rejected: str` — why it failed
    - `evidence: List[str]` — experiment IDs that invalidated it
    - `tags: List[str]`
    - `created_at: datetime`
  - Validators:
    - ID must match pattern `RJ-\d{3,}`
    - Evidence must be list of valid EXP-NNN IDs

- `ArtifactReference` (Pydantic BaseModel):
  - Fields:
    - `id: str` — format: "ART-NNN"
    - `experiment_id: str`
    - `path: str` — relative path from repo root
    - `artifact_type: str` — "parquet" | "html" | "csv" | "image"
    - `checksum: str` — SHA256 hash
    - `size_bytes: int`
    - `created_at: datetime`
  - Validators:
    - Path must not contain ".."
    - Checksum must be 64 hex chars (SHA256)

Requirements:
- All models must use Pydantic BaseModel
- All datetime fields must be timezone-aware (UTC)
- Models must support `.model_dump()` for serialization
- IDs must be unique and follow format conventions

### Validation tests (`test_models.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_hypothesis_id_format_validation` | **P0** | Rejects invalid ID formats (e.g., "HYP-1", "HYP-ABC", "001") |
| `test_experiment_status_lifecycle` | **P0** | REGISTERED → RUNNING → COMPLETED transitions valid |
| `test_experiment_immutability_when_completed` | **P0** | Cannot modify parameters after mark_completed() |
| `test_execution_metadata_captures_git_state` | P1 | All ExecutionMetadata fields populated |
| `test_hypothesis_title_max_length` | P1 | Rejects titles > 200 chars |
| `test_experiment_requires_results_when_completed` | **P0** | Status=COMPLETED requires results_summary |
| `test_artifact_reference_rejects_parent_traversal` | **P0** | Path="../data.csv" raises ValidationError |
| `test_rejected_idea_evidence_format` | P1 | Evidence list contains only valid EXP-NNN IDs |
| `test_model_serialization_roundtrip` | P1 | model.model_dump() → dict → model reproduces original |
| `test_datetime_fields_are_timezone_aware` | **P0** | created_at/updated_at have tzinfo |

### Stage 1 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Models validated with mypy (type checking)
- [ ] Documentation strings added to all classes

---

## Stage 2 — Persistence Layer

**Delivers:** YAML-based persistence for domain models. Supports save/load for Hypothesis, Experiment, ResearchNote, RejectedIdea. Creates directory structure automatically.

### Functional work

**Persistence module** (`vibe/research_journal/persistence.py`) — new file

Functions to implement:
- `ensure_research_directories() -> Path`:
  - Creates `research/hypotheses/`, `research/experiments/`, `research/notes/`, `research/rejected/`, `research/artifacts/`
  - Returns Path to research/ root
  - Idempotent (safe to call multiple times)

- `save_hypothesis(hypothesis: Hypothesis, research_root: Path | None = None) -> Path`:
  - Saves to `research/hypotheses/{hypothesis.id}.yaml`
  - Uses `hypothesis.model_dump(mode='json')` for serialization
  - Returns path to saved file
  - Raises `FileExistsError` if file already exists (prevent overwrites)

- `load_hypothesis(hypothesis_id: str, research_root: Path | None = None) -> Hypothesis`:
  - Loads from `research/hypotheses/{hypothesis_id}.yaml`
  - Returns Hypothesis instance
  - Raises `FileNotFoundError` if not found

- `save_experiment(experiment: Experiment, research_root: Path | None = None) -> Path`:
  - Saves to `research/experiments/{experiment.id}.yaml`
  - If experiment.is_immutable(), saves with read-only permissions (chmod 0o444)
  - Returns path to saved file

- `load_experiment(experiment_id: str, research_root: Path | None = None) -> Experiment`:
  - Loads from `research/experiments/{experiment_id}.yaml`
  - Returns Experiment instance

- `update_experiment_status(experiment_id: str, status: ExperimentStatus, research_root: Path | None = None) -> None`:
  - Loads experiment, updates status, saves back
  - Only allowed if current status is not COMPLETED/FAILED (immutable states)
  - Raises `ImmutabilityError` if trying to modify completed experiment

- `save_research_note(note: ResearchNote, research_root: Path | None = None) -> Path`:
  - Saves to `research/notes/{note.id}.md` (Markdown format, not YAML)
  - Frontmatter contains metadata, body contains content

- `save_rejected_idea(idea: RejectedIdea, research_root: Path | None = None) -> Path`:
  - Saves to `research/rejected/{idea.id}.yaml`

Requirements:
- Use PyYAML for serialization
- All datetime fields serialized as ISO 8601 strings
- Preserve field order for human readability (use `sort_keys=False`)
- Handle missing files gracefully (raise clear exceptions)

### Validation tests (`test_persistence.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_save_hypothesis_creates_file` | **P0** | YAML file created at correct path |
| `test_save_load_hypothesis_roundtrip` | **P0** | Loaded hypothesis equals saved hypothesis |
| `test_save_experiment_prevents_overwrite` | **P0** | FileExistsError raised if file exists |
| `test_completed_experiment_saved_readonly` | **P0** | File permissions 0o444 for completed experiments |
| `test_update_status_rejected_for_completed` | **P0** | ImmutabilityError when updating completed experiment |
| `test_load_nonexistent_hypothesis_raises` | P1 | FileNotFoundError with clear message |
| `test_research_directories_created_idempotent` | P1 | Multiple calls don't fail |
| `test_yaml_datetime_serialization` | P1 | Datetimes serialize to ISO 8601 |
| `test_research_note_markdown_format` | P1 | Note saved as .md with frontmatter + body |

### Stage 2 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Directory structure created in workspace
- [ ] Example YAML files validated for human readability

---

## Stage 3 — Git Metadata Capture

**Delivers:** Git integration module that captures commit hash, branch, dirty state using subprocess. Warns if repository is dirty (uncommitted changes).

### Functional work

**Git module** (`vibe/research_journal/git_metadata.py`) — new file

Functions to implement:
- `get_git_commit_hash(repo_path: Path | None = None) -> str`:
  - Runs `git rev-parse HEAD`
  - Returns full commit hash (40 chars)
  - Raises `GitNotFoundError` if not in Git repo
  - Uses subprocess.run() with cwd=repo_path

- `get_git_branch(repo_path: Path | None = None) -> str`:
  - Runs `git rev-parse --abbrev-ref HEAD`
  - Returns branch name
  - Returns "HEAD" if detached HEAD state

- `is_git_dirty(repo_path: Path | None = None) -> bool`:
  - Runs `git diff --quiet` and `git diff --cached --quiet`
  - Returns True if uncommitted changes exist
  - Returns False if working tree clean

- `get_python_version() -> str`:
  - Returns `sys.version` (e.g., "3.12.0")

- `capture_execution_metadata(repo_path: Path | None = None, random_seed: int | None = None) -> ExecutionMetadata`:
  - Combines all git metadata capture
  - Logs warning if is_git_dirty() is True
  - Returns ExecutionMetadata instance with all fields populated

Requirements:
- Use subprocess.run() with capture_output=True, text=True
- Handle subprocess errors gracefully (non-Git directories)
- Log warnings for dirty state (don't block execution)
- Support optional repo_path override for testing

### Validation tests (`test_git_metadata.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_get_git_commit_hash_returns_40_chars` | **P0** | Hash is exactly 40 hex characters |
| `test_get_git_branch_returns_current_branch` | **P0** | Branch name matches git CLI output |
| `test_is_git_dirty_detects_uncommitted_changes` | **P0** | Returns True after file modification |
| `test_git_not_found_raises_clear_error` | P1 | GitNotFoundError when run outside Git repo |
| `test_capture_execution_metadata_populates_all_fields` | **P0** | All ExecutionMetadata fields non-null |
| `test_dirty_state_logs_warning` | P1 | Warning logged when working tree dirty |
| `test_python_version_format` | P1 | Version string contains "3." |

### Stage 3 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Integration tested in actual Git repository
- [ ] Dirty state warning message verified

---

## Stage 4 — Lineage Graph

**Delivers:** Lineage tracking system with parent/child relationships, cycle detection, and graph traversal utilities.

### Functional work

**Lineage module** (`vibe/research_journal/lineage.py`) — new file

Classes to implement:
- `LineageGraph`:
  - `__init__(self, experiments: List[Experiment])`
  - `add_experiment(self, experiment: Experiment) -> None`
  - `get_children(self, experiment_id: str) -> List[str]` — direct children only
  - `get_descendants(self, experiment_id: str) -> List[str]` — all descendants (recursive)
  - `get_parent(self, experiment_id: str) -> Optional[str]`
  - `get_ancestors(self, experiment_id: str) -> List[str]` — all ancestors to root
  - `find_root(self, experiment_id: str) -> str` — traverse to root (no parent)
  - `validate_no_cycles(self) -> None` — raises `CycleDetectedError` if cycle exists
  - `get_depth(self, experiment_id: str) -> int` — distance from root (root=0)
  - `to_dict(self) -> Dict[str, Any]` — serialize lineage structure

Functions to implement:
- `build_lineage_graph(research_root: Path | None = None) -> LineageGraph`:
  - Loads all experiments from `research/experiments/`
  - Builds LineageGraph
  - Validates no cycles
  - Returns graph

Requirements:
- Use adjacency list representation (parent_id → [child_ids])
- Detect cycles using visited set + recursion stack
- Warn if depth > 5 levels (deep nesting may indicate design issue)
- Handle experiments with no parent (root experiments)

### Validation tests (`test_lineage.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_lineage_graph_detects_cycle` | **P0** | CycleDetectedError when A→B→C→A |
| `test_get_descendants_returns_all_children` | **P0** | Recursive traversal works |
| `test_get_ancestors_returns_path_to_root` | **P0** | Ancestor chain correct |
| `test_find_root_for_nested_experiment` | **P0** | Finds top-level parent |
| `test_lineage_depth_calculation` | P1 | Root=0, child=1, grandchild=2 |
| `test_warn_on_deep_nesting` | P1 | Warning logged for depth > 5 |
| `test_multiple_children_from_same_parent` | P1 | Graph handles branching |
| `test_orphan_experiment_has_no_parent` | P1 | get_parent() returns None for root |

### Stage 4 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Cycle detection validated with complex graphs
- [ ] Documentation includes graph visualization example

---

## Stage 5 — Experiment Registry

**Delivers:** High-level registry API that combines models + persistence + lineage. Provides create_experiment(), register_hypothesis(), get_experiment(), etc.

### Functional work

**Registry module** (`vibe/research_journal/registry.py`) — new file

Classes to implement:
- `ResearchRegistry`:
  - `__init__(self, research_root: Path | None = None)`
  - `create_hypothesis(self, title: str, rationale: str, tags: List[str] = []) -> Hypothesis`:
    - Auto-generates ID (sequential: HYP-001, HYP-002, ...)
    - Sets status=PROPOSED, created_at=now()
    - Saves to disk
    - Returns Hypothesis instance
  
  - `create_experiment(self, hypothesis_id: str | None, strategy_name: str, strategy_version: str, parameters: dict, dataset_config: dict, parent_experiment_id: str | None = None, tags: List[str] = [], random_seed: int | None = None) -> Experiment`:
    - Auto-generates ID (sequential: EXP-001, EXP-002, ...)
    - Captures ExecutionMetadata via git_metadata.capture_execution_metadata()
    - Sets status=REGISTERED, created_at=now()
    - Validates parent exists (if parent_experiment_id provided)
    - Validates no cycle would be created
    - Saves to disk
    - Returns Experiment instance
  
  - `complete_experiment(self, experiment_id: str, results: dict, conclusion: str) -> Experiment`:
    - Loads experiment
    - Calls experiment.mark_completed(results, conclusion)
    - Sets completed_at=now()
    - Saves to disk (with read-only permissions)
    - Returns updated Experiment
  
  - `get_experiment(self, experiment_id: str) -> Experiment`:
    - Loads from disk
  
  - `get_hypothesis(self, hypothesis_id: str) -> Hypothesis`:
    - Loads from disk
  
  - `list_experiments(self, status: ExperimentStatus | None = None, hypothesis_id: str | None = None) -> List[Experiment]`:
    - Scans research/experiments/
    - Filters by status/hypothesis_id if provided
  
  - `add_research_note(self, content: str, related_experiment_id: str | None = None, tags: List[str] = []) -> ResearchNote`:
    - Auto-generates ID (NOTE-001, ...)
    - Saves to disk
  
  - `reject_idea(self, idea: str, reason: str, evidence: List[str] = [], tags: List[str] = []) -> RejectedIdea`:
    - Auto-generates ID (RJ-001, ...)
    - Saves to disk

Helper functions:
- `_next_id(self, prefix: str) -> str`:
  - Scans existing files, finds max ID number
  - Returns next sequential ID (e.g., "HYP-003")

Requirements:
- Registry must be thread-safe for ID generation (use file locking or atomic operations)
- All creates must validate inputs before saving
- Experiment completion must enforce immutability
- Registry must refresh lineage graph after experiment creation

### Validation tests (`test_registry.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_create_hypothesis_generates_unique_id` | **P0** | IDs are sequential and unique |
| `test_create_experiment_captures_git_metadata` | **P0** | ExecutionMetadata populated automatically |
| `test_complete_experiment_enforces_immutability` | **P0** | Second complete_experiment() raises error |
| `test_create_experiment_validates_parent_exists` | **P0** | Raises error if parent_experiment_id invalid |
| `test_create_experiment_prevents_cycle` | **P0** | Cannot create lineage cycle |
| `test_list_experiments_filters_by_status` | P1 | Returns only COMPLETED experiments when filtered |
| `test_registry_thread_safe_id_generation` | P2 | Concurrent creates don't collide |
| `test_reject_idea_saves_evidence` | P1 | Evidence list preserved in YAML |

### Stage 5 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] End-to-end workflow tested (create hypothesis → create experiment → complete → query)
- [ ] Example usage script created

---

## Stage 6 — Query API

**Delivers:** Search and query interface for finding experiments by tag, parameter, status, result quality, date range.

### Functional work

**Query module** (`vibe/research_journal/query.py`) — new file

Classes to implement:
- `ExperimentQuery`:
  - `__init__(self, registry: ResearchRegistry)`
  - `by_tag(self, tag: str) -> List[Experiment]`:
    - Returns experiments with matching tag
  
  - `by_status(self, status: ExperimentStatus) -> List[Experiment]`:
    - Returns experiments with given status
  
  - `by_hypothesis(self, hypothesis_id: str) -> List[Experiment]`:
    - Returns all experiments for hypothesis
  
  - `by_parameter(self, param_path: str, value: Any) -> List[Experiment]`:
    - Returns experiments where parameters[param_path] == value
    - Example: by_parameter("strategy.orb_duration_minutes", 15)
  
  - `by_date_range(self, start_date: datetime, end_date: datetime) -> List[Experiment]`:
    - Returns experiments created_at within range
  
  - `by_result_quality(self, metric: str, min_value: float, max_value: float | None = None) -> List[Experiment]`:
    - Returns experiments where results_summary[metric] >= min_value (and <= max_value if provided)
    - Example: by_result_quality("sharpe_ratio", 1.0) → Sharpe >= 1.0
  
  - `combine(self, *queries: List[Experiment]) -> List[Experiment]`:
    - Intersection of multiple query results
    - Example: `combine(by_tag("orb"), by_result_quality("sharpe_ratio", 1.0))`

- `HypothesisQuery`:
  - `__init__(self, registry: ResearchRegistry)`
  - `by_status(self, status: HypothesisStatus) -> List[Hypothesis]`
  - `by_tag(self, tag: str) -> List[Hypothesis]`
  - `with_experiments(self) -> List[Tuple[Hypothesis, List[Experiment]]]`:
    - Returns hypotheses with their associated experiments

Requirements:
- Queries should scan YAML files (no database for Phase 1)
- Support case-insensitive tag matching
- Handle missing result fields gracefully (skip experiments without metric)
- Queries should be composable (chainable)

### Validation tests (`test_query.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_query_by_tag_case_insensitive` | P1 | "ORB" matches "orb" |
| `test_query_by_parameter_nested_path` | **P0** | Can query "strategy.orb_duration_minutes" |
| `test_query_by_result_quality_filters_correctly` | **P0** | Returns only experiments with Sharpe >= threshold |
| `test_query_combine_returns_intersection` | **P0** | Combine([A,B,C], [B,C,D]) → [B,C] |
| `test_query_handles_missing_metric_gracefully` | P1 | Skips experiments without requested metric |
| `test_query_by_date_range_inclusive` | P1 | Start/end dates included in results |
| `test_hypothesis_query_with_experiments` | P1 | Returns experiments grouped by hypothesis |

### Stage 6 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Query performance acceptable (<1s for 100 experiments)
- [ ] Example query notebook created

---

## Stage 7 — Artifact Tracking

**Delivers:** Artifact registry for referencing large files (reports, datasets, charts) without committing them to Git. Supports checksum validation.

### Functional work

**Artifact tracker** (`vibe/research_journal/artifact_tracker.py`) — new file

Functions to implement:
- `register_artifact(experiment_id: str, file_path: Path, artifact_type: str, research_root: Path | None = None) -> ArtifactReference`:
  - Computes SHA256 checksum of file
  - Gets file size
  - Creates ArtifactReference
  - Appends to `research/artifacts/registry.yaml`
  - Returns ArtifactReference

- `verify_artifact(artifact_ref: ArtifactReference) -> bool`:
  - Recomputes checksum of file at artifact_ref.path
  - Returns True if checksum matches
  - Returns False if mismatch or file missing

- `list_artifacts(experiment_id: str, research_root: Path | None = None) -> List[ArtifactReference]`:
  - Loads registry, filters by experiment_id
  - Returns list of artifacts

- `compute_sha256(file_path: Path) -> str`:
  - Reads file in chunks (for large files)
  - Returns hex digest

Requirements:
- Checksum computation must handle multi-GB files efficiently
- registry.yaml should be human-readable (list of artifact entries)
- Warn if artifact file > 1MB in research/ directory (should be in external storage)

### Validation tests (`test_artifact_tracker.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_register_artifact_computes_checksum` | **P0** | SHA256 hash is 64 hex chars |
| `test_verify_artifact_detects_tampering` | **P0** | Returns False after file modification |
| `test_register_artifact_appends_to_registry` | **P0** | Multiple artifacts saved to same registry |
| `test_list_artifacts_filters_by_experiment` | P1 | Returns only matching experiment_id |
| `test_compute_sha256_handles_large_files` | P2 | No memory error on 100MB file |
| `test_warn_if_artifact_in_research_dir` | P1 | Warning logged for large files in research/ |

### Stage 7 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Checksum validation tested with real report files
- [ ] .gitignore updated to exclude artifacts/

---

## Stage 8 — Integration with Existing Systems

**Delivers:** Adapters that connect BacktestResult → Experiment and ParameterSweep → Lineage. Enables automatic experiment registration during backtests and optimization runs.

### Functional work

**Backtest adapter** (`vibe/research_journal/integration/backtest_adapter.py`) — new file

Functions to implement:
- `backtest_result_to_summary(result: BacktestResult) -> dict`:
  - Extracts key metrics from BacktestResult
  - Returns dict with: sharpe_ratio, max_drawdown, expectancy_r, win_rate, total_trades, etc.

- `register_backtest_experiment(result: BacktestResult, hypothesis_id: str | None, parameters: dict, dataset_config: dict, registry: ResearchRegistry, conclusion: str = "") -> Experiment`:
  - Creates experiment via registry.create_experiment()
  - Completes experiment with results
  - Returns Experiment

**Sweep adapter** (`vibe/research_journal/integration/sweep_adapter.py`) — new file

Functions to implement:
- `register_sweep_as_lineage(sweep: ParameterSweep, parent_experiment_id: str, hypothesis_id: str | None, registry: ResearchRegistry) -> List[Experiment]`:
  - Runs sweep (or uses pre-computed results)
  - For each parameter combination, creates child experiment with parent_experiment_id
  - Returns list of created experiments

Requirements:
- Integration must be opt-in (backward compatible with existing code)
- ParameterSweep should accept optional `experiment_registry` parameter
- BacktestEngine should accept optional `experiment_id` parameter
- Trade model should add optional `experiment_id` field

### Validation tests (`test_integration.py`)

| Test | Tier | What it checks |
|------|------|----------------|
| `test_backtest_result_to_summary_extracts_metrics` | **P0** | Dict contains sharpe_ratio, max_drawdown, etc. |
| `test_register_backtest_creates_completed_experiment` | **P0** | Experiment status=COMPLETED after registration |
| `test_sweep_creates_child_experiments` | **P0** | Each parameter combo creates child with correct parent_id |
| `test_sweep_lineage_no_cycles` | **P0** | Lineage graph valid after sweep |
| `test_integration_backward_compatible` | **P0** | BacktestEngine works without experiment_id |
| `test_trade_experiment_id_field` | P1 | Trade model accepts experiment_id |

### Stage 8 Complete When:
- [ ] All P0 tests pass
- [ ] All P1 tests pass
- [ ] Example end-to-end backtest with experiment registration
- [ ] Example parameter sweep with lineage tracking
- [ ] Documentation updated with integration examples

---

## Summary

**8 stages** deliver a complete research journal framework with:
- ✅ Domain models with validation (Stage 1)
- ✅ YAML persistence (Stage 2)
- ✅ Git metadata capture (Stage 3)
- ✅ Lineage tracking (Stage 4)
- ✅ Experiment registry API (Stage 5)
- ✅ Query & search (Stage 6)
- ✅ Artifact management (Stage 7)
- ✅ Integration with backtester & optimizer (Stage 8)

**No stage depends on future stages** — each is independently testable and deployable.
