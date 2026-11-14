<!-- taskboard
{"id":"orb-roadmap","title":"ORB research roadmap","projects":["orb-research"],"status":"current","kind":"roadmap","reviewed":"2026-11-14"}
-->
# ORB research roadmap

This is the work index, not a replacement for the [research plan](../backtest-research-summaries/2026-10-31-orb-edge-research-plan.md)
or the [living results](../backtest-research-summaries/orb-exploration-results.md).
The seed reflects repository evidence reviewed November 14, not a fresh cloud-session audit.
Completed experiments can have negative or inconclusive conclusions.

```taskboard-task
{"id":"orb-exploration-baseline","title":"Record baseline, condition and entry exploration findings","project":"orb-research","status":"done","milestone":"Exploration evidence","note":"Rows 0-11 are summarized. Resting-stop row 10 is promising but cost-sensitive; completion does not mean a proven edge.","acceptance":"Experiment identities, costs, uncertainty and conclusions are recorded in the living results.","reviewed":"2026-11-14","depends_on":[],"refs":["orb-results"]}
```

```taskboard-task
{"id":"orb-next-exploration","title":"Choose and register the next bounded exploration","project":"orb-research","status":"review","milestone":"Further exploration","note":"Candidate questions include a same-entry-cutoff time-exit comparison and prior-session intraday trend. They are options, not approved runs; avoid another unbounded parameter search.","acceptance":"Choose a mechanism, exploration-only data window, trial budget and stopping rule; record why it is worth testing.","reviewed":"2026-11-14","depends_on":["orb-exploration-baseline"],"refs":["orb-results","orb-plan"]}
```

```taskboard-task
{"id":"orb-confirmation-design","title":"Freeze hypotheses and confirmation decision rules","project":"orb-research","status":"waiting","milestone":"Confirmation design","note":"No usable edge is established. Cost-model evidence and a justified hypothesis must precede reading the confirmation set.","acceptance":"Predeclare hypothesis, meaningful effect size, power, fixed variants and reject/inconclusive rules before inspecting confirmation results.","reviewed":"2026-11-14","depends_on":["execution-cost-review"],"refs":["orb-plan","orb-results"]}
```

```taskboard-task
{"id":"orb-confirmation","title":"Run the registered confirmation studies","project":"orb-research","status":"backlog","milestone":"Independent confirmation","note":"The reserved 25-stock confirmation universe is not another exploration playground. Recheck current locks and prior usage before any authorized run.","acceptance":"Run only the registered studies and record outcomes without selecting new rules on confirmation results.","reviewed":"2026-11-14","depends_on":["orb-confirmation-design"],"refs":["orb-plan"]}
```

```taskboard-task
{"id":"orb-holdout","title":"Stress survivors before the one-time final holdout","project":"orb-research","status":"backlog","milestone":"Robustness and holdout","note":"A later gate, not permission to access locked data. Stop if no rule survives confirmation.","acceptance":"Document robustness and predeclared holdout acceptance; obtain explicit authorization before the one-time final evaluation.","reviewed":"2026-11-14","depends_on":["orb-confirmation"],"refs":["orb-plan","research-north-star"]}
```
