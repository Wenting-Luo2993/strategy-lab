# Active Context

## Current Focus Area
**Current Work**: 🚀 **Interactive Brokers (IB) Paper Trading Integration** — Enabling live trading execution against IB paper accounts

**Next Priority**: ORB 2026 H1 re-validation planning and paper-trading quality monitoring design

**Context**: 
- Core execution and research tracking infrastructure in place
- Broker abstraction foundation added for IB integration
- P0 smoke path now exists for IB paper: market data -> order submission -> fill event -> operational metrics
- Operational metrics support local SQLite plus optional Supabase free-tier remote DB for Vercel dashboard work
- P1/P2 dashboard scaffold added at `apps/operational-metrics-dashboard` and validated with build/lint
- Target completion: Q3 2026 (12 weeks)

---

## Recent Decisions

### Decision: ORB Carryover Positions Are Flattened During Warmup (2026-07-28)
**Observed issue**: On 2026-07-28, the IB paper account still held a prior-day `QQQ -1` position, but the bot had no same-day order/fill records. ORB strategy logs reported `already_traded_today`, which made a carried broker position look like a clean same-day trade decision.

**Chosen for ORB**: Because ORB is strictly intraday, pre-market/start-of-day warmup should flatten any broker carryover position with a market close order before ORB entries are allowed. After a successful flatten, ORB daily trade state should be clear so the new session starts clean.

**Config direction**: Keep carryover behavior strategy-dependent. `STRATEGY__CARRYOVER_POSITION_POLICY` supports `flatten_at_market_open` for intraday strategies, `block_new_entries` for conservative manual recovery, and `manual_only` for operator-managed strategies.

| Option | Behavior | Pros | Cons | Fit |
| --- | --- | --- | --- | --- |
| 1. Flatten at start of day | Close carried broker position during warmup, then start the strategy clean | Correct for strictly intraday strategies; removes stale exposure; avoids muddy `already_traded_today` semantics | Can realize overnight loss/slippage; must only run at planned start-of-day warmup | ORB default |
| 2. Treat as already traded today | Keep position and block new entries | Avoids accidental double exposure | Conflates prior-day carryover with same-day trade; stale position may persist indefinitely | Rejected for ORB |
| 3. Managed carryover | Strategy owns the position and applies its normal exits | Useful for swing/multi-day strategies | Requires strategy-specific state reconstruction and risk rules | Future configurable strategy behavior |
| 4. Policy-based flatten/manual | Config decides flatten, block, or manual handling | Works across strategy families | Needs explicit per-strategy deployment config | General direction |

**Implementation note**: Warmup owns proactive flattening. The trading loop keeps a broker-position backstop that blocks new entries if a carryover survives warmup.

---

### Decision: Start IB Paper Trading Integration (2026-06-14)
**Chosen**: Begin Phase 1 of Interactive Brokers integration using Protocol-based broker abstraction

**Reasoning**:
- Mock exchange infrastructure is complete and stable
- Research Journal framework deployed and ready
- Paper trading infrastructure prerequisites are met
- Broker abstraction pattern proven successful in ROES (ADR-014, ADR-015)
- IB provides paper account for safe testing before live trading

**Implementation Pattern**: 
- Protocol-based abstraction (similar to ROES pluggable execution)
- Configuration-driven broker selection (mock in dev, IB in prod)
- P0 zero-cost path: local TWS/Gateway + SQLite operational metrics; optional Supabase/Vercel free tiers for hosted metrics dashboard
- 6 phases: connection → orders → reconciliation → execution → hardening → live prep

**Alternatives Considered**:
- Alpaca as first broker (rejected - less control, lower data quality)
- Monolithic IB implementation (rejected - not reusable for future brokers)
- Always use mock until all features are ready (rejected - delays paper trading readiness)

**Timing**: Target completion Q3 2026 (12 weeks)

**Related Documentation**:
- Architecture: [ADR-017: Broker Abstraction Protocol](adrs/adr-017-broker-abstraction-protocol.md)
- Feature guide: [IB Broker Integration Guide](features/ib-broker-integration-guide.md)

---

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

### 🚀 Immediate Priority (Week 1-2) - IB Phase 1: Broker Abstraction Foundation
- [x] Create `vibe/trading_bot/brokers/` package structure
- [x] Define `BrokerAPI` Protocol in `base.py` (order, account, position interfaces)
- [x] Create `BrokerOrder`, `BrokerQuote`, `FillEvent`, account/position dataclasses with validation and fill-quality math
- [x] Implement `InteractiveBrokersAPI` for connect, market data, order submission, fill wait, account summary, positions, cancel/status
- [x] Add `ib_insync` to `requirements.txt` and package dependencies
- [x] Add operational metrics recorder for expected fill, actual fill, slippage, commission, quantity, and latency
- [x] Add P0 IB paper smoke command: `python scripts/ib_paper_smoke.py --symbol AAPL --quantity 1 --submit-order`
- [x] Add Vercel-ready operational metrics dashboard scaffold
- [ ] Manual test: Connect to IB TWS/Gateway (paper trading mode)
- [ ] ADR documentation: [ADR-017: Broker Abstraction Protocol](adrs/adr-017-broker-abstraction-protocol.md) ✅ DONE

**Deliverable**: IB connection + disconnect + market data + optional paper order/fill metrics path implemented; live manual validation pending TWS/Gateway access

**Blocking**: Need TWS or IB Gateway running locally with paper account login and API access enabled for real end-to-end validation

### Secondary Priority (Week 3-4) - IB Phase 2: Order Management
- [x] Implement `submit_order()` → place market/limit/stop orders on IB
- [x] Implement `get_order_status()` → track fills and fill prices
- [x] Implement `cancel_order()` → emergency exits
- [ ] Order state tracking and lifecycle management
- [x] Fill price logging and operational metrics recording
- [x] Unit tests for broker contracts and fill metric math
- [ ] Integration tests on paper account

**Deliverable**: Can place, track, and cancel orders on IB paper account

### Third Priority (Week 5-6) - IB Phase 3: Account Reconciliation
- [ ] Implement `get_account_info()` → balance, buying power
- [ ] Implement `get_positions()` → current holdings
- [ ] Real-time reconciliation loop (60-second cadence)
- [ ] Risk limits (max position size, buying power guards)
- [ ] Mismatch alerting and Discord notifications
- [ ] Account state tests

**Deliverable**: Live account monitoring with risk controls

### Medium-Term (Week 7-8) - IB Phase 4: Execution Integration
- [ ] Wire IB into orchestrator and trading loop
- [ ] Swap `MockExchange` → `IBExecutor` in config
- [ ] Full ORB trading cycle on IB paper account
- [ ] EOD position closure via market orders
- [ ] IB order Discord notifications (sent, filled, cancelled)
- [ ] End-to-end tests with real ORB trades

**Deliverable**: Live ORB strategy trading on IB paper account ✨

### Medium-Term (Week 9-10) - IB Phase 5: Hardening & Reliability
- [ ] Reconnection logic (exponential backoff)
- [ ] Order timeout handling and auto-cancel (30s)
- [ ] Network failure recovery
- [ ] Circuit breaker (kill all on account mismatch)
- [ ] Emergency stop command
- [ ] Comprehensive audit logging
- [ ] Stress tests (simulated disconnections, rapid orders)

**Deliverable**: Production-ready IB connection stability

### Medium-Term (Week 11-12) - IB Phase 6: Live Trading Preparation
- [ ] Live account configuration template
- [ ] Position size constraints for live mode
- [ ] Manual approval workflow (human in loop)
- [ ] Daily risk reporting and P&L tracking
- [ ] Live trading runbook and documentation
- [ ] Smoke tests for live environment

**Deliverable**: Ready to transition from paper → live trading

### Background Tasks (Parallel with IB Work)
- ORB 2026 H1 re-validation plan (on hold until July 2026 data available)
- Research Journal Phase 2-3 enhancements (lower priority)
- Regime filter re-testing (paused until 2026 H1)

### Paused - ORB Research (On Hold Until 2026 H1)
- [ ] Create **HYP-004**: "Regime filters improve no-TP ORB strategy"
- [ ] Re-run regime analysis against no-TP baseline when 2026 H1 data available
- [ ] Re-test prior filter candidates (H1: atr_pctile < 0.80, H2: regime != ranging_high_vol)
- [ ] Walk-forward validation and multi-symbol generalization
- [ ] Resume investigation July 2026

## Session Notes

- IB Integration Feature Guide: [features/ib-broker-integration-guide.md](features/ib-broker-integration-guide.md)
- Broker Abstraction ADR: [adrs/adr-017-broker-abstraction-protocol.md](adrs/adr-017-broker-abstraction-protocol.md)
- P0 implementation files: `vibe/trading_bot/brokers/base.py`, `vibe/trading_bot/brokers/interactive_brokers.py`, `vibe/trading_bot/storage/operational_metrics.py`, `scripts/ib_paper_smoke.py`
- Dashboard app: `apps/operational-metrics-dashboard`
- Validation: Python compile checks pass; smoke CLI help loads; dashboard build/lint pass; pytest is not installed in current interpreter
- Keep detailed completion records in canonical guides, not here
- ROES details: `memory-bank/features/realistic-fill-guide.md`
- Research Journal details: `memory-bank/features/research-journal-guide.md`
- ORB living roadmap: [memory-bank/features/orb-research-roadmap.md](features/orb-research-roadmap.md)

**Last Updated**: 2026-06-14
