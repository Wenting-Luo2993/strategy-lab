# Research Journal Framework — Architecture Review

**Date:** 2026-05-23  
**Reviewer:** AI Assistant  
**Status:** ✅ PRD is architecturally sound with minor adaptations needed

---

## Executive Summary

The PRD proposes a **scientific research integrity framework** that fills a critical gap in the current system. Your existing backtesting infrastructure has strong foundations for **experimentation execution** but lacks **experiment tracking, lineage, and institutional memory**.

**Current State:**
- ✅ Event-driven backtester with comprehensive metrics
- ✅ Parameter sweep and optimization framework
- ✅ Pydantic-based configuration models
- ✅ Regime research framework with feature engineering
- ❌ No experiment registry or hypothesis tracking
- ❌ No immutable experiment pattern
- ❌ No Git-based metadata capture
- ❌ No artifact reference system

**Verdict:** PRD is **architecturally aligned** and **ready for implementation**. About **30%** of required components exist and can be adapted. The remaining **70%** requires new domain models and persistence layer.

---

## Current System Analysis

### ✅ What Already Exists

#### 1. **Result Capture & Metrics** (PRD FR-9, FR-10)
**Location:** `vibe/backtester/analysis/metrics.py`

**Current Implementation:**
- ✅ `BacktestResult` dataclass with comprehensive metrics
- ✅ Convexity metrics (R-multiple, expectancy, tail analysis)
- ✅ Equity metrics (Sharpe, drawdown, returns)
- ✅ Year-by-year breakdown
- ✅ Regime performance attribution
- ✅ Trade-level details with exit reasons

**Gap:** Not integrated with experiment registry; results are ephemeral

```python
@dataclass
class BacktestResult:
    overall: ConvexityMetrics
    by_year: Dict[int, ConvexityMetrics]
    equity: EquityMetrics
    trades: List[Trade]
    regime_breakdown: Dict[str, ConvexityMetrics]
    symbol: str
    start_date: str
    end_date: str
    ruleset_name: str
    ruleset_version: str
```

**PRD Alignment:** ⭐⭐⭐⭐ (Excellent foundation — needs persistence wrapper)

---

#### 2. **Parameter Sweep Framework** (PRD FR-10 Optimization Integration)
**Location:** `vibe/backtester/analysis/parameter_sweep.py`

**Current Implementation:**
- ✅ `ParameterDefinition` for sweep configuration
- ✅ `ParameterSweep` for grid/one-at-a-time search
- ✅ `SweepResult` with parameter tracking
- ✅ `to_dict()` serialization support
- ✅ Deterministic parameter combinations

**Gap:** No automatic experiment registration; no lineage tracking

```python
@dataclass
class ParameterDefinition:
    path: str           # "strategy.orb_duration_minutes"
    values: List[Any]   # [5, 10, 15]
    name: Optional[str]
    base_value: Optional[Any]

class ParameterSweep:
    def run(self) -> List[SweepResult]:
        # Executes sweep, returns results
```

**PRD Alignment:** ⭐⭐⭐⭐ (Good fit — needs experiment ID injection)

---

#### 3. **Configuration Models** (PRD: Experiment Parameters)
**Location:** `vibe/common/ruleset/models.py`

**Current Implementation:**
- ✅ Pydantic `BaseModel` for type-safe configs
- ✅ Discriminated unions for polymorphic configs
- ✅ Field validation with `@model_validator`
- ✅ YAML-friendly serialization
- ✅ Nested configuration structures

**Gap:** No versioning metadata; no immutability enforcement

```python
class StrategyRuleSet(BaseModel):
    name: str
    strategy: StrategyConfig
    exit: ExitConfig
    trade_filter: TradeFilterConfig
    # ... ~400 lines of well-structured config models
```

**PRD Alignment:** ⭐⭐⭐⭐⭐ (Perfect pattern to extend for experiment configs)

---

#### 4. **Trade Model with Metadata** (PRD: Artifact Data Model)
**Location:** `vibe/common/models/trade.py`

**Current Implementation:**
- ✅ Pydantic models with validation
- ✅ Datetime tracking (entry/exit times)
- ✅ Strategy name field
- ✅ Exit reason tracking
- ✅ Initial risk tracking

**Gap:** No experiment_id linkage; no trade attribution to hypothesis

```python
class Trade(BaseModel):
    trade_id: Optional[str]
    symbol: str
    side: str
    entry_price: float
    exit_price: Optional[float]
    entry_time: datetime
    exit_time: Optional[datetime]
    initial_risk: Optional[float]
    exit_reason: Optional[str]  # "STOP" | "TARGET" | "EOD"
```

**PRD Alignment:** ⭐⭐⭐⭐ (Good — add experiment_id field)

---

#### 5. **Regime Research Framework** (PRD: Dataset Definition)
**Location:** `vibe/backtester/analysis/regime_research/`

**Current Implementation:**
- ✅ Feature engine for market-state indicators
- ✅ Regime labeling (trending/ranging + volatility)
- ✅ Filter evaluator with overfitting guardrails
- ✅ Reproducible feature computation
- ✅ Forward-observable features only

**Gap:** No integration with experiment lineage

**PRD Alignment:** ⭐⭐⭐⭐ (Can reference regime definitions in experiments)

---

### ❌ What's Missing

#### 1. **Experiment Registry & Persistence** (FR-2, FR-3)
**Status:** Does not exist

**What's needed:**
- Experiment domain model (id, hypothesis_id, parent_id, parameters, status)
- YAML/JSON persistence to Git repository
- Immutability enforcement for completed experiments
- Unique ID generation (deterministic or sequential)
- Status lifecycle management (registered → running → completed → archived)

---

#### 2. **Hypothesis Management** (FR-1)
**Status:** Does not exist

**What's needed:**
- Hypothesis domain model (id, title, rationale, status, tags)
- YAML persistence
- Status transitions (proposed → active → validated/invalidated)
- Hypothesis-to-experiment linking

---

#### 3. **Lineage Tracking** (FR-4)
**Status:** Does not exist

**What's needed:**
- Parent/child experiment relationships
- Lineage graph traversal (find all descendants, find root)
- Cycle detection validation
- Visualization support (optional)

---

#### 4. **Git Metadata Capture** (FR-5)
**Status:** No Git integration exists

**What's needed:**
- Git commit hash capture (via subprocess or GitPython)
- Branch name capture
- Dirty state detection (uncommitted changes warning)
- Repository root detection

---

#### 5. **Artifact Registry** (FR-6)
**Status:** Does not exist

**What's needed:**
- Artifact model (path, checksum, size, type)
- Reference-only storage (no large files in Git)
- Checksum validation (SHA256)
- Artifact linking to experiments

---

#### 6. **Research Notes & Rejected Ideas** (FR-1, FR-8)
**Status:** Does not exist

**What's needed:**
- ResearchNote model (id, content, tags, related_experiment)
- RejectedIdea model (id, idea, reason, evidence)
- Freeform observation capture
- Cross-referencing between notes and experiments

---

#### 7. **Query & Search System** (FR-7)
**Status:** Does not exist

**What's needed:**
- Tag-based search
- Parameter-based filtering
- Hypothesis status queries
- Result quality ranking
- Date range queries

---

## Recommendations

### 1. **Start with Core Domain Models** (Stage 1)
Build foundational data models using Pydantic (matching existing patterns):
- `Hypothesis`
- `Experiment`
- `ResearchNote`
- `RejectedIdea`
- `ArtifactReference`

Use YAML for human-readable persistence.

---

### 2. **Leverage Existing Serialization Patterns** (Stage 2)
Extend `to_dict()` pattern from `SweepResult` and `BacktestResult`:
- Add `Experiment.to_yaml()`
- Add `Hypothesis.from_yaml()`
- Use Pydantic's built-in JSON/dict support

---

### 3. **Inject Experiment ID into Existing Workflows** (Stage 3)
Modify existing classes to accept optional `experiment_id`:
- `BacktestEngine.run(experiment_id: str | None = None)`
- `ParameterSweep.run(hypothesis_id: str | None = None)`
- `Trade.experiment_id: Optional[str] = None`

This enables backward compatibility while supporting new workflow.

---

### 4. **Git Integration via Subprocess** (Stage 4)
Use Python's `subprocess` module (no external dependencies):
```python
import subprocess

def get_git_commit_hash() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()
```

---

### 5. **Enforce Immutability with Frozen Dataclasses** (Stage 1)
Use `frozen=True` for completed experiments:
```python
@dataclass(frozen=True)
class CompletedExperiment:
    # All fields immutable after creation
```

---

### 6. **Storage Directory Structure** (Stage 2)
Align with PRD recommendation:
```
research/
├── hypotheses/
│   ├── HYP-001.yaml
│   └── HYP-002.yaml
├── experiments/
│   ├── EXP-100.yaml
│   ├── EXP-101.yaml
│   └── EXP-102.yaml
├── notes/
│   └── NOTE-001.md
├── rejected/
│   └── RJ-001.yaml
└── artifacts/
    └── registry.yaml  # References only, no large files
```

---

### 7. **.gitignore Enforcement** (Stage 4)
Add to `.gitignore`:
```
# Research artifacts (reference only)
research/artifacts/*.parquet
research/artifacts/*.csv
research/artifacts/*.html
reports/
*.png
*.jpg
```

Add pre-commit hook to block large file commits.

---

### 8. **Integration Points** (Stage 5)

**BacktestResult → Experiment:**
```python
def backtest_result_to_experiment_summary(
    result: BacktestResult,
    experiment_id: str
) -> dict:
    return {
        "experiment_id": experiment_id,
        "sharpe": result.equity.sharpe_ratio,
        "max_drawdown": result.equity.max_drawdown,
        "expectancy_r": result.overall.expectancy_r,
        # ...
    }
```

**ParameterSweep → Lineage:**
```python
sweep = ParameterSweep(...)
parent_exp_id = "EXP-100"

for idx, param_combo in enumerate(sweep.generate_combinations()):
    child_exp_id = f"{parent_exp_id}-{idx:03d}"
    # Register child experiment with parent lineage
```

---

## Acceptance Criteria

The architecture is successfully implemented when:

- ✅ All domain models (Hypothesis, Experiment, etc.) are Pydantic models
- ✅ Experiments are immutable after completion
- ✅ Git metadata captured automatically on experiment registration
- ✅ No large files (.parquet, .csv > 1MB) committed to research/
- ✅ BacktestResult integrates with experiment persistence
- ✅ ParameterSweep auto-registers child experiments
- ✅ Query API supports tag/parameter/status filtering
- ✅ Lineage graph prevents cycles
- ✅ Existing backtester code works unchanged (backward compatible)

---

## Risk Assessment

**Low Risk:**
- ✅ Domain models (straightforward Pydantic implementation)
- ✅ YAML persistence (well-understood pattern)
- ✅ Git subprocess integration (simple)

**Medium Risk:**
- ⚠️ Immutability enforcement (requires careful API design)
- ⚠️ Query performance (YAML file scanning may be slow at scale)
- ⚠️ Artifact checksum validation (adds execution overhead)

**High Risk:**
- ❌ **Migration path for existing results** — no historical experiments in registry
- ❌ **Lineage complexity** — deeply nested experiments may complicate queries

**Mitigation:**
- Start with immutable frozen dataclasses (enforced at Python level)
- Defer query optimization (in-memory caching, indexing) to later stage
- Accept that historical results (pre-framework) won't have experiment IDs
- Keep lineage depth reasonable (warn on > 5 levels)

---

## Summary

The PRD is well-aligned with existing architecture. Implementation requires:
- **4 new domain models** (Hypothesis, Experiment, ResearchNote, RejectedIdea)
- **1 persistence layer** (YAML read/write)
- **1 Git integration module** (subprocess wrapper)
- **3 integration points** (BacktestResult, ParameterSweep, Trade)

Estimated **70% new code, 30% adaptation** of existing components.

**Recommended phasing:** Start with Stage 1 (domain models + immutability) to validate design before building integration points.
