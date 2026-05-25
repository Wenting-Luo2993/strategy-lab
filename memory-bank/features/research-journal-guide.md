# Research Journal Framework - Feature Guide

**Location:** `vibe/research_journal/`  
**Status:** ✅ Production Ready (Stages 1-8 Complete)  
**Tests:** 141 passing (100% pass rate)  
**Last Updated:** 2026-05-24

---

## What is the Research Journal?

The Research Journal Framework is a scientific experiment tracking system for quantitative trading strategy development. It provides:

- **Hypothesis-Experiment Workflow**: Track research hypotheses through execution to validation
- **Reproducibility**: Automatic Git metadata capture for every experiment
- **Immutability**: Completed experiments are locked to prevent accidental modifications
- **Lineage Tracking**: Parent-child relationships for optimization iterations
- **Artifact Management**: SHA256 verification for output files and reports
- **Discovery API**: Chainable queries to find experiments by tag, parameter, results, etc.

### Use Cases

✅ **Strategy Research**
- Document hypothesis before running backtest
- Compare results across parameter variations
- Track which configurations work best

✅ **Backtester Integration**
- Auto-create experiments from backtest trades
- Compute metrics (Sharpe, expectancy, win rate)
- Link trades to experiments for audit trail

✅ **Parameter Optimization**
- Track each optimization iteration as child experiments
- View complete lineage from base strategy to final optimization
- Identify which parameter changes had biggest impact

---

## Framework Structure

### Core Entities

**Hypothesis** - Research question
```yaml
id: "HYP-001"                    # Auto-generated
title: "Test ORB edge on QQQ"
rationale: "ORB shows promise in trending markets"
status: "PROPOSED" | "ACTIVE" | "VALIDATED" | "INVALIDATED"
tags: ["orb", "volume-based"]
created_at: "2026-05-24T10:00:00+00:00"
```

**Experiment** - Specific test run
```yaml
id: "EXP-001"                    # Auto-generated
hypothesis_id: "HYP-001"
strategy_name: "ORBStrategy"
strategy_version: "1.4.2"
parameters: {orb_minutes: 5, take_profit: 2.0}
dataset_config: {symbols: ["QQQ"], period: "2024"}
status: "REGISTERED" | "RUNNING" | "COMPLETED" | "FAILED"
results_summary:                 # Only when COMPLETED
  sharpe_ratio: 1.2
  expectancy_r: 0.05
  total_pnl: 1500.0
  win_rate: 0.52
conclusion: "Edge validated on QQQ"
execution_metadata:              # Auto-captured
  git_commit: "abc123..."        # For reproducibility
  git_branch: "main"
  git_dirty: false
  python_version: "3.12.10"
created_at: "2026-05-24T10:00:00+00:00"
completed_at: "2026-05-24T11:30:00+00:00"
```

**ResearchNote** - Observations
```yaml
id: "NOTE-001"                   # Auto-generated
content: "Backtest shows 60% win rate on 5-min OR breaks"
related_experiment_id: "EXP-001"
tags: ["observation", "orb-breakouts"]
created_at: "2026-05-24T10:15:00+00:00"
```

**ArtifactReference** - Output files
```yaml
id: "ART-001"                    # Auto-generated
experiment_id: "EXP-001"
artifact_type: "backtest_report"
path: "research/artifacts/orb_2024_report.html"
checksum: "sha256..."            # For integrity verification
size_bytes: 1024000
created_at: "2026-05-24T10:20:00+00:00"
```

**RejectedIdea** - Failed hypotheses
```yaml
id: "RJ-001"                     # Auto-generated
idea: "Stop-loss placement based on daily ATR"
reason_rejected: "Reduced P&L by 40% across all timeframes"
evidence: ["EXP-002", "EXP-003", "EXP-004"]
created_at: "2026-05-24T10:25:00+00:00"
```

### Directory Structure

```
research/
├── hypotheses/
│   ├── HYP-001.yaml
│   └── HYP-002.yaml
├── experiments/
│   ├── EXP-001.yaml
│   └── EXP-002.yaml
├── notes/
│   ├── NOTE-001.md
│   └── NOTE-002.md
├── rejected/
│   ├── RJ-001.yaml
│   └── RJ-002.yaml
└── artifacts/
    ├── ART-001.yaml
    ├── orb_2024_report.html
    └── optimization_surface.png
```

---

## How to Use

### Basic Workflow

```python
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.models import ExperimentStatus

# Initialize
registry = ResearchRegistry()

# 1. Create hypothesis
hyp = registry.create_hypothesis(
    title="Test ORB edge on QQQ",
    rationale="ORB strategy shows promise in trending markets",
    tags=["orb", "volume-based"]
)
print(f"Created {hyp.id}")  # HYP-001

# 2. Create experiment (auto-captures git state)
exp = registry.create_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={"orb_minutes": 5, "take_profit": 2.0},
    dataset_config={"symbols": ["QQQ"], "start_date": "2024-01-01"},
    hypothesis_id=hyp.id,
    tags=["validation"]
)
print(f"Created {exp.id}")  # EXP-001

# 3. Add observation
note = registry.add_research_note(
    content="Backtest shows 52% win rate with 1.2 Sharpe ratio",
    related_experiment_id=exp.id,
    tags=["observation"]
)
print(f"Added {note.id}")  # NOTE-001

# 4. Complete with results (marks as IMMUTABLE)
completed = registry.complete_experiment(
    exp.id,
    results={
        "sharpe_ratio": 1.2,
        "expectancy_r": 0.05,
        "total_pnl": 1500.0,
        "win_rate": 0.52
    },
    conclusion="Edge validated on QQQ. Ready for production testing."
)
print(f"Completed {completed.id}, status={completed.status}")
```

### Query Experiments

```python
from vibe.research_journal.query import ExperimentQuery
from vibe.research_journal.models import ExperimentStatus

query = ExperimentQuery(registry)

# Find all completed validation runs
results = (query
    .by_tag("validation")
    .by_status(ExperimentStatus.COMPLETED)
    .execute())

# Filter by result metric
high_sharpe = (ExperimentQuery(registry)
    .by_result_quality("sharpe_ratio", 1.0, 2.0)
    .execute())

# Filter by parameters
five_min_orbs = (ExperimentQuery(registry)
    .by_parameter("orb_minutes", 5)
    .execute())
```

### Track Optimization Lineage

```python
# Base experiment
base_exp = registry.create_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={"orb_minutes": 5},
    dataset_config={...},
    hypothesis_id=hyp.id,
    tags=["iteration_1"]
)

# Optimization iteration 1
opt_exp1 = registry.create_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={"orb_minutes": 10},  # Changed
    dataset_config={...},
    hypothesis_id=hyp.id,
    parent_experiment_id=base_exp.id,  # Links to parent
    tags=["iteration_2"]
)

# Optimization iteration 2
opt_exp2 = registry.create_experiment(
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={"orb_minutes": 15},  # Changed again
    dataset_config={...},
    hypothesis_id=hyp.id,
    parent_experiment_id=opt_exp1.id,  # Links to previous
    tags=["iteration_3"]
)

# View lineage
lineage = registry.get_lineage_graph()
ancestors = lineage.get_ancestors(opt_exp2.id)  # [opt_exp1.id, base_exp.id]
descendants = lineage.get_descendants(base_exp.id)  # [opt_exp1.id, opt_exp2.id]
```

### Backtester Integration

```python
from vibe.research_journal.integration.backtest_adapter import BacktestResultAdapter
from vibe.common.models.trade import Trade

adapter = BacktestResultAdapter(registry)

# Get trades from backtest
trades = [...]  # List of Trade objects

# Create experiment from trades
exp = adapter.create_experiment_from_trades(
    hypothesis_id="HYP-001",
    strategy_name="ORBStrategy",
    strategy_version="1.4.2",
    parameters={"orb_minutes": 5},
    dataset_config={"symbols": ["QQQ"]},
    trades=trades,
    tags=["backtest"]
)

# Complete with backtest results
completed = adapter.complete_experiment(
    exp.id,
    trades=trades,
    conclusion="Backtest successful. Ready for paper trading."
)
```

### Artifact Management

```python
from vibe.research_journal.artifact_tracker import ArtifactTracker

tracker = ArtifactTracker(registry)

# Register output file
artifact = tracker.register_artifact(
    experiment_id="EXP-001",
    file_path=Path("reports/orb_2024_backtest.html"),
    artifact_type="backtest_report"
)
print(f"Artifact {artifact.id}: checksum={artifact.checksum[:8]}...")

# Verify integrity (detects tampering)
is_valid = tracker.verify_artifact(artifact)
if is_valid:
    print("Artifact verified - no tampering detected")

# List artifacts for experiment
artifacts = tracker.list_artifacts("EXP-001")
for art in artifacts:
    print(f"- {art.artifact_type}: {art.size_bytes} bytes")
```

---

## What Gets Tracked

### Automatically Captured
- ✅ Git commit hash (for reproducibility)
- ✅ Git branch name
- ✅ Uncommitted changes (git_dirty flag)
- ✅ Python version
- ✅ Experiment creation timestamp
- ✅ Experiment completion timestamp
- ✅ Immutability status
- ✅ Lineage (parent/child relationships)

### Manually Recorded
- 📝 Hypothesis title and rationale
- 📝 Strategy name and version
- 📝 Parameters tested
- 📝 Dataset configuration
- 📝 Test results and metrics
- 📝 Conclusions and observations
- 📝 Tags for categorization
- 📝 Artifact references and checksums

### Computed Metrics (from Backtest Integration)
- 📊 Total trades
- 📊 Win rate
- 📊 Total P&L
- 📊 Sharpe ratio
- 📊 Expectancy (in R)
- 📊 Average win/loss
- 📊 Profit factor
- 📊 Largest win/loss

---

## Accessing the Journal

### Location
Default: `d:\development\strategy-lab\research/` (or `$PWD/research/`)

### File Formats
- **YAML**: Hypotheses, experiments, artifacts, rejected ideas
- **Markdown**: Research notes (with YAML frontmatter)
- **Git-tracked**: All files in `research/` directory

### Example Paths
```
research/hypotheses/HYP-001.yaml          # Hypothesis
research/experiments/EXP-001.yaml         # Experiment
research/experiments/EXP-001.yaml         # Results + conclusion
research/notes/NOTE-001.md                # Observation
research/rejected/RJ-001.yaml             # Failed idea
research/artifacts/ART-001.yaml           # File reference
research/artifacts/orb_backtest.html      # Actual output file
```

### View with Git
```bash
# See all experiments
git log --oneline research/experiments/

# View specific experiment
cat research/experiments/EXP-001.yaml

# See all changes
git diff research/

# Track research history
git log -p research/
```

---

## Integration Points

### With Backtester
- `vibe/research_journal/integration/backtest_adapter.py`
- Auto-create experiments from backtest trades
- Compute metrics (Sharpe, expectancy, win rate)

### With Parameter Sweep
- Create parent experiment for sweep run
- Create child experiments for each variation
- Track lineage across optimization

### With Trading Bot
- Link trades to experiments
- Track which experiments are deployed
- Audit trail for live trading

---

## Best Practices

✅ **DO:**
- Create hypothesis BEFORE running backtest
- Use meaningful tags for categorization
- Complete experiments even if results are negative
- Record observations in research notes
- Register output files as artifacts
- Track optimization iterations with lineage

❌ **DON'T:**
- Modify completed experiments (they're immutable)
- Ignore git_dirty warnings (data may not be reproducible)
- Forget to add conclusion when completing
- Skip tagging experiments
- Leave experiments in RUNNING status

---

## Version History

| Version | Date | Stages | Tests | Notes |
|---------|------|--------|-------|-------|
| 1.0 | 2026-05-24 | 1-8 | 141 | Production release with backtester integration |
| 0.5 | 2026-05-16 | 1-5 | 93 | Core framework complete |

---

## Related Files

- **Implementation Guide**: `docs/backtester-mvp/research-journal-framework/IMPLEMENTATION_SUMMARY.md`
- **Stage Reviews**: `docs/backtester-mvp/research-journal-framework/stage-*-review.md`
- **Source Code**: `vibe/research_journal/`
- **Tests**: `vibe/tests/research_journal/`
