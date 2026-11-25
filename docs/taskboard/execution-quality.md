<!-- taskboard
{"id":"execution-roadmap","title":"Execution-quality measurement roadmap","projects":["execution-quality"],"status":"current","kind":"roadmap","reviewed":"2026-11-24"}
-->
# Execution-quality measurement roadmap

The [portfolio paper plan](../trading-bot-mvp/2026-11-08-portfolio-paper-trading-plan.md)
defines the measurement intent. Its old rollout estimates are not current status.
Bars alone cannot measure fill slippage. Paper fills also do not establish
real-money queue position, liquidity or execution quality.

**November 24 destination decision:** prepare a dedicated Windows/WSL2 laptop,
not the research PC. Follow the [laptop setup handoff](../trading-bot-mvp/ibkr-integration/2026-11-24-laptop-paper-bot-handoff.md).
Oracle remains the source until a separately approved cutover; no new source
stop, private transfer or destination startup is authorized by documentation.
Keep the five-symbol ruleset and risk limits unchanged for the shakedown.

```taskboard-task
{"id":"execution-laptop-prepare","title":"Prepare the dedicated laptop without starting trading","project":"execution-quality","status":"ready","milestone":"Dedicated collection host","note":"User selected a Windows/WSL2 laptop on Nov 24. Main 45fb3f0 contains the tested stop/WSL support; prior research-PC preparations do not establish laptop readiness. No broker login, private transfer or Oracle stop during preparation.","acceptance":"Record actual supported hardware/OS, non-root WSL Docker access, clean reviewed main and green guardrails, successful pinned image build/download, and approved power/reboot behavior; explicitly list remaining private-state prerequisites and stop before broker startup.","reviewed":"2026-11-24","depends_on":[],"refs":["laptop-paper-handoff","portfolio-paper"]}
```

```taskboard-task
{"id":"execution-laptop-cutover","title":"Migrate paper collection while preserving Oracle","project":"execution-quality","status":"waiting","milestone":"Exclusive host cutover","note":"Await laptop preparation and separate operator approvals for source stop, private durable-state transfer and laptop gateway start. Nov 21 account/liveness statements are historical, not current readiness. Do not start a competing login or delete the VM.","acceptance":"Refresh exposure and preflights, verify approved source stop, privately restore complete consistent runtime state and credentials with ownership/integrity checks, then record approved laptop start and exclusive paper ownership with layered verification.","reviewed":"2026-11-24","depends_on":["execution-laptop-prepare"],"refs":["laptop-paper-handoff","gateway-evidence"]}
```

```taskboard-task
{"id":"execution-laptop-shakedown","title":"Observe three complete laptop collection sessions","project":"execution-quality","status":"waiting","milestone":"Forward collection readiness","note":"Not observed. Startup and resource headroom alone cannot close upstream/parity/reconciliation incidents. Preserve the existing five-symbol strategy and all risk caps.","acceptance":"Retain three consecutive complete sessions with warm-up, fresh/completed per-symbol data, reconciled positions/orders, durable matched fills/benchmarks and commissions, publication, EOD and cooldown/alerts; explain disruptions and exclusions without treating paper fills as real-money proof.","reviewed":"2026-11-24","depends_on":["execution-laptop-cutover"],"refs":["laptop-paper-handoff","gateway-evidence","portfolio-paper"]}
```

```taskboard-task
{"id":"execution-ten-symbol-review","title":"Review ten-symbol collection after the laptop shakedown","project":"execution-quality","status":"backlog","milestone":"Collection coverage decision","note":"Deferred until three complete sessions. No added tickers or changed capital/concurrency limits are selected by this handoff; same-day fills are correlated and five-position/headroom caps may prevent a higher count.","acceptance":"If selected, review symbol entitlements and provenance, version the exact collection universe/risk configuration, estimate constraints on fill frequency and preserve cohort separation without silently raising risk, leverage or accessing reserved research data.","reviewed":"2026-11-24","depends_on":["execution-laptop-shakedown"],"refs":["laptop-paper-handoff","portfolio-paper"]}
```

```taskboard-task
{"id":"execution-capture","title":"Implement portfolio paper capture and parity reports","project":"execution-quality","status":"done","milestone":"Instrumentation","note":"The portfolio plan records stages 1-5 implemented; the incident guide records deployed use. This is implementation evidence, not a clean sample.","acceptance":"Shared ruleset, portfolio risk, broker-side protection and daily parity/slippage capture are implemented with traceable order data.","reviewed":"2026-11-14","depends_on":[],"refs":["portfolio-paper","gateway-evidence"]}
```

```taskboard-task
{"id":"execution-sample-audit","title":"Audit matched fills, session completeness and exclusions","project":"execution-quality","status":"ready","milestone":"Usable sample","note":"Audit existing retained data without placing trades. Missing bars, outages, identity collisions and mismatched strategy revisions need explicit exclusions, not silent averaging.","acceptance":"Report eligible sessions and matched entry/stop fills with trigger benchmark, side, timestamps, revision, commissions, missingness and exclusion reasons.","reviewed":"2026-11-14","depends_on":["execution-capture"],"refs":["portfolio-paper","gateway-evidence"]}
```

```taskboard-task
{"id":"execution-collect","title":"Accumulate a reviewed paper-execution sample","project":"execution-quality","status":"waiting","milestone":"Forward observations","note":"Elapsed calendar days are not sufficient evidence. Agree review cadence and precision/coverage requirements; current collection quality is not measured by this board.","acceptance":"Retain usable sessions with parity checks and a documented adequacy assessment; label all fills as paper observations.","reviewed":"2026-11-14","depends_on":["execution-sample-audit","reliability-session"],"refs":["portfolio-paper","orb-results"]}
```

```taskboard-task
{"id":"execution-cost-review","title":"Compare slippage tails with the resting-stop cost model","project":"execution-quality","status":"backlog","milestone":"Cost-model decision","note":"Row 10 is sensitive to extra slippage. Separate entry and protective-stop fills, adverse tails and uncertainty rather than reporting only a mean.","acceptance":"Publish distribution and uncertainty by order role, assess model sensitivity, and record whether to retain, stress or reject the assumed costs; do not claim live-money validation.","reviewed":"2026-11-14","depends_on":["execution-collect"],"refs":["orb-results","portfolio-paper"]}
```
