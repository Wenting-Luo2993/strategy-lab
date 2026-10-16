# Local Backtest Research Viewer

Dependency-light, local-only inspection for the schema v10+ research SQLite
database. The server uses Node's built-in `node:sqlite`; the browser receives
only allowlisted JSON views and static HTML/CSS/MJS.

## Runtime

Use **Node.js 22.13.0 or newer**. `tools/backtest-research-viewer/.node-version`
pins the minimum LTS baseline. Node 22.13.0 is where `node:sqlite` stopped
requiring the experimental command-line flag and includes the `DatabaseSync`
read-only API used here. Node still labels the module a release candidate
(Stability 1.2), so the viewer deliberately uses only this small, tested API
surface. The tool's `package.json` enforces the minimum. Prefer an LTS release
(Node 22.13+ or Node 24+); newer non-LTS releases also satisfy the API floor.

## Run

From the repository root:

```powershell
node tools\backtest-research-viewer\server.mjs
```

The database resolves exactly like the Python research store:

1. `RESEARCH__DB_PATH`
2. `RESEARCH__DATA_DIR\research.sqlite3`
3. OS-local application data (`%LOCALAPPDATA%\strategy-lab\research.sqlite3`
   on Windows)

Choose a different database only when the process starts:

```powershell
node tools\backtest-research-viewer\server.mjs --db C:\local\research.sqlite3
```

Open `http://127.0.0.1:4178`. `--port` may select another local port.
`--host` accepts loopback values only.

## Experiment-first review

The primary explorer lists **controlled experiments**, not unrelated segment
runs. A nested walk-forward experiment declares its parameter axes and candidate
values, objective, fixed reference configuration and complete split plan.
Opening it shows a fold overview, then aligned training/validation candidate
comparisons, the validation selection rationale and the one frozen test result.
Every linked segment opens the existing chart/trade dialog; closing it leaves
the experiment in place.

```powershell
python scripts\run_nested_walk_forward.py `
  --symbol QQQ --dev-start 2023-01-03 --dev-end 2023-12-29 `
  --oos-start 2024-01-02 --oos-end 2024-06-28 `
  --axis orb_minutes=strategy.orb_duration_minutes:5,15,30 `
  --record-runs --experiment-title "ORB duration comparison"
```

Schema v11 adds `research_experiments` and explicit many-to-many
`research_experiment_runs` membership. Each invocation has a distinct study ID;
deduplicated segment runs may belong to multiple experiments. Membership is
recorded during evaluation, before a final report exists. Interrupted studies,
inconclusive folds, failed candidates and recording gaps remain visible.
Finalized study definitions/results are immutable. The driver stays free of I/O.

**Training is not a hidden performance shortlist:** it evaluates and checks all
declared candidates. Validation uses the connected-plateau medoid and freezes
the chosen setting before test. Experiment check outcomes and stored segment-run
lifecycle states are distinct and labelled separately. A missing run validation
report is shown as **Not validated**, never a reassuring green pass.

`--record-test-only` retains the experiment's candidate comparison summaries but
omits training/validation trade/equity drill-downs. Missing summaries, untested
settings, reserved final holdout and display bounds are explicit; no partial
fold set is presented as a stitched OOS result.

Older runs have no experiment identity. They remain in **Individual segment
runs & ungrouped history**, without guessing membership from dates, notes or
parameter similarity. Schema v10 databases remain readable, with an explicit
empty experiment view until migrated by a writer. Viewing never migrates or
rewrites a database. Synthetic demo studies use `synthetic_demo/v1` and are
visibly labelled as UI fixtures, not research evidence.

## Run detail

Open a run alias in the explorer for a wide, performance-first dialog inspired
by the supplied QuantConnect reference:

- Headline equity, net profit, return, total costs, trade count, win rate,
  expectancy in R, and Sharpe. Missing values stay unavailable, not zero.
- Performance: equity/return toggle, 1m/3m/1y/all windows, timestamp/value
  inspection with pointer or keyboard-accessible observation sliders,
  intraday drawdown, session-return bars, and recorded statistics with their
  calculation versions.
- Trades: loaded-row P&L distribution and statistics, symbol/exit-reason and
  outcome filters, and a paginated ledger with entry/exit timestamps in their
  recorded exchange offsets. Filters operate on the loaded rows; the server's
  cap and any incomplete coverage are disclosed.
- Configuration, Validation, and Provenance & lifecycle keep parameters,
  findings, identities, checksums, retention, transitions, and publication
  status available without burying the charts. Tabs support arrow/Home/End
  keyboard navigation.

Validation findings expose their retained observed value, expected bound, and
diagnostic context in an expandable evidence panel, with recursive credential
redaction. New calculations are audited independently: expectancy against the
net R sample, and Sharpe against exchange-local session-close returns. Candidate
review flags cover extreme expectancy/Sharpe, single-session concentration
(including leave-one-out Sharpe), and almost constant nonzero returns.
These flags request investigation, not a declaration that a strategy is invalid.
Old runs are not revalidated merely by viewing them.

The role warning remains above every tab. An unlabelled run is **not** inferred
to be a test run, and a test role does not imply validation has completed.
No records are quarantined, relabelled, or deleted by opening this dialog.

Equity is a line of retained observations, not fabricated OHLC candles. X
positions reflect elapsed time, not observation index. Return uses recorded
starting capital rather than the first returned point and is not rebased when
zooming. Session returns are computed from the last full-curve observation of
each recorded session date, before display downsampling; the first session
uses recorded starting capital. If capital is unavailable that first return
and the cumulative-return chart remain unavailable. Intraday drawdown also
keeps its full-run peak when zooming, including starting capital in the peak
when available so an initial loss cannot disappear. Full-curve risk extrema are shown
separately because display downsampling can omit a trough.

Benchmark, exposure, margin, turnover, PSR, and capacity are not invented from
unrelated metrics. Missing trade/equity rows have explicit availability
messages; independent fetch errors do not blank the run summary or the other
retained-row panel.

## Security boundary

- Opens one fresh `DatabaseSync` connection per request with `readOnly: true`.
- Enforces `PRAGMA query_only=ON`, `trusted_schema=OFF`, foreign keys, and a
  bounded busy timeout.
- Does **not** use SQLite's `immutable=1` URI flag: this database is WAL-backed
  and may receive newly committed runs while the viewer is open. Claiming it is
  immutable can ignore WAL changes and contradict refresh-on-demand. Fresh
  read-only/query-only connections preserve both safety and current reads.
- Binds to `127.0.0.1` by default, rejects non-loopback bind addresses,
  non-loopback peers, and non-loopback/malformed Host headers.
- Accepts only `GET` and `HEAD`.
- Has no arbitrary SQL, database picker, upload, file-reading, mutation, or
  dynamic table/column endpoint.
- Uses parameter binding for every user filter and allowlists all sort fields,
  endpoint paths, and static assets.
- Bounds page sizes to 100 and search/filter values to 100 characters.
- Bounds detail collections (metrics/findings/lineage/artifacts) and reports
  their total/truncation state rather than materializing unbounded rows.
- Redacts credential-shaped keys and JWT/Bearer-shaped values from returned
  registry/configuration data and error surfaces. Browser-visible database and
  artifact paths are home/environment-relative or basename-only.

Schema v9 adds the `run_trades` and `run_equity_points` tables, so runs recorded
with `full` or `diagnostic` retention expose row-level trade and equity detail.
Availability is *measured* rather than inferred from the declared retention
profile: a run can claim full retention and still have no rows (recorded before
v9, or interrupted mid-recording), so the UI reports what is actually present.
Summary-retained runs continue to show counts and checksums only.
Artifact previews are available only for database-referenced,
repository-relative files that resolve beneath the checkout after symlink
resolution. Images are limited to PNG/JPEG/GIF/WebP and 5 MB; Markdown, CSV,
JSON, text, YAML, HTML, XML, and logs are rendered as escaped text and limited
to 1 MB. External, absolute, missing, oversized, and unsupported files remain
references only.

Schema v9 artifact-publication metadata is also available through
artifact-ID-addressed endpoints. Staged bytes resolve from
`RESEARCH__ARTIFACT_STAGING_DIR`, or from `artifact-staging` beside an explicit
viewer database when that variable is unset. The server verifies containment,
size, and SHA-256 before returning a bounded preview; browser requests never
provide a staging path or object key.

## Test

```powershell
node --test tools\backtest-research-viewer\tests\*.test.mjs
node --check tools\backtest-research-viewer\server.mjs
node --check tools\backtest-research-viewer\public\app.mjs
```
