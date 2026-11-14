<!-- taskboard
{"id":"reliability-roadmap","title":"Live-service reliability roadmap","projects":["live-reliability"],"status":"current","kind":"roadmap","reviewed":"2026-11-14"}
-->
# Live-service reliability roadmap

The [incident register](../trading-bot-mvp/ibkr-integration/gateway-troubleshooting.md)
owns operational evidence. This board is not a live monitor or authorization to
restart, cancel orders, flatten positions or reset risk.

```taskboard-task
{"id":"reliability-order-identity","title":"Repair reused order identity and protective-stop recovery","project":"live-reliability","status":"done","milestone":"Safe exit recovery","note":"v1.4.14 / 385a516 deployed bot-only at November 14 04:28 UTC. Startup and non-destructive journal migration were verified.","acceptance":"Cross-lifetime fills cannot contaminate a restored stop; cancellation remains acknowledged and reconciled before closing remaining exposure.","reviewed":"2026-11-14","depends_on":[],"refs":["gateway-evidence","reliability-spec"]}
```

```taskboard-task
{"id":"reliability-exposure","title":"Resolve and verify outstanding broker exposure","project":"live-reliability","status":"waiting","milestone":"Broker reconciliation","note":"Historical observation only: November 14 04:29 UTC showed TSLA -4 and no open orders. No newer position check is implied. Requires fresh evidence and operator-authorized handling.","acceptance":"Record fresh broker-confirmed exposure and working orders, the authorized disposition and its verified result; do not infer flatness from health or restart.","reviewed":"2026-11-14","depends_on":[],"refs":["gateway-evidence"]}
```

```taskboard-task
{"id":"reliability-upstream","title":"Investigate unresolved upstream and quote failures","project":"live-reliability","status":"backlog","milestone":"Dependency recovery","note":"10197 and quote timeouts are not explained by the order-identity fix. Operator confirms no IBKR session outside the VM; do not label an external login as proven.","acceptance":"Preserve timestamped layer-specific evidence, distinguish local transport/data readiness from broker-side causes, and document remaining uncertainty.","reviewed":"2026-11-14","depends_on":[],"refs":["gateway-evidence","reliability-spec"]}
```

```taskboard-task
{"id":"reliability-readiness","title":"Make phase readiness and failed-exit escalation actionable","project":"live-reliability","status":"backlog","milestone":"Operational assurance","note":"Known blockers exist, but green liveness is not exposure protection. Full phase/freshness policy and bounded alert delivery remain work.","acceptance":"Demonstrate phase-appropriate freshness and broker reconciliation, explicit unknown states, and observable bounded escalation when exits or notifications fail.","reviewed":"2026-11-14","depends_on":[],"refs":["reliability-spec"]}
```

```taskboard-task
{"id":"reliability-session","title":"Observe a complete session after recovery","project":"live-reliability","status":"waiting","milestone":"Full-session evidence","note":"Successful deployment is not full-day reliability. The next session, restart behavior and live EOD recovery remain unobserved in the retained evidence.","acceptance":"Record revision-specific warm-up, per-symbol data and account freshness, exit reconciliation and cooldown delivery; document failures instead of closing the incident prematurely.","reviewed":"2026-11-14","depends_on":["reliability-order-identity","reliability-exposure"],"refs":["gateway-evidence","reliability-spec"]}
```
