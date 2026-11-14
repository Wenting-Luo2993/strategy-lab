<!-- taskboard
{"id":"execution-roadmap","title":"Execution-quality measurement roadmap","projects":["execution-quality"],"status":"current","kind":"roadmap","reviewed":"2026-11-14"}
-->
# Execution-quality measurement roadmap

The [portfolio paper plan](../trading-bot-mvp/2026-11-08-portfolio-paper-trading-plan.md)
defines the measurement intent. Its old rollout estimates are not current status.
Bars alone cannot measure fill slippage. Paper fills also do not establish
real-money queue position, liquidity or execution quality.

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
