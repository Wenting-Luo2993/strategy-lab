# IBKR Operational Reliability and Testing Product Specification

**Status:** Proposed CI/CD and operational assurance; focused source safeguards
and local regressions implemented separately from pipeline enforcement.
**Evidence baseline:** Monday November 9 through Wednesday November 11, 2026,
including the successful restart/warm-up and subsequent intraday failure.
Production safeguards: `d31d7a4`, deployed after separately approved Gateway
recovery at November 12 00:08 UTC (November 11 evening EST). The CI/CD pipeline
remains proposed. Connectivity recovered, but three positions remain open with
no open orders returned by the post-recovery broker query.

**Latest follow-up:** November 12's trading day also suffered an intraday
upstream stall and failed EOD exits. Separately approved recovery at November 13
03:38 UTC runs `a1b5cab` (same bot code, updated documentation). Fresh broker
positions at 03:39 UTC: MSFT +3, TSLA +4, GOOGL +6, QQQ +2, with no open orders
returned. This supersedes the earlier current-exposure snapshot, not its history.
See troubleshooting section J. The user knows of no competing login; the source
of `10197` and repeated upstream stalls remains unresolved.

The [gateway troubleshooting guide](gateway-troubleshooting.md) owns the detailed
timeline, sanitized evidence, recovery procedures and incident closure status.
This specification defines the desired outcomes, not an implementation plan.
Only explicitly recorded observation windows establish operational success.

## Problem and intended outcome

The bot can be alive and its Gateway API port reachable while broker operations
and market data are unusable. Recent incidents also exposed competing Gateway
ownership, fragmented client recovery and insufficient automated regression gates.
Operators need to distinguish these failures, know whether trading is safely
blocked, and verify recovery without disturbing orders or risk state.

The intended outcome is an evidence-based path from change review to approved
deployment and subsequent operating-session assurance. Passing CI establishes
software behavior under tested conditions; it cannot guarantee future IBKR
availability or replace observing the next real trading session.

## Observed issues, mitigations and uncertainty

| Issue | Observed evidence and current mitigation | Remaining gap or uncertainty |
|---|---|---|
| Competing authenticated Gateways, Monday | At 13:20 UTC the legacy systemd timer started an old Gateway beside Docker production. The timer was disabled, legacy services stopped and deployment preflight guards added. | This does not explain the earlier 05:16 UTC re-login, which predates the timer. Behavioral prevention needs a durable gate, not only script-text checks. |
| Fragmented API-client ownership | Data/diagnostic recovery could leave execution disconnected. Execution recovery fix `4d7fee5` deployed Monday evening. The complete shared execution/data client and quote coordination shipped in `faf7e61` early November 11 UTC. Borrowed providers no longer own transport lifecycle; behavioral tests cover overlapping quote cancellation and recovery. | Multiple API clients to **one** Gateway are supported; they are not competing Gateway logins. Shared ownership fixes local lifecycle issues, not an upstream login/session stall. |
| Upstream session stalled, Tuesday | Local API connections succeeded while account, position and order requests timed out with `2110`/`2103` warnings. Logs showed 56 failed execution-health cycles and safe deferral of equity/carryover reads. The legacy timer remained disabled. | The exact broker-side reason recovery stalled remains unknown. More local socket reconnections cannot necessarily repair the upstream session. Log counts are not an execution-journal audit. |
| Restart timing | The old 04:00 UTC daily restart preceded North America maintenance; the Sunday-only cold restart also preceded Sunday's 01:00 ET token reset. Both schedules changed to 07:15 UTC (02:15 EST / 03:15 EDT), confirmed in actual IBC logs after approved deployment. Wednesday's daily restart and warm-up subsequently succeeded. | A deployed mitigation, not proof of root cause or full-session reliability. The Sunday-only cold restart remains unobserved. |
| Incomplete health signals | Container TCP health and process liveness passed despite unusable broker operations. Current readiness uses alive plus websocket-or-heartbeat, not a complete fresh broker-operation/data contract. The dashboard is not a Gateway/IBC log stream. | Need layered, phase-aware readiness and actionable failure classification; stale dashboard data alone does not identify a Gateway failure. |
| Separate market-data conflict | Earlier `10197` competing-session/entitlement symptoms were followed by successful retries. | The cause remains unresolved; later historical-data success does not establish permanent live-data access or close this follow-up. |
| Wednesday intraday dependency failure | Scheduled restart at 07:15 UTC and warm-up around 14:25 UTC succeeded, followed by 91 account-risk errors and 95 dashboard account/position persistence failures. Four `10197` errors preceded three `1100` events around 19:04 UTC and an IBC retry message. Legacy services/timer remained inactive. | The competing session's location and upstream failure cause remain unknown. Successful morning startup is not full-session reliability; dashboard fills are not authoritative current exposure. |
| False readiness and missing escalation | Both health endpoints returned 200 after broker failure. No successful alert/cooldown marker was found in the retained window. Known failure blockers and alert attempts subsequently deployed in `d31d7a4`. | Full freshness/phase policy and durable notification delivery remain proposed. Absence of earlier log markers does not establish webhook configuration or delivery state. |
| Unconfirmed end-of-day exits | AMZN, MSFT and TSLA exits failed at 20:55 UTC because protective-stop cancellations were unacknowledged. Repeated cancellation produced `10148`; the client library locally labelled the trade `Cancelled`. | Deployed safeguards reject rejection-derived cancellation as terminal, avoid resending PendingCancel and alert on uncertainty. After recovery, broker positions were MSFT -3, AMZN -7, TSLA +4, with no open orders returned. Deployment did not flatten exposure or establish working protection. |
| Invalid bar after a data gap | All five symbols timed out near the close; ten completed-bar handlers failed on `NoneType.__format__`. An empty aggregation placeholder could be emitted when trading resumed in a later interval. | Source now drops the empty interval and starts a real bar from the new trade, without inventing OHLC data. Freshness/completeness assurance remains a separate requirement. |
| Secondary symptoms | P&L request timeouts and `10185` cancellation errors were also observed. | Subscription lifecycle/causality is not established by this investigation; retain as follow-up rather than attributing every symptom to one root cause. |

### Deployment baseline and latest operational position

The approved `faf7e61` rollout recreated Gateway and bot around November 11
05:31-05:35 UTC. Both containers were subsequently healthy; IB connection,
trading-loop and publisher startup markers were present. API liveness returned
200 and after-hours readiness returned the expected 503.

A disposable deployed-code probe passed connection sharing, borrower disconnect
isolation, concurrent owner reconnect with normal retry settings, and fresh
server-time, account, positions, open-order and execution reads. An initial
one-attempt/10-second probe failed immediate recovery; its underlying cause was
not captured, so the shorter settings are not an established cause. The probes
placed no test orders and reset no production risk state.

Wednesday subsequently supplied positive evidence for the 07:15 UTC daily
restart, 14:25 UTC warm-up and initial trading. Seven distinct filled order IDs
were logged across five symbols; that is not seven completed round trips.
The afternoon failure invalidates any full-day success claim. The last retained
runtime log was 20:59:27 UTC; an independent read-only diagnostic connection then
timed out during synchronization with upstream/data-farm warnings. It could not
verify current positions or working orders and placed/cancelled no orders.
The later approved bot-only rollout failed because execution synchronization still
timed out against the existing Gateway. Separately approved Gateway + bot recovery
then passed at November 12 00:08:20 UTC with `d31d7a4`, healthy containers and
fresh startup markers. A fresh read-only snapshot at 00:08:51 UTC confirmed
MSFT -3, AMZN -7 and TSLA +4 shares, with no open orders returned. This establishes
open exposure, not successful EOD exits. No diagnostic orders or risk resets were
performed. The Sunday-only cold restart and next full trading session remain
unobserved; the guide records the rollout timeline.

### Deployed safeguards, operational assurance still pending

The `d31d7a4` changes address reproducible software defects, not the unknown broker
outage cause:

- Known upstream, account-risk and per-symbol polling failures block readiness
  and relevant new entry submissions; recovery clears only the matching failure.
  Upstream restoration messages alone do not release entries: fresh account
  verification is required. Existing exit handling remains enabled.
- Account exceptions include their type, even for an empty `TimeoutError`.
  Existing risk behavior already blocked entries when evaluation failed; this
  is not evidence that the earlier empty error string allowed trading.
- Dependency and unconfirmed protective-cancellation failures attempt a
  deduplicated operator alert. Missing webhook configuration and unsuccessful
  delivery are logged. This is not durable delivery/retry assurance.
- PendingCancel retries wait rather than send another cancel. Real ib_insync
  `10147`/`10148` error events are tested: their local `Cancelled` state cannot
  authorize a market close. A later authoritative confirmation can resolve it;
  otherwise ambiguity remains fail-closed and requires reconciliation.
- Empty interval placeholders are not emitted as real bars after data gaps.

These are reactive safeguards, not a freshness TTL or complete broker-readiness
redesign. Existing cached position reads are not authoritative reconciliation.
The alert path does not yet guarantee retries after a failed delivery; already
working orders require reconciliation rather than an assumption that blocking
new submissions cancels them. No automatic Gateway restart or full warm-up loop
was introduced, and no existing equity baseline, halt or journal is reset.

## Goals and boundaries

The product must prevent known ownership/recovery regressions, reject unsafe
deployments before production mutation, preserve fail-closed trading behavior,
and report fresh evidence at the layer that failed. Readiness must distinguish
process liveness, local transport, upstream broker operations, execution/risk
eligibility, per-symbol data and downstream publishing. An expected closed market
or a valid risk halt must not be mistaken for an infrastructure failure.

This specification defines high-level acceptance, not CI job implementation.
The source safeguards above do not implement the proposed pipeline, monitoring,
or production changes. This document does not authorize restarts or deployment,
introduce an auto-remediation loop, promise broker uptime, or change trading
strategy/risk policy. Exact jobs, YAML, mock design, module APIs, commands,
timeouts, alert thresholds and engineering task breakdown belong to later design.

CI must not require production secrets or start a second real Gateway with
production credentials. Verification must not submit/cancel live test orders,
reset session equity or risk halts, or weaken live-data requirements to pass.
Production-changing actions retain explicit operator approval and the existing
deployment safety process.

## Existing coverage versus proposed capability

[Deploy Guardrails](../../../.github/workflows/deploy-guardrails.yml) currently
runs shell syntax, ShellCheck, Compose validation with dummy secrets, and
[`tests/deploy`](../../../tests/deploy/test_ibkr_docker_guardrails.py).
Its path filters cover deployment assets, the bot Dockerfile, deployment skill,
deployment tests and the workflow itself; broker-only changes do not trigger it.
It does not run the trading-bot regression suites. The other current workflow
deploys the dashboard, not trading-bot regression coverage.

The deployment tests protect static configuration/script invariants and check
restart timing across winter/summer and weekend dates. Legacy timer tests chiefly
inspect script text; they do not demonstrate rejection before production mutation.
Relevant behavioral coverage already exists for
[shared-client ownership and quotes](../../../vibe/tests/trading_bot/test_ib_shared_client.py),
[warm-up recovery](../../../vibe/tests/trading_bot/test_warmup_phase.py), and
[portfolio risk](../../../vibe/tests/trading_bot/test_portfolio_risk.py).
Recorded local regression success is not equivalent to an automatically enforced
PR gate. The stages below are proposed requirements, not claims of implemented
end-to-end coverage.

The new focused regressions additionally cover actual client-library cancellation
error semantics and no-close safety in
[resting-stop execution](../../../vibe/tests/trading_bot/test_resting_stop_entry.py),
independent [readiness blockers](../../../vibe/tests/trading_bot/test_health.py),
missing-symbol polling/recovery in
[orchestrator integration](../../../vibe/tests/trading_bot/test_dashboard_orchestrator_integration.py),
and [empty intervals after gaps](../../../vibe/tests/trading_bot/test_data.py).
These local tests do not need production credentials or assert broker availability.
The broad local run also exposed 34 existing dashboard API tests using the removed
`httpx.AsyncClient(app=...)` argument. That test-harness compatibility gap must be
resolved when establishing the full required gate; it is not evidence that these
broker safeguards failed, nor a reason to silently declare the whole suite green.

## High-level assurance pipeline and gates

| Priority / stage | Required capability and gate | Outcome-based acceptance |
|---|---|---|
| 1. PR regression CI | Automatically run relevant shared-client, warm-up, risk, data, readiness, notification and order-lifecycle coverage for trading-bot changes, alongside deployment guardrails. Failed required checks prevent promotion. | Broker-only changes receive coverage. Detect borrower lifecycle errors, overlapping subscription cancellation, empty bars, silent dependency failures and unsafe cancellation acknowledgements before release, without production access. |
| 2. Deployment safety integration gate | Exercise deployment behavior against an active legacy Gateway, an enabled-but-inactive timer, incompatible/foreign stacks and unsafe actions. Reject unsafe state before production mutation; bot-only updates preserve Gateway ownership. | Rejection leaves production workloads unchanged with an actionable reason. A safe bot-only update does not restart/recreate Gateway or create a competing authenticated session. Evidence demonstrates behavior, not merely matching script text. |
| 2. Recovery integration gate | Exercise local API connectivity with unresponsive upstream requests, transport loss, concurrent recovery and restoration. Preserve a single transport owner and fail closed while prerequisites are unavailable. | Simulated upstream failure cannot report trading-ready merely because the socket is connected. Recovery preserves session equity and valid risk halts, avoids duplicate order/subscription effects, and restores eligible operations only after fresh evidence and reconciliation. |
| 2. Trading-day failure and exit gate | Exercise healthy warm-up followed by partial/multi-symbol data loss, failed account reads, delayed fills, PendingCancel, rejected cancellation and unavailable end-of-day exits. Include the real client library's error-to-status behavior. | Missing data cannot fabricate bars or enable stale-data entries. Failures do not stop exit monitoring. Unconfirmed stop cancellation never authorizes a duplicate/unsafe close; unresolved exposure is explicitly reported, not counted as flattened. Existing orders and risk state survive recovery. |
| 2. Alert and evidence gate | Exercise missing webhook configuration, delivery rejection/timeout, repeated failures, restoration and a new failure episode, plus downstream publishing loss. | Operators receive actionable, deduplicated incidents with layer/symbol, time, revision and uncertainty. Failed delivery is observable, with a designed bounded retry/escalation path. Dashboard/publisher success never substitutes for broker confirmation. |
| 3. Approved deployment verification and readiness | Verify the running release, effective daily/Sunday restart configuration and bounded fresh read-only broker operations, rather than relying on old startup markers. Prefer the running bot's shared client; any diagnostic connection is temporary, isolated and order-free. | Results identify which layers passed, failed or remain unobserved, with timestamps and actionable failure categories. Fresh server-time/account/positions/orders/executions evidence is separate from live-data readiness. Diagnostics leave production client ownership, orders and risk state intact. |
| 4. Full-session operational assurance | Observe actual daily/Sunday restart, pre-market readiness, intraday per-symbol data/account/publishing freshness, end-of-day broker reconciliation and cooldown/summary delivery. Distinguish expected outside-market behavior from failure. | Morning success cannot close a full-day incident. Each phase has evidence; end-of-day positions, working orders and protective coverage are broker-confirmed or explicitly unknown. Missing cooldown, stale publishing and failed exits escalate even if containers remain healthy. |

Priorities are staged delivery order, not dates or reliability commitments.
Existing behavioral tests should be wired into CI first; deployment and recovery
integration gates then strengthen what is proven before release. Broker-aware
verification and scheduled assurance extend that proof into approved operation,
without turning CI into a production broker client.

The promotion sequence is therefore **source regressions -> simulated deployment
and partial-failure gates -> approved release -> fresh read-only verification ->
whole-session observation**. A failed stage retains its evidence and blocks the
unsafe next action; it does not automatically restart the Gateway, flatten
positions or reset risk state.

November 12 adds acceptance cases: a pre-open close timeout must not be treated
as cancellation when its order can still fill at the open; cooldown's cached
position report must be reconciled with fresh broker state rather than instructing
operators to clear it by restarting; and intermittent recovery must not generate
unbounded alert noise. The 86 logged system-error sends show why per-episode
deduplication alone is not sufficient operational assurance.

## Evidence and completion criteria

Each gate must produce reviewable, sanitized evidence tied to the tested or
running revision and observation window. Report passed, failed and not-observed
outcomes separately. Distinguish local transport loss, upstream unresponsiveness,
operator-required authentication, session/entitlement conflicts, stale symbol data
and publishing failures; retain unknown causes rather than inventing a diagnosis.

The capability is complete when the stages above can demonstrate their stated
outcomes, failures block unsafe promotion or entries as appropriate, and operators
can identify the next safe action without exposing credentials/account details.
An unresolved broker outage must remain visible and trading must remain safely
gated; neither a green CI result nor a successful disposable probe closes it.
The early Monday re-login, Tuesday/Wednesday upstream failures, `10197` origin,
P&L/subscription symptoms and Wednesday's confirmed open exposure without observed
working protection remain
separate follow-ups until the incident guide records evidence resolving them.
