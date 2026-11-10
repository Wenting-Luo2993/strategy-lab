# IB Gateway Connection Troubleshooting

This is the living guide for IB Gateway connection incidents, missing IB market
data, and recovery verification. Update the incident register below when an issue
recurs; keep observed evidence separate from suspected causes and deployed fixes.

**Current open incident: 2026-11-10.** The operator reported another failed trading
session. Today's production logs have **not** been inspected: the SSH terminal
disconnected and returned to local PowerShell. Cause and current broker state are
unknown. Do not deploy the shared-client refactor as a presumed fix, or restart the
gateway before preserving the incident evidence.

## Production and safety

Follow the [deployment skill](../../../.claude/skills/deploy-trading-bot/SKILL.md)
and [operations runbook](../../../deploy/ibkr-docker/README.md).

| Component | Production location / responsibility |
|---|---|
| Checkout | `/opt/strategy-lab`, branch `main` |
| Stack | Compose project `strategy-lab`; not the old systemd installation |
| Gateway | `strategy-lab-ib-gateway`; IBC login and connectivity to IBKR |
| Bot | `strategy-lab-trading-bot`; execution, data polling, risk state and dashboard publisher |
| API route | Bot -> `ib-gateway:4004` socat relay -> gateway-local `4002`; no host-published IB port |
| Daily restart configuration | `AUTO_RESTART_TIME` and `TWS_COLD_RESTART` are both set to 04:00 UTC; inspect logs for the actual restart/login sequence |
| Trading session | `America/New_York`; warm-up at 09:25 ET, regular market at 09:30 ET |

Use the user's SSH terminal as `ubuntu`. If it shows a Windows PowerShell prompt,
stop and ask the user to reconnect; do not send Linux commands or search for keys.
The commands below are **remote Linux commands**, not local Windows commands.

`deploy.sh status` and `check` do not restart services and are safe during market
hours (they may refresh Git remote refs). Changing production requires approval
and the deployment harness. Weekday 09:25-16:05 ET changes require specific
incident approval before using `--allow-market-hours`; ordinary release approval
does not authorize that override.

Never read or print `secrets/`, environment dumps, credential files, or unfiltered
Docker configuration. Do not submit a diagnostic order, manually invoke full
production warm-up, clear execution journals, delete session equity state, or
reset a daily-loss halt. Warm-up can manage carryover positions and place orders.
Preserve working orders and protective stops while diagnosing.

## Start here: capture evidence before restarting

### 1. Confirm host, time, deployed code and liveness

```bash
cd /opt/strategy-lab
pwd
date -u '+UTC %Y-%m-%d %H:%M:%S'
TZ=America/New_York date '+ET %Y-%m-%d %H:%M:%S %Z'
git rev-parse --short HEAD
bash deploy/ibkr-docker/deploy.sh status
printf 'STATUS EXIT=%s\n' "$?"
bash deploy/ibkr-docker/deploy.sh check
printf 'PREFLIGHT EXIT=%s\n' "$?"
docker inspect --format 'name={{.Name}} image={{.Image}} started={{.State.StartedAt}} restarts={{.RestartCount}} state={{.State.Status}} oom={{.State.OOMKilled}}' \
  strategy-lab-ib-gateway strategy-lab-trading-bot
```

Record the last successful deployment and running image, not just checkout HEAD:
a scripts/docs-only pull can leave the bot image at an older code revision.
Preserve failed preflight output; do not bypass it. A recent `StartedAt`, nonzero
restart count, OOM flag, or memory/disk shortage warrants investigation before
assuming an IBKR login issue.

### 2. Read bounded, timestamped gateway AND bot logs

Start before the overnight restart and include the failed warm-up. For the open
2026-11-10 incident, `00:00 UTC` includes the previous evening's deployments and
the scheduled `04:00 UTC` restart. The November warm-up is `14:25 UTC`; during
daylight saving time it is `13:25 UTC`. Always derive ET times for the incident
date, not a hard-coded offset.

```bash
# Replace with the incident's UTC window when investigating another day.
SINCE=2026-11-10T00:00:00Z
UNTIL="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
for container in strategy-lab-ib-gateway strategy-lab-trading-bot; do
  printf '\n=== %s (%s to %s) ===\n' "$container" "$SINCE" "$UNTIL"
  docker logs --timestamps --since "$SINCE" --until "$UNTIL" "$container" 2>&1 \
    | grep -viE 'password|passwd|TWS_USERID|IbLoginId|user.?name|secret|token|api.?key|service.?key|authorization|webhook' \
    | sed -E 's/\b(DU|U)[A-Z]?[0-9]+\b/[account]/g; s#https?://[^[:space:]]+#[url]#g' \
    | grep -iE 'login|log-in|authentication|disclaimer|session|restart|shutdown|connect|disconnect|socket|1100|1101|1102|10197|10141|10089|326|warm.?up|equity|snapshot|halt|blocked|timed? out|timeout|farm|error|exception|traceback|polling|publisher|outbox|ORB' \
    | tail -n 200
  printf 'Pipeline exits (docker, redact-filter, sed, event-filter, tail): %s\n' "${PIPESTATUS[*]}"
done
```

This is a triage excerpt, not a complete log archive or guaranteed secret scrubber.
Do not share raw configuration dumps; redact any remaining sensitive content
before attaching evidence. `grep` exit 1 can mean no matches; a nonzero Docker
exit means collection failed, not that no incident occurred. If output reaches
the tail limit, narrow the time window around each transition. Do not infer the
absence of an event from filtered/truncated output or rotated logs.

Build a timeline with: gateway restart, login completion, first loss/re-login
event, legacy service activation if any, bot socket loss/reconnect, failed IB
requests, warm-up result, equity snapshot, and first/last fresh market-data bar.
Include the timestamp and source container for each observation.

### 3. Classify the failing layer

| Evidence | What it establishes | What it does NOT establish |
|---|---|---|
| Gateway container `healthy` | Its TCP probe to gateway-local `4002` succeeds | Authenticated IBKR session, responsive account/order APIs, or market-data entitlement |
| Relay `4004` listens | socat is listening | Gateway login or upstream IBKR service health |
| Bot `/api/health` is 200 | Bot/API process is alive | Execution connection, completed warm-up, fresh data, or safe entries |
| `/health/ready` is 503 | Readiness condition is not met | A gateway outage by itself; after-hours 503 is normal |
| Old `Connected to IB` or publisher-start marker | Startup succeeded at that timestamp | Connection or publishing still works now |
| Fresh server-time response | An API round trip completed | Fresh account synchronization or live quotes for every symbol |
| Fresh account, positions, open-order and execution reads | Those specific request paths work | Market data is live, or orders would execute correctly |
| Timestamped live quote and expected recent bars per symbol | Those market-data paths work for that symbol/window | Future overnight recovery or every other symbol's entitlement |
| Fresh local bars but stale dashboard | Failure may be downstream of ingestion | That the gateway is disconnected |

Do not restart solely because a dashboard has no bars. Check the market calendar,
ingestion timestamps, publisher/outbox failures and persistence separately. A
missing dashboard row is not evidence that no orders or fills occurred.

**When SSH is unavailable:** the current
[Supabase publisher](../../../vibe/trading_bot/publishing/remote_data_publisher.py)
and [dashboard adapter](../../../apps/operational-metrics-dashboard/src/data/supabaseAdapter.ts)
publish/read trading records, bars, annotations and numeric operational metrics,
not a Gateway/IBC log stream. Dashboard freshness can corroborate a symptom but
cannot recover the login/restart/error timeline. Restore SSH or obtain sanitized
container logs from the operator; do not retrieve service-role credentials or
infer a connection diagnosis from missing remote rows.

## Known failure modes and responses

### A. Legacy timer starts a second gateway / competing login

**Observed 2026-11-09:** `trading-bot-phase2-start.timer` remained enabled after
the Docker cutover. At `13:20:18 UTC`, it activated legacy `ibc-gateway` and
`xvfb-ibkr`, despite the service unit files themselves being disabled. Its start
service has `Requires=ibc-gateway.service`.

Inspect the legacy state shown by `deploy.sh status` and rejected by `check`.
The timer must be **inactive AND disabled**, and the old services must not run.
Disabled service files alone do not prevent dependency activation.

Once this condition is confirmed, ask for approval to disable/stop the timer and
stop the old units using the incident procedure in the deployment skill. Verify
their resulting state, then use an approved `deploy.sh gateway` action if the
Docker login still requires recovery. Never run both stacks.

**Persistent prevention:** [035eb3c](https://github.com/Wenting-Luo2993/strategy-lab/commit/035eb3c)
added timer handling to cutover, rollback and deployment preflight.

**Causal limit:** the November 9 `05:16:17 UTC` re-login event occurred more than
eight hours **before** the timer fired. The timer proves a competing local
gateway existed later; it does not explain that earlier event or every failure.

### B. Gateway is up but IBKR session needs re-login

Look for `Re-login is required`, incomplete login, authentication/2FA prompts, or
IB upstream connectivity loss. Preserve the surrounding sequence, including
whether IBC actually completed the requested login. A healthy TCP port does not
rule this out.

Check for other Gateway/TWS logins, the disabled legacy timer, and operator-held
sessions. The Compose setting `EXISTING_SESSION_DETECTED_ACTION=primary` is not
proof that another session cannot interrupt this one. Do not repeatedly restart
both processes or assume the daily restart and a later re-login have one cause.
If operator interaction is required, use the runbook's approved credential/VNC
procedure; never expose VNC/API ports publicly or enter secrets into chat.

An approved harness `gateway` action recreates **both gateway and bot** and is
more disruptive than `bot`. Capture evidence first, then verify fresh API and
data responses after recovery. The November 9 early re-login remains unexplained.

### C. Error 10197: competing live/paper market-data session

This is a market-data session conflict, not proof of duplicate API client IDs.
Multiple API clients connected to one gateway are not separate IBKR logins.
Identify which live/paper/TWS/Gateway sessions are active and verify the account's
market-data sharing permissions with the operator.

During the November 9 recovery checks, the first data probe emitted `10197` and
QQQ/AMZN history requests failed; a later retry returned complete sessions for all
five symbols. That demonstrates intermittent access, **not a resolved cause**.
A shared IB client cannot by itself fix an external login/data-session conflict.

Do not substitute delayed/frozen quotes for live execution data. Recheck live
quotes and bars separately for QQQ, AMZN, GOOGL, MSFT and TSLA after any correction.

### D. Data client recovers but execution client stays disconnected

**Reproduced before 4d7fee5:** warm-up could reconnect a data/diagnostic client
while the persistent execution client stayed disconnected. A subsequent equity
snapshot failed with `Not connected` and could persist an entry block.

[4d7fee5](https://github.com/Wenting-Luo2993/strategy-lab/commit/4d7fee5)
reconnects/reconciles the actual execution client before carryover/equity reads,
even when optional health checks are disabled. Failed connection recovery defers
those reads and retries warm-up; trading-only socket loss also triggers recovery.
It preserves existing session equity and daily-loss halts.

**Deployment evidence:** the November 9 evening release reported `Deploy OK at
4d7fee5`, followed by a disposable read-only reconnect probe. This does not prove
the next morning's real warm-up succeeded. Verify the currently running image and
the failed session's logs before declaring a regression or attributing it to a
different cause.

The shared-client refactor is still **unreleased as of 2026-11-10 investigation**.
It addresses lifecycle ownership and overlapping quote requests, not IBKR login
availability. Do not treat its local regression results as production evidence.

### E. API client ID collision versus broker-side connectivity loss

| Signal | Interpretation / next evidence |
|---|---|
| `326` / client ID already in use | Two API sockets attempted the same ID. Identify the owner; do not reuse production ID `201` for diagnostics or confuse this with `10197`. |
| `502`, refused connection, connect timeout | Check the correct network namespace/port, gateway login, API access and resources. Timeout alone does not identify which layer failed. |
| `504` / `Not connected` | Request was attempted on a disconnected client. Verify the execution owner's recovery, not just another client's connection. |
| `1100` | Gateway lost its upstream IBKR connection; the local socket can still exist. Investigate reset, network and session evidence. |
| `1101` | Upstream connection restored with market-data requests lost; verify re-subscription and fresh data. Do not assume a socket reconnect handler alone covers it. |
| `1102` | Upstream connection restored with data subscriptions retained; verify actual fresh responses rather than blindly opening another login. |
| `2103` / `2105` | Market/historical data farm interruption; correlate duration and affected requests. |
| `2104`, `2106`, `2158` | Farm-connection status messages, not failures merely because they use the IB error callback. |
| `2107` / `2108` | Farm is idle/on-demand; not by itself a reason to restart. |

These are diagnostic branches, not claims that all codes appeared in our incidents.
See the [IBKR message-code reference](https://interactivebrokers.github.io/tws-api/message_codes.html).

### F. Live-data entitlement or paper disclaimer

`10089` occurred during the [July 7 local burn-in](phase-0-local-burn-in-runbook.md#readonly-smoke-test):
account access and delayed data worked while live data failed. Live quotes worked
after subscription/settings propagation and retry. Check API-specific entitlement
and paper-session sharing, not only whether a GUI displays a price. Delayed data
can be a clearly labelled read-only connectivity control, never proof of live
execution readiness.

For `10141` / paper disclaimer acceptance, the broker raises
`IBOperatorActionRequired`. The operator must complete the required Gateway prompt;
blind reconnect loops are not a solution. This code path is supported by the
adapter; it is not established as the cause of the November 10 incident.

### G. Quote cancellation, pacing or stale samples

Execution benchmarks, data polling and optional resting-entry gap-fill quotes
must not cancel each other's same-contract subscriptions. The pending shared-client
implementation coalesces overlapping calls, bounds each caller's timeout and rejects
quotes from a previous socket generation.

IB permits at most one new tick-by-tick request per instrument within
[15 seconds](https://interactivebrokers.github.io/tws-api/tick_data.html).
Qualification, pacing and tick arrival consume the quote deadline. A quote timeout
can therefore occur with a connected gateway; inspect request timing, data mode
and subscriptions before restarting. No fresh tick after hours is not equivalent
to a failed login. Do not weaken timestamp/delayed-data checks or hide an unknown
gap-fill classification to make a smoke check pass.

## Recovery verification: what is enough to close an incident?

Capture a timestamped post-recovery result for each relevant layer. Report which
checks passed, failed or were not observed; do not collapse everything into "healthy."

1. Harness success marker and exit 0 for an approved change; correct running
   version, no active legacy gateway or enabled legacy timer, sufficient resources.
2. New login/reconnect evidence **after** the failure, followed by bounded fresh
   server-time, account, positions, open-order and execution requests. Old startup
   markers and a socket flag alone are insufficient.
3. Live, exchange-timestamped data and expected recent/completed bars for every
   enabled symbol during a suitable market window. Historical success does not
   prove live entitlement; after-hours missing ticks need an explicit caveat.
4. Real warm-up success, session equity captured or safely reused, and no new
   unexpected entry block. Do not remove a valid risk halt to achieve readiness.
5. Local data persistence and dashboard publishing advance with matching times.
   Audit durable execution records if determining whether any trades occurred.
6. Observe the actual next scheduled gateway restart and next market warm-up
   before claiming overnight recovery is proven. A deliberate disconnect/reconnect
   of a separate diagnostic client covers only that probe's lifecycle.

Any active API probe must use a distinct unused client ID, bounded requests, no
order submission/cancellation and a temporary execution-journal directory. Do not
assume `execution_db_path=":memory:"` avoids disk writes: the execution store uses
a path-based SQLite URI. Disconnect the probe and clean up its temporary directory.
Keep account identifiers/balances out of shared evidence. Prefer an existing
approved read-only diagnostic; do not run the full production warm-up as a probe.

## Incident register

| Incident / status | Evidence and action | Remaining uncertainty / closure requirement |
|---|---|---|
| 2026-07-07 live-data entitlement: historical recovery observed | `10089` while account/delayed data worked; live data later succeeded in local burn-in. | Exact propagation mechanism was not established; not evidence of present entitlement for every symbol. |
| 2026-11-09 early re-login: **unresolved cause** | Scheduled restart at 04:00 UTC; login completed 04:01:50; re-login required 05:16:17. | This predates the legacy timer. Need the surrounding Gateway/IBC evidence; do not backfill a causal explanation. |
| 2026-11-09 legacy timer: remediated, recurrence guard added | Old gateway started at 13:20:18 UTC. Timer disabled, old units stopped, approved harness gateway recovery completed. Guard fix `035eb3c`. | Reconfirm live disabled/inactive state on each recurrence. Does not close the earlier re-login issue. |
| 2026-11-09 execution reconnect bug: fix deployed, next-session outcome unverified | Reproduction plus `4d7fee5`; harness success and disposable read-only reconnect smoke. | Inspect actual failed November 10 warm-up; local reproduction/smoke is not next-session proof. |
| 2026-11-09 recovery-probe `10197`: **unresolved cause** | First QQQ/AMZN history requests failed; retry returned all five complete 390-bar RTH sessions and quotes. | Identify competing session/permissions; later success alone is not a permanent fix. |
| 2026-11-10 repeated session failure: **open, log access blocked** | Operator reported failure. SSH disconnected; current-day production logs not read. Shared-client release held. | Reconnect as `ubuntu` in `/opt/strategy-lab`, collect overnight + warm-up logs, classify layer, then choose remediation. No current root cause or no-trades claim is established. |

For each follow-up, append the UTC/ET timeline, deployed code/image, affected
symbols/operations, sanitized evidence, confirmed cause versus hypotheses, approved
actions, post-change checks and remaining observation window. Update the open row
only when fresh evidence supports doing so; retain the earlier incident history.
