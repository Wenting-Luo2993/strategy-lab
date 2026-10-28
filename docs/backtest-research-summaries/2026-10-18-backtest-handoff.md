# Backtest research handoff

**Updated:** 2026-10-28
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

---

## Execution roadmap from 2026-10-28

This is the active execution plan. It supersedes the short recommendation list
above without changing the recorded facts about the October 18 study.

### Plan review record — 2026-10-28

The roadmap was reviewed once against the current schema, publishing allowlist,
study-management CLI, reliability contracts, artifact sanitizer, and Git state.
The review corrected these issues:

- final-holdout reliability eligibility was described as architectural fact but
  is currently only an operator rule; Phase 0A now closes it in code;
- the publication phase referenced historical schema v12 even though the
  current schema is v14; migration ownership is now v15 for Phase 0A and v16
  (or the next available version) for Phase 1;
- the nested-study command incorrectly implied a policy JSON file was always
  required; default Policy v2 is valid and persisted without the flag;
- private trade/equity portability could not safely reuse the generic artifact
  sanitizer, which intentionally rejects ledgers and event-level shapes; the
  plan now requires a dedicated private run-detail contract;
- the branch-sync gate now records the actual one-commit-ahead/HTTP-403 push
  blocker instead of claiming the roadmap is already remote.

No change was made to the research conclusion: the adaptive ORB direction study
remains rejected, and the final holdout remains locked.

### Governing rules

These rules apply to every phase:

1. **Keep the final holdout locked.** Do not authorize or execute
   `manage_research_study.py final-holdout` unless a frozen methodology has
   already passed every registered development-stage reliability criterion and
   the user explicitly approves the one-time holdout read.
2. **Do not tune Policy v2 to rescue ORB.** The next study keeps the existing
   confidence, fold-consistency, degradation, neighborhood, cost-stress, and
   regime requirements unless a policy change is justified independently and
   registered before any new outcome is inspected.
3. **Do not call reused data fresh confirmation.** The completed direction
   study exposed its TEST outcomes. A study that chooses direction because of
   those outcomes and then reruns the same windows is exploratory follow-up,
   not independent confirmation.
4. **Separate the three decisions.** Record engine integrity, procedure
   completion, and research reliability independently. A completed study may
   be rejected; a positive point estimate may be inconclusive; an engine
   failure is not an economic result.
5. **Preserve negative evidence.** Rejected and inconclusive experiments remain
   immutable and publishable. Archive them from the active list when useful;
   do not delete them to make the history look cleaner.
6. **No secret or licensed-data shortcuts.** Never commit SQLite, market
   parquet, service-role credentials, or generated bytecode. Do not upload
   licensed market data or row-level research artifacts until entitlement,
   private storage, and access-control gates are approved.
7. **Every phase has a stop condition.** Do not proceed merely because the
   command exited successfully.
8. **Final-holdout eligibility must be code-enforced.** The roadmap requires a
   `RELIABLE` development verdict before authorization, but the current store
   checks only that a study is frozen. Phase 0A closes that safety gap before
   any final-holdout authorization can be considered.

### Roadmap status

| Lane | Current state | Meaning |
| --- | --- | --- |
| Deterministic engine and research architecture | **Implemented with one holdout-eligibility hardening gap** | The simulator, integrity contract, split/selection procedure, reliability policy, robustness composition, methodology freeze, exactly-once token consumption, persistence, and package boundaries exist and are tested. However, authorization does not yet enforce a persisted `RELIABLE` development verdict. |
| ORB research hypothesis | **Rejected under current adaptive design** | The ten-fold direction study had a positive mean but failed confidence/stability criteria. It is not eligible for final holdout. |
| Local inspection | **Operational** | SQLite remains authoritative; the local viewer can inspect parent experiments, criteria, retained trades, and equity where locally available. |
| Portable remote summaries | **Operational but incomplete** | 175 run summaries were published and reconciled, but the remote model cannot reconstruct parent studies or row-level evidence. |
| Full cross-device research model | **Not operational** | Parent experiments, memberships, reliability/robustness/regime records, and authenticated trade/equity artifacts still need publication and deployment. |
| Data realism calibration | **Incomplete by evidence** | Slippage needs calibration against live fills; point-in-time universe membership, delistings, and complete corporate actions are incomplete. |
| Browser E2E | **Desirable hardening** | Unit/server/adapter/build coverage exists; repeatable full browser workflows remain to be checked in. |

### Dependency order

```text
Phase 0: establish an authoritative working environment
  ├─> Phase 0A: enforce reliability eligibility for final holdout
  ├─> Phase 1: publish the parent-study/reliability read model
  │     └─> Phase 2: activate and validate remote experiment inspection
  └─> Phase 3: pre-register the next ORB question
        └─> Phase 4: prove the study setup without consuming new evidence
              └─> Phase 5: execute development-only evidence
                    ├─> rejected/inconclusive -> Phase 6A: stop or form a new hypothesis
                    └─> reliable -> Phase 6B: freeze and request holdout authorization
                          └─> Phase 7: authorized exactly-once final holdout

Phase 8 (artifact portability), Phase 9 (slippage/data calibration), and
Phase 10 (browser E2E) can proceed after Phase 0 without changing research
outcomes, but each has its own external gates.
```

## Phase 0 — establish the authoritative working environment

**Goal:** know which database, dataset, code revision, and remote state the next
action will use.

### Steps

1. Confirm the branch and remote synchronization:

   ```powershell
   git branch --show-current
   git fetch origin
   git rev-list --left-right --count HEAD...origin/wenting-luo2993-backtest-implementation-review
   git status --short
   ```

   Expected after publication: branch
   `wenting-luo2993-backtest-implementation-review`, divergence `0 0`, and no
   source/document changes. At the 2026-10-28 review, roadmap commit `dfaad59`
   is intentionally one commit ahead because the active GitHub credential is
   `wentingluo_microsoft`, which received HTTP 403 when pushing to
   `Wenting-Luo2993/strategy-lab`. Resolve the GitHub account/permission issue
   and push; do not rewrite or duplicate the commit. Generated `.pyc` changes
   are not evidence of source work and must not be committed.

2. Decide which SQLite database is authoritative on this device:
   - If the source-device database was securely transferred, set
     `RESEARCH__DB_PATH` to that copy.
   - If it was not transferred, treat Supabase as summary-only and do not claim
     access to the October 18 parent study, criteria, retained trades, or equity.
   - Never merge two SQLite files by copying tables manually.

3. Verify the authoritative database before any write:

   ```powershell
   python -c "import os, sqlite3; p=os.environ['RESEARCH__DB_PATH']; c=sqlite3.connect(p); print(c.execute('PRAGMA integrity_check').fetchone()[0]); print(c.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]); c.close()"
   ```

   Expected: `ok` and the current supported migration version. A version
   mismatch is a migration task, not permission to edit tables by hand.

4. Configure and verify market data explicitly:

   ```powershell
   $env:BACKTEST__DATA_DIR = '<absolute local parquet directory>'
   python -c "from vibe.backtester.data.paths import resolve_market_data_dir, available_symbols; print(resolve_market_data_dir()); print(available_symbols())"
   ```

   Do not rely on an accidental fallback into another checkout. Confirm QQQ
   coverage and hashes against the approved/candidate manifest before research.

5. Run the smallest environment smoke checks:

   ```powershell
   python -m pytest tests\unit\research_pipeline\test_methodology_controls.py tests\unit\research_pipeline\test_reliability.py tests\unit\backtester\test_engine_integrity.py -q
   npm --prefix tools\backtest-research-viewer run check
   ```

### Exit gate

Proceed only when the code revision, writable SQLite path, data directory,
schema version, and available symbol coverage are explicit and recorded in the
session. If the authoritative database is unavailable, Phases 1-2 may proceed
against an isolated fixture or new database, but they must not claim to migrate
or republish the October study.

## Phase 0A — enforce final-holdout eligibility in code

**Goal:** make the documented “reliable before holdout” rule a deterministic
store invariant rather than an operator convention.

### Current defect

`freeze_study()` verifies study identity and the pre-registered policy hash.
`authorize_final_holdout()` verifies only that a frozen definition exists.
Neither currently requires the experiment's persisted
`StudyReliabilityReport.verdict` to be `RELIABLE`. Exactly-once token claiming
works, but a rejected or inconclusive frozen study could still be authorized by
the current API.

### Steps

1. Add one core development-eligibility check used by both authorization and
   claim, while keeping authorization-state requirements separate:
   - experiment exists and is terminal;
   - immutable study reliability report exists;
   - engine-integrity evidence and the reliability procedure are complete;
   - report policy hash matches the experiment and frozen definition;
   - verdict is exactly `reliable`.
   Authorization additionally requires no prior authorization. Claim requires
   exactly one matching, unconsumed authorization whose recorded eligibility
   basis still matches. This preserves the state sequence
   `INELIGIBLE -> ELIGIBLE -> AUTHORIZED -> CLAIMED -> TERMINAL`.
2. Enforce the same rule in SQLite where practical so direct SQL or a future
   caller cannot bypass the Python check. Use a forward-only migration after
   schema v14; never edit an applied migration.
3. Make failure messages distinguish absent report, policy mismatch,
   non-reliable verdict, and already-authorized state.
4. Add regression tests proving `rejected`, `inconclusive`,
   `review_required`, missing-report, and policy-mismatch studies cannot be
   authorized or claimed. Preserve the existing concurrent exactly-once claim
   test for a reliable fixture.
5. Keep explicit user authorization as a second gate. A `RELIABLE` verdict
   makes a study eligible; it does not authorize access.

### Validation

```powershell
python -m pytest tests\unit\research_pipeline\test_methodology_controls.py tests\unit\research_pipeline\test_reliability.py tests\unit\research_pipeline\test_p11_registry_migration.py -q
```

### Exit gate

Phase 7 remains prohibited until the store refuses every non-reliable study at
both authorization and claim time. Existing frozen studies are not
retroactively promoted.

### Implementation record — 2026-10-19

Implemented schema v15 and the store-level guard on
`backtest-roadmap-implementation`:

- authorization and claim both fail closed unless the development experiment
  is `completed`, frozen, backed by a persisted `reliable` report, and that
  report records complete engine-integrity and procedure prerequisites;
- experiment, report, and frozen-definition policy hashes must agree;
- authorization records the policy hash, frozen-definition hash, and exact
  reliability-report payload hash that made the study eligible;
- claim re-evaluates research eligibility inside its `BEGIN IMMEDIATE`
  transaction and rejects missing or stale authorization evidence;
- SQLite triggers independently reject unsupported direct authorization or
  claim transitions;
- historical authorizations receive nullable v15 evidence columns and are not
  silently promoted into claimable authorizations.

Regression coverage includes every non-reliable verdict, missing reports,
running experiments, incomplete prerequisites, policy mismatch, stale report
evidence, direct-SQL authorization bypass, immutable records, and concurrent
exactly-once claiming.

The research-user consultant approved Phase 0A as the correct first safety
increment. Its required product distinction is now explicit: `RELIABLE` means
“eligible for explicit user-authorized final holdout,” not “profitable,”
“approved,” or already authorized. Phase 1 must publish separate development
reliability, holdout eligibility, authorization, claim, and result states
rather than collapsing them into one green status.

After reproducing a direct-SQL mutation of an authorization evidence hash, the
consultant temporarily rejected the exit gate. Schema v15 now replaces the
original transition guard and makes all three recorded eligibility-basis
fields immutable through `AUTHORIZED -> CLAIMED` and
`CLAIMED -> COMPLETED/INTERRUPTED`. Nine regression cases cover every field and
legal transition. With 34 focused tests and the complete 895-test
research-pipeline suite passing, the consultant issued final **APPROVE** with
no remaining Phase 0A blockers.

## Phase 1 — publish complete parent-study decision evidence

**Goal:** make the remote read model capable of representing what the local
experiment dialog already shows, without publishing prohibited event-level
data.

### Scope

Add versioned, immutable publication contracts for:

- `research_experiment` definition and procedure outcome;
- experiment/run membership with fold, candidate, and segment role;
- methodology-freeze identity and hashes;
- study reliability report and structured criterion rows;
- robustness summaries and scenario identities;
- causal regime summary rows;
- final-holdout status/decision metadata, without authorization tokens;
- archive visibility state.

Do **not** include trades, fills, raw bars, equity points, credentials, local
paths, or authorization tokens in these payloads.

### Steps

1. Add local schema v16 (Phase 0A owns v15) and the corresponding remote
   schema/RLS migration plus typed publication payload contract. Use new event
   types and idempotency keys; do not overload `run_summary`. If another
   migration lands first, use the next available version; never reuse a
   version or edit schema v12-v15 migrations that may already be applied.
2. Add payload construction from immutable SQLite records.
3. Extend the recursive prohibited-key tests so the new payloads cannot contain
   event-level data or secrets.
4. Enqueue parent-study publications transactionally when their immutable
   decision records are finalized. Existing terminal studies need an explicit,
   deterministic backfill command rather than implicit startup mutation.
5. Extend reconciliation to identify missing, divergent, and orphaned parent
   experiments, memberships, policies, criteria, robustness, and regime rows.
6. Extend TypeScript types and decoders. Unknown versions must fail visibly;
   missing historical fields must render `Not assessed`, never pass.
7. Update the remote experiment list/detail UI to consume these records.
8. Add fixture, decoder, adapter, pagination, redaction, and reconciliation
   tests before any deployment.

### Validation

At minimum:

```powershell
python -m pytest tests\unit\research_pipeline\test_p12_publishing.py tests\unit\research_pipeline\test_p11_registry_migration.py tests\unit\research_pipeline\test_reliability.py tests\unit\research_pipeline\test_robustness_v1.py -q
npm --prefix apps\operational-metrics-dashboard test
npm --prefix apps\operational-metrics-dashboard run build
```

Also test a rejected, reliable, inconclusive, interrupted, archived, and legacy
`Not assessed` fixture. Add a dedicated publication-migration test for the
version allocated above rather than extending the historical schema-v12
fixture.

### Exit gate

Do not deploy until an independently decoded remote fixture reproduces the
local parent experiment's identity, fold membership, policy hash, reliability
verdict, failed criteria, and robustness/regime summaries without prohibited
rows or secrets.

## Phase 2 — deploy and validate remote experiment inspection

**Goal:** make cross-device experiment review operational, not merely coded.

### External prerequisites

- Intended Supabase project confirmed.
- Owner approval for the schema/read-model activation.
- Dedicated server-side publisher credential provisioned.
- Anonymous/browser role separated from the service role.
- RLS and RPC changes reviewed independently.

### Steps

1. Apply the reviewed schema, indexes, RLS, and bounded RPC changes.
2. Configure the production dashboard with the anonymous URL/key and
   `NEXT_PUBLIC_RESEARCH_DATA_SOURCE=supabase`. Never expose the service role.
3. Run publisher status before flushing:

   ```powershell
   python scripts\publish_research.py status
   ```

4. Backfill immutable parent-study decision records.
5. Flush in bounded batches and reconcile:

   ```powershell
   python scripts\publish_research.py flush
   python scripts\publish_research.py reconcile
   ```

6. Compare the October direction experiment locally and remotely:
   - same experiment ID and title;
   - 130 memberships;
   - same frozen definition/policy hashes;
   - `REJECTED` verdict;
   - same failed criteria and statistical confidence;
   - same robustness and regime summaries;
   - no implied final-holdout promotion.
7. Validate multi-page reads, filters, stable detail URLs, response sizes, query
   plans, timeouts, CSP origins, and anonymous denial of private artifacts.
8. Simulate interrupted publication and reconciliation recovery.

### Exit gate

Remote inspection becomes an accepted portable read model only when
reconciliation reports zero missing/divergent/orphaned records and a second
device reproduces the local study-level decision. SQLite remains execution
authority even after this gate passes.

## Phase 3 — pre-register the next ORB research question

**Goal:** define a narrower experiment without using the final holdout or
retroactively optimizing the policy.

### Required user decision

Choose one fixed direction **before execution**:

- **Symmetric control (recommended for the cleanest methodology):** retains the
  production baseline and does not adopt long-only because of inspected TEST
  outcomes.
- **Long-only hypothesis:** acceptable only with a written economic rationale
  that stands independently of “it looked better in the rejected study.”

Do not include both directions in the candidate grid. The question is duration
stability under one fixed direction, not another adaptive direction search.

### Proposed registered design

- Symbol: QQQ.
- Direction: one fixed user-approved value.
- Candidate duration grid: `5 / 15 / 30` minutes. Three values are retained
  because neighborhood stability cannot be assessed from one isolated point.
- Reliability policy: immutable Policy v2 defaults.
- Execution/metric identities: current versions.
- Warmup: 50 sessions.
- Train/validation/test windows: retain `252 / 63 / 63` and 63-session step
  unless untouched data coverage forces a documented redesign.
- Recording: all roles, not `--record-test-only`, so selection and failures
  remain inspectable.
- Final holdout: the existing 2025-01-02 through 2026-04-27 lock, unread.
- Regime labels: reporting evidence only, not filters or candidate axes.

### Fresh-evidence gate

Inventory every session already exposed as TRAIN, VALIDATION, or TEST in prior
studies. Classify the proposed data as:

1. **Untouched development evidence:** may support a confirmatory claim.
2. **Previously used TRAIN/VALIDATION only:** may support additional
   development, with reuse disclosed.
3. **Previously inspected TEST evidence:** exploratory only; cannot be called
   independent confirmation.
4. **Locked final holdout:** prohibited.

The October direction study ends on 2024-10-10 while the development lock ends
2024-12-31, but the remaining interval alone is too short for the proposed
multi-fold design. If no sufficient untouched historical data exists outside
the inspected windows, choose explicitly between:

- waiting for new forward data;
- adding an approved, previously unused symbol/universe with proper
  point-in-time limitations;
- running an exploratory fixed-direction analysis labelled as reused evidence.

Do not silently promote option three into “confirmation.”

### Artifacts to create before execution

1. A concise hypothesis statement and economic rationale.
2. An optional Reliability Policy v2 JSON file only when explicitly pinning or
   overriding defaults; no outcome-driven overrides. The experiment persists
   the effective policy and hash even when the CLI flag is omitted.
3. A ruleset with fixed `strategy.direction_mode`.
4. The exact candidate axis and split dates.
5. A data-reuse inventory identifying previously exposed sessions.
6. A frozen-study definition draft.

### Exit gate

No research run starts until the user has approved the fixed direction and the
study is labelled honestly as confirmatory or exploratory based on data reuse.

## Phase 4 — prove the study setup without consuming evidence

**Goal:** catch configuration and persistence defects before a long run.

### Steps

1. Run unit tests for the chosen direction/ruleset path, axis mutation,
   split planning, engine integrity, experiment recording, reliability, and
   robustness.
2. Generate the split plan and review:
   - fold count;
   - exact session boundaries;
   - no overlap;
   - purge/embargo/warmup;
   - no final-holdout session.
3. Verify candidate IDs and effective rulesets differ only by
   `orb_duration_minutes`; direction must be constant.
4. Run an isolated synthetic or deliberately non-evidentiary smoke study in a
   temporary database. Do not use `--skip-leakage` as evidence.
5. Inspect the experiment dialog:
   - all expected candidates in every fold;
   - validation selection and frozen test agree;
   - all run links resolve;
   - integrity/reliability states are distinct;
   - missing evidence does not look green.
6. Verify no row was written to the authoritative database during temporary
   testing.

### Exit gate

Candidate count, fold count, role count, effective rulesets, and persisted
membership must reconcile exactly before using real development data.

## Phase 5 — execute the development-only study

**Goal:** produce immutable evidence under the pre-registered design.

### Command template

Values in angle brackets must be taken from the approved registration, not
chosen interactively after results begin:

```powershell
$env:RESEARCH__DB_PATH = '<authoritative research.sqlite3>'
$env:BACKTEST__DATA_DIR = '<approved parquet directory>'

python scripts\run_nested_walk_forward.py `
  --symbol QQQ `
  --ruleset '<fixed-direction ruleset.yaml>' `
  --dev-start <YYYY-MM-DD> `
  --dev-end <YYYY-MM-DD> `
  --oos-start 2025-01-02 `
  --oos-end 2026-04-27 `
  --train-sessions 252 `
  --validation-sessions 63 `
  --test-sessions 63 `
  --step-sessions 63 `
  --warmup-sessions 50 `
  --axis orb_minutes=strategy.orb_duration_minutes:5,15,30 `
  --record-runs `
  --experiment-title '<registered title>'
```

`--oos-start`/`--oos-end` declare the reserved final holdout to the planner;
this command must not evaluate that segment.

If the approved registration uses an explicit policy file, append:

```powershell
  --reliability-policy '<registered policy-v2.json>'
```

Otherwise omit the flag and record the effective default Policy v2 hash emitted
by the experiment. Do not create a nominal policy file after seeing outcomes.

### During execution

1. Capture the exact command, commit SHA, database path, data-manifest identity,
   start time, and experiment ID.
2. Keep INFO logging bounded. Do not enable `--verbose` for a full study unless
   diagnosing a specific problem.
3. A recording failure must remain visible but must not mutate the research
   result. Investigate any incomplete membership before interpreting the study.
4. Do not inspect partial fold outcomes to alter the running configuration.
5. If interrupted, preserve the interrupted experiment and rerun as a new
   experiment identity after fixing the cause.

### Post-run reconciliation

Verify:

- SQLite `PRAGMA integrity_check = ok`;
- every declared fold reached a terminal procedure state;
- exact expected candidate/run membership;
- every frozen TEST run has engine integrity `PASS`;
- leakage/data-integrity evidence exists;
- stitched OOS includes all and only completed non-overlapping TEST folds;
- reliability policy hash equals the registered hash;
- robustness and regime evidence are present;
- retained rows reproduce published checksums;
- no final-holdout access was recorded.

Open the local viewer and manually reconcile headline expectancy, trade count,
fold outcomes, selected settings, failed criteria, and at least one retained
TEST ledger/equity curve.

### Exit gate

The study result is accepted as recorded evidence only after persistence,
membership, integrity, and decision reports reconcile. This gate says the
evidence is trustworthy—not that the strategy passed.

## Phase 6 — act on the deterministic verdict

### 6A — `REJECTED`

1. Record which dispositive criteria failed.
2. Preserve and publish the negative study.
3. Do not run the final holdout.
4. Decide whether the underlying economic hypothesis is rejected or whether a
   genuinely new hypothesis exists.
5. Any new hypothesis must start a new pre-registration cycle. It may not add
   filters solely because they explain this study's TEST losses.

If the fixed-direction, three-duration design is again unstable, the default
recommendation is to stop ORB parameter research rather than continue mining.

### 6B — `INCONCLUSIVE`

1. Distinguish missing evidence from broad uncertainty.
2. Repair missing plumbing only when the evidence was not captured correctly.
3. If the sample is simply insufficient, wait for or acquire genuinely new
   development evidence; do not lower sufficiency/confidence thresholds after
   seeing the result.
4. Keep the final holdout locked.

### 6C — `REVIEW_REQUIRED`

1. Inspect the exact observed values, expected bounds, and context.
2. Resolve data/engine anomalies as engineering defects.
3. For plausible but unusual outcomes, document the review without converting
   it to pass.
4. Rerun only if the original evidence is invalidated by a concrete defect.

### 6D — `RELIABLE`

1. Reconcile every passing criterion and manually review engine integrity,
   retained TEST rows, and methodology identity.
2. Freeze the exact study definition:

   ```powershell
   python scripts\manage_research_study.py --db $env:RESEARCH__DB_PATH `
     freeze <experiment-id> <frozen-definition.json> --frozen-by Bloom
   ```

3. Present the frozen definition, reliability report, and one-time acceptance
   rule to the user.
4. Verify Phase 0A is deployed and the store independently reports the study
   eligible for authorization.
5. Stop and request explicit authorization. Reliability does not itself grant
   permission to read the final holdout.

## Phase 7 — final holdout, only after explicit authorization

**This phase is intentionally dormant.**

If and only if Phase 6D completed and the user approves:

1. Generate a unique authorization token outside the repository.
2. Record attributed authorization with a specific reason:

   ```powershell
   python scripts\manage_research_study.py --db $env:RESEARCH__DB_PATH `
     authorize-final-holdout <experiment-id> `
     --authorization-token '<secret token>' `
     --requested-by Bloom `
     --reason '<user-approved reason>'
   ```

3. Execute exactly once with the frozen ruleset:

   ```powershell
   python scripts\manage_research_study.py --db $env:RESEARCH__DB_PATH `
     final-holdout <experiment-id> `
     --ruleset '<frozen ruleset.yaml>' `
     --data-dir $env:BACKTEST__DATA_DIR `
     --authorization-token '<same secret token>'
   ```

4. Persist and publish the final `PASSED`, `FAILED`, or `INCONCLUSIVE` verdict.
5. Reconciliation must show one access claim and one terminal result. Never
   rerun a failed economic outcome; rerun only if the recorded attempt is
   formally invalidated by an execution/infrastructure failure under the
   existing exactly-once rules.

## Phase 8 — private trade/equity portability

**Goal:** allow authorized cross-device drill-down without weakening the safe
summary allowlist.

### Steps

1. Confirm that derived trades/equity may be privately stored under the intended
   entitlement and organizational policy.
2. Define a **dedicated private run-detail artifact contract and destination**.
   The existing generic artifact sanitizer deliberately rejects trade/ledger
   names and event-level keys; do not weaken it or the summary publication
   allowlist. The new contract must accept only the canonical retained trade
   and equity shape and must reject raw bars, fills, orders, credentials, and
   arbitrary files.
3. Deploy the private `research-artifacts` bucket and signed-preview function.
4. Keep anonymous access denied.
5. Publish one compressed, versioned object per run containing retained trade
   and equity detail—never raw market bars.
6. Bind the object to run identity and existing
   `trade_ledger_sha256`/`equity_curve_sha256`.
7. On download, verify size, content type, run identity, sequence completeness,
   and both hashes before rendering.
8. Add interrupted upload, stale claim, duplicate delivery, corrupted object,
   expired signed URL, and unauthorized access tests.
9. Add retention and explicit deletion policy without deleting the immutable
   run summary or decision history.

### Exit gate

A second device must reproduce the same retained trade/equity checksums and
viewer detail while an anonymous client remains unable to enumerate or fetch
objects.

## Phase 9 — calibration and data-quality lane

These tasks improve claims but are not prerequisites for the next single-symbol
development study.

### Slippage calibration

1. Define a privacy-safe live-fill evidence format.
2. Collect enough fills across spread, volatility, size, and time-of-day bands.
3. Compare modeled versus observed entry/exit slippage without tuning on P&L.
4. Register a new execution-model version for any approved calibration change.
5. Re-freeze goldens and rerun relevant studies; never silently reinterpret old
   runs under new execution assumptions.

### Point-in-time universe and corporate actions

1. Select a licensed source for membership, delistings, splits, dividends, and
   symbol changes.
2. Create immutable, dated universe/corporate-action manifests.
3. Make portfolio/cross-sectional research fail closed when required history is
   unavailable.
4. Retain survivorship badges on static historical universes.
5. Validate pooled/per-symbol results and shared-capital accounting after
   integration.

## Phase 10 — repeatable browser E2E

**Goal:** convert the manual viewer checks into a checked-in regression suite.

Cover:

- experiment list and 90%-viewport dialog;
- fold navigation and candidate matrix;
- nested run dialog preserving parent scroll/open folds;
- reliability explanations and archived-study filtering;
- retained trade/equity loading and checksum/error boundaries;
- narrow viewport without document-level overflow;
- remote fixture and Supabase-adapter study parity;
- rejected/inconclusive/review/reliable and legacy `Not assessed` states;
- keyboard navigation and Escape closing only the topmost dialog.

Use an isolated fixture database and deterministic browser viewport. Do not
depend on the owner's real SQLite file or running local services.

## Documentation and execution discipline

For every completed phase:

1. Update this section with date, commit, commands, test counts, produced IDs,
   and deviations from plan.
2. Update the September implementation plan only when roadmap status changes.
3. Create a focused analysis document for a substantive research conclusion;
   do not bury the conclusion in a commit message.
4. Record decisions before implementation when they affect methodology,
   licensing, security, retention, or final-holdout access.
5. Commit and push code plus documentation together, excluding generated
   caches and device-local evidence.

### Immediate next action

The immediate operational step is **Phase 0**, including resolving the push
credential. The first safety coding increment is **Phase 0A**, because the
currently documented holdout-eligibility rule is not yet enforced by the store.
The next product increment is **Phase 1: parent-study and reliability
publication**, which closes the largest cross-device architecture gap without
consuming research evidence. In parallel, the next user decision is the fixed
direction and honest evidence classification in **Phase 3**. No new ORB study
should occur before that decision is recorded, and no final-holdout action may
occur before Phase 0A is complete.
