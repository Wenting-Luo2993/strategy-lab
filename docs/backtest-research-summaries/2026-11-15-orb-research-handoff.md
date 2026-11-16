<!-- taskboard
{"id":"orb-continuation-handoff","title":"ORB research cross-device handoff (November 15)","projects":["orb-research"],"status":"current","kind":"reference","reviewed":"2026-11-15"}
-->
# ORB research continuation handoff

**Observation date:** 2026-11-15. The user requested documenting the proposed
next work and stopping for the day, to continue on another device. No further
experiment, reserved-data access, publication or production action is
authorized by that request.

## Resume the right code and documents

- Repository: `Wenting-Luo2993/strategy-lab`.
- Continuation branch: `wenting-luo2993-backtest-continuation`. Fetch its latest
  remote revision and verify the actual checkout/merge base and working-tree
  status before changes. A default-branch checkout alone does not contain all
  this continuation work. Use a separate worktree/session if needed; do not
  discard another checkout's changes.
- Before this handoff commit, branch and upstream were both at `62dfa88`.
  This handoff is committed afterward; use the latest remote branch, not that
  older SHA as a hard pin.
- Read the [living findings](orb-exploration-results.md), [canonical research
  plan](2026-10-31-orb-edge-research-plan.md#backtesting-while-paper-fill-evidence-accumulates-2026-11-15),
  [roadmap/tasks](../taskboard/orb-research.md) and
  [north star](2026-10-31-backtest-research-north-star.md). Historical October
  and November 1 handoffs retain evidence but not the current next actions.

The objective remains **positive net returns with less drawdown, limited
overnight exposure and returns that complement buy-and-hold**.

## Evidence already completed

| Study | Identity and takeaway |
| --- | --- |
| Fixed 15m entry-confirmation stack | `01a1db6f-6ada-75f0-85f9-3f8640d084eb`: five symbol runs, 3,956 trades; mean +0.0349R, nominal 95% CI [+0.0041, +0.0657]. Small, tail/cost-sensitive exploration estimate, not an established usable edge or isolated proof of the filters |
| Movement-to-cost/persistence diagnostics | `01a1e0a2-dc39-7228-9efe-6be1427096a0`: one experiment, three comparison trials, zero new simulation runs. On 3,434 measurable trades, opportunity high-low +0.0244R [-0.0415, +0.0902], persistence +0.0399R [-0.0093, +0.0892], interaction -0.0223R [-0.1306, +0.0861] |

Neither diagnostic advances under its frozen rule. Later low-opportunity
coverage is only 23 trades; unavailable persistence affects 522 trades earning
40.59% of baseline dollars. Preserve the disclosed ATR-seed wording erratum:
the actual pinned helper uses the first 20 true ranges' mean, then Wilder
recursion, shifted one session. Do not rewrite the frozen protocol or retune
normalization/windows to rescue this result.

The programme has **104 trials**, not the seven recorded locally. Both latest
studies are local-only; queued publication records are not a hosted rollout.
The [living findings](orb-exploration-results.md#trial-count) preserve the
full count and aggregate evidence.

## Recommended next work, not execution approval

1. **First: review and freeze a matched two-arm design.** Compare the unchanged
   full stack against the same 15m completed-close/body ORB without additional
   RVOL/VWAP/EMA/range-ATR gates. Common fills, exits, risk/capital rules and
   data boundaries are essential. Both arms need engine simulations; deleting
   retained trades does not supply a control. Freeze paired all-session net
   dollars, uncertainty, meaningful effect, risk/drawdown acceptance, fixed
   adverse-cost scenarios, trial budget and stop rules before running.
2. **Supporting: frozen-stack payoff diagnostics.** First audit retained trade,
   excursion and price-path availability. Explain failed breakouts versus
   open-profit giveback, adverse movement, exit reasons and holding time.
   This is attribution, not an exit sweep; hindsight excursions cannot become
   entry-time features.
3. **Later option: portfolio contribution.** Separately design one fixed
   allocation comparing buy-and-hold with buy-and-hold plus ORB, with real
   capital/risk constraints and explicit benchmark/cash assumptions. Do not sum
   independent accounts and call that an executable shared-capital portfolio.

The [plan](2026-10-31-orb-edge-research-plan.md#backtesting-while-paper-fill-evidence-accumulates-2026-11-15)
is the canonical design discussion; backlog tasks live only in the roadmap.
No new ruleset, allocation, effect threshold or trial budget is frozen here.
Paper execution evidence can accumulate in parallel, but its readiness was
not checked in this documentation-only session.

## Cross-device boundaries

**Git carries code, skills, registrations, aggregate findings and this handoff,
not the local research database or private/data artifacts.** Planning and
reading can resume immediately from the branch. Viewer drill-downs and study
execution require separately available matching local evidence.

| Local item | Continuation requirement |
| --- | --- |
| Research SQLite | Nominal Windows location `%LOCALAPPDATA%\strategy-lab\orb_edge_2026.sqlite3`; Windows Store Python redirects it into its package `LocalCache`. Resolve using the recording Python runtime, as in the [viewer instructions](../../tools/backtest-research-viewer/README.md#run). Do not create an empty DB or substitute an old preview snapshot |
| Exploration OHLC data | Locate the original five parquet files and verify their hashes against `config/research/orb_entry_stack_15m_exploratory_v1.json`. The old handoff's drive paths are device-specific, not guaranteed locations on the next machine |
| Private diagnostic artifacts | Original session `231d03a4-6367-4816-8615-81421b4a4f22`, under its `files\movement-noise` directory: results JSON, manifest, aggregate contrast CSV and private joined trade-feature CSV. These files are not in Git |
| Baseline daily-chart cache | `daily-series\01a1db6f-6ada-75f0-85f9-3f8640d084eb.json` beside the actual recording DB on the original device; also retained under that session's `files\` directory. This is a separate staged chart artifact, not part of SQLite |
| Dependencies and services | `node_modules`, generated taskboard HTML and local servers do not transfer through Git. Install/restore dependencies only when needed by the chosen command; do not assume port 4178 or the synthetic hosted preview is running on the new device |

Do not commit/upload private ledgers, credentials, raw account exports or source
datasets to make the handoff portable. Any private-file transfer needs an
approved destination/method and data-license/privacy review. Use a consistent
SQLite backup if a transfer is later authorized, not a live-file-only copy that
can omit WAL transactions. If artifacts are absent, state that limitation;
do not rerun an immutable study just to refill the viewer or fabricate records.

All work remains within the five-symbol 2018-05-01 to 2024-12-31 exploration
interval. Confirmation symbols and the final holdout remain locked. No cloud
schema/publication, hosted release, broker or Oracle operations are implied.

## Reporting and local review

The [reporting skill](../../.claude/skills/report-experiment-results/SKILL.md)
requires: indicator meaning/formula/worked examples; actual experiment method
(labels versus simulations); measured results and uncertainty; conclusions;
then concrete next steps tied to the goal. The skill file was validated, but
native activation was not available in the original active session's stale
catalog. Follow the `CLAUDE.md` direct-file fallback if discovery still fails;
do not claim a refresh without verification.

Relevant saved implementation commits:

| Commit | Change |
| --- | --- |
| `d8ee6a8` | Completed diagnostic evidence, recording fixes and preserved erratum |
| `4a833f8` | Dedicated local diagnostic panel and correct database instructions |
| `8b9e6b1` | Five-part chat-reporting skill |
| `62dfa88` | Direct-file fallback for stale skill discovery |

To rebuild the read-only taskboard on the new device:

```powershell
python -B tools\taskboard\build.py --check
python -B tools\taskboard\build.py
```

**Suggested first instruction for the next session:** "Read this handoff,
verify the latest continuation branch and local evidence availability, then
review the matched two-arm protocol design with me. Do not run experiments or
access reserved data yet."
