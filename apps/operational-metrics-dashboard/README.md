# Live Trading Dashboard

Static-exportable Next.js dashboard for IB paper-trading state, chart context, operations health, and execution quality.

## Data Source

The dashboard defaults to bundled fixture data so it can build and render without broker or Supabase credentials. Set `NEXT_PUBLIC_DASHBOARD_DATA_SOURCE=supabase` to read the Phase 1 Supabase read model with browser-safe anonymous credentials.

```bash
NEXT_PUBLIC_DASHBOARD_DATA_SOURCE=fixture
NEXT_PUBLIC_DASHBOARD_FIXTURE=live
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
```

Fixture names:

- `live`: live market day with an open position, order events, OHLCV bars, annotations, and execution metrics.
- `closed`: closed-market review with no open positions.
- `empty`: no dashboard rows yet.
- `unavailable`: degraded remote store state with publish backlog counts.

The Supabase adapter reads `accounts`, `equity_snapshots`, `positions`, `order_events`, `price_bars`, `trades`, `operational_metrics`, and `strategy_annotations` using only the anonymous key and RLS read policies. Never expose a service-role key to this app.

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

The app uses `output: "export"` and writes the static site to `out/`.

## Deploy To GitHub Pages Or Vercel

1. Create a Vercel project from `apps/operational-metrics-dashboard`.
2. Add the browser-safe Supabase environment variables if live data is enabled.
3. Deploy with static export enabled, or publish the generated `out/` directory to GitHub Pages.

The dashboard is client-rendered from static fixtures or public Supabase REST reads and does not require a server runtime after export.
