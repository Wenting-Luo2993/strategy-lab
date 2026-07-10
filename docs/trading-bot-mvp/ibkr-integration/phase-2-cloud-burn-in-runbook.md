# Phase 2 Cloud Trading Bot Burn-In Runbook

## Status Snapshot

As of 2026-07-09, Phase 2 is in progress on the current Oracle `VM.Standard.E2.1.Micro` instance. The bot is running as a host `systemd` service rather than Docker for this burn-in slice.

The current validation was performed outside market hours. The bot initialized successfully, selected the Interactive Brokers provider, and then slept until the next market warmup. Market-hours validation remains required.

## Current Services

| Service | State | Purpose |
| --- | --- | --- |
| `ibc-gateway.service` | Active/enabled | Starts IBC + IB Gateway in paper mode. |
| `trading-bot-phase2.service` | Active/enabled | Runs the trading bot with paper IB execution and IB realtime market data snapshots. |

Current bot command:

```bash
/opt/strategy-lab/.venv/bin/python -m vibe.trading_bot.main run
```

## Runtime Configuration

The Phase 2 service uses these non-secret settings:

```ini
Environment=ACTIVE_RULESET=orb_exp073_paper_burn_in
Environment=DATA__PRIMARY_PROVIDER=interactive_brokers
Environment=BROKER__BROKER_TYPE=interactive_brokers
Environment=BROKER__MODE=paper
Environment=BROKER__IB_HOST=127.0.0.1
Environment=BROKER__IB_PORT=4002
Environment=BROKER__IB_CLIENT_ID=201
Environment=BROKER__IB_MARKET_DATA_TYPE=1
Environment=BROKER__IB_CONNECT_TIMEOUT=30
Environment=BROKER__HEALTH_CHECK_ENABLED=true
Environment=BROKER__HEALTH_CHECK_SYMBOL=QQQ
Environment=DATA__POLL_INTERVAL_WITH_POSITION=60
Environment=DATA__POLL_INTERVAL_NO_POSITION=300
```

`DATA__PRIMARY_PROVIDER=interactive_brokers` intentionally opts out of Finnhub WebSocket for Phase 2. IB market data uses a separate provider connection derived from the execution client id, so execution and data do not share the same IB client id.

## Deployed Code Path

- `InteractiveBrokersExecutionEngine` adapts IB orders/account/positions to the existing execution interface.
- `InteractiveBrokersDataProvider` adapts IB live market data snapshots to the REST polling provider interface.
- `TradingOrchestrator` selects IB execution when `BROKER__BROKER_TYPE=interactive_brokers`.
- `TradingOrchestrator` selects IB market data when `DATA__PRIMARY_PROVIDER=interactive_brokers`.
- The active ruleset is `orb_exp073_paper_burn_in`, which caps burn-in orders at one share.

## Latest Validation Evidence

Confirmed on 2026-07-09:

- Service env contains `DATA__PRIMARY_PROVIDER=interactive_brokers`.
- Bot log shows `Creating Interactive Brokers market data provider`.
- Bot log shows `Primary provider: Interactive Brokers (type=rest, real_time=True)`.
- Bot log shows `Market Status: CLOSED` and `Data Source: Yahoo Finance historical plus Interactive Brokers at market open`.
- Bot log shows `All components initialized successfully` and `Trading loop started`.
- IB paper account check showed no open positions, no open orders, and no open trades.
- Memory after restart was approximately 579 MiB available with 3.4 GiB swap free.

## Next Market-Hours Validation

Run these checks after the next warmup/open window:

```bash
systemctl is-active trading-bot-phase2.service
journalctl -u trading-bot-phase2.service --since "today 13:20 UTC" --no-pager \
  | grep -E "Interactive Brokers|Started REST|REALTIME BAR|SIGNAL|TRADE|ERROR|Heartbeat"
```

Expected evidence:

- Warmup verifies IB broker/account/quote health.
- REST polling starts only while the market is open.
- IB snapshot polling creates realtime bars for `QQQ`.
- During market hours with IB realtime active, Yahoo delayed fallback is not used for signal evaluation.
- No Finnhub WebSocket connection is attempted.
- If an order is generated, it is capped to one share and reconciled through IB paper account state.

## Remaining Tasks

- Observe at least one full market session with the IB realtime provider active.
- Confirm ORB entry/exit behavior using IB-fed realtime bars, including `breakeven_plus_ticks` stop movement if a position opens.
- Add restart reconciliation before submitting any new order after bot or Gateway restart.
- Add alerting for broker connection, provider connection, read-only/API-mode mismatch, and stale market data.
- Move the burn-in to the larger always-free VM shape when ready, then repeat the same smoke and market-hours checks.
- Keep live mode disabled until paper burn-in, reconciliation, and live-readiness review are complete.