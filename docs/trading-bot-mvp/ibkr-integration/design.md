# IBKR Oracle Cloud Integration Design

**Status**: Active implementation tracker  
**Created**: 2026-07-06  
**Last Updated**: 2026-07-27  
**Scope**: Plan Interactive Brokers paper/live execution for the Oracle Cloud trading bot deployment, with special focus on IB Gateway login/session operations.

---

## Summary

We have already proven the local paper-trading execution path: Python trading bot, `ib_insync`, IB Gateway/TWS authentication, account access, order placement, and paper fills against QQQ. The next feature is not primarily an order API problem. The hard part is operating IB Gateway reliably on Oracle Cloud, because IBKR intentionally requires an interactive login flow and periodic reauthentication.

This design stages the integration so the first Oracle Cloud deployment uses IBC automation from the start, with remote desktop access kept only as a fallback for 2FA, unexpected dialogs, and emergency inspection.

Recommended path:

1. Continue local paper trading until execution metrics and bot behavior are stable.
2. Move IB Gateway, IBC, and the trading bot onto the Oracle VM together.
3. Use IBC to automate Gateway startup, routine dialogs, scheduled restarts, and normal login flow.
4. Keep SSH-tunneled remote desktop available only for manual intervention.
5. Dockerize Gateway/IBC and bot services after the IBC operating model is stable.

---

## Existing Foundation

The repository already contains the core broker integration foundation:

- `vibe/trading_bot/brokers/interactive_brokers.py` implements an `ib_insync`-based Interactive Brokers adapter.
- `memory-bank/adrs/adr-017-broker-abstraction-protocol.md` defines the broker abstraction that lets the trading bot switch between mock and IB execution.
- `memory-bank/features/ib-broker-integration-guide.md` documents local IB paper-account setup and smoke tests.
- `scripts/ib_paper_smoke.py` validates account access, market data, paper order placement, fills, and execution metrics.

This cloud design should extend that foundation instead of replacing it.

---

## Goals

- Run the trading bot against an IBKR paper account on Oracle Cloud.
- Keep IB Gateway available for the bot during market hours.
- Provide a practical login and recovery process despite IB Gateway being a GUI Java application.
- Preserve broker abstraction boundaries so strategies do not talk directly to IB Gateway.
- Capture enough operational telemetry to know whether the broker session, market data, and order execution path are healthy.
- Avoid live trading until the paper system has demonstrated stable unattended behavior.

---

## Non-Goals

- Bypassing IBKR security controls.
- Building a custom replacement for IB Gateway or Client Portal Gateway.
- Starting directly with live trading.
- Moving strategy logic into the broker adapter.
- Treating Docker as a login automation solution by itself. Docker can package Gateway, but IBC or manual remote desktop still handles the GUI/login problem.

---

## Key Requirement: IB Gateway Session Management

IB Gateway is a GUI Java application. On a headless cloud VM it still needs a display, either a real remote desktop session or a virtual display such as Xvfb. The bot can only use the socket API after Gateway is running, logged in, and accepting API clients.

Operational implications:

- Gateway may log out after IBKR maintenance windows or session expiry.
- First-time API client connections may require a confirmation dialog.
- Two-factor authentication may require manual approval depending on account settings.
- Automatic restart is useful, but cannot guarantee recovery from every authentication prompt.
- The bot must treat broker connectivity as an external dependency with health checks, circuit breakers, and clear alerts.

---

## Architecture

### Stage 1 Target: Oracle VM With IBC Automation

```text
Oracle VM
├── Lightweight desktop environment (XFCE or similar)
├── Remote desktop access for fallback only (SSH-tunneled VNC/noVNC)
├── IBC
│   └── starts and supervises IB Gateway
├── IB Gateway launched by IBC
├── Trading bot process
├── Health monitor
├── Local SQLite operational metrics
└── Discord notifications
```

The trading bot connects to Gateway on localhost:

```text
TradingBot -> InteractiveBrokersAPI -> ib_insync -> 127.0.0.1:4002 -> IB Gateway -> IBKR
```

This is the first Oracle deployment target. IBC is responsible for routine Gateway startup, login form filling, expected dialog dismissal, and restart after disconnects. Manual remote desktop access remains available for 2FA, unexpected dialogs, IBKR notices, and emergency inspection, but it is not the primary operating model.

### Stage 2 Target: Docker Compose

```text
docker-compose.yml
├── ib-gateway      # IB Gateway + IBC + Xvfb + optional VNC/noVNC
├── trading-bot     # strategy orchestration and broker adapter
├── metrics-store   # SQLite volume initially; PostgreSQL later if needed
├── monitoring      # health checks and dashboards
└── notifier        # Discord notifications, if split from bot later
```

In this stage, the strategy still uses the broker abstraction. The container boundary changes deployment mechanics, not the application contract.

---

## Recommended Service Boundaries

For the near-term MVP, the trading bot can connect directly to `InteractiveBrokersAPI`. Longer term, separate the strategy engine from execution:

```text
Strategy Engine
      |
      v
Execution Service
      |
      v
InteractiveBrokersAPI / ib_insync
      |
      v
IB Gateway
```

Benefits:

- Strategies can be tested without a live IB session.
- Broker restarts do not necessarily restart strategy state.
- Multiple strategies can share one execution gateway with account-level risk controls.
- A future broker can be added behind the execution service without changing strategy code.

For the current MVP, keep this as a planned evolution rather than adding the service boundary immediately.

---

## Phased Implementation Plan

### Execution Status

| Phase | Status | Evidence / Notes | Next Decision |
| --- | --- | --- | --- |
| Phase 0: Local Paper Trading Burn-In | Complete | Live API quotes validated for `QQQ`, `GOOGL`, `AMZN`, and `TSLA`; paper market buy/sell filled; non-marketable limit timeout/cancel validated; QQQ position reconciled back to baseline. | Proceed to Phase 1 planning for Oracle VM + IBC. |
| Phase 1: Oracle VM IBC Deployment | Complete | Oracle VM reachable; 4 GB swap configured; Java, Xvfb, x11vnc, IB Gateway stable, IBC 3.24.1, repo checkout, Python venv, IBC wrapper, and `ibc-gateway.service` are prepared. IBC/Gateway starts in paper mode; host firewall drops non-loopback `4001`/`4002`; readonly cloud smoke passed; paper market buy/sell filled; non-marketable limit timeout/cancel validated; final QQQ position reconciled with zero open orders. | Proceed to Phase 2 cloud trading bot integration and longer burn-in on a larger VM shape. |
| Phase 2: Cloud Trading Bot Integration | Complete for paper burn-in MVP | `trading-bot-phase2.service` is running on the Oracle VM in paper mode with `orb_exp073_paper_burn_in`; strategy execution routes through the Interactive Brokers execution adapter; `DATA__PRIMARY_PROVIDER=interactive_brokers` uses IB live/snapshot polling instead of Finnhub WebSocket; Stage 5 dashboard publishing is enabled and validated against Supabase; health API reports alive/ready. Current paper state on 2026-07-27: `QQQ -1`, zero open IB orders. | Continue observing multi-session paper burn-in while preparing the Docker boundary. |
| Phase 3: IBC Hardening | In progress, sufficient to start Phase 4 planning | `ibc-gateway.service` and `trading-bot-phase2.service` are supervised by systemd; bot startup waits for IB API port `4002`; IBC paper warning handling was fixed with `AcceptNonBrokerageAccountWarning=yes`; Discord notifications cover order events and unresolved dashboard publish states; manual restart/flatten/redeploy/reconcile flow was exercised on 2026-07-27. Remaining gaps: formal restart/reconciliation runbook, watchdog near market open, log rotation, and multi-session recovery evidence. | Start Phase 4 Docker design in parallel, but keep Phase 3 hardening open until the remaining ops runbooks/watchdogs are complete. |
| Phase 4: Dockerized Deployment | Started | Initial Docker Compose scaffold and runbook were added under `deploy/ibkr-docker/` and `phase-4-dockerized-deployment-runbook.md`. The design uses separate `ib-gateway` and `trading-bot` services, with the bot sharing the Gateway network namespace so it can keep using `127.0.0.1:4002`; the Supabase `RemoteDataPublisher` remains an in-process worker inside `trading-bot`; `docker compose config` passes locally. Do not cut over production until paper burn-in and Phase 3 recovery runbooks are proven. | Populate ignored runtime inputs on a test host, build the shadow stack, then validate Gateway health, bot readiness, paper order smoke, and Supabase Stage 5 publishing. |
| Phase 5: Live Readiness Review | Deferred | Live trading remains out of scope until paper cloud stability is proven. | Review risk controls and require explicit live-mode approval. |

### Phase 0: Local Paper Trading Burn-In

Status: complete as of 2026-07-08. See `phase-0-local-burn-in-runbook.md` for command outputs, order results, and lessons learned.

Tasks:

- Run paper trading locally for multiple sessions with tiny size.
- Validate market data availability, order submission, fills, cancel paths, reconnect behavior, and metrics capture.
- Confirm the trading bot can run in `readonly` mode for preflight checks.
- Record expected fill, actual fill, slippage, latency, commission, order status, and session health.

Exit criteria:

- At least several paper orders complete successfully.
- A failed Gateway/session state produces a clear error and does not leave the bot in an ambiguous trading state.
- The local runbook is documented well enough to repeat from a clean machine.

### Phase 1: Oracle VM IBC Deployment

Status: complete as of 2026-07-09. See `phase-1-oracle-ibc-runbook.md` for Oracle VM setup, IBC/Gateway service configuration, fallback GUI access, and validation commands.

Tasks:

- Provision or update the Oracle VM with enough memory for Java Gateway plus the bot. Prefer the Ampere A1 free-tier shape if available over a 1 GB micro VM.
- Install Java, IB Gateway, IBC, Python dependencies, and a lightweight desktop or virtual display environment.
- Configure remote desktop access through SSH tunneling for fallback inspection only.
- Store IBKR credentials outside git using environment files or secret storage with restricted file permissions.
- Configure IBC for paper Gateway startup, login automation, expected dialog handling, and scheduled restart behavior.
- Enable Gateway API socket access, restrict trusted IPs to localhost, and persist Gateway settings.
- Add a supervised process manager such as `systemd` for IBC/Gateway and the bot.
- Deploy the trading bot with environment-specific config pointing to `127.0.0.1:4002` for paper Gateway.
- Run readonly smoke tests first, then one tiny paper order smoke test from the Oracle VM.

Exit criteria:

- Rebooting the Oracle VM brings up IBC and Gateway without manual shell commands.
- Bot connects to IB Gateway on Oracle localhost after IBC reports Gateway ready.
- Account summary and positions load successfully.
- QQQ or configured paper symbol can be quoted.
- A tiny paper order can be submitted and filled from the Oracle VM.
- Unexpected authentication prompts alert the operator rather than silently failing.
- Discord or equivalent alerting reports broker connect/disconnect and order outcomes.

### Phase 2: Cloud Trading Bot Integration

Status: complete for the paper burn-in MVP as of 2026-07-27. See `phase-2-cloud-burn-in-runbook.md` for service configuration and validation history.

Current VM/runtime state:

- Oracle VM shape remains `VM.Standard.E2.1.Micro` with 4 GB swap; usable for controlled paper burn-in but still memory-tight.
- `ibc-gateway.service` runs IB Gateway paper mode on localhost port `4002`.
- `trading-bot-phase2.service` runs `python -m vibe.trading_bot.main run` from `/opt/strategy-lab`.
- Active ruleset is `orb_exp073_paper_burn_in`, based on EXP-073 parameters, with `max_shares: 1` for paper burn-in risk control.
- Execution uses `InteractiveBrokersExecutionEngine`; mock execution is no longer used when `BROKER__BROKER_TYPE=interactive_brokers`.
- Market data is configured with `DATA__PRIMARY_PROVIDER=interactive_brokers`, `BROKER__IB_MARKET_DATA_TYPE=1`, and a distinct IB data-provider client id derived from the execution client id.
- Market-hours validation has now been performed on the Oracle VM. The bot connected to IB Gateway, warmed cache, used Interactive Brokers as the primary real-time provider, generated and filled paper `QQQ` orders, persisted dashboard rows, and published the Stage 5 read model to Supabase.

Tasks:

- [x] Complete market-hours validation with IB realtime data polling: provider connection, snapshot quotes, realtime bar aggregation, and no Finnhub substitution while IB realtime is active.
- [ ] Continue longer paper burn-in and record service uptime, memory/swap pressure, Gateway reconnect behavior, order/fill outcomes, and final account reconciliation.
- Add startup checks that fail closed when Gateway is disconnected, account id mismatches, market data is stale, or Gateway API read-only state conflicts with broker mode.
- Add broker health and market data provider status into existing system status notifications.
- Add restart/reconciliation checks before allowing new orders after bot or Gateway restart.
- Migrate burn-in to the larger always-free VM shape once prepared, then repeat service and paper account validation.
- Add a paper/live guardrail that requires explicit config and operator approval for live mode.

Exit criteria:

- [x] During paper mode, the orchestrator can complete warmup only when IBC/Gateway and broker account checks pass.
- [x] During market hours, IB realtime market data feeds the active ORB strategy path and the bot does not depend on Finnhub WebSocket.
- Broker failures prevent new orders and produce actionable alerts.
- Restarting the bot or Gateway reconciles positions, open orders, and recent executions before new orders can be submitted.
- A multi-session paper burn-in completes with documented memory, connectivity, order, and reconciliation results.
- Existing mock/backtest paths remain unaffected.

### Phase 3: IBC Hardening

Status: in progress as of 2026-07-27. The current systemd/IBC operating model is stable enough to start Phase 4 design work, but Phase 3 should not be marked complete until the remaining runbooks, watchdogs, and recovery evidence are captured.

Tasks:

- [x] Tune IBC restart windows around IBKR maintenance and the bot's market schedule.
- [ ] Capture and rotate IBC, Gateway, and bot logs.
- [ ] Add a restart/reconciliation runbook for cases where Gateway restarts while positions or orders are open.
- [x] Keep manual remote desktop access as a fallback for 2FA and unexpected IBKR notices.
- [ ] Add a watchdog that alerts if Gateway is not logged in or the API socket is unavailable near market open.

Exit criteria:

- [x] Routine Gateway restart is handled automatically.
- [ ] Unexpected authentication prompts alert the operator rather than silently failing.
- [x] Bot startup waits for a confirmed Gateway API session.
- [ ] Operator can recover from an IBC/Gateway failure using the runbook without changing application code.

### Phase 4: Dockerized Deployment

Status: started as of 2026-07-27. See `phase-4-dockerized-deployment-runbook.md` and `deploy/ibkr-docker/` for the initial Compose scaffold. Keep production on the VM/systemd path until Phase 3 recovery operations and multi-session paper burn-in are proven.

Tasks:

- [x] Package IB Gateway, IBC, Xvfb, and optional VNC/noVNC into an `ib-gateway` service scaffold.
- [x] Package the bot separately so bot deploys do not rebuild Gateway.
- [x] Persist Gateway settings and logs through Docker volumes in the Compose design.
- [x] Add Docker health checks for Gateway socket availability and bot process health.
- [x] Keep secrets out of images and compose files by using ignored runtime inputs and example env files only.
- [ ] Populate a test host with ignored Gateway/IBC runtime inputs and real secret files.
- [ ] Build and start the shadow Docker stack without affecting production systemd services.
- [ ] Run read-only IB probe, tiny paper order smoke, Stage 5 Supabase validation, and restart persistence checks against the shadow stack.

Exit criteria:

- `docker compose up -d` can recover the paper stack after reboot.
- Health checks expose whether Gateway is running, logged in, and API-ready.
- Manual VNC/noVNC fallback remains available for 2FA and unexpected dialogs.

### Phase 5: Live Readiness Review

Tasks:

- Run paper trading in Oracle through several market sessions.
- Review execution metrics, disconnect incidents, duplicate order prevention, and manual interventions.
- Add account-level max position, max daily loss, max order quantity, and kill-switch controls if not already present.
- Perform a live-mode dry run that confirms configuration without submitting orders.

Exit criteria:

- Live trading requires an explicit config change and a final human approval step.
- Paper operations have demonstrated stable restart and recovery behavior.
- Risk controls are enforced outside the strategy signal itself.

---

## Configuration Plan

Proposed Oracle paper config values:

```bash
BROKER_TYPE=interactive_brokers
BROKER_MODE=paper
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=1
IB_ACCOUNT_ID=<paper-account-id>
IB_EXCHANGE=SMART
IB_CURRENCY=USD
IB_READONLY=false
```

Recommended guardrails:

- Use a distinct `IB_CLIENT_ID` for smoke tests, bot runtime, and any manual diagnostics.
- Keep `IB_HOST=127.0.0.1` unless there is a deliberate reason to expose Gateway across hosts.
- Require `BROKER_MODE=live` plus a separate explicit live-trading enable flag before live order submission.
- Default cloud boot behavior should be `readonly` until all Gateway/account checks pass.

---

## Security Considerations

- Do not commit IBKR credentials, Gateway settings containing credentials, or IBC config with secrets.
- Prefer SSH-tunneled remote desktop access over publicly exposed VNC.
- Restrict IB Gateway API clients to localhost.
- Use Oracle firewall rules to expose only required ports.
- Store secrets in environment files with restrictive permissions or a cloud secret store.
- Treat screenshots, logs, and diagnostics as potentially sensitive because account ids and balances can appear in them.
- Assume 2FA may still require manual action. Automation should alert and pause, not attempt to bypass account security.

---

## Monitoring And Alerts

Minimum broker health signals:

- Gateway process running.
- API socket accepting connections.
- `ib_insync` connected.
- Expected account id present.
- Market data quote received recently for the configured symbol universe.
- Order placement disabled/enabled state matches config.
- Last broker disconnect time.
- Last successful order/fill event.

Recommended Discord alerts:

- Gateway/API unavailable during pre-market warmup.
- Broker reconnect succeeded or failed.
- Account id mismatch.
- Order submitted, filled, cancelled, or rejected.
- Bot entered fail-closed mode due to broker health.
- Manual intervention required for Gateway authentication.

---

## Failure Modes And Responses

| Failure Mode | Expected Response |
| --- | --- |
| Gateway not running | Bot fails closed; alert operator; systemd/IBC attempts restart. |
| Gateway running but logged out | Bot fails closed; alert manual intervention or IBC recovery status. |
| 2FA prompt pending | Alert operator; keep bot from submitting orders. |
| Duplicate IB client id | Alert and exit; use configured unique client id per process. |
| Market data unavailable | Do not trade symbols requiring live quotes; alert during warmup. |
| Order status unknown after submit | Enter reconciliation mode; query open orders/positions before any further orders. |
| Bot restart with open positions | Load positions from broker before strategy resumes; do not assume local state is complete. |
| Oracle VM reboot | IBC/systemd restarts Gateway; bot waits for broker health before trading. |

---

## Open Questions

- Which Oracle VM shape will host Gateway reliably with enough memory headroom?
- Which fallback remote desktop option should be standardized for IBC inspection: VNC, noVNC, or another SSH-tunneled approach?
- Which IBC version and Gateway channel should be standardized?
- How often does the paper account require manual 2FA in practice?
- Should execution become a separate service before live trading, or after paper stability is proven?
- Should operational metrics remain SQLite/Supabase for MVP or move to PostgreSQL during Dockerization?

---

## Next Steps

1. Add an Oracle VM IBC runbook under this folder.
2. Add an IBC evaluation note with chosen version, config layout, restart schedule, and secret-handling approach.
3. Extend the existing IB paper smoke test checklist for Oracle-specific validation.
4. Add broker health requirements to the trading bot warmup phase.
5. Decide the first Docker Compose boundary after IBC-managed Oracle paper trading is stable.
