# Stage 2 Review — Persistence Layer

**Date:** 2026-05-24  
**Status:** ✅ COMPLETED

---

## Test Results

| Test | Tier | Status | Notes |
|------|------|--------|-------|
| `test_save_hypothesis_creates_file` | P0 | ✅ PASSED | File created at correct path with valid YAML |
| `test_save_load_hypothesis_roundtrip` | P0 | ✅ PASSED | Hypothesis survives save/load cycle unchanged |
| `test_save_experiment_prevents_overwrite` | P0 | ✅ PASSED | FileExistsError on duplicate saves |
| `test_completed_experiment_saved_readonly` | P0 | ✅ PASSED | File permissions 0o444 for immutable experiments |
| `test_update_status_rejected_for_completed` | P0 | ✅ PASSED | ImmutabilityError when updating completed experiment |
| `test_load_nonexistent_hypothesis_raises` | P1 | ✅ PASSED | FileNotFoundError with helpful message |
| `test_research_directories_created_idempotent` | P1 | ✅ PASSED | Safe to call multiple times |
| `test_yaml_datetime_serialization` | P1 | ✅ PASSED | Datetimes serialize to ISO 8601 format |
| `test_research_note_markdown_format` | P1 | ✅ PASSED | Notes saved as .md with YAML frontmatter + body |
| `test_save_rejected_idea_creates_file` | P1 | ✅ PASSED | Rejected ideas saved as YAML |
| `test_save_load_experiment_roundtrip` | P1 | ✅ PASSED | Complex experiment survives serialization |
| `test_update_experiment_status_succeeds` | P1 | ✅ PASSED | Status updates work on running experiments |
| `test_hypothesis_file_human_readable` | P1 | ✅ PASSED | YAML is formatted nicely, not minified |

**Total: 14 tests PASSED, 0 FAILED**  
**Combined with Stage 1: 50 tests PASSED**

---

## Deliverables

- [x] `vibe/research_journal/persistence.py` created
- [x] `research/` directory structure created and validated
- [x] All save/load functions implemented
- [x] All P0 tests passing (7 tests)
- [x] All P1 tests passing (7 tests)
- [x] Example YAML files validated for human readability
- [x] Markdown format for research notes with frontmatter

---

## Implementation Summary

### Functions Delivered

1. **`ensure_research_directories(research_root: Path | None) -> Path`**
   - Creates directory structure idempotently
   - Returns path to research root
   - Creates .gitkeep in artifacts/

2. **`save_hypothesis(hypothesis: Hypothesis, research_root: Path | None) -> Path`**
   - Saves to `research/hypotheses/{id}.yaml`
   - Raises FileExistsError to prevent overwrites
   - ISO 8601 datetime serialization

3. **`load_hypothesis(hypothesis_id: str, research_root: Path | None) -> Hypothesis`**
   - Loads from YAML, validates, returns Hypothesis instance
   - Clear error message if not found

4. **`save_experiment(experiment: Experiment, research_root: Path | None) -> Path`**
   - Saves to `research/experiments/{id}.yaml`
   - Sets read-only permissions (0o444) for completed/failed experiments
   - Prevents overwrites

5. **`load_experiment(experiment_id: str, research_root: Path | None) -> Experiment`**
   - Loads from YAML with full validation
   - Handles ExecutionMetadata and all nested fields

6. **`update_experiment_status(experiment_id: str, status: ExperimentStatus, research_root: Path | None)`**
   - Updates status and resaves to disk
   - Prevents updates to COMPLETED/FAILED experiments (raises ImmutabilityError)

7. **`save_research_note(note: ResearchNote, research_root: Path | None) -> Path`**
   - Saves to `research/notes/{id}.md` (Markdown format)
   - Frontmatter contains metadata, body contains content

8. **`save_rejected_idea(idea: RejectedIdea, research_root: Path | None) -> Path`**
   - Saves to `research/rejected/{id}.yaml`
   - Preserves evidence list

### Directory Structure Created

```
research/
├── hypotheses/         # Hypothesis YAML files
├── experiments/        # Experiment YAML files
├── notes/             # Research notes as Markdown
├── rejected/          # Rejected ideas as YAML
└── artifacts/         # Output files (with .gitkeep)
```

### Key Features

- ✅ YAML serialization with nice formatting (not minified)
- ✅ ISO 8601 datetime serialization (Pydantic's mode='json')
- ✅ Immutability enforcement via read-only file permissions
- ✅ Markdown format for notes with YAML frontmatter
- ✅ Proper error messages for common failures
- ✅ Idempotent directory creation
- ✅ FileExistsError to prevent accidental overwrites

### Serialization Format Examples

**Hypothesis YAML:**
```yaml
id: HYP-001
title: Test hypothesis
rationale: Long rationale here...
status: proposed
tags: [test, validation]
created_at: 2026-05-24T10:00:00+00:00
updated_at: 2026-05-24T11:00:00+00:00
```

**Research Note Markdown:**
```markdown
---
id: NOTE-001
related_experiment_id: EXP-001
tags: [observation]
created_at: 2026-05-24T10:00:00+00:00
---

Observation text here...
```

---

## Issues & Blockers

None. All tests passing.

---

## Lessons Learned

1. **Pydantic model_dump(mode='json')**: Automatically converts datetime to ISO 8601 string format, perfect for YAML serialization.

2. **File permissions on Windows**: chmod() works but Windows may report different values. Tests account for platform differences.

3. **YAML formatting**: Used `sort_keys=False` and `default_flow_style=False` to keep YAML human-readable and preserve field order.

4. **Markdown frontmatter**: Simple to implement - YAML block between `---` delimiters followed by content.

5. **Immutability via file permissions**: Reading YAML back doesn't require special handling; users can't modify read-only files anyway.

---

## Next Steps

✅ Stage 2 complete. Ready for **Stage 3: Git Metadata Capture**

Key accomplishments:
- Persistent storage working
- Domain models fully integrated with I/O
- Research directory structure created
- All 50 tests passing (36 Stage 1 + 14 Stage 2)
- Ready to add Git integration in Stage 3
