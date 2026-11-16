<!-- taskboard
{"id":"reliability-roadmap","title":"Live-service reliability roadmap","projects":["live-reliability"],"status":"current","kind":"roadmap","reviewed":"2026-11-16"}
-->
# Live-service reliability roadmap

The [incident register](../trading-bot-mvp/ibkr-integration/gateway-troubleshooting.md)
owns operational evidence. This board is not a live monitor or authorization to
restart, cancel orders, flatten positions or reset risk.

```taskboard-task
{"id":"reliability-order-identity","title":"Repair reused order identity and protective-stop recovery","project":"live-reliability","status":"done","milestone":"Safe exit recovery","note":"v1.4.14 / 385a516 deployed bot-only at November 14 04:28 UTC. Startup and non-destructive journal migration were verified.","acceptance":"Cross-lifetime fills cannot contaminate a restored stop; cancellation remains acknowledged and reconciled before closing remaining exposure.","reviewed":"2026-11-14","depends_on":[],"refs":["gateway-evidence","reliability-spec"]}
```

```taskboard-task
{"id":"reliability-exposure","title":"Resolve and verify outstanding broker exposure","project":"live-reliability","status":"done","milestone":"Broker reconciliation","note":"After approved Nov 16 recovery, normal bot EOD management ran. Fresh read-only server-time/positions/all-open-orders requests at 21:47:43 UTC returned no nonzero positions and no open orders. Probe disconnected; no investigator orders or manual flattening. Earlier snapshots remain in the incident guide; this is flatness at the observed time, not future protection.","acceptance":"Record fresh broker-confirmed exposure and working orders, the authorized disposition and its verified result; do not infer flatness from health or restart.","reviewed":"2026-11-16","depends_on":[],"refs":["gateway-evidence"]}
```

```taskboard-task
{"id":"reliability-upstream","title":"Investigate unresolved upstream and quote failures","project":"live-reliability","status":"waiting","milestone":"Dependency recovery","note":"Nov 16: 10197 then 1100/IBC stall, no observed legacy duplicate, heavy VM pressure. Approved recovery passed at 19:50 UTC. Post-recovery QQQ/GOOGL quote timeouts were followed by bars; QQQ cooldown history timed out. No new 10197/1100 error lines in the inspected post-recovery window. Await resource/request evidence and IBKR session investigation; do not infer an outside login.","acceptance":"Preserve timestamped layer-specific evidence, distinguish local transport/data readiness from broker-side causes, and document remaining uncertainty.","reviewed":"2026-11-16","depends_on":[],"refs":["gateway-evidence","reliability-spec"]}
```

```taskboard-task
{"id":"reliability-incident-diagnostics","title":"Review request diagnostics and rotating host evidence","project":"live-reliability","status":"review","milestone":"Dependency recovery","note":"Local changes add request phases/IDs, correlated numeric errors, legacy process metadata, pressure deltas and opt-in bounded recording. Focused regressions pass. Not committed, deployed or running on the VM.","acceptance":"Timeout evidence distinguishes pacing/qualification/tick wait and unrelated errors; recording bounds disk use, reports failures and reads no credentials or process arguments.","reviewed":"2026-11-16","depends_on":[],"refs":["gateway-evidence"]}
```

```taskboard-task
{"id":"reliability-diagnostics-rollout","title":"Deploy diagnostics and record a subsequent incident","project":"live-reliability","status":"waiting","milestone":"Dependency recovery","note":"Requires reviewed changes on main, explicit deployment/recording approval and a working SSH session. No unattended watcher has been enabled.","acceptance":"Verify deployed diagnostics and a single active recorder; correlate request failures with timestamped host/process evidence without changing trading safeguards.","reviewed":"2026-11-16","depends_on":["reliability-incident-diagnostics"],"refs":["gateway-evidence"]}
```

```taskboard-task
{"id":"reliability-readiness","title":"Make phase readiness and failed-exit escalation actionable","project":"live-reliability","status":"backlog","milestone":"Operational assurance","note":"Known blockers exist, but green liveness is not exposure protection. Full phase/freshness policy and bounded alert delivery remain work.","acceptance":"Demonstrate phase-appropriate freshness and broker reconciliation, explicit unknown states, and observable bounded escalation when exits or notifications fail.","reviewed":"2026-11-14","depends_on":[],"refs":["reliability-spec"]}
```

```taskboard-task
{"id":"reliability-session","title":"Observe a complete session after recovery","project":"live-reliability","status":"waiting","milestone":"Full-session evidence","note":"November 16 intraday recovery is not full-day reliability. EOD flatness was verified at 21:47 UTC, but cooldown had incomplete parity and dashboard-publication errors. Next-session warm-up, sustained data and complete cooldown delivery remain unobserved.","acceptance":"Record revision-specific warm-up, per-symbol data and account freshness, exit reconciliation and cooldown delivery; document failures instead of closing the incident prematurely.","reviewed":"2026-11-16","depends_on":["reliability-order-identity","reliability-exposure"],"refs":["gateway-evidence","reliability-spec"]}
```

```taskboard-task
{"id":"reliability-dashboard-metric-identity","title":"Resolve dashboard metric identity conflicts and recover failed publication","project":"live-reliability","status":"in_progress","milestone":"Reliable dashboard publication","note":"Local migration, base-schema reapplication guard, immutable retries and recovered-dead-letter alerts passed focused Python/PostgreSQL checks. Operator reports running the SQL and approved commit/push, bot-only release, consistent backup and targeted queue recovery. Actual rollout and remote publication verification remain pending.","acceptance":"Inventory legacy writers, preserve distinct metrics under one canonical identity, validate same-name/same-time different-symbol writes and idempotent retries, then verify an approved migration and targeted queue recovery without deleting evidence.","reviewed":"2026-11-16","depends_on":[],"refs":["gateway-evidence","dashboard-operations"]}
```

```taskboard-task
{"id":"reliability-parity-alert-followup","title":"Explain incomplete parity and placement discrepancies","project":"live-reliability","status":"backlog","milestone":"Actionable parity evidence","note":"Nov 16 parity: AMZN matched; GOOGL entry-price/R, MSFT entry-price and TSLA entry-minute mismatches. QQQ historical request timed out but stored empty exception text. Separate incomplete replay, late placement and actual fill differences; do not suppress the warning or recalibrate from this disrupted paper session alone.","acceptance":"Expose historical-fetch exception type and incomplete coverage, assess bounded retry, and correlate order submission/fill timing with replay bars before attributing mismatches to strategy or slippage.","reviewed":"2026-11-16","depends_on":[],"refs":["gateway-evidence"]}
```
