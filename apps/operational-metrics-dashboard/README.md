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

Both adapters implement the same field-catalog-driven analysis contract. The
workspace supports typed AND/OR filters, selectable screening columns, arbitrary
grouping dimensions, aggregate measures, trust populations, and chart
encodings. It intentionally has no fixed experiment compare tray. Operational
limits are visible in the UI: 5,000 candidate runs, 500 groups, 500 response
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

P12 deliberately does not publish trades, fills, raw bars, trade ledgers, equity
points, or equity curves. The remote research UI never requests or implies access
to those local-only artifacts.

### Browser credential boundary

Only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are accepted.
The build fails if a research/Supabase service-role variable is present or if
the anonymous-key setting contains a service-role JWT. Do not add
`RESEARCH_SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, or any
service-role value to dashboard environment configuration or GitHub Pages
secrets. Research writes remain in trusted Python tooling.

The applied Supabase schema grants `SELECT` on `research_publications` to
`anon`/`authenticated`, enables RLS, and revokes insert, update, delete,
truncate, references, and trigger privileges. The dashboard adapter exposes no
mutation method.

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
