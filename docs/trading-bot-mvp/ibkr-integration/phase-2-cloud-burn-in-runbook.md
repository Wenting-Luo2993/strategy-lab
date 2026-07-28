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
Environment=STRATEGY__CARRYOVER_POSITION_POLICY=flatten_at_market_open
Environment=DATA__POLL_INTERVAL_WITH_POSITION=60
Environment=DATA__POLL_INTERVAL_NO_POSITION=60
TimeoutStartSec=240
ExecStartPre=/bin/bash -lc 'for i in {1..180}; do if timeout 1 bash -c "</dev/tcp/127.0.0.1/4002" 2>/dev/null; then exit 0; fi; echo "Waiting for IB Gateway API port 4002 ($i/180)"; sleep 1; done; exit 1'
```

`DATA__PRIMARY_PROVIDER=interactive_brokers` intentionally opts out of Finnhub WebSocket for Phase 2. IB market data uses a separate provider connection derived from the execution client id, so execution and data do not share the same IB client id.

IBC paper warning automation must be enabled in `/etc/ibkr/config.ini`:

```ini
AcceptNonBrokerageAccountWarning=yes
AcceptIncomingConnectionAction=accept
```

Without `AcceptNonBrokerageAccountWarning=yes`, Gateway can display the paper-account non-brokerage warning after restart and block API clients with IB error `10141`.

`STRATEGY__CARRYOVER_POSITION_POLICY=flatten_at_market_open` is the ORB paper burn-in default. ORB is strictly intraday, so planned start-of-day warmup closes any broker position carried from a prior session with a market close order before new entries are allowed. Intraday restarts skip automatic flattening; if a broker position is still open when a fresh signal appears, the trading loop blocks the new entry and logs `carryover_position_active`. For non-intraday strategies, use a strategy-specific policy such as `block_new_entries` or `manual_only` until managed carryover state reconstruction is implemented.

## Deployed Code Path

- `InteractiveBrokersExecutionEngine` adapts IB orders/account/positions to the existing execution interface.
- `InteractiveBrokersDataProvider` adapts IB live market data snapshots to the REST polling provider interface.
- `TradingOrchestrator` selects IB execution when `BROKER__BROKER_TYPE=interactive_brokers`.
- `TradingOrchestrator` selects IB market data when `DATA__PRIMARY_PROVIDER=interactive_brokers`.
- The active ruleset is `orb_exp073_paper_burn_in`, which caps burn-in orders at one share.

## Latest Validation Evidence

Confirmed on 2026-07-28:

- Production health checks showed `ibc-gateway.service`, `trading-bot-phase2.service`, and `trading-bot-phase2-start.timer` active; Docker shadow stack was stopped; IB API port `4002`, `/api/health`, and `/health/ready` were healthy.
- The IB paper account held an existing `QQQ -1` position, but same-day logs showed zero order/fill/submission lines. ORB emitted `already_traded_today`, exposing a carryover semantics issue rather than evidence of a same-day trade.
- Policy decision: ORB should flatten broker carryovers during start-of-day warmup and then treat the session as clean. Other strategies should configure carryover behavior explicitly.

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

Confirmed on 2026-07-15:

- `trading-bot-phase2-start.timer` fired at `13:20 UTC`; `ibc-gateway.service`, `trading-bot-phase2.service`, and the timer were active during the market-hours status check.
- IB API probe connected to account `DUQ886014` with net liquidation visible; no QQQ paper position was open.
- A timestamp issue was found: IB quote snapshots returned naive UTC timestamps, and the aggregator treated naive timestamps as Eastern, shifting realtime bars into afternoon market time and causing ORB opening-window detection to miss `09:30`.
- `InteractiveBrokersDataProvider` now marks naive IB quote timestamps as UTC before passing them to the bar aggregator.
- After deploying the fix and restarting the bot, warmup fetched fresh July 15 bars, ORB calculation used Eastern timestamps from `09:30-04:00` onward, and ORB levels were valid for July 15: high `$724.35`, low `$722.78`.

Confirmed on 2026-07-16:

- `trading-bot-phase2-start.timer` fired at `13:20:11 UTC`; `ibc-gateway.service`, `trading-bot-phase2.service`, and the timer were active during the market-hours status check.
- Initial status showed healthy services, valid IB connectivity, no QQQ paper position, Eastern realtime bars, and valid ORB levels, but ORB range was `$0.00` because 5-minute no-position polling contributed only one IB quote snapshot to the `09:30` opening bar.
- Phase 2 no-position polling was tightened from `300s` to `60s` in `trading-bot-phase2.service` and the service was restarted with no open QQQ position.
- After restart, warmup fetched fresh July 16 yfinance bars through `12:15 ET`; ORB recalculated to high `$713.59`, low `$711.20`, range `$2.39`.
- The bot generated a `SHORT_BREAKOUT` signal and filled a one-share QQQ paper sell at `$708.11`; Discord `ORDER_SENT`, `ORDER_FILLED`, and ORB notifications were sent successfully.

Post-market alert fix on 2026-07-16:

- The July 15 evening Discord `IB connection failed` alert was caused by a Gateway restart race, not a persistent broker outage.
- At `23:45 UTC`, `ibc-gateway.service` restarted. Because `trading-bot-phase2.service` requires Gateway, systemd stopped and restarted the sleeping bot. The bot relaunched before Gateway reopened API port `4002`, exhausted its 3 application-level IB connection attempts, and correctly sent a `SYSTEM_ERROR` alert.
- `trading-bot-phase2.service` now includes an `ExecStartPre` readiness gate that waits up to 180 seconds for `127.0.0.1:4002` before launching the Python bot. This keeps expected Gateway startup latency out of the bot's bounded retry/error-alert path.
- The unit was daemon-reloaded without restarting the currently running bot because a one-share QQQ paper short was open at the time of the fix.

## Next Market-Hours Validation

Before the next market day, run the compressed lifecycle simulation locally or on the VM to validate phase routing without touching IB or placing orders:

```bash
cd /opt/strategy-lab
. .venv/bin/activate
PYTHONPATH=/opt/strategy-lab python -m pytest vibe/tests/trading_bot/test_lifecycle_simulation.py -q
```

This test uses `MockMarketScheduler` and orchestrator `testing_mode` to fast-forward through day 1 warmup, market-hours trading, post-close cooldown, and day 2 warmup in seconds.

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