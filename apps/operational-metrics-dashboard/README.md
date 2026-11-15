# Strategy Lab Dashboard

Static-exportable Next.js dashboard for IB paper-trading operations and the
shared read-only backtest research surface.

Routes:

- `/`: live trading state, chart context, operations health, and execution quality.
- `/research`: a flexible run-summary analyzer plus the retained publication
  explorer/detail surface.

## Data Source

The dashboard defaults to bundled fixture data so it can build and render without broker or Supabase credentials. Set `NEXT_PUBLIC_DASHBOARD_DATA_SOURCE=supabase` to read the Phase 1 Supabase read model with browser-safe anonymous credentials.

```bash
NEXT_PUBLIC_DASHBOARD_DATA_SOURCE=fixture
NEXT_PUBLIC_DASHBOARD_FIXTURE=live
NEXT_PUBLIC_RESEARCH_DATA_SOURCE=fixture
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

Fixture names:

- `live`: live market day with an open position, order events, OHLCV bars, annotations, and execution metrics.
- `closed`: closed-market review with no open positions.
- `empty`: no dashboard rows yet.
- `unavailable`: degraded remote store state with publish backlog counts.

The Supabase adapter reads `accounts`, `equity_snapshots`, `positions`, `order_events`, `price_bars`, `trades`, `operational_metrics`, and `strategy_annotations` using only the anonymous key and RLS read policies. Never expose a service-role key to this app.

The research route has one strict read-only adapter contract and exactly two
implementations:

- `FixtureResearchDataAdapter`: deterministic development and UI-test records.
- `SupabaseResearchDataAdapter`: anonymous `GET` requests to
  `research_publications`, with server-side filtering, stable ordering, exact
  counts, and range pagination; catalog `GET` and a read-only PostgREST RPC
  provide the analysis surface.

The **Flexible analysis workspace** section is no longer mounted in the
research page; its catalog/analysis requests are not issued by that section.
The existing adapter/analysis contract remains available to other consumers.
It supports typed AND/OR filters, selectable screening columns, arbitrary
grouping dimensions, aggregate measures, trust populations, and chart
encodings. It intentionally has no fixed experiment compare tray. Operational
limits are enforced by the retained analysis contract: 5,000 candidate runs, 500 groups, 500 response
rows, 300 chart marks, 512 KiB responses, 40 filter nodes at depth 5, 24
screening columns, and 8 order rules. Queries above cost/cardinality policy are
rejected rather than silently sampled. Truncation metadata remains visible
above the inspectable result table.

Current validated summaries are the default population. Legacy/pre-P2b and
validation-attention records require explicit opt-in. Metric calculation
versions cannot be aggregated together unless version is a grouping dimension.
Analysis definitions can be saved to a bounded URL (4 KiB) and bounded local
storage (16 KiB); malformed, oversized, or unknown-field state falls back to
the safe default.

The fixture includes current `RUN-000003`-shaped parameter, dataset, validation,
lineage, and metric fields. No event-level data is present.

Set `NEXT_PUBLIC_RESEARCH_DATA_SOURCE=supabase` to use the remote read model.
The route decodes publication schema version 1 defensively and preserves
methodology, execution-realism identity, validation findings, aggregate evidence
counts/checksums, lineage, legacy/pre-P2b/survivorship badges, publication
versions, and reconciliation-visible IDs.

P12 publications still exclude trades, fills, raw bars, trade ledgers and
intraday equity points. The separately approved daily aggregate read model
adds experiment-specific net R and last-retained-session equity; it does not
change immutable publications or grant access to those local-only artifacts.

### Experiment details and interactive charts

Experiment dialogs load their timeline/outcome and daily series on opening,
not on the explorer's initial page load. Requests share an in-flight promise;
an LRU cache retains at most 12 experiments for five minutes, removes failed
loads, and prevents a closed or replaced dialog receiving late results.
Chart components/libraries are dynamically imported only with the detail view.

Tables show net expectancy/CI, stored equity Sharpe, full-history drawdown and
calendar duration, net-dollar profit factor and net P&L. Numeric summaries use
three decimals (percentages/currency two); expanded raw evidence preserves
original precision. Independent runs and train/validation/test roles are not
silently aggregated into a portfolio.

- **TradingView Lightweight Charts** (existing dependency): cumulative R,
  equity and drawdown; attribution remains visible.
- **Apache ECharts** (modular imports): distributions and year/symbol diagnostic
  bars, with tooltip and category zoom. Its scatter/heatmap modules are not
  loaded when no approved aggregate excursion data exists.

Charts are centred and full-width in a wider dialog, with 440px time charts
and 400px diagnostic charts. Date inputs or drag/zoom change the analysis
window. Cumulative R restarts at zero; daily drawdown uses the preceding
retained session equity (or recorded initial capital) as its baseline and
recomputes the selected-window high-water mark. All-history restores the full
data. Calendar-year buttons select January 1 through December 31, clipped to
the selected member's first/last data dates for partial boundary years. Years
without data are omitted; year selection recalculates the same range metrics.
Calculations consume complete daily points, never a sampled display path.
Daily drawdown is not the stored intraday max drawdown. Missing equity stays
unavailable; it is never rebuilt from closed-trade P&L or cumulative R.

A separate responsive 2x2 comparison grid displays daily buy-and-hold
benchmarks, cumulative R, **reconstructed closed-trade equity**, and drawdown
in R. Ticker toggles control the independent member curves. Shared
time-window controls synchronize all four charts live during a drag or zoom;
range metrics recalculate after the gesture settles. Linked crosshairs show
the same hovered ticker and date, with each chart's own value and axis units.
Initial ranges are seeded from the complete data calendar rather than the
library's pre-paint range, and preserved when container or price-axis widths
change. Narrow panels still show the complete selected window.
The **All** checkbox adds the sum of every independent account, including
hidden individual tickers. Five $100,000 accounts therefore start at $500,000;
cumulative R is summed, and drawdown is recalculated from the combined R curve.
This is not a shared-capital portfolio backtest. All is unavailable across
different candidates, walk-forward folds, roles, or repeated ticker accounts;
fixed-rule symbol membership ordinals are not temporal folds. Missing
member observations remain unavailable instead of being filled with zero.
Benchmark and
closed-trade equity rebase to each account's recorded capital at the first
selected daily close. Closed-trade equity uses complete retained exit-session
net P&L and is not substituted for missing intraday equity. Source-price ratios
do not explicitly credit dividends or charge benchmark costs.
Use `daily-series <experiment-id> --price-dir vibe\data\parquet` in the trusted
publication CLI to include daily benchmark aggregates. Existing payloads remain
readable and show missing comparison data explicitly. This implementation does
not apply the hosted schema, publish data or deploy the hosted frontend.

Apply `docs/backtest-research-summaries/supabase-research-daily-series.sql` before
enabling daily-series reads on the hosted release. It grants browser SELECT
only and exposes rows only for published experiment outcomes. Trusted local
Python builds one bounded derived row (200 members, 50,000 daily points,
2 MiB compact JSON maximum) without modifying SQLite evidence:

```powershell
python -B scripts\publish_research.py --db <research-db> daily-series <experiment-id>
# Explicit publication, after schema application and ordinary outcome publishing:
python -B scripts\publish_research.py --db <research-db> daily-series <experiment-id> --publish
```

The command refuses partial retained trades/equity and reports a derived-payload
checksum. Referenced trade/equity checksums identify the original evidence;
they are not checksums of the daily downsample itself. No raw rows, credentials,
broker account records or market bars are uploaded. Legacy runs with no retained
trade series cannot produce R charts. The bundled `fixture-daily` experiment
is explicitly synthetic; Supabase never falls back to it.

**Rollout status:** implemented and previewed locally; daily-series SQL,
data publication and the hosted static release have not been applied by this
change. Row 12's original diagnostic retention contains no actual equity points.

### Browser credential boundary

Only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are accepted.
Trading and research use this same project URL and public anon/publishable key
(stored in `NEXT_PUBLIC_SUPABASE_ANON_KEY`); no separate research browser key
is needed. Read-only access comes from database
grants and RLS policies, not from the key's name. These public credentials do
not grant access to the private historical-data bucket.
The build fails if a research/Supabase service-role variable is present or if
the anonymous-key setting contains a service-role JWT. Do not add
`RESEARCH_SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, or any
service-role value to dashboard environment configuration or GitHub Pages
secrets. Research writes remain in trusted Python tooling.

The applied Supabase schema grants `SELECT` on `research_publications` to
`anon`/`authenticated`, enables RLS, and revokes insert, update, delete,
truncate, references, and trigger privileges. The dashboard adapter exposes no
mutation method.

`research_experiment_conclusions` holds one editable, one-line conclusion per
experiment and follows the same select-only rules. The dashboard reads it with
`getExperimentConclusions`. It shows the conclusion under the experiment
identity in the publication table and at the top of the experiment detail. If
the table is missing, the adapter returns no conclusions instead of failing.
Writes come only from `scripts/publish_research.py conclusion set`.

Apply `docs/backtest-research-summaries/supabase-research-schema.sql` to add the
normalized `research_analysis_*` read model, field catalog, typed filter
evaluator, bounded aggregate RPC, RLS, and grants. Browsers cannot select the
normalized run/dimension/metric tables directly and never receive a service-role
credential. The RPC is `STABLE`, summary-only, and validates field references as
catalog values rather than interpolating SQL identifiers.

Monetary values are formatted with their explicit field currency. Missing net
liquidation, position P&L, and legacy-trade currencies are preserved as unknown
rather than being inferred from the account or instrument currency. Mixed- or
unknown-currency P&L is not aggregated, and `BASE` or missing currencies are
shown as unavailable instead of being silently formatted as USD.

Equity P&L rows consume the backward-compatible `pnl_provenance`,
field-specific provenance, and `pnl_version` metadata. A value is labeled
broker P&L only when its provenance is `broker`; historical rows without
provenance are labeled as source unknown. Execution-quality summaries include
only valid slippage version 2 metrics.

When multiple accounts are present, use the account selector in the header.
Equity, positions, order events, trades, metrics, annotations, and P&L are scoped
to that account. Open positions remain visible until a zero-quantity snapshot is
published; they are not hidden merely because their price/P&L is unchanged.
Closed-trade P&L prefers authoritative trade records and otherwise uses
quantity-weighted execution prices for partial fills. Legacy events without a
trade ID consume entry quantities chronologically per account and symbol, so
sequential or partial closes cannot reuse an earlier entry.

Supabase trade history is fetched in deterministic 1,000-row pages instead of
being capped to the latest 100 trades. The Performance totals cover all
collected trades for the selected account; only the table display is paginated.

## Local Development

```bash
npm install
npm run dev
npm test
```

Open `http://localhost:3000`, or choose another port with:

```bash
npx next dev --port 3007
```

## Static Export

```bash
npm run build
```

The app uses `output: "export"` and writes the static site to `out/`, including
`out/index.html` and `out/research/index.html`. Both routes perform browser-side
anonymous reads and require no API route or server runtime.

## Deploy To GitHub Pages Or Vercel

1. Create a Vercel project from `apps/operational-metrics-dashboard`.
2. Add the browser-safe Supabase environment variables if live data is enabled.
3. Deploy with static export enabled, or publish the generated `out/` directory to GitHub Pages.

The dashboard is client-rendered from static fixtures or public Supabase REST
reads and does not require a server runtime after export. Configure
`NEXT_PUBLIC_RESEARCH_DATA_SOURCE=supabase` only after the select-only RLS schema
has been applied to the intended project.

The GitHub Pages workflow defaults the research data source to
`NEXT_PUBLIC_DASHBOARD_DATA_SOURCE`, using the same two public Supabase secrets
for both routes. If neither data-source variable is configured, both use
fixtures. Set the GitHub variable `NEXT_PUBLIC_RESEARCH_DATA_SOURCE=fixture`
to keep research on demo data explicitly, or `supabase` to enable it independently.
For local development or other hosts, set each route's data-source variable
explicitly. Changing these build-time settings requires rebuilding the static
site; adding or rotating a separate research key is not necessary.
