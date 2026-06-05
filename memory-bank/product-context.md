# Product Context

## The Problem
Trading strategies often look profitable in backtests but fail in production due to:
- **Overfitting**: Strategies optimized on small samples don't generalize
- **Regime Blindness**: Ignoring market conditions leads to unpredictable performance
- **Execution Mismatch**: Real-world slippage and latency destroy theoretical edges
- **Operational Fragility**: Manual intervention, missed market opens, stale data connections

## User Workflow

### Research Phase (Strategy Researchers)
1. Define strategy parameters and entry/exit rules
2. Run backtests across 7+ years of historical data
3. Analyze regime-specific performance (trending vs ranging, high vs low volatility)
4. Test filter hypotheses with full-history validation
5. Validate out-of-sample (2025+) to prevent overfitting
6. Stress-test slippage assumptions (5, 10, 15 ticks)

### Deployment Phase (Live Traders)
1. Configure trading parameters in YAML (symbols, position size, stop-loss)
2. Start trading bot 5 minutes before market open (9:25 AM EST)
3. **Warmup phase** (9:25-9:30 AM): Prefetch data, connect providers, health checks
4. **Trading phase** (9:30 AM-4:00 PM): Execute ORB strategy, monitor positions
5. **Cooldown phase** (4:00-4:05 PM): Process final data, disconnect providers
6. Receive Discord notifications for: market start, ORB levels, order fills, daily summary

### Monitoring Phase (System Operators)
1. Subscribe to Discord channel for real-time notifications
2. Monitor health checks (data staleness, connection state)
3. Review daily summaries (P&L, R-multiple, win rate)
4. Investigate anomalies (missed entries, stale data, disconnections)

## Success Criteria

### Research Success
- ✅ Regime filters **validated** across full history (not just 2-year samples)
- ✅ Hypotheses **rejected early** if they fail out-of-sample testing
- ✅ Edge **survives** realistic slippage (10 ticks for QQQ)
- ✅ Exit analysis identifies **source of edge** (e.g., EOD exits = 702% of profits)

### Execution Success
- ✅ **Zero missed market opens** due to connection failures
- ✅ **Sub-60-second** detection of stale data and provider failover
- ✅ **Accurate order execution** matching backtest assumptions (entry price, stop-loss, EOD exit)
- ✅ **Complete audit trail** via Discord notifications and logs

### Operational Success
- ✅ **Automatic lifecycle management** (warmup → trading → cooldown)
- ✅ **Health monitoring** catches provider disconnections before trading starts
- ✅ **Structured notifications** with version tracking for debugging
- ✅ **Timezone correctness** (no DST bugs, date changes aligned with market timezone)

## Completed Product Capabilities

### Execution Infrastructure
- **ROES realistic-fill**: complete and backward compatible by default; realistic execution is explicit opt-in.
	- Guide: [memory-bank/features/realistic-fill-guide.md](features/realistic-fill-guide.md)
	- Deep guide: [docs/backtester-mvp/realistic-fill/completion-and-usage-guide.md](../docs/backtester-mvp/realistic-fill/completion-and-usage-guide.md)

### Research Infrastructure
- **Research Journal Framework**: complete and canonical for hypothesis, experiment, lineage, and artifact tracking.
	- Guide: [memory-bank/features/research-journal-guide.md](features/research-journal-guide.md)
	- Deep guide: [docs/backtester-mvp/research-journal-framework/IMPLEMENTATION_SUMMARY.md](../docs/backtester-mvp/research-journal-framework/IMPLEMENTATION_SUMMARY.md)

## Key Differentiators

### vs. Manual Trading
- **Consistency**: No emotional decisions, missed entries, or late exits
- **Speed**: Real-time data processing and sub-second order submission
- **Discipline**: Stop-loss and exit rules enforced programmatically

### vs. Basic Backtesting Tools
- **Regime Awareness**: Not just overall stats—performance analyzed by market condition
- **Overfitting Prevention**: Full-history validation + out-of-sample testing required
- **Execution Realism**: Slippage, intrabar stops, bar flush timing modeled accurately

### vs. Generic Trading Bots
- **Phase-Based Lifecycle**: Warmup phase prevents trading on stale data
- **Provider Failover**: Automatic switch to backup data source if primary fails
- **Research Integration**: Backtest findings directly inform live trading filters
