# Active Context

## Current Focus Area
**Next Priority**: ORB 2026 H1 re-validation planning and paper-trading quality monitoring design

**Context**: Core execution and research tracking infrastructure are in place; near-term work shifts to validation cadence and applied research workflow.

---

## Recent Decisions

### Decision: Defer ORB Research Until 2026 H1 Data Available (2026-05-23)
**Chosen**: Continue on hold pending 2026 H1 (6-month) validation period

**Reasoning**:
- 2026 YTD (Jan-Apr) shows -0.17R expectancy (validation FAILED)
- H3 filter failed for first time (made performance worse)
- Too small sample size (4 months) for confident conclusions
- Similar pattern to 2020 COVID crash - suggests regime shift
- Will use Research Journal framework when resuming

**Timeline**: Resume investigation July 2026

**Next**: Implement Research Journal Phase 1 to support future hypothesis testing

---

## Background: ORB Strategy Validation Status
- 2025 OOS: +0.16R (validation passed) ✅
- 2026 YTD: -0.17R (validation FAILED) ⚠️
- Paper trading paused pending 2026 H1 validation

## Previous Decisions

### Decision: Implement Research Journal Before Further Strategy Work (2026-05-23)
**Canonical record**: [memory-bank/adrs/adr-016-research-journal-framework-adoption.md](adrs/adr-016-research-journal-framework-adoption.md)

### Completed Feature References
- ROES realistic fill / execution mode: [memory-bank/features/realistic-fill-guide.md](features/realistic-fill-guide.md)
- Research Journal framework: [memory-bank/features/research-journal-guide.md](features/research-journal-guide.md)
- Durable execution contract: [memory-bank/adrs/adr-015-roes-default-legacy-opt-in-realistic.md](adrs/adr-015-roes-default-legacy-opt-in-realistic.md)
- Durable research workflow: [memory-bank/adrs/adr-016-research-journal-framework-adoption.md](adrs/adr-016-research-journal-framework-adoption.md)

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
**Canonical record**: see the Research Journal workflow ADR and the optimization pipeline implementation guidance.

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

### Research Journal Adoption
1. **Q**: How much historical ORB research should be backfilled into the journal now vs incrementally?
   - **Action**: Prioritize key baseline and promotion experiments first.

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

### Immediate Priority (This Week)
- [ ] Prepare ORB 2026 H1 re-validation plan using completed Research Journal workflow
- [ ] Define paper-trading execution quality metrics aligned to ROES realistic mode

### Secondary Priority (Next Week)
- [ ] Backfill selected historical ORB experiments into Research Journal for lineage continuity
- [ ] Add focused ROES edge-case hardening tests where gaps are discovered

### Third Priority (Next 2 Weeks) - Research Journal Phase 2-3
- [ ] Metadata integrity (git commit hashes, config checksums, lineage graph)
- [ ] Framework integrations (backtesting auto-registration, optimization lineage)
- [ ] Query interface (by hypothesis, tag, parameter, regime)
- [ ] Timeline browsing and duplicate detection

### Medium-Term (Next Month) - Mock Exchange & Paper Trading Infrastructure
- [ ] Build mock exchange simulator (order matching, realistic fills)
- [ ] Paper trading orchestrator (real-time data → mock execution)
- [ ] Real-time monitoring dashboard (equity curve, regime tracking)
- [ ] Discord notifications for paper trading
- [ ] Validation that paper trading matches backtest expectations

### Long-Term (Next Quarter) - Interactive Brokers Integration
- [ ] IB API client library integration (ib_insync)
- [ ] TWS/Gateway connection management
- [ ] Order management (submit, modify, cancel)
- [ ] Position and account reconciliation
- [ ] IB paper account setup and testing
- [ ] Live trading risk controls and emergency stop

### Paused - ORB Research (On Hold Until 2026 H1)
- [ ] Create **HYP-004**: "Regime filters improve no-TP ORB strategy"
- [ ] Re-run regime analysis against no-TP baseline when 2026 H1 data available
- [ ] Re-test prior filter candidates (H1: atr_pctile < 0.80, H2: regime != ranging_high_vol)
- [ ] Walk-forward validation and multi-symbol generalization
- [ ] Resume investigation July 2026

## Session Notes

- Keep detailed completion records in canonical guides, not here.
- ROES details: `memory-bank/features/realistic-fill-guide.md`
- Research Journal details: `memory-bank/features/research-journal-guide.md`
- ORB living roadmap: [memory-bank/features/orb-research-roadmap.md](features/orb-research-roadmap.md)

**Last Updated**: 2026-06-04
