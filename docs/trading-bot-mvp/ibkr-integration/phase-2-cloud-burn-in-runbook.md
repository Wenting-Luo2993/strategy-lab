# Phase 2 Cloud Trading Bot Burn-In Runbook

## Status Snapshot

As of 2026-07-13, Phase 2 is in progress on the current Oracle `VM.Standard.E2.1.Micro` instance. The bot is running as a host `systemd` service rather than Docker for this burn-in slice.

Market-hours recovery validation completed on 2026-07-13 after enabling IBC automation for the paper non-brokerage warning. The bot initialized successfully, selected the Interactive Brokers provider, completed intraday warmup, sent Discord notifications, and placed a one-share QQQ paper short from the active ORB ruleset.

## Current Services

| Service | State | Purpose |
| --- | --- | --- |
| `ibc-gateway.service` | Active/enabled | Starts IBC + IB Gateway in paper mode. |
| `trading-bot-phase2.service` | Active/enabled | Runs the trading bot with paper IB execution and IB realtime market data snapshots. |
| `trading-bot-phase2-start.timer` | Active/enabled | Starts `trading-bot-phase2.service` on weekdays at 13:20 UTC for pre-market warmup. |

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

IBC paper warning automation must be enabled in `/etc/ibkr/config.ini`:

```ini
AcceptNonBrokerageAccountWarning=yes
AcceptIncomingConnectionAction=accept
```

Without `AcceptNonBrokerageAccountWarning=yes`, Gateway can display the paper-account non-brokerage warning after restart and block API clients with IB error `10141`.

## Deployed Code Path

- `InteractiveBrokersExecutionEngine` adapts IB orders/account/positions to the existing execution interface.
- `InteractiveBrokersDataProvider` adapts IB live market data snapshots to the REST polling provider interface.
- `TradingOrchestrator` selects IB execution when `BROKER__BROKER_TYPE=interactive_brokers`.
- `TradingOrchestrator` selects IB market data when `DATA__PRIMARY_PROVIDER=interactive_brokers`.
- The active ruleset is `orb_exp073_paper_burn_in`, which caps burn-in orders at one share.

## Latest Validation Evidence

Confirmed on 2026-07-13:

- `trading-bot-phase2.service` was inactive because it stopped fail-closed on 2026-07-10 after an IB connection failure and no weekday start timer existed yet.
- `trading-bot-phase2-start.timer` was added and enabled; next run was scheduled for the following weekday at 13:20 UTC.
- `AcceptNonBrokerageAccountWarning=yes` was set in `/etc/ibkr/config.ini`; after restarting `ibc-gateway.service`, an API probe connected to account `DUQ886014` without error `10141`.
- Bot log shows execution client `201`, IB data client `202`, and warmup health probe client `221` all reached `API connection ready`.
- Bot log shows `WARM-UP COMPLETE - Ready for trading!`.
- Bot log shows ORB levels for QQQ on 2026-07-13: high `$718.43`, low `$716.35`, range `$2.08`.
- Bot generated a `SHORT_BREAKOUT` signal and filled a one-share QQQ paper sell at `$712.11`.
- Discord logs show `ORDER_SENT`, `ORDER_FILLED`, and ORB notifications sent successfully.

Confirmed on 2026-07-14:

- `trading-bot-phase2-start.timer` fired automatically at `13:20:07 UTC` and started `trading-bot-phase2.service` without manual action.
- `ibc-gateway.service`, `trading-bot-phase2.service`, and `trading-bot-phase2-start.timer` were all active during the market-hours status check.
- IB API probe connected to account `DUQ886014`; no QQQ paper position was open at the time of the check.
- A stale-data issue was found: IB snapshot bars used `volume=0`, causing the trade-based `BarAggregator` to reject every snapshot as invalid trade data and keep strategy evaluation on the prior day's final bar.
- `InteractiveBrokersDataProvider` now emits positive synthetic volume for quote snapshots so the existing aggregator accepts IB snapshot updates.
- After deploying the fix and restarting the bot, warmup fetched/merged fresh July 14 bars, strategy evaluation used `2026-07-14 11:55:00-04:00`, ORB levels were calculated for July 14, and the ORB Discord notification sent successfully.

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