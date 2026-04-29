# Product Requirements Document: Live Trading Dashboard

**Version:** 1.8.0  
**Last Updated:** 2026-04-17  
**Status:** Draft  

---

## 1. Executive Summary

### 1.1 Problem Statement

The trading bot currently operates headlessly with Discord notifications as the only real-time feedback mechanism. There is no centralized view to:
- Monitor live equity curve and open positions
- Analyze trading performance across configurable time periods
- Track operational health of the system and its dependencies
- Identify execution quality issues (slippage, fill rates)

### 1.2 Solution Overview

Build a **live remote dashboard** that provides real-time visibility into trading activity, aggregated performance metrics, and operational health monitoring. The dashboard will be accessible from any device with a web browser, updating in real-time as trading events occur.

### 1.3 Success Criteria

| Metric | Target |
|--------|--------|
| Data latency | < 2 seconds from trade event to dashboard display |
| Dashboard uptime | 99.5% during market hours |
| Page load time | < 3 seconds initial load |
| User satisfaction | Dashboard becomes primary monitoring tool (replacing Discord for live monitoring) |

---

## 2. User Stories

### 2.1 Primary Personas

| Persona | Description | Primary Goals |
|---------|-------------|---------------|
| **Active Trader (You)** | Monitors the bot during market hours | See live P&L, verify trades executed correctly, catch issues early |
| **End-of-Day Reviewer** | Reviews performance after market close | Analyze daily/weekly/monthly performance, identify patterns |
| **System Operator** | Ensures bot is running correctly | Monitor API health, catch connectivity issues, track execution quality |

### 2.2 User Stories

#### Live Monitoring
- **US-1:** As a trader, I want to see my live equity curve updating in real-time so I can understand my current P&L without checking Discord notifications.
- **US-2:** As a trader, I want to see all open positions with current P&L so I can decide whether to manually intervene.
- **US-3:** As a trader, I want to see a live feed of recent trades so I can verify orders executed as expected.

#### Performance Analysis
- **US-4:** As a reviewer, I want to see aggregated metrics (win rate, expectancy, profit factor) for selectable time periods (day/week/month/quarter/year) so I can track performance trends.
- **US-5:** As a reviewer, I want to see performance broken down by symbol so I can identify which assets perform best.
- **US-6:** As a reviewer, I want to see performance broken down by strategy so I can compare different trading approaches.

#### Operational Monitoring
- **US-7:** As an operator, I want to see the health status of downstream APIs (Alpaca, Finnhub) so I can identify connectivity issues.
- **US-8:** As an operator, I want to see WebSocket connection status and message latency so I can detect data feed problems.
- **US-9:** As an operator, I want to track order slippage over time so I can identify execution quality degradation.
- **US-10:** As an operator, I want to receive visual alerts when system health degrades so I don't miss critical issues.

---

## 3. Functional Requirements

### 3.1 Live Trading View

#### 3.1.1 Equity Curve
**Priority:** P0 (Must Have)

| Requirement | Description |
|-------------|-------------|
| Real-time updates | Equity line updates within 2 seconds of any position value change |
| Intraday view | Shows today's equity progression starting from market open |
| Historical overlay | Option to overlay previous days/benchmark for comparison |
| Annotations | Visual markers for trade entry/exit points on the curve |
| Zoom controls | Ability to zoom into specific time ranges |

#### 3.1.2 Open Positions Panel
**Priority:** P0 (Must Have)

| Field | Description |
|-------|-------------|
| Symbol | Ticker symbol |
| Side | Long/Short |
| Quantity | Number of shares |
| Entry Price | Average entry price |
| Current Price | Real-time market price |
| Unrealized P&L | Dollar amount (color-coded green/red) |
| Unrealized P&L % | Percentage gain/loss |
| Duration | Time in position |
| Stop Loss / Take Profit | Current exit levels (if applicable) |

#### 3.1.3 Live Trade Feed
**Priority:** P0 (Must Have)

- Scrolling feed of recent trade events (last 20-50 trades)
- Event types: ORDER_SENT, ORDER_FILLED, ORDER_CANCELLED, TRADE_CLOSED
- Each event displays: timestamp, symbol, side, quantity, price, P&L (for closed trades)
- Color coding: green for profitable exits, red for losses, blue for entries
- Click to expand for full trade details

### 3.2 Performance Analytics

#### 3.2.1 Time Period Selector
**Priority:** P0 (Must Have)

| Period | Definition |
|--------|------------|
| Today | Current trading day |
| This Week | Monday through current day |
| This Month | 1st of month through current day |
| This Quarter | Start of fiscal quarter through current day |
| This Year | January 1st through current day |
| All Time | Entire trading history |
| Custom | User-defined date range |

#### 3.2.2 Core Trading Metrics
**Priority:** P0 (Must Have)

| Metric | Description | Formula |
|--------|-------------|---------|
| Total P&L | Net profit/loss in dollars | Sum of all trade P&L |
| Total P&L % | Return percentage | (Ending Equity - Starting Equity) / Starting Equity |
| Win Rate | Percentage of winning trades | Winning Trades / Total Trades |
| Expectancy | Average expected return per trade | (Win Rate x Avg Win) - (Loss Rate x Avg Loss) |
| Profit Factor | Ratio of gross profit to gross loss | Gross Profit / Gross Loss |
| Average Win | Mean profit on winning trades | Sum of Wins / Number of Wins |
| Average Loss | Mean loss on losing trades | Sum of Losses / Number of Losses |
| Largest Win | Best single trade | Max(Trade P&L) |
| Largest Loss | Worst single trade | Min(Trade P&L) |
| Trade Count | Number of completed trades | Count of closed trades |

#### 3.2.3 Extended Trading Metrics
**Priority:** P1 (Should Have)

| Metric | Description |
|--------|-------------|
| Sharpe Ratio | Risk-adjusted return (annualized) |
| Sortino Ratio | Downside risk-adjusted return |
| Max Drawdown | Largest peak-to-trough decline |
| Max Drawdown Duration | Longest time to recover from drawdown |
| Average Trade Duration | Mean time positions held |
| R-Multiple | Distribution of trade outcomes in risk units |
| Consecutive Wins/Losses | Longest win/loss streaks |
| Recovery Factor | Net Profit / Max Drawdown |

#### 3.2.4 Breakdown Views
**Priority:** P1 (Should Have)

| Breakdown | Description |
|-----------|-------------|
| By Symbol | Performance metrics for each traded symbol |
| By Strategy | Performance metrics for each strategy |
| By Day of Week | Performance patterns by trading day |
| By Time of Day | Performance patterns by entry hour |
| By Market Condition | Performance in trending vs. ranging markets (if tagged) |

### 3.3 Operational Monitoring

#### 3.3.1 System Health Dashboard
**Priority:** P0 (Must Have)

| Component | Health Indicators |
|-----------|-------------------|
| Bot Service | Running/Stopped, Uptime, Memory usage, Last heartbeat |
| Alpaca API | Connected/Disconnected, Response latency, Error rate |
| Finnhub WebSocket | Connected/Disconnected, Last message received, Message rate |
| Database | Connected, Query latency, Storage usage |

**Health Status Colors:**
- Green: Healthy (all checks passing)
- Yellow: Degraded (partial issues, bot still functional)
- Red: Unhealthy (critical issues requiring attention)

#### 3.3.2 Connection Quality Indicators
**Priority:** P1 (Should Have)

| Indicator | Description |
|-----------|-------------|
| WebSocket Latency | Time between server push and client receipt |
| Data Feed Freshness | Time since last price update per symbol |
| API Response Time | Average Alpaca API response time (rolling 5 min) |
| Reconnection Count | Number of WebSocket reconnects today |
| Message Rate | Tick messages per second |

#### 3.3.3 Execution Quality Metrics
**Priority:** P1 (Should Have)

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| Average Slippage | Mean slippage across all orders | > 0.1% |
| Slippage by Symbol | Slippage breakdown per symbol | > 0.15% for any symbol |
| Fill Rate | % of orders fully filled | < 99% |
| Partial Fills | Count of partially filled orders | > 0 |
| Order Latency | Time from signal to order submission | > 1 second |
| Fill Latency | Time from order submission to fill | > 5 seconds |

#### 3.3.4 Alerts & Notifications
**Priority:** P1 (Should Have)

Visual and audio alerts for:
- System health status changes (healthy -> degraded/unhealthy)
- Disconnection events lasting > 30 seconds
- Slippage exceeding threshold on any order
- No trades when expected (e.g., ORB breakout signal but no fill)
- Error rate spike

### 3.4 Trade History & Analysis

#### 3.4.1 Trade Journal
**Priority:** P1 (Should Have)

| Feature | Description |
|---------|-------------|
| Searchable history | Filter by symbol, date range, strategy, outcome |
| Trade details | Full information for each trade (entry/exit times, prices, P&L) |
| Export | Download trade history as CSV |
| Annotations | Add notes to individual trades (for review purposes) |

#### 3.4.2 Calendar Heat Map
**Priority:** P2 (Nice to Have)

- Visual calendar showing daily P&L
- Color gradient from red (losses) to green (profits)
- Click on day to see trade details
- Quick identification of winning/losing patterns

### 3.5 Strategy View

#### 3.5.1 Currently Active Strategy: ORB (Opening Range Breakout)

The trading bot currently runs a single strategy. The dashboard must display strategy-specific context so the user can verify the bot is interpreting market data correctly.

**Strategy Summary:**

| Parameter | Value |
|-----------|-------|
| Strategy Name | Opening Range Breakout (ORB) |
| Traded Symbols | AAPL, GOOGL, MSFT (configurable) |
| Primary Timeframe | 5-minute bars |
| ORB Window | 9:30–9:35 AM EST (first 5-minute bar) |
| Entry: Long | Close breaks above ORB High; candle body ≥ 50% of bar range |
| Entry: Short | Close breaks below ORB Low; candle body ≥ 50% of bar range |
| Take Profit | 2× ORB Range above entry (long) / below entry (short) |
| Stop Loss | ORB Low (long) / ORB High (short) |
| Entry Cutoff | No new entries after 3:00 PM EST |
| EOD Exit | All open positions closed at market close (4:00 PM EST) |
| Volume Filter | Optional (currently disabled); threshold = 1.5× average |
| Risk per Trade | Max 10% of capital per position |
| Indicator | ATR-14 (used to validate ORB range size) |

#### 3.5.2 Intraday Stock Charts
**Priority:** P0 (Must Have)

One candlestick chart per traded symbol, showing the current trading day's intraday price action. The chart provides visual confirmation that the bot is trading at the correct levels.

| Element | Description |
|---------|-------------|
| Chart type | Candlestick (OHLC) on 5-minute bars |
| Timeframe | Current trading day (9:30 AM – 4:00 PM EST) |
| ORB High/Low | Horizontal lines marking the ORB high and low levels for today |
| ORB Zone | Shaded region between ORB High and ORB Low |
| Entry markers | Triangle markers on the chart at trade entry price/time |
| Exit markers | Triangle markers at trade exit price/time (TP = green, SL = red, EOD = gray) |
| Current price | Real-time price line updated as ticks arrive |
| Volume | Volume bars in a sub-panel below the price chart |
| Layout | Symbol tabs or a 2-column grid (e.g., AAPL | GOOGL on one row, MSFT alone) |

**Outside market hours:** Show last trading day's chart. If no data is available, show a placeholder.

#### 3.5.3 Per-Strategy Indicator Overlays
**Priority:** P1 (Should Have)

Each strategy may require additional indicator overlays on its stock chart. For ORB:

| Indicator | Overlay Location | Description |
|-----------|-----------------|-------------|
| ATR-14 | Sub-panel | 14-period Average True Range — shows daily volatility context |
| ORB Range | Main chart annotation | Text label showing ORB range in dollars and % of price |
| Volume MA-20 | Volume sub-panel | 20-period volume moving average to support volume filter visualization |

**P2 indicators (future strategies):**

| Indicator | When to Add |
|-----------|-------------|
| VWAP | When a VWAP-based strategy is added |
| EMA-9 / EMA-21 | When a trend-following strategy is added |
| RSI-14 | When a mean-reversion strategy is added |
| Bollinger Bands | When a volatility breakout strategy is added |

#### 3.5.4 Strategy Status Summary
**Priority:** P1 (Should Have)

A compact status card per symbol showing whether the strategy has acted today:

| Field | Description |
|-------|-------------|
| Symbol | Ticker |
| ORB Established | Yes/No — whether today's ORB levels were calculated |
| ORB High / Low | Today's levels (e.g., $182.45 / $181.20) |
| ORB Range | Range in dollars and % (e.g., $1.25 / 0.69%) |
| Signal Today | Long / Short / None |
| Trade Taken | Yes/No (whether entry cutoff passed with no fill) |
| Current Status | Open position / Closed / Watching |

---

## 4. Non-Functional Requirements

### 4.1 Performance

| Requirement | Target |
|-------------|--------|
| Initial page load | < 3 seconds |
| Real-time update latency | < 2 seconds |
| Smooth scrolling | 60 FPS in trade feed |
| Historical data query | < 2 seconds for 1 year of data |

### 4.2 Accessibility

| Requirement | Description |
|-------------|-------------|
| Remote access | Accessible from any device with browser (desktop, tablet, phone) |
| Mobile responsive | Key views usable on mobile (equity curve, open positions, health status) |
| Authentication | Secure login required for access |
| Session management | Auto-logout after inactivity |

### 4.3 Reliability

| Requirement | Target |
|-------------|--------|
| Dashboard uptime | 99.5% during market hours (9:30 AM - 4:00 PM EST) |
| Graceful degradation | Dashboard remains usable if real-time updates fail |
| Reconnection | Automatic reconnect with exponential backoff |
| Data consistency | No stale data displayed; show "last updated" timestamp |

### 4.4 Security

| Requirement | Description |
|-------------|-------------|
| Authentication | **Phase 1: None.** Dashboard is publicly accessible to anyone with the URL. Acceptable because the dashboard is read-only and the URL is not advertised. Add auth in a later phase if needed. |
| Transport encryption | HTTPS/WSS only (Let's Encrypt via Caddy) |
| No trading actions | Dashboard is read-only; no ability to place/cancel orders |
| Rate limiting | Not required in Phase 1 |

### 4.5 Infrastructure Cost

**Hard Requirement: Total hosting cost must be $0/month.**

All infrastructure choices — compute, database, networking — must stay within free tiers or be self-hosted at no charge. Any technology or service that requires payment (even after a trial period) is disqualified.

**Design constraint that shapes these choices:** The trading bot runs on Oracle Cloud Always Free AMD micro (1 vCPU, 1 GB RAM). This instance is fully committed to the bot. The dashboard must run on a completely separate machine — co-hosting anything alongside the bot on 1 vCPU / 1 GB RAM risks starving the trading loop of CPU and memory during market hours.

**The dashboard is a monitoring tool, not a trading-critical service.** A human opens it intentionally to check on the bot. It does not need to be always-on. A cold start of 30–60 seconds when opening the dashboard is acceptable — the user is already present and waiting. This changes which hosting options are viable.

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Trading bot | Oracle Cloud Always Free AMD micro (existing) | Unchanged. Bot is the sole tenant on this VM. |
| Database | **Neon Postgres free tier** | Required because the dashboard runs on a separate machine and cannot read a SQLite file on the Oracle Cloud VM. Neon is free (0.5 GB), never pauses, and the SQLite schema migrates 1:1. |
| Dashboard (FastAPI + Streamlit) | **Render free web service** | Free, publicly accessible, no credit card required. Spins down after 15 min inactivity and takes ~30–60s to cold-start — acceptable for a monitoring dashboard opened intentionally by a human. Dashboard is fully isolated from the bot. |
| TLS/HTTPS | Managed by Render (automatic) | No configuration needed; Render provides HTTPS on its `*.onrender.com` subdomain. |

**Why this split makes sense:**

| Concern | Co-hosting on 1 vCPU | Render + Neon |
|---------|---------------------|---------------|
| Bot safety during market hours | ❌ Dashboard queries compete for CPU/RAM | ✅ Completely separate machines |
| Cold start (30–60s) | N/A | ✅ Acceptable — human opens it deliberately |
| Database access | SQLite on same VM | Neon Postgres, accessible from any host |
| Cost | $0 | $0 |

**Why not other options:**

| Option | Problem |
|--------|---------|
| **Supabase free tier** | Pauses the entire project after 1 week of inactivity — database becomes inaccessible until manually unpaused. Unacceptable for a bot that may go days without a dashboard visit. |
| **Fly.io free tier** | 256 MB RAM per machine — too tight for Streamlit. |
| **GitHub Pages / Vercel** | Static files only — cannot run Python, FastAPI, or WebSockets without a full JS rewrite. |
| **Streamlit Community Cloud** | Requires a public GitHub repo. Trading logic and API keys must stay private. |
| **Grafana Cloud** | Expects Prometheus/InfluxDB time-series sources. Cannot render custom candlestick charts with trade markers. |
| **Co-hosting on Oracle Cloud VM** | 1 vCPU / 1 GB RAM is fully committed to the trading bot. Adding a web server risks starving the trading loop. |

**Constraints that follow from this:**
- Bot and dashboard run on entirely separate machines — no shared process, no shared filesystem
- Dashboard is read-only on the database — it never writes trade records
- Bot writes trade data to Neon Postgres; dashboard reads from the same Neon database
- Dashboard cold starts are acceptable; bot uptime is non-negotiable

### Future Migration Path

FastAPI is the stable separation point — Streamlit communicates with it over HTTP, so any future frontend can replace Streamlit by pointing at the same API URL. The database is already on Neon Postgres, so hosting the dashboard elsewhere requires only a config change (API base URL).

| Stage | Bot | Dashboard | Database | Trigger |
|-------|-----|-----------|----------|---------|
| **Phase 1** | Oracle Cloud AMD micro | Render free tier | Neon Postgres free tier | Default |
| **Low cost** | Oracle Cloud or any VPS | Fly.io, Railway, or any PaaS with persistent processes | Neon Postgres | When Render cold starts become annoying, or budget allows $5–7/month |
| **Scale-out** | Any VPS | Static JS frontend on GitHub Pages / Vercel | Neon or managed Postgres | When Streamlit limits are hit or a mobile-first UI is needed |

**What stays stable across all stages:**
- FastAPI endpoint contracts and WebSocket protocol
- Trade data model (Neon Postgres schema unchanged)
- Dashboard UI logic — only the API base URL config changes

---

## 5. Information Architecture

### 5.1 Navigation Structure

```
Dashboard
├── Live View (default)
│   ├── Equity Curve
│   ├── Open Positions
│   └── Trade Feed
├── Charts
│   ├── AAPL (candlestick + ORB levels + entry/exit markers)
│   ├── GOOGL (candlestick + ORB levels + entry/exit markers)
│   ├── MSFT (candlestick + ORB levels + entry/exit markers)
│   └── Strategy Status (ORB summary per symbol)
├── Performance
│   ├── Metrics (with time period selector)
│   ├── By Symbol
│   ├── By Strategy
│   └── Distributions
├── Operations
│   ├── System Health
│   ├── Connection Status
│   └── Execution Quality
└── History
    ├── Trade Journal
    └── Calendar
```

### 5.2 Default View

When the user opens the dashboard, always show the Live View: equity curve, open positions, trade feed, and stock charts. The dashboard has no concept of market hours — it shows the same layout at all times.

Performance metric calculations that are time-dependent (e.g., "today's P&L") are handled server-side using trade timestamps. The UI always requests the same data; the API returns whatever is current.

---

## 6. Data Requirements

### 6.1 Data Sources

| Data | Source | Update Frequency |
|------|--------|------------------|
| Account equity | Trading bot (via API) | Real-time on position changes |
| Open positions | Trading bot (via API) | Real-time |
| Trade history | Trading bot database | On each trade |
| Current prices | Trading bot (from data provider) | Real-time during market hours |
| Health metrics | Trading bot health monitor | Every 10 seconds |
| Order events | Trading bot (via WebSocket) | Real-time |

### 6.2 Calculated Metrics

All performance metrics (expectancy, Sharpe, etc.) should be calculated on the dashboard side from raw trade data to:
- Avoid duplicating calculation logic
- Allow flexible time period filtering
- Enable breakdown views without pre-aggregation

### 6.3 Historical Data Retention

| Data Type | Retention |
|-----------|-----------|
| Trade records | Indefinite |
| Equity snapshots | Daily close for 5 years; intraday (5-min) for 90 days |
| Health metrics | 90 days |
| Order events | 1 year |

### 6.4 Database Requirements

**Storage engines:**

| Environment | Database | Rationale |
|-------------|----------|-----------|
| Local development | SQLite (`./data/trades.db`) | Zero setup, already implemented, sufficient for local testing |
| Production | **Neon Postgres** (free tier) | Dashboard runs on Render (separate machine from the bot); cannot read a SQLite file on the Oracle Cloud VM. Neon is free, never pauses, and the schema migrates 1:1 from SQLite. |

**Current gap — prerequisite for the dashboard to function:**

The database schema and storage layer exist today, but the trading bot does not yet write trade records to the database. Specifically:
- Trade entries (order filled) are not persisted
- Trade exits (position closed, P&L calculated) are not persisted
- As a result, the dashboard trade history is always empty and all performance metrics return zero

This must be resolved before the dashboard can display any meaningful data. It is a **Phase 1 prerequisite**, not a dashboard task — the fix belongs in the trading bot's execution path, not in the dashboard UI or API.

**Required behaviour once fixed:**
- Every filled order entry must create a trade record (symbol, side, quantity, fill price, timestamp, strategy)
- Every position close must update that record (exit price, exit timestamp, P&L, status = closed)
- Records must survive bot restarts — the SQLite file is the source of truth

---

## 7. Phases & Prioritization

### Phase 1: Core Live Monitoring (MVP)
**Target:** 2 weeks

**Prerequisite (trading bot, not dashboard):** Wire trade persistence — bot must write trade entries and exits to SQLite before Phase 1 dashboard work begins. Without this, all dashboard data will be empty.

| Feature | Priority |
|---------|----------|
| [Prereq] Trade persistence wired in trading bot execution path | P0 |
| Live equity curve | P0 |
| Open positions panel | P0 |
| Live trade feed | P0 |
| Basic health status | P0 |
| Remote access via HTTPS (no login required) | P0 |
| Intraday stock charts (candlestick + ORB levels + trade markers) | P0 |
| Strategy status summary per symbol | P0 |
| Free hosting on Oracle Cloud Always Free | P0 (hard requirement) |

### Phase 2: Performance Analytics
**Target:** 2 weeks after Phase 1

| Feature | Priority |
|---------|----------|
| Time period selector | P0 |
| Core trading metrics | P0 |
| Extended trading metrics | P1 |
| Symbol breakdown | P1 |
| Strategy breakdown | P1 |
| Per-strategy indicator overlays (ATR-14, volume MA-20) | P1 |

### Phase 3: Operational Excellence
**Target:** 2 weeks after Phase 2

| Feature | Priority |
|---------|----------|
| Detailed health dashboard | P1 |
| Execution quality metrics | P1 |
| Visual alerts | P1 |
| Trade journal | P1 |

### Phase 4: Polish & Extensions
**Target:** Ongoing

| Feature | Priority |
|---------|----------|
| Mobile optimization | P2 |
| Calendar heat map | P2 |
| Advanced filtering | P2 |
| Day-of-week analysis | P2 |
| Export functionality | P2 |

---

## 8. Open Questions

| # | Question | Impact | Resolution Required By | Status |
|---|----------|--------|------------------------|--------|
| 1 | Should the dashboard support multiple trading accounts? | Affects data model and UI | Phase 1 | **Resolved: No.** Single account only for now. |
| 2 | Should there be multiple user access levels (admin vs. viewer)? | Affects authentication design | Phase 1 | **Resolved: No auth in Phase 1.** Dashboard is publicly accessible. No login, no roles. Revisit in a later phase if the URL is shared more broadly. |
| 3 | Should real-time price data be included (beyond position values)? | Affects data bandwidth requirements | Phase 1 | **Resolved: Yes.** Stock charts require real-time OHLCV data. Sourced from the bot's existing data provider (Finnhub/Polygon). |
| 4 | Should the dashboard include backtesting comparison views? | Affects scope significantly | Phase 2 | Open |
| 5 | Should alerts be pushed to email/SMS in addition to dashboard? | Affects notification infrastructure | Phase 3 | Open |
| 6 | What free hosting platform to use for the dashboard? | Affects deployment architecture | Phase 1 | **Resolved: Oracle Cloud Always Free** — co-host bot, FastAPI, and Streamlit on the same VM. No cold starts. Reads SQLite directly. Caddy handles HTTPS. See §4.5 for why Render, Grafana, and GitHub Pages were ruled out. |

---

## 9. Appendix

### 9.1 Glossary

| Term | Definition |
|------|------------|
| **Expectancy** | The average amount you can expect to win (or lose) per trade. Positive expectancy indicates a profitable system. |
| **Profit Factor** | Ratio of gross profits to gross losses. > 1 is profitable. > 2 is considered excellent. |
| **Sharpe Ratio** | Risk-adjusted return measuring excess return per unit of volatility. > 1 is acceptable, > 2 is excellent. |
| **Sortino Ratio** | Like Sharpe but only penalizes downside volatility, not upside. |
| **Max Drawdown** | The largest percentage decline from a peak to a trough before a new peak. |
| **R-Multiple** | Trade P&L expressed as a multiple of initial risk (e.g., +2R = profit of 2x the risk amount). |

### 9.2 Related Documents

- `CLAUDE.md` - Trading bot code patterns and architecture
- `CLAUDE_MEMORY.md` - Lessons learned and incident fixes
- `vibe/trading_bot/notifications/payloads.py` - Current event types and data structures

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.8.0 | 2026-04-17 | Claude | Revised §4.5: 1 vCPU constraint means bot must be sole VM tenant; dashboard on Render (cold start acceptable for monitoring tool) + Neon Postgres; updated migration path |
| 1.7.0 | 2026-04-17 | Claude | Revised §4.5: ARM Ampere (not AMD), two-container isolation rationale, Neon as DB migration target; ruled out Render (spin-down), Supabase (pausing), Fly.io (RAM), Grafana, GitHub Pages |
| 1.6.0 | 2026-04-17 | Claude | Added migration path to §4.5: SQLite→Postgres as the unlock for separating dashboard from bot VM; Streamlit→JS as the path to static hosting |
| 1.5.0 | 2026-04-17 | Claude | Clarified §4.5 hosting: Oracle Cloud co-host only; added rationale table ruling out Render, GitHub Pages, Grafana, Streamlit Cloud |
| 1.4.0 | 2026-04-17 | Claude | Added §6.4 database requirements: SQLite local vs production paths, current persistence gap as Phase 1 prerequisite |
| 1.3.0 | 2026-04-17 | Claude | Simplified default view: always show Live View regardless of market hours; metric time-gating is server-side only |
| 1.2.0 | 2026-04-17 | Claude | Phase 1 auth: no login required; dashboard publicly accessible via HTTPS URL |
| 1.1.0 | 2026-04-17 | Claude | Added: ORB strategy details (§3.5), intraday stock charts (§3.5.2), per-strategy indicator overlays (§3.5.3), strategy status card (§3.5.4), free hosting hard requirement (§4.5), Charts nav section (§5.1), Phase 1/2 updates |
| 1.0.0 | 2026-04-17 | Claude | Initial draft |
