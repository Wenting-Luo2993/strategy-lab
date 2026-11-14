<!-- taskboard
{"id":"gateway-evidence","title":"Gateway incidents and recovery evidence","projects":["live-reliability","execution-quality","deployment-cicd"],"status":"current","kind":"evidence","reviewed":"2026-11-14"}
-->
# IB Gateway Connection Troubleshooting

This is the living guide for IB Gateway connection incidents, missing IB market
data, and recovery verification. Update the incident register below when an issue
recurs; keep observed evidence separate from suspected causes and deployed fixes.

For proposed testing gates and operational assurance outcomes, see the
[reliability testing product specification](reliability-testing-product-spec.md).

**Current incident: 2026-11-13 intermittent live-data timeouts and `10197`.**
Read-only inspection found missing quote snapshots followed by resumed bars,
overlapping account-summary requests, and intermittent VM scheduling/swap pressure.
Local regressions reproduce a packet-buffer race and account-summary overlap;
fixes and passive monitoring were **deployed at `c3b7a7c`** in the explicitly
approved 20:55 UTC Gateway recovery. Fresh quotes/account reads recovered, but
QQQ polling timeouts and a failed TSLA EOD exit remain. A later
`10197` establishes a separate broker-reported market-data session conflict whose
source remains unknown. See section K. Earlier exposure snapshots in I/J are
historical, not current position or protection verification.

**Previous incident: 2026-11-10, investigated 2026-11-11 UTC.** Production logs
confirm Gateway's upstream connection failed during the IBKR maintenance window
and remained broken through market hours. The legacy timer stayed disabled, and
the bot's execution reconnect fix retried and blocked trading as designed.
The server-side reason it did not recover is not established. The approved
post-maintenance schedule and shared-client release `faf7e61` deployed on November
11 UTC; the next scheduled restart and warm-up subsequently succeeded, as recorded
in section I, but did not prevent the new intraday failure.
See the incident timeline below; the shared-client refactor alone is not a fix
for an unauthenticated/unresponsive Gateway.

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
| Restart configuration | Deployed and verified in IBC logs: daily `AUTO_RESTART_TIME=07:15 AM`; Sunday-only `TWS_COLD_RESTART=07:15`, both UTC. The incident ran the old 04:00 UTC schedule. |
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
    | grep -E ' IBC:| - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - ' \
    | grep -viE 'password|passwd|key|token|secret|login.?id|user.?name|credential|authorization|webhook' \
    | sed -E 's/\b(DU|U)[A-Z]?[0-9]+\b/[account]/g; s#https?://[^[:space:]]+#[url]#g' \
    | grep -iE 'login|log-in|authentication|disclaimer|session|restart|shutdown|connect|disconnect|socket|1100|1101|1102|10197|10141|10089|326|warm.?up|equity|snapshot|halt|blocked|timed? out|timeout|farm|error|exception|traceback|polling|publisher|outbox|ORB' \
    | tail -n 200
  printf 'Pipeline exits (docker, runtime-filter, redact-filter, sed, event-filter, tail): %s\n' "${PIPESTATUS[*]}"
done
```

This keeps structured runtime messages, excluding raw Java command lines and
startup configuration dumps. It is not a complete log archive or guaranteed secret scrubber.
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

Include the **associated live username**, not just another login with the paper
username. The [official IBKR paper-data FAQ](https://www.interactivebrokers.com/support/faq/content/market-data-considerations-paper-trading-1136176599)
documents that a paper session sharing a live user's subscriptions receives no
data when both sessions run on different devices. Multiple sockets to the same
Gateway are a different case. This mechanism is not proof of the source of a
particular `10197`.

Ask about already-running desktop/mobile/trading-enabled browser sessions first.
Do not open another brokerage session just to inspect settings during an incident.
Review the linked sharing user via an existing appropriate session or an approved
maintenance window: [Client Portal paper-account settings](https://www.ibkrguides.com/clientportal/papertradingaccount.htm).
Do not disable sharing as a supposed live-data fix: paper may then receive only
delayed data. If session inventory does not explain the error, preserve its UTC
time/request ID and ask IBKR support to investigate session/entitlement state.
Do not invent a historical-login audit screen or assume support has established
a cause before it responds.

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
4d7fee5`, followed by a disposable read-only reconnect probe. November 10 logs
subsequently confirmed that the owner retried, failed on upstream account/order
timeouts, and deferred carryover/equity reads as intended. It did not make an
unresponsive Gateway healthy. Always inspect the failed session before declaring
that a connection retry itself is broken.

The shared-client refactor was **deployed in the approved `faf7e61` rollout** after
the initial 2026-11-11 investigation. It addresses lifecycle ownership and
overlapping quote requests, not IBKR login availability. See section H for the
bounded deployed-code probe results; section I records the subsequent scheduled
restart/warm-up success and intraday failure.

### E. API client ID collision versus broker-side connectivity loss

| Signal | Interpretation / next evidence |
|---|---|
| `326` / client ID already in use | Two API sockets attempted the same ID. Identify the owner; do not reuse production ID `201` for diagnostics or confuse this with `10197`. |
| `502`, refused connection, connect timeout | Check the correct network namespace/port, gateway login, API access and resources. Timeout alone does not identify which layer failed. |
| `504` / `Not connected` | Request was attempted on a disconnected client. Verify the execution owner's recovery, not just another client's connection. |
| `1100` | Gateway lost its upstream IBKR connection; the local socket can still exist. Investigate reset, network and session evidence. |
| `1101` | Upstream connection restored with market-data requests lost; verify re-subscription and fresh data. Do not assume a socket reconnect handler alone covers it. |
| `1102` | Upstream connection restored with data subscriptions retained; verify actual fresh responses rather than blindly opening another login. |
| `2110` | Gateway reports its connection to the upstream server is broken. A local `API connection ready` marker does not contradict this; verify fresh account/order responses. |
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
must not cancel each other's same-contract subscriptions. The deployed shared-client
implementation coalesces overlapping calls, bounds each caller's timeout and rejects
quotes from a previous socket generation.

IB permits at most one new tick-by-tick request per instrument within
[15 seconds](https://interactivebrokers.github.io/tws-api/tick_data.html).
Qualification, pacing and tick arrival consume the quote deadline. A quote timeout
can therefore occur with a connected gateway; inspect request timing, data mode
and subscriptions before restarting. No fresh tick after hours is not equivalent
to a failed login. Do not weaken timestamp/delayed-data checks or hide an unknown
gap-fill classification to make a smoke check pass.

### H. Overnight upstream stall and restart timing (November 10)

**Confirmed failure layer:** Gateway's upstream session, not a local TCP failure
or the legacy timer. API clients could connect to Gateway, but Gateway reported
`2110` and `2103` and did not complete account/order synchronization. The bot
correctly kept retrying and blocked trading. The exact server-side reason the
session failed to recover is not available in the collected logs.

| UTC timestamp | Observed evidence |
|---|---|
| Nov 10 04:00:04 | Bot logged `Peer closed connection` during the configured Gateway auto-restart. |
| Nov 10 04:01:59 | IBC logged successful login after the restart. |
| Nov 10 05:18:13 | Gateway displayed `Attempt 2: server error, will retry in seconds...` (00:18 EST). |
| Nov 10 14:25:09 | Warm-up data client reached `API connection ready`, then received `2110` (upstream broken) and `2103` (market-data farm broken). Account/position/order requests timed out. |
| Nov 10 14:31:17-18 | Execution health failed after its retries; carryover/equity reads were skipped and warm-up was scheduled to retry. |
| Nov 10 21:03:22 | The same request-timeout/recovery failure was still blocking the final trading-cycle attempts. |
| Nov 11 04:00:31 / 04:01:58 | Gateway container restarted, then IBC completed a fresh login. Bot container had not restarted. |
| Nov 11 05:13 | Host and bot deployment were still `4d7fee5`; legacy timer/services inactive and disabled. No OOM flag; ample free disk/memory. Both containers were healthy despite the prior day's failure. |
| Nov 11 05:16-05:18 | Disposable read-only requests returned server time, positions, open orders, executions and a fresh account summary. Four symbols returned complete Nov 10 RTH history; QQQ returned no bars under the initial 12-second deadline, with code `162` observed. |
| Nov 11 05:19:03-05:20:18 | Gateway showed a new server-connection transition during maintenance, then finished starting. The QQQ retry received `1102` (upstream restored, data maintained) and all 390 expected Nov 10 minute bars. |

An aggregate scan of the retained bot logs for November 10 14:00-22:00 UTC
found **56 failed execution-health cycles**, 56 deferred carryover/equity reads
and 336 `2110` warnings, with failures in every market-hour bucket. It found no
`IB execution client healthy` or `Connected to IB at` success markers in that
window. These are log observations, not an execution-journal audit.

No gateway restart, deployment, order action or production risk-state edit was
performed during the initial investigation. The November 11 05:20 recovery happened
without operator intervention; the separately approved deployment below followed
at 05:31 UTC. All initial diagnostic clients were disconnected. The first
diagnostic script also mistakenly called a synchronous account-summary accessor
inside an async loop; its `RuntimeError` was a **probe bug**, not an IB failure.
The corrected async probe completed a fresh account-summary request successfully.

**Why change the schedule:** IBKR's
[published North America reset window](https://brokerage.ibkr.com/en/software/systemStatus.php)
is 00:15-01:45 ET Sunday-Friday, extending to 02:00 ET Saturday. Both the November 9
05:16 UTC re-login and November 10 05:18 UTC server error fall inside that window.
This correlation identifies a recovery risk; it does not prove why IBKR returned
the error or why recovery stalled on one night but succeeded on the next.
The old 04:00 UTC auto-restart ran **before** maintenance, leaving no scheduled
post-maintenance restart before trading.

There was a separate confirmed configuration error: `TWS_COLD_RESTART` maps to
IBC's **Sunday-only** `ColdRestartTime`, not a daily cold restart. IBC logged its
next cold restart as November 15 at 04:00 UTC. That is before the Sunday 01:00 ET
token invalidation described in
[IBKR's auto-restart guidance](https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm)
and the [IBC configuration reference](https://github.com/IbcAlpha/IBC/blob/master/resources/config.ini).
Correcting this is preventative; it is not established as Tuesday's cause.

**Deployed mitigation:** set `AUTO_RESTART_TIME=07:15 AM` and
`TWS_COLD_RESTART=07:15` with `TZ` / `TIME_ZONE` remaining `Etc/UTC`.
07:15 UTC is 02:15 EST / 03:15 EDT: after the published maintenance windows and
Sunday token reset, before market warm-up. Guardrail tests exercise winter/summer
and Saturday/Sunday dates. The daily restart remains an IBC auto-restart; this
does not add a new cron job, watchdog or daily full-authentication requirement.

**Rollout status:** user approved the combined schedule/shared-client release;
`faf7e61` landed on `main` and
[Deploy Guardrails passed](https://github.com/Wenting-Luo2993/strategy-lab/actions/runs/42966276695).
The final shared-client, warm-up and deployment regression selection passed
62 tests. Outside market hours, preflight passed and
`bash deploy/ibkr-docker/deploy.sh gateway --yes` returned exit 0 with
`Gateway + bot OK at faf7e61`.

| UTC timestamp, Nov 11 | Post-deployment evidence |
|---|---|
| 05:31:51 | Gateway recreated; pinned image remains `10.50.1f`. |
| 05:31:56 | IBC scheduled Sunday cold restart for **Nov 15 07:15**. |
| 05:33:28 | IBC login completed. |
| 05:33:31-32 | Read-Only API remained false; IBC changed auto-restart from **04:00 AM to 07:15 AM**. |
| 05:33:51 | Bot recreated with the complete reviewed shared-client implementation. |
| 05:36:09 | Both containers healthy; `/api/health` 200, `/health/ready` 503 (normal after hours). Harness confirmed IB connection, trading loop and publisher startup, with no ERROR/Traceback lines. |
| 05:37:34 | Disposable client `9277`, using the deployed broker/factory code, passed shared-object identity, borrowed-provider disconnect isolation, concurrent owner recovery after diagnostic socket loss, and fresh server-time/positions/open-orders/executions/account-summary requests. |

The first post-deploy diagnostic used a shortened 10-second connect timeout and
one attempt: sharing passed, but immediate socket recovery raised
`IBConnectionFailed`. Its underlying cause was not captured. Repeating with the
normal 20-second timeout, three attempts and two-second retry delay passed both
concurrent recovery calls; only informational farm codes `2104`, `2106`, `2158`
were observed during that recovery. This is a bounded recovery observation, not
proof that the first failure was caused by the shorter settings.
Both probes used a disposable client and temporary execution journal, submitted
or cancelled no orders, disconnected on exit and removed their temporary
directories. They did not disconnect the production client's socket or invoke
production warm-up.

At this rollout checkpoint, the next daily restart was November 11
**07:15 UTC / 02:15 EST** and warm-up **14:25 UTC / 09:25 EST**.
Both subsequently succeeded; see section I for those observations and the later
intraday failure. The Sunday-only cold restart remains unobserved.

Successful historical queries are not proof of fresh market-hours ticks, a
completed future warm-up, or the absence of trades during the failed session.
The server-side cause of the stalled recovery, next-cycle reliability, and the
previous `10197` conflict remain distinct follow-ups.

### I. November 11: morning recovery succeeded, intraday upstream failure recurred

Read-only inspection began at 21:01 UTC / 16:01 EST. Production remained at
`faf7e61`; remote main was two documentation commits ahead. Both containers were
healthy, had no Docker restarts or OOM flags, and legacy services/timer remained
disabled and nonrunning. Harness preflight passed. No restart, deployment, order
submission/cancellation or risk-state change was performed by the investigator.

| UTC timestamp | EST | Evidence |
|---|---|---|
| 07:15:01 | 02:15:01 | IBC displayed scheduled restart in progress. This was an application restart, not a Docker container restart. |
| 07:16:49 | 02:16:49 | Login completed; auto-restart remained set to 07:15 AM. |
| 14:25:15-32 | 09:25:15-32 | Execution client 201 reconnected; execution health passed with zero positions and a QQQ quote. Warm-up completed. |
| Morning session | Morning session | Resting entries were submitted for all five symbols; broker fill logs corroborated trading activity. Dashboard activity was not merely evidence of process liveness. |
| 16:26:01 onward | 11:26:01 onward | Account-risk evaluation exceptions began. The retained-log scan through 20:59 UTC counted 91 such errors; the messages claimed entries were blocked, but this inspection did not audit enforcement. |
| 17:44:12, 18:17:44, 18:47:41, 19:00:57 | 12:44:12, 13:17:44, 13:47:41, 14:00:57 | IB reported `10197`: no market data during competing live session. The source of that competition is not established; disabled local legacy services do not exclude an external session. |
| 19:04:10-23 | 14:04:10-23 | Three `1100` upstream-connectivity-lost errors. |
| 19:04:30 | 14:04:30 | IBC displayed `Attempt 3: server error, will retry in seconds...`. |
| 20:55:16-47 | 15:55:16-47 | AMZN, MSFT and TSLA time-exit attempts failed because protective-stop cancellation did not reach a terminal state. |
| 20:57:12 | 15:57:12 | AMZN cancellation retry received `10148`, already `PendingCancel`. The local wrapper subsequently displayed `Cancelled`; this is not independently verified broker cancellation or a closing fill. |
| 20:58:27-20:59:27 | 15:58:27-15:59:27 | Live timestamped-data requests timed out for all five symbols. |
| 21:01 onward | 16:01 onward | API health/readiness both returned 200 despite these failures. A temporary client 9278 failed connection synchronization with `TimeoutError` and codes `2110`, `2103`, `2105`, `2157`; it disconnected in `finally`. Current positions and working orders could not be established. |

The final retained-log summary ended at 20:59:27 UTC. It contained seven distinct
order IDs with `Filled` status: AMZN one, QQQ two, GOOGL two, MSFT one and TSLA one.
These are order-log observations, not seven round-trip trades or an authoritative
post-close position snapshot. No cooldown-complete, notification-sent or upstream
`1101`/`1102` restoration marker was found in the scanned window.

Other retained evidence included 95 dashboard account/position persistence failures,
account P&L timeouts, P&L cancellation errors (`10185`), and ten completed-bar handler
errors (`NoneType.__format__`). Repeated data-gap warnings compared the previous
afternoon with today's open; their count is not a count of intraday outages.

**Safety conclusion:** morning recovery is now observed, but full-session
reliability is not. Do not infer that AMZN/MSFT/TSLA are flat, or that protective
orders are active, from local cached state or dashboard rows. The next priority is
authoritative broker position/order reconciliation after approved recovery or
operator inspection. Do not automatically flatten, cancel protection, reset risk
state or restart on this evidence alone.

In deployed `faf7e61`, the risk-evaluation exception handler logs the failure but does not itself send a
Discord alert. A lack of alerts is therefore not a health guarantee. The exact
source of the competing session, reason for upstream recovery failure, and final
position/order state remain unresolved.

#### Source follow-up (deployed after approved recovery)

Focused source fixes now reject empty aggregation placeholders, block readiness
and relevant new entries on known dependency failures, and attempt deduplicated
dependency/uncertain-exit alerts. Missing webhook configuration and unsuccessful
delivery are logged; delivery is not guaranteed or durably retried.

Cancellation now waits on an existing PendingCancel instead of resending it.
Real ib_insync error-event regressions confirm that `10147`/`10148` can set
`Cancelled` locally without broker acknowledgement; this state is treated as
unresolved, not permission to submit a market close. A real later confirmation
can resolve the wait. Otherwise, reconcile with IBKR rather than assuming
cancellation or protection.

Upstream restoration messages alone cannot release entries: fresh account
verification or the complete warm-up health path must also succeed. Existing
risk halts, equity snapshots and exit monitoring are preserved. These changes
do not implement freshness expiry, authoritative position reconciliation,
automatic Gateway recovery or the proposed CI pipeline. They do not establish
why IBKR failed. The approved rollout and subsequent exposure snapshot are recorded
below; see the [consolidated product spec](reliability-testing-product-spec.md).

#### Approved evening rollout and broker reconciliation

The user approved bot-only deployment, then separately approved Gateway + bot
recovery after bot-only verification failed. All changes used `deploy.sh` from
main, outside market hours; no investigator order submissions/cancellations or
risk-state resets were performed.

| UTC timestamp | EST | Evidence |
|---|---|---|
| November 11 23:39 | 18:39 | Preflight passed: clean main, legacy services/timer inactive and disabled, no foreign containers, 15 GB free. |
| November 11 23:49:02 | 18:49:02 | Bot-only deployment of `d31d7a4` recorded `verify-failed`, exit 1. Startup exhausted three IB connection attempts; `reqExecutionsAsync()` timed out. API health failed and startup markers were absent. |
| November 11 23:51:40 | 18:51:40 | Bot unhealthy with one Docker restart; Gateway unchanged since 05:31 UTC, TCP health still healthy. The filtered Gateway window retained the 19:04 server-error dialog and no later matching login-success marker. |
| November 12 00:04:18 | November 11 19:04:18 | Approved Gateway recreation started. |
| November 12 00:06:48-53 | November 11 19:06:48-53 | IBC login completed, daily restart remained 07:15 UTC and Read-Only API remained false. Sunday cold restart remained November 15 07:15 UTC. |
| November 12 00:07:08 | November 11 19:07:08 | Bot recreated against the recovered Gateway. |
| November 12 00:08:20 | November 11 19:08:20 | Harness printed `Gateway + bot OK at d31d7a4`, exit 0. Both containers healthy; IB connection, trading loop and publisher markers present; no ERROR/Traceback lines; API health 200. After-hours readiness subsequently returned expected 503. |
| November 12 00:08:51 | November 11 19:08:51 | Disposable read-only client 9279 obtained fresh server time, positions and all-open-orders responses. Positions: MSFT -3, AMZN -7, TSLA +4 shares. All-open-orders response was empty. |
| November 12 00:10:22 | November 11 19:10:22 | Corrected asynchronous account-summary probe returned 96 fresh rows including NetLiquidation; no account identifier or balance was printed. |

Two diagnostic-reporting mistakes were corrected without changing production:
`reqAccountSummaryAsync()` returns no row list, so testing its return value
incorrectly printed `account_summary_received: false`; a follow-up used a
synchronous accessor inside an async loop and raised an event-loop error.
The final probe awaited the async accessor after the fresh request and succeeded.
Neither mistake invalidates the separate position/open-order responses above.

**Exposure remains open:** the account was not flat after recovery and the fresh
all-open-orders response showed no working orders. Do not claim active protective
stops or successful Wednesday EOD flattening. Operator action/reconciliation is
required; deploying the fix did not close these positions. The execution request
returned zero rows after UTC midnight; that is not a complete Wednesday fill audit.
Diagnostic clients disconnected in `finally` and did not use a production journal.
This recovery proves current startup/connectivity, not the next full trading day
or the cause of the upstream/session conflict. Next daily restart is November 12
07:15 UTC; next warm-up is November 12 09:25 EST.

### J. November 12: intraday failure recurred after successful morning recovery

Read-only inspection began November 13 02:58 UTC / November 12 21:58 EST.
Production was still `d31d7a4`; main was one documentation commit ahead.
Both containers reported healthy, API liveness was 200 and after-hours readiness
503. Legacy services/timer remained disabled/nonrunning; preflight passed.
The user reported no other known session using the same IBKR username. This is
useful context, not proof that competing authentication/entitlement is excluded.

The retained-log window was November 12 05:00 UTC through November 13 03:00 UTC:

| UTC timestamp | EST | Evidence |
|---|---|---|
| 07:16:33 | 02:16:33 | IBC login completed after the scheduled restart; daily restart remained 07:15 UTC. |
| 14:25-14:28 | 09:25-09:28 | Warm-up attempted to flatten Wednesday's AMZN/MSFT/TSLA carryovers. It logged close-order fill timeouts before the market opened, then warm-up complete. |
| 14:30:07-11 | 09:30:07-11 | Broker fill logs show those three carryover market orders filled at the open. Earlier timeout messages did not mean the orders had ceased to exist. |
| 14:30-15:00 | 09:30-10:00 | Eight Filled log records across carryover closes and new entries; these are not eight round-trip trades. |
| 16:17:14 onward | 11:17:14 onward | Live-data timeouts began; 288 matching data-failure lines in the window. |
| 17:27:52 onward | 12:27:52 onward | Account-risk evaluation timeouts began; 77 such errors. Exception types are now visible. |
| Around 19:22 | Around 14:22 | Intraday `1100` upstream loss; six `10197` competing-session errors occurred in the window. Seven total `1100` events include the earlier overnight episode and must not all be attributed to this intraday failure. |
| 19:23:14 | 14:23:14 | IBC displayed `Attempt 3: server error, will retry in 10 seconds...`. |
| 20:55-21:00 | 15:55-16:00 | Protective-stop cancellations timed out for all five symbols; exit handling remained fail-closed rather than submitting an unconfirmed close. |
| 21:06:36 | 16:06:36 | Cooldown reported five locally tracked positions still open. Its suggestion to restart to clear stale state is not a safe reconciliation instruction: restarting does not flatten broker exposure. |
| November 13 03:25:15 | November 12 22:25:15 | Fresh read-only client 9281 failed synchronization with TimeoutError and codes 2103, 2105, 2110, 2151, 2157. Current exposure could not yet be verified. |

There were 86 logged `SYSTEM_ERROR` notification sends, unlike the previous
day's retained-log silence. This proves send-path activity, not that every
message was seen by the operator. Intermittent failure/recovery can produce many
separate alert episodes despite per-episode deduplication; grouping and delivery
assurance remain requirements. New diagnostics do not prevent an upstream outage.

#### Approved recovery and authoritative snapshot

After evidence capture, the user explicitly approved `deploy.sh gateway --yes`.
The harness pulled documentation-only `a1b5cab`, recreated Gateway and bot, and
recorded `action=gateway result=ok sha=a1b5cab` at November 13 03:38:16 UTC
(November 12 22:38:16 EST). It printed `Gateway + bot OK`, exit 0; both
containers were healthy, IB/loop/publisher markers present, API liveness 200
and after-hours readiness 503. No ERROR/Traceback lines were reported.

At 03:39:49 UTC a fresh read-only client 9282 obtained server time, positions,
all open orders and 96 account-summary rows including NetLiquidation:

| Symbol | Broker position (shares) |
|---|---:|
| MSFT | +3 |
| TSLA | +4 |
| GOOGL | +6 |
| QQQ | +2 |

**No open orders were returned.** AMZN was absent from the position response;
the reason it differs from cooldown's local five-position report has not been
established by a fill audit. The four remaining positions need operator
reconciliation/action; no active protection is demonstrated. Diagnostic clients
disconnected in `finally`; no investigator orders/cancellations or risk reset
were performed.

The recurring upstream stall and origin of `10197` remain unresolved. The
disabled local timer is not an established explanation. Further investigation
should correlate IBKR authentication/session history and Gateway evidence around
14:22 EST, and distinguish market-data permission/session conflicts from account
connectivity loss. Another successful restart is recovery, not root-cause closure.
Next scheduled restart: November 13 07:15 UTC; next warm-up: November 13 09:25 EST.

### K. November 13: intermittent snapshot failures, client races and later `10197`

Inspection used the operator-authorized SSH connection and read-only harness,
filtered logs, container state/resources and host `vmstat`. No diagnostic broker
client, orders, cancellations, risk resets, restart or deployment were performed.
The host checkout remained `a1b5cab`; `status` and `check` passed at approximately
16:46 UTC / 11:46 EST. Both containers had zero Docker restarts and no OOM flag;
the scheduled Gateway login completed at 07:16:46 UTC. Legacy units/timer remained
disabled/nonrunning. The running bot still has the earlier `d31d7a4` safeguards;
later main commits through `1dd360d` do not contain a bot fix for this incident.

| UTC timestamp | EST | Evidence |
|---|---|---|
| 14:30:25 | 09:30:25 | Three `322` errors: maximum account-summary requests exceeded. QQQ snapshot timed out. |
| 15:55:46 | 10:55:46 | AMZN timestamped-live-quote request timed out; a no-live-bar alert followed. Later completed bars show data resumed. |
| 16:34:42-49 | 11:34:42-49 | GOOGL quote timed out and alert send was logged; subsequent completed bars resumed. |
| About 16:46-48 | About 11:46-48 | Harness initially showed readiness 200. One direct request timed out; a later request returned 503. QQQ/AMZN snapshots timed out at 16:47:01/20. |
| 16:47-48 | 11:47-48 | Gateway CPU snapshot about 204%; host load average 7.23. Two one-second `vmstat` interval rows showed 32-38% steal, one 39% I/O wait, with active swap-in/out. These are short samples, not proof of a sustained bottleneck or the cause of every timeout. |
| 16:50:13-15 | 11:50:13-15 | All five symbols emitted completed 11:45 bars; QQQ/AMZN each contained one sampled quote. Recovery was intermittent, not a complete outage throughout the window. |
| 16:50:30 | 11:50:30 | Another QQQ live-quote timeout. |
| 16:50:38.943 | 11:50:38.943 | `10197`, request 2277: `No market data during competing live session`. No corresponding IBC transition was retained by the filtered 16:49-16:53 window. |
| About 16:52 | About 11:52 | Six further one-second interval samples mostly returned to 0-2% steal, with 0-19% I/O wait and intermittent swap I/O. Ignore `vmstat`'s initial since-boot row when comparing intervals. |
| About 17:05-06 | About 12:05-06 | Process-name-only inventory found one Java process on the host, belonging to the expected Gateway container, and only the two expected running containers. Wrapper counts since 14:15 had risen to two `10197` and eight `10185`; `322` remained three. |

Through the earlier 16:48 inspection, the retained wrapper-error counts since
14:15 UTC were three `322` and seven `10185` (P&L cancellation not subscribed),
not `1100`/`10197`. The later `10197` changes that classification; do not describe
the whole day as free of session-conflict errors. P&L timeout/cancellation symptoms
remain unresolved. The operator explicitly confirmed: **no IBKR session outside
the VM at any point in time**. Do not attribute `10197` to an outside login contrary
to that information. The process snapshot also found no current duplicate local
Gateway, but cannot reconstruct prior processes or IBKR's server-side session
state. The origin of the broker-reported conflict is therefore unresolved; ask
IBKR to investigate stale/session-entitlement state rather than treating an
external login as established. No fresh broker position/open-order reconciliation
was performed during this investigation.

#### Reproduced client defects and branch-only fixes

- **Packet-buffer race:** `ib_insync 0.9.86` clears each pending ticker's
  `tickByTicks` at the next `tcpDataArrived`. Polling that list every 50 ms can miss
  an update that was received and already cleared. The new implementation captures
  the quote synchronously in `Ticker.updateEvent` and awaits that captured result.
  It does not accept a pre-existing buffer without a new update. Tests reproduce
  tick arrival followed by clearing before the polling task can resume.
- **Account-summary overlap:** execution, risk and dashboard consumers share one
  broker but previously opened independent summary subscriptions concurrently.
  A per-broker lock now covers the complete owned request/read/cancel lifecycle,
  including timeout and cancellation. This leaves room for IB's initial summary
  subscription within its two-active-summary limit. Tests reproduce overlap and
  verify the next request starts only after the previous cleanup.

Quote pacing, the caller's full timeout budget, shared-consumer ownership,
disconnect generation checks, cancellation of both subscriptions and fail-closed
entry/readiness blockers are preserved. Tick and disconnect callbacks are removed
on success, timeout, cancellation and partial setup failure. These changes
preserve the library-provided tick timestamp; they do not independently certify
its exchange-time provenance. Before the fix, five new regression cases failed;
after it, 273 related broker, execution, data-ingestion, risk and readiness tests
passed.

**Initial status before the later approved recovery: branch-only fixes.** These fixes remove
reproducible client failure modes; they do not explain all observed timeouts,
repair VM contention, resolve `10197`, guarantee P&L delivery or establish that
the earlier upstream-stall cause is fixed. Keep the approved deployment procedure
and market-hours restrictions. After an approved rollout, observe fresh data for
all five symbols, account requests and the full session; separately verify the
linked live/paper sharing relationship without creating another login, and escalate the
sanitized `10197` timeline to IBKR support. No delayed-data fallback or forced
session takeover was added.

#### Initial deployment approval and passive connection monitoring

The operator approved deployment on November 13, but did not approve a
market-hours Gateway restart. Read-only `status`/`check` at approximately
18:55 UTC / 13:55 EST passed preflight; readiness was 503, and all five live-data
requests plus account-risk reads were timing out. The filtered Gateway log
retained an IBC server-error dialog event at 18:34:54 UTC. Bot-only deployment
cannot be assumed to restore this stalled Gateway. The request for explicit
market-hours Gateway recovery approval was unanswered; no restart was performed.

A passive monitor was added to `deploy.sh status`; `status --watch-connections`
repeats socket/process/resource metadata every 60 seconds in the foreground.
It uses the Gateway's network namespace rather than host-only `ss`, discovers
the expected bot address from the shared Docker network, and flags other peers,
extra bot sockets or additional JVMs. It reads no arguments, environment,
credentials or packet contents, opens no IB client, and installs no service.
See the [runbook](../../../deploy/ibkr-docker/README.md#passive-gateway-connection-audit)
for scope and limitations.

At 19:03:14 UTC / 14:03:14 EST, a one-shot read-only execution of this monitor
reported one expected bot socket, one loopback relay/probe connection and one
Gateway JVM (also the only host JVM); no unexpected peer warnings. This is a
current snapshot, not proof that an unexpected connection never existed or
that client 201 owns that socket. It does not observe IBKR-side session state
and does not resolve `10197`. No foreground watcher was left running.

#### Explicit market-hours approval and completed recovery

The operator subsequently explicitly authorized
`deploy.sh gateway --yes --allow-market-hours`. The deferred after-hours
bot-only job was stopped first, so it cannot cause a second deployment.
Fresh 20:47 UTC preflight passed; Deploy Guardrails was green for `c3b7a7c`.
The harness pulled four commits from `a1b5cab` through `c3b7a7c` (two earlier
documentation/dashboard commits, quote/account fixes `22a3b87`, and the monitor).

| UTC timestamp | EST | Evidence |
|---|---|---|
| 20:49:55 | 15:49:55 | Gateway container recreated. |
| 20:52:03-10 | 15:52:03-10 | IBC login completed; Read-Only API remained false and daily restart remained 07:15 UTC. Sunday cold restart remained November 15 07:15 UTC. |
| 20:52:28 | 15:52:28 | Bot container recreated with the new image. |
| 20:54:07 | 15:54:07 | Warm-up health passed, intraday carryover flattening was explicitly skipped, and the existing session equity snapshot was reused. No risk reset was performed. |
| 20:55:02 | 15:55:02 | Harness recorded `action=gateway result=ok sha=c3b7a7c`, printed `Gateway + bot OK`, exit 0. Both containers healthy, IB/loop/publisher markers present, API health 200; two error lines were warned, not an error-free verification. |
| 20:55:55 | 15:55:55 | Fresh read-only client 9283 obtained server time, 96 account-summary rows including NetLiquidation, positions AMZN -7, TSLA -4, QQQ -2, GOOGL -6, and matching BUY stop quantities for those four symbols, all `PreSubmitted`. |
| 20:55:56-20:56:06 | 15:55:56-15:56:06 | The deployed quote path returned newly timestamped quotes for all five symbols. Probe codes were only 2104/2106/2158. |
| 20:56:17 | 15:56:17 | After probe disconnect, passive audit found one bot connection, one local relay connection, and one JVM; no unexpected peers. |
| About 20:57 | About 15:57 | API health 200 and readiness 503. The earlier direct readiness request had timed out. QQQ polling timed out at 20:54:36 and 20:56:30 despite successful independent quotes. P&L cancellation `10185` recurred. Short VM intervals again showed active swap/I/O wait and steal (one 50% interval); not proof of one causal explanation. |
| 20:57:16-20:59:40 | 15:57:16-15:59:40 | TSLA EOD exit repeatedly failed: `Open IB order already exists for TSLA; refusing duplicate submission`. Do not bypass this guard or assume the restart flattened the account. |
| 21:00:41 | 16:00:41 | Bot entered cooldown. Completion/summary delivery was not yet observed in this verification window. |
| 21:01:15 | 16:01:15 | Fresh read-only client 9284 returned only TSLA -4 shares and a TSLA BUY-4 STP order `PreSubmitted`. AMZN/QQQ/GOOGL were absent from the position response. This snapshot does not certify overnight protection or explain every locally reported partial-close quantity. |
| 21:01:19 | 16:01:19 | After probe disconnect, passive audit again showed one expected bot socket, one loopback relay, one JVM and no unexpected-peer warnings. |

Both probe clients disconnected in `finally`. Client 9283 used a temporary
execution-journal directory; 9284 used raw read-only IB without a journal.
No investigator orders/cancellations, account-balance output in probe results,
daily-loss reset or manual flattening were performed. Normal bot EOD handling
continued after restart.

**Deployment is complete, not incident closure.** TSLA exposure remains open
in the latest authoritative snapshot and requires operator reconciliation.
The stop's presence is not a guarantee of continued/overnight protection.
QQQ polling, P&L subscriptions, VM scheduling pressure and the underlying
Gateway/`10197` cause remain unresolved; the packet-race fix must not be presented
as curing all live-data failures. Monitoring is installed and available through
`status --watch-connections`, but no unattended watcher or durable recording
service was enabled. Next daily restart: November 14 07:15 UTC; next Sunday
cold restart: November 15 07:15 UTC; next regular warm-up: November 16 09:25 EST.

#### Evening status follow-up: exposure persists, no open orders

At November 14 00:29-00:31 UTC / November 13 19:29-19:31 EST, the running
release remained `c3b7a7c` (reported version `v1.4.13`); remote main was one
documentation commit ahead. Both containers were healthy with zero restarts
and no OOM flags. The bot completed cooldown at 21:05:47 UTC, logged the
unclosed TSLA position, sent an operator-alert marker at 21:07:10 and entered
sleep until November 16 14:25 UTC / 09:25 EST. Cooldown publishing still had
pending/failed/dead-letter records; its completion is not delivery assurance.

Fresh read-only client 9285 at **00:30:29 UTC** obtained server time, 96 summary
rows including NetLiquidation, **TSLA -4 shares and no open orders**. The earlier
`PreSubmitted` stop is therefore not current protection evidence. Its earlier
DAY time-in-force is consistent with expiration, but no expiration/fill audit
was performed to establish why it disappeared. The client disconnected in
`finally`, without orders, cancellations or a journal.

The first simultaneous audit saw two bot-IP sockets while this diagnostic was
connected. After it disconnected, the 00:30:45 audit returned to one expected
bot socket, one local relay and one JVM, without warnings. This extra observed
socket was our probe, not evidence of an unknown login.

Both health endpoints returned 200, including readiness. Inspection of
`api/health.py` confirms readiness checks liveness, connection/heartbeat and
dependency blockers, **not market phase or protection of broker exposure**.
It must not be interpreted as safe-to-trade or successful EOD reconciliation.
After-hours data was not used to certify the next market session's live bars.

The TSLA log took the normal client-side EOD exit branch rather than the
broker-protected branch, while a restored broker stop still existed.
`trade_executor._close_position_locked` only cancels recognized protective
stops, and `ib_exchange._refuse_if_restored_orders_open` then blocks a new
submission for a symbol with any restored open order. This explains the
observed safe refusal instead of a completed exit and indicates a mismatch
between restored broker orders and local protective-stop tracking. At this
point the precise restoration/journal cause had not yet been audited; the
follow-up below establishes it. Removing the duplicate-order guard is not
an appropriate fix.
No production changes or manual trading actions were made in this follow-up.

#### November 14 read-only audit: reused order ID corrupts stop restoration

A follow-up investigation of deployed `c3b7a7c` identified and reproduced the
specific restoration defect behind the TSLA EOD refusal. At 00:35 UTC, read-only
`status`/`check` still showed that release, healthy containers, both health
endpoints 200 and disabled/inactive legacy trading services. The execution
journal was queried with SQLite `mode=ro`; no records were changed.

The broker's API order ID is not a globally unique historical order identity:

| Evidence | Symbol / purpose | API order ID | Permanent ID |
|---|---|---|---|
| November 11 execution at 15:43:35 UTC | QQQ BUY 2 protective-stop fill | 169 | 974881495 |
| November 13 `openOrder` at 20:53:00 UTC | TSLA BUY 4 protective stop, parent 168, stop 404.40, DAY | 169 | 1743199735 |

The current submitted-order journal row for 169 correctly identifies TSLA and
`order_role=protective_stop`, with parent 168. This is **not missing stop-role
metadata**. The execution journal still contains the distinct older QQQ fill.
Entry ID 168 also has both a November 11 QQQ fill and a November 13 TSLA fill
with different permanent IDs. Other historical collisions exist for IDs
12, 16, 20 and 144, so a TSLA-only exception is not a sufficient repair.

The causal path in the deployed source is:

1. `InteractiveBrokersExecutionEngine.initialize` replays durable executions
   into `_orders`, indexed only by `str(broker_order_id)`.
   `_on_broker_execution` creates order 169 from the historical QQQ fill.
2. Restoring the current broker stop finds that existing object. The existing
   branch updates quantity/status but retains the historical symbol, permanent
   ID, fills and metadata. The broker still has a TSLA stop, while the
   execution-engine object describes QQQ with historical filled quantity 2.
3. `_adopt_restored_protective_stops` does not correct it: that fallback skips
   orders that already have a role. `TradeExecutor.restore_pending_entries`
   consequently associates the stop with QQQ, not TSLA.
4. TSLA follows the ordinary client-side EOD branch. Its close routine finds
   no recognized TSLA protective stop to cancel first. The separate restored
   symbol guard correctly sees an actual open TSLA broker order and refuses
   the market submission. The logged error is a **local safety refusal before
   a new close order is sent**, not an IBKR rejection of that close.

An offline fake-broker reproduction against this source used the two distinct
order lifetimes above, without any production connection. With historical
replay, it restored order 169 as QQQ, retained filled quantity 2, reported no
TSLA protection, and produced the exact duplicate-submission refusal. The
control without the historical fill restored TSLA with filled quantity 0 and
recognized its stop. This corroborates the journal and observed log sequence;
it is not a runtime memory dump.

Today's execution journal also confirms that **not all positions failed to
exit**: MSFT's protective stop filled at 14:40:27 UTC; EOD BUY fills closed
AMZN 7 at 20:56:53, QQQ 2 at 20:56:55 and GOOGL 6 at 20:57:11. No TSLA EOD
fill was present. TSLA retried and failed at 20:57:16, 20:58:30 and 20:59:40,
then cooldown warned and the bot slept until Monday. The existing cooldown
text calling this "stale state" and suggesting a restart is misleading:
the broker position is real, and restarting can replay the same collision.

At **00:38:29 UTC / November 13 19:38:29 EST**, a fresh read-only probe
(client 9286) again returned **TSLA -4 shares and no open orders**, with fresh
server time 00:38:28 UTC. The probe used no execution journal and disconnected
in `finally`. No investigator order submissions, cancellations, risk resets,
deployments or service restarts were performed.

#### Durable order identity repair (v1.4.14)

The repair separates internal order identity from the API routing number:

- New submissions receive a UUID (`sl:...`) persisted **before** transmission
  and sent in IB's `orderRef`. Bracket parents and children have separate
  identities, linked durably. Journal failure before transmission prevents
  placement; partial-placement handling still exposes/cancels placed legs.
- Acknowledgements and executions bind the identity to account/nonzero `permId`.
  Replay uses that identity, or account/permanent ID for older executions,
  never API order number alone. Unidentifiable legacy executions are isolated
  by canonical execution ID rather than attached to a current order.
- `ib_order_lifetimes` stores separate submission records. Migration archives
  existing submitted rows there without deleting/changing the original
  `ib_submitted_orders` table or execution IDs. API-number-only lookup fails
  explicitly when ambiguous. Overwritten historical metadata is not invented.
- Open-order reconciliation takes contract, side, quantity and prices from
  the broker. Submission metadata is attached only through a verified identity.
  Unbound legacy metadata is not borrowed because its API number matches.
  Role-less stops can be adopted against matching broker exposure; an
  unclassifiable live IB stop blocks initialization rather than becoming an
  entry eligible for timeout cancellation. Foreign-client/account orders
  require explicit reconciliation.
- Engine order IDs and lifecycle aggregation use durable identities. Current
  API routing handles are kept separately and revalidated before cancellation.
  A late historical fill cannot poll/cancel the new order, contaminate its
  filled quantity, or release its duplicate-order guard. Dashboard fill events
  preserve the API number and carry the durable ID in `strategy_order_id`.
- EOD keeps the existing sequence: cancel protection, confirm terminal status,
  reconcile fills, then close only the remaining exposure. Cooldown now
  explicitly warns that restarting does not flatten or protect positions.

Regression coverage reproduces QQQ169 -> TSLA169 and same-symbol reuse, both
with verified stop roles and legacy role adoption. It covers no fill, partial
fill and complete stop-fill races at cancellation, corrections, late callbacks,
reused cancellation handles, unacknowledged cancellations, different clients,
pre-ack recovery and non-destructive migration.

**Local validation:** 758 bot tests passed with the existing incompatible
`vibe/tests/trading_bot/dashboard/test_api.py` excluded. The full-suite attempt
confirmed that file's 34 existing HTTPX `AsyncClient(app=...)` failures; no
identity/lifecycle test failures remained. Dependencies and those unrelated
API tests were not changed. The exact passing command was:

```text
python -B -m pytest vibe/tests/trading_bot --ignore=vibe/tests/trading_bot/dashboard/test_api.py -q
```

**Rollout preparation:** November 14 04:16 UTC read-only harness preflight
passed; production still ran `c3b7a7c` at that observation. A read-only journal
audit found 84 executions, all with account/nonzero permanent ID, 122 submitted
rows, and `PRAGMA quick_check = ok`. Deployment must retain an online SQLite
backup and verify the migrated row counts and fresh startup markers.
Deployment/exposure results are recorded separately below when observed.

**Rollback limitation:** the retained legacy table is evidence, not an
up-to-date compatibility mirror. Old bot images still contain the collision
defect and do not read new lifetime records. Do not blindly downgrade and
resume trading, delete the journal, or overwrite new executions with an older
backup. Any approved rollback requires broker/journal reconciliation first.

#### Approved bot-only deployment and post-deploy verification

At **November 14 04:28:11 UTC / November 13 23:28:11 EST**, the user-authorized
`deploy.sh bot --yes` completed at **`385a516` (v1.4.14)** with `Deploy OK` and
exit 0. The Gateway was not restarted. The main checkout advanced three
commits from `c3b7a7c`: the two previous evidence updates and the identity fix.
An owner-only SQLite online backup was retained at
`/app/data/local/ib_executions.pre-identity-20261114T042545Z.db`
(host: `deploy/ibkr-docker/data/trading-bot/local/`).

The harness confirmed bot health, IB connection, trading-loop and publisher
markers, no ERROR/Traceback lines, and `/api/health = 200`. Post-deploy status
at 04:29 UTC showed both containers healthy and `/health/ready = 503` outside
the trading phase. Startup was waiting for **Monday November 16 09:25 EST**.

Read-only migration verification confirmed version 1.4.14, SQLite integrity
`ok`, **84 executions, 122 original submitted rows and 122 archived lifetime
rows**, zero changed/missing original submission rows, and zero missing
execution IDs relative to the backup.

At **04:29:19 UTC**, fresh read-only client 9287 returned **TSLA -4 shares and
no open orders**, with server time 04:29:18. It disconnected in `finally`;
the subsequent passive audit returned one bot socket, one relay and the
same single JVM, with no unexpected peers. No investigator trades,
cancellations or risk resets occurred. The deployment did not flatten or
provide protection for that exposure. Next-session live EOD behavior is not
yet observed; source regression success is not a complete incident closure.

The independent upstream `10197`/quote-timeout investigation remains open.
This identity defect explains the observed TSLA exit refusal, not those
connectivity errors or the exact cause of the DAY stop's later disappearance.

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
7. Observe the rest of the trading session, including broker-confirmed end-of-day
   positions/working orders, unresolved protection/exit state, cooldown and summary
   delivery. Morning success is not sufficient to close an intraday failure.

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
| 2026-11-09 early re-login: **unresolved server-side cause** | Scheduled restart at 04:00 UTC; login completed 04:01:50; re-login required 05:16:17. | Predates the legacy timer and falls inside IBKR maintenance. Correlation is not proof of the reason for re-login. |
| 2026-11-09 legacy timer: remediated, recurrence guard added | Old gateway started at 13:20:18 UTC. Timer disabled, old units stopped, approved harness gateway recovery completed. Guard fix `035eb3c`. | Reconfirm live disabled/inactive state on each recurrence. Does not close the earlier re-login issue. |
| 2026-11-09 execution reconnect bug: fix deployed, retry behavior observed | Reproduction plus `4d7fee5`; November 10 logs show retries and deferred equity/carryover reads on upstream failure. | Reconnect cannot restore a stuck Gateway session; keep risk-state protections intact. |
| 2026-11-09 recovery-probe `10197`: **unresolved cause** | First QQQ/AMZN history requests failed; retry returned all five complete 390-bar RTH sessions and quotes. | Identify competing session/permissions; later success alone is not a permanent fix. |
| 2026-11-10 repeated session failure: **mitigation deployed; next morning recovered** | Upstream server error at 05:18 UTC; `2110`/`2103` and account/order timeouts through market close. Legacy timer disabled; `4d7fee5` retries worked. Approved gateway action deployed `faf7e61` on Nov 11; the scheduled daily restart and warm-up then succeeded. | Exact server-side reason for the stall remains unknown; Wednesday's afternoon failure is separate evidence. Sunday cold restart remains unobserved. |
| 2026-11-11 recovery observation: read-only probes passed after an empty history response | Fresh account/order/server-time requests worked. All five Nov 10 histories returned 390 bars after QQQ retry; `1102` arrived during the observed maintenance transition. No manual restart. | This is current probe evidence, not a deployed fix, fresh market-session data, or a guarantee about the next warm-up. |
| 2026-11-11 intraday failure: **connectivity recovered; exposure open** | Morning restart/warm-up succeeded; afternoon `10197`, then `1100`/IBC error and three failed EOD exits. Bot-only `d31d7a4` rollout failed against the stalled Gateway; separately approved Gateway recovery passed at Nov 12 00:08 UTC. Fresh positions: MSFT -3, AMZN -7, TSLA +4; no open orders returned. | Resolve open exposure/protection with operator authorization; investigate competing session/upstream cause and observe the next full session. Successful deployment is not successful flattening. See section I. |
| 2026-11-12 intraday recurrence: **recovered; four positions open** | Scheduled login and initial trading succeeded; data/account failures, six `10197` events and an afternoon upstream stall followed. All five locally tracked EOD exits failed. Approved Gateway recovery succeeded Nov 13 03:38 UTC at `a1b5cab`. | Fresh broker positions: MSFT +3, TSLA +4, GOOGL +6, QQQ +2; no open orders returned. Resolve exposure, audit AMZN discrepancy, investigate recurring upstream cause and observe next full session. See section J. |
| 2026-11-13 intermittent live-data failures: **identity repair deployed, TSLA exposure open** | Packet-buffer and summary-concurrency fixes deployed in approved Gateway recovery at `c3b7a7c`. Audit proved historical QQQ169 collided with the current TSLA stop. The v1.4.14 identity fix deployed bot-only at November 14 04:28 UTC, `385a516`; startup and journal-preservation checks passed. | Fresh November 14 04:29 UTC snapshot: TSLA -4 and **no open orders**. Deployment did not flatten exposure. Next live EOD remains unobserved. No outside sessions per operator. Quote timeouts and broker-side `10197` remain separate unresolved issues. See section K. |

For each follow-up, append the UTC/ET timeline, deployed code/image, affected
symbols/operations, sanitized evidence, confirmed cause versus hypotheses, approved
actions, post-change checks and remaining observation window. Update the open row
only when fresh evidence supports doing so; retain the earlier incident history.
