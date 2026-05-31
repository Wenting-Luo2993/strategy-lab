# Active Context

## Current Focus Area
**Status**: Realistic Order Execution Simulator (ROES) - Phase 1 Complete ✅ → Phase 2 Next

**Primary Task**: Move from infrastructure work (ROES Phase 1) to ORB research continuation

**Context**: ROES Phase 1 provides production-ready execution engine with pluggable models. All 114 tests passing. Full backward compatibility with legacy FillSimulator verified.

---

## Recent Decisions

### Decision: Complete ROES Phase 1 Before ORB Research (2026-05-31)
**Chosen**: Execute full Phase 1 (6 tasks, 114 tests) for Realistic Order Execution Simulator

**Reasoning**:
- ORB research on hold until 2026 H1 (currently showing -0.17R degradation)
- ROES Phase 1 provides foundation for realistic paper trading and execution quality monitoring
- Protocol-based architecture enables easy addition of new slippage/impact/volume models
- Full backward compatibility ensures no disruption to existing backtests

**Deliverables**:
- Task 1: Order/Fill models with validation (23 tests)
- Task 2: Slippage models - FixedTickSlippage (legacy-compatible) + SqrtVolumeSlippage (20 tests)
- Task 3: Volume models - UnlimitedVolume + ParticipationRateVolume (15 tests)
- Task 4: Impact models - NoImpact + SqrtImpact (16 tests)
- Task 5: ExecutionConfig with legacy() and realistic() factories (20 tests)
- Task 6: ExecutionSimulator with market/limit order routing (20 tests)

**Impact**: Ready for Phase 2 (ADV computation, pending orders, latency), then Phase 3 (validation), then Phase 4 (docs)

**Next**: ROES Phase 2 - ADV computation, pending order queue, limit order depth tracking

---

## Background: ORB Strategy Validation Status
- 2025 OOS: +0.16R (validation passed) ✅
- 2026 YTD: -0.17R (validation FAILED) ⚠️
- Paper trading paused pending 2026 H1 validation

## Previous Decisions

### Decision: Implement Research Journal Before Further Strategy Work (2026-05-23)
**Chosen**: Build Research Journal / Experiment Registry Framework as next priority

**Reasoning**:
- Current research lacks reproducibility and institutional memory
- Risk of repeated mistakes (re-testing rejected ideas, undocumented parameter changes)
- Need lineage tracking for optimization runs and derived experiments
- Framework will prevent accidental overfitting and data snooping
- Essential foundation before scaling to multiple strategies or intensive optimization

**Alternatives Considered**:
- Continue ad-hoc research (rejected - too risky, knowledge loss)
- Use external tools like MLflow (rejected - not tailored to trading research needs)
- Implement after more strategies tested (rejected - better to build foundation now)

**Impact**: All future research will be traceable, reproducible, and scientifically defensible

---

### Decision: Remove Take-Profit Gate (2026-05-16)
**Chosen**: EOD-only exits (no TP, no trailing stop)

**Reasoning**: 
- ORB has convex payoff - edge comes from tail winners (top 10% = 60% of profits)
- Fixed 2R TP caps returns at 2R, but many trades run 3R, 4R, 5R+
- EOD exits have 90.9% win rate and contribute +1,327R (702% of total profits)
- Trailing stops would cut winners early and conflict with convex structure

**Alternatives Considered**:
- Trailing stop (rejected - cuts tail winners)
- Wider TP (e.g., 3R or 4R) - not tested, but defeats purpose of letting winners run
- Time-based exits - not analyzed yet

**Impact**: Expectancy improved from -0.012R to +0.11R baseline

---

### Decision: Pause Paper Trading Deployment (2026-05-23)
**Chosen**: Wait for 2026 H1 (6-month) validation before deploying

**Reasoning**:
- 2026 YTD (Jan-Apr) shows -0.17R expectancy (failed validation)
- Similar to 2020 COVID failure pattern
- H3 filter (atr_pctile < 0.80) failed for first time - made performance worse
- Both long and short sides negative

**Alternatives Considered**:
- Deploy anyway with smaller position size (rejected - edge not proven in current regime)
- Add new filters for 2026 regime (rejected - high overfitting risk on 4 months of data)

**Impact**: Paper trading on hold until 2026 H1 shows recovery to positive expectancy

---

### Decision: Prefer Reusable Hypothesis Infrastructure Over One-Off Scripts (2026-05-28)
**Chosen**: Extend `scripts/optimize_strategy.py` + `OptimizationPipeline` for hypothesis sweeps and journal registration; retire per-hypothesis runner scripts.

**Reasoning**:
- One-off scripts increase maintenance cost and fragment workflow.
- Generic CLI + pipeline flags support many hypotheses through parameterization.
- Built-in journal registration enforces lineage and reproducibility by default.

**Impact**: New hypotheses should be executed via reusable optimization infrastructure with journal linkage, not custom orchestration scripts.

## Known Blockers

### Current Implementation Blockers
*No active blockers for Research Journal implementation.*

---

### Deferred ORB Strategy Blockers (On Hold)

#### Blocker 1: 2026 Performance Degradation
**Issue**: Strategy expectancy collapsed from +0.16R (2025) to -0.17R (2026 YTD)

**Status**: ⏸️ **On hold** - waiting for 2026 H1 data (6 months) before investigation

**Context**: 
- Similar to 2020 COVID failure pattern
- Too small sample size (4 months) for definitive conclusions
- Will investigate using Research Journal framework when H1 data available

**Future Action**: 
1. Wait until July 2026 for 2026 H1 data
2. Create hypothesis in Research Journal
3. Run experiments with proper lineage tracking
4. Document findings and conclusions

---

#### Blocker 2: H3 Filter Failed in 2026 YTD
**Issue**: ATR percentile filter (< 0.80) made performance worse in 2026 for first time

**Status**: ⏸️ **On hold** - part of broader 2026 analysis (see Blocker 1)

**Context**: 
- H3 filter worked in 6 of 7 years (2018-2024) and improved 2025 OOS
- Sample size too small to recalibrate threshold
- Will re-evaluate with Research Journal experiment tracking

**Future Action**:
1. Document H3 filter as hypothesis in Research Journal
2. Test on 2026 H1 data when available
3. Consider dynamic threshold experiments if static filter fails

## Open Questions

### Research Journal Implementation
1. **Q**: What persistence format is best for experiment metadata (YAML vs JSON)?
   - **Hypothesis**: YAML is more human-readable for research notes, JSON for structured data
   - **Action**: Prototype both, evaluate readability and git diff quality

2. **Q**: How granular should experiment IDs be (per backtest run vs per parameter set)?
   - **Hypothesis**: One experiment = one backtest run (most atomic, best reproducibility)
   - **Action**: Review PRD requirements and define ID generation strategy

3. **Q**: Should we backfill all historical ORB experiments or start fresh?
   - **Hypothesis**: Backfill key runs (baseline, no TP, H3 filter) for reference
   - **Action**: Prioritize manual backfill for documented experiments in user memory

### Mock Exchange Design
4. **Q**: How realistic should fill simulation be (simple vs complex matching engine)?
   - **Hypothesis**: Start simple (market orders = instant fill at market price), add complexity later
   - **Action**: Design minimum viable mock exchange for ORB strategy validation

5. **Q**: Should mock exchange support limit orders for initial implementation?
   - **Hypothesis**: ORB uses market orders only, defer limit order support
   - **Action**: Review strategy requirements, implement market orders first

### Future Strategy Development (Deferred)
6. **Q**: Why did 2026 performance degrade so sharply?
   - **Status**: On hold - waiting for 2026 H1 data (6 months)
   - **Action**: Re-analyze using Research Journal framework when data available

7. **Q**: Should we add regime-based on/off switching?
   - **Status**: Deferred - research with proper hypothesis tracking via Research Journal
   - **Action**: Create hypothesis entry when ready to investigate

## What's Next

### Immediate Priority (Next Session)
- [ ] Create **HYP-004**: "Regime filters improve no-TP ORB strategy"
- [ ] Re-run `scripts/analyze_regimes.py` against no-TP baseline and register as **EXP-069+**
- [ ] Re-test prior filter candidates (`atr_pctile < 0.80`, `regime != ranging_high_vol`) under corrected baseline
- [ ] Apply promotion checklist with emphasis on single-year stability and OOS consistency

### Secondary Priority
- [ ] Run walk-forward validation:
   `python scripts/optimize_strategy.py --mode full --walk-forward --output-dir reports/optimization/orb_walk_forward`
- [ ] Validate generalization out-of-sample before any deployment change

### Third Priority
- [ ] Run multi-symbol validation (SPY, IWM) for 5-minute ORB with no TP
- [ ] Compare cross-symbol robustness versus QQQ baseline

### Immediate (This Week) - Research Journal Foundation
- [ ] **Research Journal Phase 1**: Core registry (experiment model, hypothesis model, persistence layer)
- [ ] Define domain models (Hypothesis, Experiment, ResearchNote, RejectedIdea, ArtifactRegistry)
- [ ] Implement immutable experiment pattern with validation
- [ ] Set up Git-based storage structure (`research/hypotheses/`, `research/experiments/`, etc.)
- [ ] Implement artifact references (no large files in Git - external storage only)
- [ ] **Research Journal Phase 2**: Metadata integrity (git integration, checksums, lineage graph)
- [ ] Write unit tests for experiment immutability, lineage integrity, config checksums

### Short-Term (This Month) - Complete Backtest Workflow
- [ ] **Research Journal Phase 3**: Framework integrations (backtesting, optimization, validation)
- [ ] **Research Journal Phase 4**: Research UX (querying, timeline browsing, duplicate detection)
- [ ] Backfill existing ORB experiments into registry (2018-2026 runs with full metadata)
- [ ] End-to-end workflow: Hypothesis → Experiment → Backtest → Analysis → Conclusion
- [ ] Integration tests: Full reproducibility test, crash recovery, concurrent experiments
- [ ] Optimization framework integration (auto-register runs, lineage tracking)

### Medium-Term (Next 2 Months) - Paper Trading with Mock Exchange
- [ ] Build mock exchange simulator (order matching, realistic fills, slippage modeling)
- [ ] Implement optimized ORB strategy based on Research Journal findings
- [ ] Paper trading infrastructure (real-time data → mock execution)
- [ ] Real-time monitoring dashboard (equity curve, regime tracking, order book)
- [ ] Discord notifications for paper trading (entries, exits, daily P&L)
- [ ] Validate paper trading matches backtest expectations (execution quality, slippage)

### Long-Term (Next Quarter) - Live Paper Trading with Interactive Brokers
- [ ] Interactive Brokers API integration (TWS/Gateway connection)
- [ ] IB paper account setup and testing
- [ ] Order management system (submit, modify, cancel orders via IB API)
- [ ] Position reconciliation (sync IB positions with internal state)
- [ ] Risk management layer (position limits, daily loss limits, emergency stop)
- [ ] Transition from mock exchange to IB paper account
- [ ] Final validation before live trading (if metrics meet targets)

## Session Notes

**Last Updated**: 2026-05-28

**Session Handoff (2026-05-28)**:
- Journal counters: next IDs are **HYP-004**, **EXP-069**, **ART-017**, **NOTE-005**
- Confirmed best config: 5-minute ORB, no TP (`multiplier: 0`), risk 1%
- Definitive sweep EXP-032 supersedes EXP-004 after correcting TP grid to include tp=0
- Regime filter conclusions in EXP-001/002/003 require re-validation against no-TP baseline
- Production ruleset updated in `vibe/rulesets/orb_production.yaml`
- Windows encoding fix applied in `vibe/research_journal/persistence.py` with `encoding='utf-8'`
- HYP-004 baseline registered as EXP-069; focused trailing-stop slice registered as EXP-071..074
- Preliminary HYP-004 result: break-even trailing stop reduces losing trades materially, but lowers total P&L versus no-trailing baseline
- Caution: trailing-stop variants show implausible `expectancy_r` / `max_win_r` values, so R-multiple accounting likely has a bug under trailing-stop exits; compare dollar P&L, drawdown, win rate, and loser counts until fixed

**Progress Since Last Session**:
- Created memory bank structure for project documentation
- Documented validation failure in 2026 YTD
- Paused paper trading deployment pending investigation
- Reviewed Research Journal PRD and prioritized as next implementation
- Defined clear roadmap: Research Journal → Complete Backtest Workflow → Mock Exchange → IB Integration
- **Updated Copilot instructions** to enforce memory bank maintenance (especially ADR)
- **Added ADR-011**: Memory bank maintenance enforcement
- **Streamlined copilot-instructions.md** from 250+ lines to 85 lines (moved details to memory bank)
- **Enhanced memory-bank/README.md** with detailed ADR template and update workflows
- **Enhanced tech-context.md** with cloud-agnostic deployment guidelines
- **Restructured ADR system**: Converted adr.md to index (67 lines), created individual ADR files in `adrs/`
- **Added ADR-013**: Keep documentation files under ~200 lines for optimal token usage
- **Optimized Copilot instructions**: Changed from "read all files every time" to selective reading based on task type
- **Shortened agent descriptions**: Reduced all 3 agent descriptions from verbose (with examples) to concise one-liners
- **Created prd-breakdown-architect agent**: New agent that breaks down PRDs into staged execution plans with architecture review, TDD specs, and test-first approach (134 lines)

**Key Insights**:
- Framework is working correctly by rejecting weak performance (2026 YTD failure)
- Need institutional memory before scaling research (prevent knowledge loss)
- Research Journal will make future optimization and validation reproducible
- Git-based storage for metadata, external storage for large artifacts
- Phased approach to paper trading: mock exchange first, then IB paper account, then live
- **Memory bank must stay current** - Copilot now enforces ADR updates after every significant decision
- **Concise instructions work better** - Core rules in copilot-instructions.md, details in memory bank
- **File size matters for AI assistants** - ~200 line limit optimizes token budget and scannability
- **ADR index + individual files** - Scalable pattern for growing decision history
- **Selective reading > read everything** - Read memory bank files based on task type to save tokens
- **Agent descriptions should be concise** - One-line descriptions for agent invocation efficiency
- **Specialized agents for complex workflows** - prd-breakdown-architect codifies PRD breakdown methodology (architecture review → execution plan → TDD specs → stage reviews)

**Roadmap Priorities**:
1. **Immediate**: Research Journal (scientific backbone)
2. **Short-term**: Complete backtest workflow with full traceability
3. **Medium-term**: Paper trading with mock exchange (safe validation)
4. **Long-term**: Interactive Brokers integration (real execution)
