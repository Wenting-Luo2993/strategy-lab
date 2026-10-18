# Backtest pipeline and viewer handoff

> **Latest architecture and remaining-work plan:** see
> [2026-10-17-backtest-decision-boundaries-and-cleanup-plan.md](2026-10-17-backtest-decision-boundaries-and-cleanup-plan.md).

**Updated:** 2026-10-16, after the experiment-dialog change.
**Branch:** `wenting-luo2993-backtest-implementation-review`
**Current focus:** trustworthy local inspection of controlled backtest experiments,
not tuning strategy profitability.

## Start here on another device

Fetch and check out the branch above, preserving any unrelated local work.
Read this handoff before treating older audit findings as current defects.
The coding agent's conversation, running servers, and local databases do not
transfer with Git; this document is the portable continuation point.

Use Python 3.12 and Node.js 22.13 or newer. The repository root README describes
virtual-environment setup; the existing dependency manifest is
`vibe/trading_bot/requirements.txt`, including pytest. The local viewer uses
Node built-ins and does not require `npm install`.

Useful context, in order:

1. This handoff and `tools/backtest-research-viewer/README.md`.
2. `2026-09-09-backtest-pipeline-implementation-plan.md`, especially the current
   local-viewer implementation status and remaining work.
3. `2026-10-14-target-architecture-gap-audit.md`, especially the later updates
   and sections 9-11. Its opening diagnosis is historical, not today's status.
4. `2026-10-12-shared-data-analysis-and-artifact-design.md` for remote data,
   licensing, and publication boundaries.

All three design/audit files are in this directory.

## What is implemented now

- The genuine entry point is `scripts/run_nested_walk_forward.py`, using
  `NestedWalkForwardDriver`, exchange sessions, separated train/validation/test
  windows, purge/embargo, eligibility checks, and frozen held-out evaluation.
- Training evaluates, scores, and checks every candidate. It does **not**
  performance-shortlist the grid. Eligible training candidates proceed to
  validation, which selects a connected-plateau medoid. Test never selects
  parameters. The ORB-duration example varies only `orb_minutes = 5, 15, 30`,
  mapped to `strategy.orb_duration_minutes`; it is not training every ORB knob.
- `--record-runs` persists an explicit parent experiment, candidate/fold/run
  membership, selection rationale, and final study outcome. An optional
  `--experiment-title` names the controlled question. `--record-test-only`
  retains comparison summaries but omits train/validation row-level run detail.
- SQLite schema v11 adds `research_experiments` and
  `research_experiment_runs`. Reusable segment runs can belong to multiple
  experiments; experiment identity is separate from the run fingerprint.
- The local viewer opens experiment details in a native dialog occupying
  **90% of viewport width and height**. Content scrolls independently; the close
  control stays visible. A linked run opens the existing equity/trades dialog
  above it. Closing that run preserves the experiment's scroll and open folds.
  Escape closes the topmost dialog.
- The flexible analysis workspace is removed from the **local viewer UI and
  static assets**, not from the shared remote dashboard. Local bounded analysis
  APIs and the shared component remain intact.
- Missing validation reports say **Not validated**. Driver evaluation checks
  and stored run lifecycle validation are explicitly separate.
- Credibility checks independently reconcile mean net-R expectancy and
  session-based Sharpe. Review diagnostics cover extreme expectancy/Sharpe,
  single-session concentration, and nearly constant nonzero returns, in
  addition to the sample-aware win-rate gate. Finding evidence appears in the
  run Validation tab. Metric calculation version 2 is unchanged.

Recent code commits:

| Commit | Change |
| --- | --- |
| `9ea40db` | Expanded expectancy/Sharpe audits and credibility evidence |
| `1733976` | Experiment provenance, grouped inspection, synthetic fixture |
| `3d19d53` | 90%-viewport experiment dialog; local workspace removal |

## What Git does not bring to the other device

- The real research SQLite database, retained equity/trade rows, and market
  parquet are device-local and are **not included in this push**.
- At handoff, the source device's real DB has 151 runs with no explicit parent
  experiment. They remain individually inspectable. Do not invent membership
  from notes, dates, or parameter similarity.
- The new experiment preview is a **separate synthetic database**, with three
  folds and 21 linked segment runs. It exercises the real driver, recorder,
  sink, store, and viewer, but supplies synthetic engine results and a fixture
  validator. It is not a real-market backtest or proof that validation passed.
- Existing historical segment studies predate parent-experiment persistence.
  No new real-market experiment has been recorded under this new hierarchy yet.
- Source-device preview URLs used ports 4178 (real DB) and 4180 (synthetic).
  Those servers do not run on another device merely because Git was pulled.

Do not commit SQLite/parquet, credentials, or generated Python bytecode to
solve portability. Remote artifact transport/`ArtifactDestination` remains
deferred; no remote activation or upload was performed for this viewer work.

## Reproduce the synthetic viewer without market data

Run from the repository root in the configured Python environment. This
PowerShell recipe creates a uniquely named temporary DB and never overwrites
an existing real database:

```powershell
$env:RESEARCH_DEMO_DB = Join-Path ([System.IO.Path]::GetTempPath()) ("strategy-lab-demo-" + [guid]::NewGuid().ToString() + ".sqlite3")
python -c "import os, runpy; from pathlib import Path; fixture = runpy.run_path(r'tests\unit\research_pipeline\test_experiments.py'); print(fixture['build_experiment_fixture'](Path(os.environ['RESEARCH_DEMO_DB']))[0])"
node tools\backtest-research-viewer\server.mjs --db $env:RESEARCH_DEMO_DB --port 4180
```

If fixture generation fails, resolve that error before starting the server.
Keep the database path printed/stored in `RESEARCH_DEMO_DB`; the environment
variable must be set again if using another terminal. Open
`http://127.0.0.1:4180/` and select the synthetic experiment.
The generated DB is disposable fixture data.

For real data, explicitly configure `BACKTEST__DATA_DIR` to an existing local
directory containing the required parquet, and verify the resolved directory
before executing. The resolver can fall back to a main checkout; do not assume
a fresh worktree has data or use an unrelated checkout without permission.
Use the recorded nested-walk-forward example in the viewer README, choosing
windows covered by the available data. Do not use `--skip-leakage` to obtain a
successful-looking result; missing evidence blocks completion by design.

The real research DB defaults to OS-local application data. `RESEARCH__DB_PATH`
can select an explicit database for both recording and viewing.

## Validation and known limits

Latest viewer source passed all **38 Node tests** and syntax checks after the
dialog/workspace change. Browser checks covered the actual 90% dimensions,
desktop and narrow layouts, fold navigation, nested run drill-down, retained
scroll/folds, Escape, and refresh not reopening a dismissed experiment.

The experiment persistence fixture passed **7 Python tests** after its final
synthetic-curve changes. Earlier, the focused persistence/recording/store suite
passed 57 tests after the JSON-number fix; the broader research suite passed
832 tests before the final serialization/presentation refinements. These are
recorded results, not a claim that the broad suite was rerun after every edit.

Useful commands:

```powershell
npm --prefix tools\backtest-research-viewer run check
npm --prefix tools\backtest-research-viewer test
python -m pytest tests\unit\research_pipeline\test_experiments.py tests\unit\research_pipeline\test_recording_executor.py -q
```

Important invariants and caveats:

- Do not use identity-oriented `canonical_json()` for experiment payloads:
  it normalizes floats to strings. Store typed JSON numbers with
  `json.dumps(..., allow_nan=False)`. Invalid nonfinite metrics are explicitly
  diagnosed, not replaced with zero.
- The run sink can leave stored segment runs at `validating` while the driver
  reports completed evaluation checks. Do not promote persisted run state just
  to make the UI green; verify actual finalization evidence.
- Incomplete fold sets must not produce stitched OOS evidence. OOS P&L shown
  across test folds is a sum with capital resets, not a compounded portfolio.
- Old results are not retroactively revalidated by opening the viewer.
- Schema migrations are forward-only; v11 has already been applied on the
  source device. Use a new migration for any future DDL changes.
- The old fixed-parameter `vibe/backtester/analysis/walk_forward.py` is
  deprecated regime-stability analysis, not genuine OOS optimization. Keep its
  labels honest; do not mistake its rows for current nested-walk-forward tests.
- Keep the SQLite-backed research journal facade and `--journal`. The user
  explicitly retained it, as well as `plausibility_alpha = 0.05`.

## Recommended continuation

1. Recreate the fixture and let the user inspect the experiment dialog and
   segment charts/trades. Prioritize manual validation and understandable
   evidence, not profitability.
2. Once local licensed data is available, record a fresh, explicitly grouped
   real nested study. Reconcile candidate settings, folds, selector evidence,
   run links, and retained rows end to end.
3. Investigate the remaining driver-check versus stored-run-finalization
   boundary on that real study. Independently reconcile displayed headline
   metrics against its trade/equity rows; do not weaken validation to finish.
4. Add repeatable browser coverage for the experiment/run dialog interactions
   if further UI changes need it; current interaction verification was manual
   browser automation, not a checked-in browser suite.
5. Return to the remote dashboard after the local inspection workflow is useful.
   Remote row-level trades/equity remain a desired capability. Do not activate
   uploads, change licensing policy, or revive artifact transport work merely
   to reproduce the local fixture.

The user permits removal of obsolete experimental runs, but no additional
historical deletion was performed in the experiment/dialog changes. Preserve
unknown local changes; generated tracked `.pyc` modifications on the source
device were intentionally left out of commits.
