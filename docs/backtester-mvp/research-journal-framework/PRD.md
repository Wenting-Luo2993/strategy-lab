# PRD — Research Journal / Experiment Registry Framework

## Overview

The Research Journal / Experiment Registry framework is the scientific backbone of the trading research platform.

Its purpose is not analytics or optimization itself.
Its purpose is preserving research integrity.

This framework ensures that every hypothesis, experiment, conclusion, rejection, optimization run, and derived strategy configuration is:

* traceable
* reproducible
* queryable
* auditable
* scientifically defensible

This system becomes the institutional memory of the research engine.

Without this layer, optimization devolves into:

* parameter fishing
* undocumented intuition
* unreproducible discoveries
* accidental data snooping
* forgotten failures
* repeated dead ends

The framework must integrate tightly with:

* strategy framework
* backtest engine
* optimization framework
* validation pipeline
* artifact storage
* metrics system

---

# Goals

## Primary Goals

### 1. Preserve Scientific Process Integrity

Every research claim must have:

* rationale
* methodology
* dataset definition
* configuration
* timestamp
* author/source
* reproducibility information
* conclusion

---

### 2. Prevent Knowledge Loss

Researchers must never lose:

* failed ideas
* invalidated hypotheses
* surprising discoveries
* regime observations
* parameter sensitivities
* optimization lessons

---

### 3. Enable Reproducibility

Every experiment should be reproducible from:

* stored configuration
* referenced code version
* dataset version
* strategy version
* seed values
* execution metadata

---

### 4. Enable Lineage Tracking

The system must support:

* parent/child experiments
* derived experiments
* optimization lineage
* filter evolution
* strategy evolution

Example:

```text
ORB Baseline
 ├── Add Volume Filter
 │    ├── Optimize Threshold
 │    └── Test Across Regimes
 └── Add Gap Filter
```

---

### 5. Reduce Repeated Mistakes

The framework should help prevent:

* re-testing rejected ideas
* accidental overfitting
* duplicate experiments
* hidden survivorship bias
* undocumented parameter changes

---

# Non-Goals

## This system is NOT:

### A notebook replacement

Jupyter remains useful for exploratory analysis.

---

### A metrics engine

Metrics belong to backtest/analytics modules.

---

### A visualization platform

Charts/dashboards are secondary concerns.

---

### A production trading system

This framework supports research infrastructure only.

---

# Core Concepts

---

# 1. Hypothesis

Represents a research idea before experimentation.

Example:

```yaml
id: HYP-001
title: ORB performs better on high relative volume days
rationale:
  High RVOL may indicate institutional participation
status: active
created_at: 2026-05-18
tags:
  - orb
  - volume
  - momentum
```

---

# 2. Experiment

Represents a single scientific test.

Experiments are immutable after completion.

Example:

```yaml
id: EXP-104
hypothesis_id: HYP-001
parent_experiment_id: EXP-100

strategy:
  name: ORBStrategy
  version: 1.4.2

dataset:
  symbol_universe: SP500
  period: 2018-2024

parameters:
  orb_minutes: 15
  rvol_threshold: 1.5

execution:
  git_commit: a91bc22
  random_seed: 42

result_summary:
  sharpe: 1.12
  max_drawdown: -8.4

conclusion:
  RVOL filter improves Sharpe but reduces trade count

status: completed
```

---

# 3. Research Note

Freeform structured observations.

Example:

```yaml
id: NOTE-22
related_experiment: EXP-104

content:
  ORB edge weakens significantly during low-volatility years.

tags:
  - regimes
  - volatility
```

---

# 4. Rejected Idea Registry

Explicitly tracks failed concepts.

Example:

```yaml
id: RJ-009

idea:
  Using previous day's candle color as directional bias

reason_rejected:
  No statistical edge after transaction costs

evidence:
  EXP-221
```

---

# 5. Artifact Registry

Tracks generated outputs:

* equity curves
* reports
* optimization surfaces
* parameter heatmaps
* CSV exports

---

# Functional Requirements

# FR-1 Hypothesis Management

System must support:

* create hypothesis
* update status
* attach rationale
* tagging
* linking experiments

Statuses:

* proposed
* active
* validated
* invalidated
* archived

---

# FR-2 Experiment Registration

System must register experiments BEFORE execution.

Experiment registration includes:

* hypothesis reference
* strategy version
* parameter set
* dataset definition
* execution metadata
* expected output paths

Experiment IDs must be unique.

---

# FR-3 Immutable Completed Experiments

Completed experiments cannot be modified.

Allowed post-completion changes:

* additional notes
* tags
* references

Core experiment metadata must remain immutable.

This is critical for scientific integrity.

---

# FR-4 Lineage Tracking

Experiments must support:

* parent experiment
* child experiments
* derived hypotheses
* optimization ancestry

---

# FR-5 Reproducibility Metadata

Each experiment must capture:

## Code Metadata

* git commit hash
* branch
* strategy version

## Dataset Metadata

* data source
* date range
* survivorship settings
* split adjustments

## Execution Metadata

* timestamp
* runtime environment
* random seed
* config checksum

---

# FR-6 Artifact Tracking

Experiments must register:

* report paths
* chart paths
* exported datasets
* optimization results

Artifacts should include hashes/checksums where possible.

---

# FR-7 Search & Query

Researchers must be able to query by:

* hypothesis
* tag
* strategy
* parameter
* regime
* result quality
* status
* date range

---

# FR-8 Duplicate Detection

System should warn when:

* nearly identical experiment already exists
* same parameter set already tested
* rejected idea already explored

This is advisory, not blocking.

---

# FR-9 Conclusion Recording

Experiments must include:

* summary conclusion
* confidence level
* limitations
* follow-up ideas

---

# FR-10 Optimization Integration

Optimization framework must:

* auto-register optimization runs
* store search space
* store objective function
* store OOS results
* store selected parameter rationale

---

# FR-11 Validation Integration

Validation framework must record:

* IS/OOS splits
* walk-forward configs
* Monte Carlo configs
* stress test configurations

---

# FR-12 Research Journal Timeline

System should support chronological browsing of:

* hypotheses
* experiments
* conclusions
* failures
* discoveries

---

# Architecture

# Recommended Structure

```text
research/
├── hypotheses/
├── experiments/
├── notes/
├── rejected/
├── artifacts/
├── lineage/
└── registry/
```

---

# Suggested Domain Models

## Hypothesis

```python
class Hypothesis:
    id: str
    title: str
    rationale: str
    status: HypothesisStatus
    tags: list[str]
```

---

## Experiment

```python
class Experiment:
    id: str
    hypothesis_id: str | None
    parent_experiment_id: str | None

    strategy_name: str
    strategy_version: str

    parameters: dict
    dataset_config: dict

    execution_metadata: ExecutionMetadata

    status: ExperimentStatus

    results_summary: dict

    conclusion: str | None
```

---

# Integration Points

# Optimization Framework

Optimization runs automatically:

* create experiment entries
* store search spaces
* store selected configs
* register OOS validation

---

# Backtest Engine

Backtest engine should expose:

```python
BacktestResult.to_experiment_summary()
```

---

# Strategy Framework

Strategies should expose:

```python
strategy.version
strategy.signature()
```

---

# Storage Recommendations

## 1. Storage Architecture (Initial Phase)

### 1.1 Source of Truth: Git Repository

The entire research system is initially stored inside a Git-based repository.

This includes:

* hypotheses
* experiment metadata
* research notes
* lineage definitions
* configuration files
* small result summaries

Git provides:

* full version history
* branching for research exploration
* reproducibility via commit hashes
* diff-based audit trails

---

### 1.2 What CAN Be Stored in Git

Only lightweight, human-readable, or structured metadata is allowed.

#### Allowed content:

* `*.md` → research notes, conclusions, hypotheses
* `*.yaml / *.json` → experiment registry, configurations
* small summary outputs (text-only)
* experiment lineage definitions
* git-tracked code references (commit hashes)
* lightweight result summaries (aggregated metrics only)

---

### 1.3 What MUST NOT Be Stored in Git

The repository must explicitly exclude all large or binary research outputs.

#### Prohibited from Git commits:

* `.parquet` files
* `.csv` files containing large datasets
* raw backtest trade logs
* tick/minute-level market data
* large images, charts, or equity curves
* any dataset exceeding “human review size”

---

### 1.4 Rationale

Storing large datasets in Git leads to:

* repository bloat
* degraded performance
* broken cloning workflows
* loss of meaningful diff readability
* poor collaboration scalability

Git should remain a **knowledge system**, not a data warehouse.

---

## 2. External Data & Artifact Handling (Future-Ready Design)

### 2.1 External Storage Responsibility

All large datasets and artifacts MUST be stored outside Git in a dedicated storage layer.

Examples (future phase):

* local filesystem
* object storage (e.g., S3-compatible system)
* database or data lake

---

### 2.2 Git Stores References Only

Instead of storing raw data, Git stores:

* file paths
* URIs (e.g., S3 links)
* checksum hashes
* dataset version identifiers

#### Example:

```yaml id="ref123"
dataset:
  type: parquet
  location: s3://research-data/exp_104/results.parquet
  checksum: a91f3c...
```

---

### 2.3 Benefits of This Split

This separation ensures:

* Git remains fast and lightweight
* experiments remain reproducible
* datasets can scale independently
* storage and compute concerns are decoupled
* research history remains clean and readable

---

## 3. Enforcement Rules (Critical)

### 3.1 Commit Guardrail

The system MUST enforce a pre-commit rule:

> Block commits containing `.parquet`, `.csv` (above size threshold), or other large artifacts.

---

### 3.2 Metadata-Only Principle

Every Git commit in the research system must represent:

> “knowledge about experiments”, not “data produced by experiments”

---

### 3.3 Validation Requirement

CI or local hooks should validate:

* file types
* file sizes
* allowed directories
* artifact leakage prevention

---

## 4. Acceptance Criteria

This storage design is considered correctly implemented when:

* Git repo contains only metadata and code
* no large datasets are committed
* experiments remain fully reproducible via references
* external artifacts are referenced, not embedded
* repository remains lightweight and cloneable (<100–200MB typical early stage)
* CI/.gitignore prevents accidental dataset commits

---

# Example Workflow

## Step 1 — Create Hypothesis

```text
HYP-001:
ORB performs better during high volatility regimes
```

---

## Step 2 — Register Experiment

```text
EXP-101:
Test ATR percentile filter
```

---

## Step 3 — Execute Backtest

Framework auto-attaches:

* git commit
* config hash
* metrics
* artifact paths

---

## Step 4 — Record Conclusion

```text
Filter improves Sharpe but reduces opportunity count.
Likely useful only in trend regimes.
```

---

## Step 5 — Spawn Derived Experiment

```text
EXP-102:
Optimize ATR threshold
```

---

# Test-Driven Development Requirements

This framework is heavily integrity-oriented.

Tests are mandatory.

---

# Unit Tests

# UT-1 Unique Experiment ID Generation

Verify:

* IDs are unique
* deterministic format
* thread-safe generation

---

# UT-2 Experiment Immutability

Verify:

* completed experiments reject modification
* metadata integrity preserved

Example:

```python
with pytest.raises(ImmutableExperimentError):
    completed_experiment.parameters["orb"] = 30
```

---

# UT-3 Lineage Graph Integrity

Verify:

* no cyclic ancestry
* parent references valid
* lineage traversal works

---

# UT-4 Config Checksum Stability

Verify:

* identical configs produce identical hashes
* order-independent hashing

---

# UT-5 Duplicate Detection

Verify:

* same experiment config detected
* tolerance thresholds configurable

---

# UT-6 Serialization Roundtrip

Verify:

* experiment → YAML → object is lossless

---

# UT-7 Artifact Registration

Verify:

* missing artifact paths rejected
* checksum generation works

---

# UT-8 Git Metadata Capture

Verify:

* commit hash captured
* detached HEAD handled gracefully

---

# UT-9 Hypothesis State Machine

Verify valid transitions:

```text
proposed -> active
active -> validated
active -> invalidated
```

Verify invalid transitions rejected.

---

# Integration Tests

# IT-1 Optimization Framework Integration

Scenario:

* optimizer launches parameter sweep
* experiments auto-created
* lineage preserved

Assertions:

* all runs registered
* parent optimization recorded
* OOS validation linked

---

# IT-2 Full Reproducibility Test

Scenario:

* rerun experiment from stored metadata

Assertions:

* identical parameters loaded
* identical dataset loaded
* identical outputs reproduced

---

# IT-3 Backtest Auto-Registration

Scenario:

* backtest completes

Assertions:

* experiment auto-updated
* metrics stored
* artifacts registered

---

# IT-4 Crash Recovery

Scenario:

* execution interrupted mid-run

Assertions:

* experiment marked failed
* partial artifacts preserved
* audit trail retained

---

# IT-5 Concurrent Experiment Registration

Scenario:

* multiple experiments launched simultaneously

Assertions:

* no ID collisions
* registry consistency maintained

---

# IT-6 Derived Experiment Workflow

Scenario:

```text
baseline -> filter -> optimization -> validation
```

Assertions:

* lineage chain preserved
* ancestry query works

---

# Acceptance Criteria

System is considered production-ready when:

* all experiments reproducible
* lineage graph queryable
* completed experiments immutable
* optimization framework integrated
* validation framework integrated
* duplicate detection operational
* audit trail preserved
* all integrity tests passing

---

# Recommended Implementation Order

## Phase 1 — Core Registry

* experiment model
* hypothesis model
* persistence layer
* immutable experiments

---

## Phase 2 — Metadata Integrity

* git integration
* checksums
* artifact registry
* lineage graph

---

## Phase 3 — Framework Integrations

* backtesting integration
* optimization integration
* validation integration

---

## Phase 4 — Research UX

* querying
* timeline browsing
* duplicate detection
* reporting utilities

---

# Key Design Principle

The framework should optimize for:

```text
scientific defensibility > convenience
```

A slower but reproducible research process is vastly more valuable than a fast but unverifiable one.

This matters especially in trading research because false discoveries are extremely easy to create accidentally.

