# Backtest research handoff

**Updated:** 2026-10-18  
**Branch:** `wenting-luo2993-backtest-implementation-review`  
**Researcher:** Bloom  
**Current focus:** Preserve the completed ORB direction study, interpret its
rejected reliability verdict correctly, and continue with a narrower
confirmatory experiment without reading the locked final holdout.

## Start here on the other device

Fetch and check out the branch above. Read these documents in order:

1. This handoff.
2. `2026-10-18-orb-direction-statistical-reliability-v2.md`.
3. `2026-10-18-engine-integrity-reliability-and-hypothesis-analysis.md`.
4. `2026-10-17-backtest-decision-boundaries-and-cleanup-plan.md`.
5. `2026-09-09-backtest-pipeline-implementation-plan.md` for the historical
   implementation record.

The Git branch transfers code and narrative conclusions. It does **not**
transfer the local SQLite database, retained trade/equity rows, market parquet,
running viewer, or terminal history.

## Current implementation state

The deterministic decision system now has both intended boundaries:

- **Did the engine run correctly?** Engine-integrity reports live in
  `vibe/backtester/core/integrity.py` and are required for frozen TEST runs.
- **Is the result reliable and stable?** Reliability Policy v2 lives in
  `vibe/research_pipeline/reliability.py` and combines statistical confidence,
  sample sufficiency, fold transfer, parameter stability, cost stress,
  neighborhood robustness, causal regime evidence, and review gates.

Policy v2 adds a 95% Student-t confidence interval, t-statistic, standard error,
one-sided evidence confidence above the registered threshold, one-sided
p-value, seeded-bootstrap interval, and stored verdict reasoning. Policy v1
remains replayable with its original hash.

The local research viewer now:

- hides explicitly archived calibration studies;
- uses compact study and statistical-confidence tables;
- explains each reliability criterion through Fluent UI information callouts;
- left-aligns experiment names;
- distinguishes diagnostic cash P&L from the R-based promotion metric;
- renders human-readable verdict reasoning, including legacy persisted
  criterion IDs.

The ORB strategy supports the registered `symmetric`, `long_only`, and
`short_only` direction modes. Causal regime reporting uses only prior-session
information and keeps regimes outside the candidate grid.

## Completed ORB direction experiment

**Experiment ID:** `01a151f2-d815-7f0f-86b0-cba0f586413e`  
**Title:** `ORB direction comparison - statistical reliability v2`

Design:

| Item | Value |
| --- | --- |
| Symbol | QQQ |
| Candidate grid | 5/15/30-minute ORB × symmetric/long-only |
| Folds | 10 |
| Per-fold windows | 252 TRAIN / 63 VALIDATION / 63 TEST sessions |
| Step | 63 sessions |
| Warmup | 50 sessions |
| Recorded segment runs | 130 |
| Stitched TEST trades | 502 |
| Development evidence end | 2024-10-10 |
| Final holdout | 2025-01-02 through 2026-04-27, reserved and unread |

Headline result:

| Measure | Result |
| --- | ---: |
| Mean TEST expectancy | `+0.0749007R` |
| t-statistic versus `0R` | `1.0863` |
| Evidence confidence above `0R` | 86.1% |
| 95% Student-t interval | `[-0.0606R, +0.2104R]` |
| Seeded-bootstrap interval | `[-0.0492R, +0.2071R]` |
| Positive TEST folds | 5/10 |
| Validation-to-TEST degradation | `0.2972R` |
| Overall reliability verdict | **REJECTED** |

The positive point estimate is promising but statistically inconclusive. The
overall verdict is rejected because fold consistency, validation-to-TEST
degradation, and parameter-neighborhood stability failed. Cost stress and
causal regime coverage passed.

Long-only was selected in 7/10 folds. At each fold's selected duration, the
descriptive paired direction evidence favored long-only in 6/10 folds with a
mean difference of only `+0.0351R`; its approximate 95% paired interval
`[-0.105R, +0.176R]` crosses zero. This does not establish a reliable long-only
advantage.

See `2026-10-18-orb-direction-statistical-reliability-v2.md` for the complete
fold, robustness, regime, and statistical interpretation.

## Supabase publication decision

Publish the 130 completed runs as **immutable research evidence**, including
their rejected/unsuccessful outcomes. Do not describe that publication as
strategy promotion:

- The adaptive ORB direction procedure is not eligible for freeze or final
  holdout use.
- Publishing evidence makes the run summaries available across devices and
  preserves negative results against selective reporting.
- The local SQLite database remains authoritative for execution evidence.

Important current limitation: the Supabase publication contract supports only
`run_summary` and `registry_record`. It does not yet publish
`research_experiments`, experiment/run membership, study reliability reports,
criterion rows, robustness evidence, regime observations, or retained
trade/equity detail. The 130 queued run payloads therefore carry
`research_reliability: not_assessed` even though the parent study has a
persisted **REJECTED** verdict.

Publication completed on the source device on 2026-10-18. The existing
dashboard Supabase URL and service key were mapped in memory to the publisher's
expected variable names for this one operation; no secret value was copied into
the repository.

| Publication item | State |
| --- | ---: |
| Direction-study run summaries published | 130 |
| Other run summaries published | 45 |
| Total publication outbox | 175 published |
| Reconciliation | 175 matched; 0 missing/divergent/orphaned |
| Failed/dead-letter rows | 0 |

For future publication, configure the publisher-specific names shown in
`deploy/ibkr-docker/secrets/trading-bot.env.example`:

```text
RESEARCH_SUPABASE_URL
RESEARCH_SUPABASE_SERVICE_ROLE_KEY
```

Keep the research service role in a trusted local environment and separate from
browser/anonymous credentials. `scripts/publish_research.py` loads the
repository-root `.env`; the deploy secret file is not loaded automatically, so
export those two variables into the invoking shell or use an explicitly trusted
secret-loading wrapper. Then:

```powershell
python scripts\publish_research.py status
python scripts\publish_research.py flush
python scripts\publish_research.py reconcile
```

The flush publishes every ready outbox row, not only one experiment. Always run
`status` before future flushes.

For full-fidelity continuation, securely transfer the source device's local
SQLite database separately. Supabase alone cannot reconstruct the parent
experiment or retained TEST trades/equity:

```text
C:\Users\kiml2\AppData\Local\strategy-lab\research.sqlite3
```

Do not commit that database, market parquet, credentials, or generated Python
bytecode to Git.

## Local viewer

On the source device, the viewer snapshot is:

```text
tmp\research-viewer.sqlite3
```

Start the viewer with:

```powershell
node tools\backtest-research-viewer\server.mjs `
  --db tmp\research-viewer.sqlite3 `
  --port 4178
```

The snapshot must be recopied from the authoritative local database after new
experiments. The viewer process and snapshot are local conveniences and do not
transfer through Git.

## Archived calibration experiments

These studies remain immutable in SQLite but are hidden from the active viewer:

- `01a14c4a-0082-7a43-8fb2-529c24d49850`
- `01a14c50-89ba-774f-8e08-dc0af0698857`
- `01a1517c-29f0-765b-bd83-5f5ec7ff23c9`

The prior full-decision-evidence duration study remains active:

- `01a1517f-9e61-70e2-8e9b-5e83eba78711`

Archiving is deliberate; do not physically delete immutable evidence.

## Validation completed

Before the real experiment:

- 89 targeted Python tests passed.
- 42 viewer tests passed.
- Viewer JavaScript syntax checks passed.
- Policy v1 hash compatibility was verified.

After the final wording/viewer changes:

- 935 research-pipeline, direction-mode, and ruleset tests passed; one
  Windows-only symlink test was skipped because symlink creation is unavailable
  on this host.
- 43 viewer tests passed.
- Viewer JavaScript syntax and `git diff --check` passed.

The real experiment completed all ten folds, persisted all 130 expected linked
runs, recorded passing engine-integrity reports, and returned SQLite
`integrity_check = ok`.

## Logging issue fixed

The direction experiment emitted approximately 540 MB of output because ORB
timestamp and level-calculation diagnostics logged once per bar at INFO. Those
messages have been moved to DEBUG for future runs. The completed experiment was
not stuck; it exited successfully.

## Recommended continuation

1. Securely transfer the authoritative SQLite database if the work computer
   needs full experiment, trade, and equity inspection.
2. Treat the 175 matched Supabase summaries as a portable read model, not as a
   complete experiment backup or strategy promotion.
3. Extend the remote publication/read model for parent experiments and study
   reliability before relying on Supabase as the sole cross-device research
   store.
4. Keep the final holdout locked.
5. Register a narrower confirmatory experiment with fixed direction and a small
   predeclared duration set. Do not add regime filters or expand the candidate
   grid based on the completed TEST outcomes.
6. Retain the Policy v2 requirements: confidence interval wholly above `0R`,
   at least 60% positive folds, degradation no greater than `0.25R`, stable
   neighbors, and positive cost-stress expectancy.

If the simpler fixed rule remains unstable in new development windows, reject
the hypothesis rather than iteratively mining more filters from these TEST
results.
