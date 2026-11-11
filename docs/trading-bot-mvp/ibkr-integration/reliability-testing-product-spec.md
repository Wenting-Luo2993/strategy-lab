# IBKR Operational Reliability and Testing Product Specification

**Status:** Proposed testing and assurance capabilities; documentation only.
**Evidence baseline:** Monday November 9 and Tuesday November 10, 2026 incidents,
including the November 11 UTC rollout recorded in documentation revision
`38293d2`. Last verified production release: `faf7e61`.

The [gateway troubleshooting guide](gateway-troubleshooting.md) owns the detailed
timeline, sanitized evidence, recovery procedures and incident closure status.
This specification defines the desired outcomes, not an implementation plan.
No later scheduled-cycle success is assumed.

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
| Restart timing | The old 04:00 UTC daily restart preceded North America maintenance; the Sunday-only cold restart also preceded Sunday's 01:00 ET token reset. Both schedules changed to 07:15 UTC (02:15 EST / 03:15 EDT), confirmed in actual IBC logs after approved deployment. | A deployed mitigation, not proof of root cause or future reliability. The next actual scheduled restart and warm-up had not been observed in the evidence baseline. |
| Incomplete health signals | Container TCP health and process liveness passed despite unusable broker operations. Current readiness uses alive plus websocket-or-heartbeat, not a complete fresh broker-operation/data contract. The dashboard is not a Gateway/IBC log stream. | Need layered, phase-aware readiness and actionable failure classification; stale dashboard data alone does not identify a Gateway failure. |
| Separate market-data conflict | Earlier `10197` competing-session/entitlement symptoms were followed by successful retries. | The cause remains unresolved; later historical-data success does not establish permanent live-data access or close this follow-up. |

### Last verified position

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

These are bounded observations of the deployed code, not proof that the running
bot completed its next scheduled restart, warm-up, or market-hours data cycle.
Those observations remain pending in the incident record.

## Goals and boundaries

The product must prevent known ownership/recovery regressions, reject unsafe
deployments before production mutation, preserve fail-closed trading behavior,
and report fresh evidence at the layer that failed. Readiness must distinguish
process liveness, local transport, upstream broker operations, execution/risk
eligibility, per-symbol data and downstream publishing. An expected closed market
or a valid risk halt must not be mistaken for an infrastructure failure.

This specification does **not** implement CI/CD, tests, bot changes, monitoring,
automation or production changes. It does not authorize restarts or deployment,
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

## High-level assurance pipeline and gates

| Priority / stage | Required capability and gate | Outcome-based acceptance |
|---|---|---|
| 1. PR regression CI | Automatically run relevant existing shared-client, warm-up and risk regression coverage for trading-bot changes, alongside applicable deployment guardrails. Failed required checks prevent promotion. | Broker-only and related lifecycle/risk changes receive meaningful coverage. A regression in borrower lifecycle isolation, quote cancellation, recovery or risk-state preservation is detected before release. CI operates without production access. |
| 2. Deployment safety integration gate | Exercise deployment behavior against an active legacy Gateway, an enabled-but-inactive timer, incompatible/foreign stacks and unsafe actions. Reject unsafe state before production mutation; bot-only updates preserve Gateway ownership. | Rejection leaves production workloads unchanged with an actionable reason. A safe bot-only update does not restart/recreate Gateway or create a competing authenticated session. Evidence demonstrates behavior, not merely matching script text. |
| 2. Recovery integration gate | Exercise local API connectivity with unresponsive upstream requests, transport loss, concurrent recovery and restoration. Preserve a single transport owner and fail closed while prerequisites are unavailable. | Simulated upstream failure cannot report trading-ready merely because the socket is connected. Recovery preserves session equity and valid risk halts, avoids duplicate order/subscription effects, and restores eligible operations only after fresh evidence and reconciliation. |
| 3. Approved deployment verification and readiness | Verify the running release, effective daily/Sunday restart configuration and bounded fresh read-only broker operations, rather than relying on old startup markers. Prefer the running bot's shared client; any diagnostic connection is temporary, isolated and order-free. | Results identify which layers passed, failed or remain unobserved, with timestamps and actionable failure categories. Fresh server-time/account/positions/orders/executions evidence is separate from live-data readiness. Diagnostics leave production client ownership, orders and risk state intact. |
| 4. Scheduled operational assurance | Observe the actual post-maintenance restart, pre-market readiness and market-hours per-symbol data/publishing freshness. Distinguish expected outside-market behavior from failure. | The incident record includes actual restart and warm-up outcomes before claiming next-cycle recovery. During suitable market windows, each enabled symbol has fresh expected data and publishing progress; failures identify the affected layer/symbol and needed operator follow-up. |

Priorities are staged delivery order, not dates or reliability commitments.
Existing behavioral tests should be wired into CI first; deployment and recovery
integration gates then strengthen what is proven before release. Broker-aware
verification and scheduled assurance extend that proof into approved operation,
without turning CI into a production broker client.

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
The early Monday re-login, Tuesday's stalled recovery and `10197` follow-up remain
separate uncertainties until the incident guide records evidence resolving them.
