<!-- taskboard
{"id":"orb-roadmap","title":"ORB research roadmap","projects":["orb-research"],"status":"current","kind":"roadmap","reviewed":"2026-11-14"}
-->
# ORB research roadmap

This is the work index, not a replacement for the [research plan](../backtest-research-summaries/2026-10-31-orb-edge-research-plan.md)
or the [living results](../backtest-research-summaries/orb-exploration-results.md).
The seed reflects repository evidence reviewed November 14, not a fresh cloud-session audit.
Completed experiments can have negative or inconclusive conclusions.

```taskboard-task
{"id":"orb-exploration-baseline","title":"Record baseline, condition and entry exploration findings","project":"orb-research","status":"done","milestone":"Exploration evidence","note":"Rows 0-12 are summarized. Resting-stop row 10 and fixed-stack row 12 have small positive estimates but remain tail/cost-sensitive; completion does not mean a proven edge.","acceptance":"Experiment identities, costs, uncertainty and conclusions are recorded in the living results.","reviewed":"2026-11-14","depends_on":[],"refs":["orb-results"]}
```

```taskboard-task
{"id":"orb-next-exploration","title":"Choose and register the next bounded exploration","project":"orb-research","status":"done","milestone":"Further exploration","note":"User approved one fixed 15m close/RVOL/VWAP/EMA/ATR entry stack. Registration orb_entry_stack_15m_exploratory_v1.json freezes rules, five exploration symbols, 2018–2024, one trial and no tuning. This is an article-entry interpretation, not its full exit replication.","acceptance":"Choose a mechanism, exploration-only data window, trial budget and stopping rule; record why it is worth testing.","reviewed":"2026-11-14","depends_on":["orb-exploration-baseline"],"refs":["orb-results","orb-plan"]}
```

```taskboard-task
{"id":"orb-entry-stack-experiment","title":"Implement and evaluate the fixed 15m entry-confirmation stack","project":"orb-research","status":"done","milestone":"Further exploration","note":"Experiment 01a1db6f-6ada-75f0-85f9-3f8640d084eb recorded locally: 3,956 trades, +0.035R [0.004,0.066], underpowered versus +0.05R; best-20-day removal leaves +0.003R. All five engine-integrity reports pass. Indicators/guardrails implemented; no tuning, publication, confirmation/holdout or production changes.","acceptance":"Record all five symbol runs, pooled uncertainty, frequency, stability, tails, costs, gate diagnostics and exact provenance; update the living findings without tuning thresholds.","reviewed":"2026-11-14","depends_on":["orb-next-exploration"],"refs":["orb-plan","orb-results"]}
```

```taskboard-task
{"id":"orb-entry-stack-year-attribution","title":"Explain the frozen stack's 2020/2022 profit concentration","project":"orb-research","status":"done","milestone":"Mechanism attribution","note":"Read-only attribution of row 12 is documented: 2020/2022 supply 91.49% of net dollars and 85.03% of net R, with long/short dominance switching. Both years remain tail-dependent; no causal regime filter or buy-and-hold complementarity is established. No new strategy trial or reserved-data access.","acceptance":"Preserve annual counts/expectancy/uncertainty/PF/dollars, direction and timing/frequency attribution, best-day/year exclusions, source reconciliation and diagnostic limitations in the living results.","reviewed":"2026-11-14","depends_on":["orb-entry-stack-experiment"],"refs":["orb-results","orb-plan"]}
```

```taskboard-task
{"id":"orb-movement-noise-design","title":"Review and register a movement-to-noise/persistence study","project":"orb-research","status":"review","milestone":"Potential regime exploration","note":"A documented hypothesis, not a selected filter: movement relative to friction plus directional persistence known before entry may distinguish useful ORB opportunities. User-selected goal is positive net returns, less drawdown, limited overnight exposure and complementing buy-and-hold. Measurements, windows/cutoffs, risk basis, trial budget and execution approval remain open.","acceptance":"Approve a bounded protocol and freeze causal feature availability/warm-up, exact comparisons, meaningful-effect and drawdown/complementarity criteria, costs, trial counting and stopping rules before outcome inspection; distinguish reused-data robustness from genuine confirmation.","reviewed":"2026-11-14","depends_on":["orb-entry-stack-year-attribution"],"refs":["orb-plan","orb-results","research-north-star"]}
```

```taskboard-task
{"id":"orb-movement-noise-exploration","title":"Evaluate the registered movement-to-noise/persistence hypothesis","project":"orb-research","status":"backlog","milestone":"Potential regime exploration","note":"Potential next experiment only; not implemented or run. Start only after protocol registration and explicit execution authorization. Use the five reused exploration symbols and 2018-2024, including unfavorable periods; never choose a filter from 2020/2022 alone. Confirmation/final holdout remain locked.","acceptance":"Report predeclared causal attribution across years/sides/tickers, uncertainty, costs/tails and all-session opportunity rates; if separately authorized, compare fixed executable gates with an unchanged-stack control and the declared risk/complementarity objective, preserving negative/inconclusive outcomes.","reviewed":"2026-11-14","depends_on":["orb-movement-noise-design"],"refs":["orb-plan","orb-results","research-north-star"]}
```

```taskboard-task
{"id":"orb-performance-dashboard","title":"Show experiment performance tables and actual equity curves on both research surfaces","project":"orb-research","status":"in_progress","milestone":"Research presentation","note":"Local and hosted code now includes year shortcuts and a 2x2 daily comparison: independent ticker buy-and-hold benchmarks, cumulative R, explicitly reconstructed closed-trade equity from complete exit-session net P&L, and drawdown in R. Curves rebase to recorded capital in the selected window; ticker toggles hide/show members. Original intraday equity remains unretained and is not fabricated. Row 12 exports all five price/closed-equity daily series without rerunning strategy or changing evidence. Flexible workspace removed. Hosted schema application, aggregate publication and release still pending.","acceptance":"Show expectancy/CI, equity Sharpe, drawdown metrics, net-dollar profit factor and provenance-labelled equity curves per experiment; preserve immutable evidence and anonymous/private-artifact boundaries on the hosted dashboard.","reviewed":"2026-11-14","depends_on":["orb-entry-stack-experiment"],"refs":["orb-plan","orb-results"]}
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
