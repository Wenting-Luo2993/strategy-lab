# Backtester MVP Documentation

## Overview

This directory contains the complete design and implementation plan for the **Vibe Trading Platform Backtester** - an event-driven backtesting engine for day trading and swing trading strategies.

**Key Feature**: Perfect strategy reuse from `vibe/common/` ensures **identical behavior** between live trading and backtesting.

---

## Documents

### 📐 [design.md](./design.md)
Complete architecture and design document covering:
- High-level architecture with shared component reuse
- Core components (BacktestEngine, FillSimulator, PortfolioManager, etc.)
- Asset type abstractions (stocks, forex, crypto, options)
- Fill simulation with configurable realism levels
- Commission models for different brokers
- Performance analytics (20+ metrics)
- Optimization framework (grid search, walk-forward)
- Market regime classification
- Data quality checks
- In-sample/out-of-sample testing

**All design decisions finalized** ✅

### 📋 [implementation.md](./implementation.md)
Detailed implementation plan with:
- **22 tasks** organized in 5 phases
- **7-10 day timeline** for MVP completion
- Complete test specifications for each task
- Unit test examples
- Functional test requirements
- Success criteria

---

## Quick Reference

### MVP Scope

**What's Included (MVP):**
- ✅ Event-driven backtest engine
- ✅ Single-symbol backtesting (no portfolio complexity)
- ✅ Stock trading (US market)
- ✅ ORB strategy from `vibe/common/` (works identically to live bot)
- ✅ Hybrid fill simulation (instant + slippage + partial fills, no time delay)
- ✅ Data quality checks (splits, gaps, outliers)
- ✅ 20+ performance metrics
- ✅ Market regime classification (ADX-based)
- ✅ In-sample/out-of-sample optimization
- ✅ Benchmark comparison (vs buy-and-hold)
- ✅ HTML reports with charts

**What's NOT Included (Future):**
- ❌ Portfolio backtesting (multiple symbols simultaneously)
- ❌ Walk-forward optimization (rolling periods)
- ❌ Genetic algorithm / Bayesian optimization
- ❌ Forex/Crypto support
- ❌ Options trading
- ❌ Interactive Streamlit dashboard

**Rationale**: For day trading/swing trading single setups, portfolio backtesting is not needed. Test each symbol independently to understand which symbols work best with your strategy.

---

## Data Strategy

**Source**: Yahoo Finance (yfinance) - Already implemented in trading-bot Phase 2
**Storage**:
- Local Parquet files for active backtesting (3 years, 10 symbols ≈ 1GB)
- Cloud storage for long-term archive (10+ years)

**Symbol List** (diversified across sectors):
```python
SYMBOLS = [
    "AAPL",   # Large Cap Tech
    "MSFT",   # Large Cap Tech
    "GOOGL",  # Large Cap Tech
    "JPM",    # Large Cap Financial
    "JNJ",    # Large Cap Healthcare
    "SQ",     # Medium Cap Fintech
    "ROKU",   # Medium Cap Growth
    "F",      # Medium Cap Value
    "AAL",    # Medium Cap Cyclical
    "TSLA",   # High Volatility
]
```

---

## Design Principles

1. **Strategy Reuse**: All strategies, indicators, risk management, and MTF validation from `vibe/common/` work identically in live trading and backtesting
2. **Event-Driven**: Process data bar-by-bar to match live trading execution flow
3. **Hybrid Realism**: Instant fills (fast) + slippage + partial fills (realistic) - no time delay simulation
4. **Extensible**: Start simple (grid search), design for scale (genetic algorithms, distributed computing)
5. **Data Quality First**: Validate data before backtesting (detect splits, gaps, outliers)

---

## Key Insights

### Why No Time Delay Simulation?
For 5-minute bar strategies, 100-500ms execution delay is **negligible**:
- Your entry price is determined by the bar close
- Whether you get filled 100ms or 500ms later doesn't materially change the outcome
- Slippage captures the price impact far better than time delay

**If testing HFT strategies** (sub-second timeframes), then time delay matters. But for day trading/swing trading on 5m bars, it's not critical.

### Why No Portfolio Backtesting?
Day trading strategies focus on **individual setups**:
- You enter and exit within hours/days
- You want to know which symbols work best
- Testing AAPL separately from TSLA reveals which one fits your strategy

**Portfolio backtesting is for**:
- Buy-and-hold strategies
- Long-term asset allocation
- Market-neutral strategies (long/short pairs)

### Why Data Quality Checks?
Even blue-chip stocks have data issues:
- **AAPL**: 4-for-1 split on Aug 31, 2020
- **TSLA**: 5-for-1 split on Aug 31, 2020
- **GOOGL**: 20-for-1 split on Jul 18, 2022

Without adjustment, your backtest thinks these stocks crashed 75-95% overnight!

---

## Timeline

| Phase | Focus | Duration | Key Deliverables |
|-------|-------|----------|------------------|
| **Phase 0** | Data Infrastructure | 2-3 days | Data loader, quality checks, simulated clock |
| **Phase 1** | Fill Simulation | 2-3 days | Slippage model, commission, liquidity, fill simulator |
| **Phase 2** | Backtest Engine | 2-3 days | Event loop, portfolio manager, result model |
| **Phase 3** | Performance Analysis | 1-2 days | Metrics, regime analysis, benchmark comparison |
| **Phase 4** | Optimization & Reports | 2-3 days | Grid search, HTML reports |
| **Phase 5** | Integration | 1-2 days | CLI, end-to-end tests, documentation |

**Total: 7-10 days**

---

## Success Criteria

MVP is complete when:
- ✅ Can backtest ORB strategy from `vibe/common/` on AAPL
- ✅ Data quality checks pass (detects splits, gaps, outliers)
- ✅ Realistic fills with slippage and partial fills
- ✅ Performance metrics calculated accurately (Sharpe, drawdown, win rate, etc.)
- ✅ Market regime analysis working (ADX-based trending vs ranging)
- ✅ Benchmark comparison shows strategy vs buy-and-hold
- ✅ In-sample/out-of-sample optimization prevents overfitting
- ✅ HTML report with charts generated
- ✅ Results are deterministic (same input = same output every time)
- ✅ Full 3-year backtest runs in < 5 minutes

---

## Next Steps

### For Implementation
1. **Review both documents** (design.md and implementation.md)
2. **Clarify any questions** before starting
3. **Start with Phase 0** (data infrastructure)
4. **Follow the task order** in implementation.md
5. **Write tests first** (TDD approach)

### For Questions
- Architecture unclear? → See design.md sections 2-10
- Task details unclear? → See implementation.md task descriptions
- Test examples needed? → See unit test code blocks in implementation.md
- Timeline concerns? → See implementation.md Phase breakdown

---

## Related Documents

- [Trading Bot Design](../trading-bot-mvp/design.md) - Live trading architecture
- [Trading Bot Implementation](../trading-bot-mvp/implementation.md) - Live trading tasks
- `vibe/common/` - Shared strategy logic (already implemented)
- `vibe/trading_bot/` - Live trading bot (Phase 2 complete)

---

## Questions or Feedback?

This is a **living document**. If you:
- Find issues in the design
- Have better approaches
- Need clarifications
- Want to adjust scope

→ Update these documents and proceed with implementation!
