<!-- taskboard
{"id":"reliability-roadmap","title":"Live-service reliability roadmap","projects":["live-reliability"],"status":"current","kind":"roadmap","reviewed":"2026-11-17"}
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
{"id":"reliability-incident-diagnostics","title":"Review request diagnostics and rotating host evidence","project":"live-reliability","status":"done","milestone":"Dependency recovery","note":"Request phases/IDs, correlated numeric errors, legacy process metadata, pressure deltas and opt-in bounded recording passed focused regressions. Operator-approved v1.4.15 / 53016ce bot-only deployment verified November 17 01:27 UTC. Recorder code is available but unattended recording is off; incident correlation remains separate work.","acceptance":"Timeout evidence distinguishes pacing/qualification/tick wait and unrelated errors; recording bounds disk use, reports failures and reads no credentials or process arguments.","reviewed":"2026-11-17","depends_on":[],"refs":["gateway-evidence"]}
```

```taskboard-task
{"id":"reliability-diagnostics-rollout","title":"Record a subsequent incident with deployed diagnostics","project":"live-reliability","status":"waiting","milestone":"Dependency recovery","note":"v1.4.15 / 53016ce diagnostics deployment is verified. Await separate approval for one recorder and a subsequent incident to correlate; the approved release explicitly did not start unattended recording. No executor-occupancy or event-loop-lag monitor was added.","acceptance":"Verify deployed diagnostics and a single active recorder; correlate request failures with timestamped host/process evidence without changing trading safeguards.","reviewed":"2026-11-17","depends_on":["reliability-incident-diagnostics"],"refs":["gateway-evidence"]}
```

```taskboard-task
{"id":"reliability-readiness","title":"Make phase readiness and failed-exit escalation actionable","project":"live-reliability","status":"backlog","milestone":"Operational assurance","note":"Known blockers exist, but green liveness is not exposure protection. Full phase/freshness policy and bounded alert delivery remain work.","acceptance":"Demonstrate phase-appropriate freshness and broker reconciliation, explicit unknown states, and observable bounded escalation when exits or notifications fail.","reviewed":"2026-11-14","depends_on":[],"refs":["reliability-spec"]}
```

```taskboard-task
{"id":"reliability-session","title":"Observe a complete session after recovery","project":"live-reliability","status":"waiting","milestone":"Full-session evidence","note":"November 16 intraday recovery is not full-day reliability. EOD flatness was verified at 21:47 UTC. The dashboard backlog is recovered and v1.4.15 / 53016ce diagnostics deployed, but parity remains incomplete. November 17 scheduled Gateway restart, warm-up, sustained data and complete cooldown delivery remain unobserved.","acceptance":"Record revision-specific warm-up, per-symbol data and account freshness, exit reconciliation and cooldown delivery; document failures instead of closing the incident prematurely.","reviewed":"2026-11-17","depends_on":["reliability-order-identity","reliability-exposure"],"refs":["gateway-evidence","reliability-spec"]}
```

```taskboard-task
{"id":"reliability-dashboard-metric-identity","title":"Resolve dashboard metric identity conflicts and recover failed publication","project":"live-reliability","status":"done","milestone":"Reliable dashboard publication","note":"Operator-applied metric-ID migration independently confirmed; approved v1.4.15 / 53016ce bot-only release and consistent backup completed. At November 17 01:28:57 UTC, seven targeted immutable retries were published, all 110 historical dead letters had published successors, all 29 affected metric IDs matched remote versions/names/timestamps and four same-time parity rows coexisted. No unresolved queue rows or eligible retries; all original dead letters and 591 failure records preserved.","acceptance":"Inventory legacy writers, preserve distinct metrics under one canonical identity, validate same-name/same-time different-symbol writes and idempotent retries, then verify an approved migration and targeted queue recovery without deleting evidence.","reviewed":"2026-11-17","depends_on":[],"refs":["gateway-evidence","dashboard-operations"]}
```

```taskboard-task
{"id":"reliability-parity-alert-followup","title":"Explain incomplete parity and placement discrepancies","project":"live-reliability","status":"backlog","milestone":"Actionable parity evidence","note":"Nov 16 parity: AMZN matched; GOOGL entry-price/R, MSFT entry-price and TSLA entry-minute mismatches. QQQ historical request timed out but stored empty exception text. Separate incomplete replay, late placement and actual fill differences; do not suppress the warning or recalibrate from this disrupted paper session alone.","acceptance":"Expose historical-fetch exception type and incomplete coverage, assess bounded retry, and correlate order submission/fill timing with replay bars before attributing mismatches to strategy or slippage.","reviewed":"2026-11-16","depends_on":[],"refs":["gateway-evidence"]}
```
