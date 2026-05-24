# Project Brief

## Purpose
A production-ready algorithmic trading system that backtests, optimizes, and executes Opening Range Breakout (ORB) strategies on QQQ with real-time data feeds and comprehensive risk management.

## Users
- **Strategy Researchers**: Backtest and optimize ORB strategies using historical data with regime analysis
- **Live Traders**: Execute automated trades during market hours with real-time monitoring
- **System Operators**: Monitor system health, performance metrics, and receive Discord notifications

## Goals
1. **Robust Backtesting**: Validate strategies across 7+ years of data with regime filtering and out-of-sample testing
2. **Reliable Execution**: Connect to real-time data providers (Polygon/Finnhub) with automatic failover and sub-second latency
3. **Risk Management**: Enforce position sizing, stop-loss, and end-of-day exit rules with slippage modeling
4. **Operational Excellence**: Phase-based lifecycle (warmup, trading, cooldown), health monitoring, and structured Discord notifications
5. **Research Framework**: Analyze regime effectiveness, time-of-day patterns, and filter hypotheses with overfitting guardrails

## Constraints
- **Market Hours**: NYSE trading hours (9:30 AM - 4:00 PM EST)
- **Instrument**: QQQ only (multi-symbol support planned)
- **Real-time Data**: Polygon.io (primary) and Finnhub (backup) for tick/bar data
- **Execution**: Paper trading mode initially, with infrastructure for live trading
- **Timezone**: All datetime operations must be market-timezone-aware (EST/EDT)
- **Notification**: Discord webhooks for system status, orders, and daily summaries
