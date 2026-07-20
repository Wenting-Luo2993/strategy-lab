-- Live Trading Dashboard Phase 1 Supabase read model.
-- Run with a privileged Supabase role. Browser clients should use anon read-only policies only.

create table if not exists public.accounts (
    account_id text primary key,
    broker text not null,
    display_name text not null,
    currency text not null default 'USD',
    mode text not null,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.trades (
    trade_id text primary key,
    account_id text not null,
    symbol text not null,
    side text not null,
    quantity numeric not null,
    entry_price numeric not null,
    entry_time timestamptz not null,
    exit_price numeric,
    exit_time timestamptz,
    status text not null,
    pnl numeric,
    pnl_pct numeric,
    strategy text,
    exit_reason text,
    broker_order_id text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.order_events (
    event_id text primary key,
    account_id text not null,
    broker text not null,
    broker_order_id text not null,
    strategy_order_id text,
    trade_id text,
    event_type text not null,
    symbol text not null,
    side text not null,
    quantity numeric not null,
    price numeric,
    expected_price numeric,
    slippage_bps numeric,
    latency_ms numeric,
    occurred_at timestamptz not null,
    raw_status text
);

create table if not exists public.price_bars (
    symbol text not null,
    timeframe text not null,
    bar_start timestamptz not null,
    open numeric not null,
    high numeric not null,
    low numeric not null,
    close numeric not null,
    volume numeric not null,
    provider text not null,
    ingestion_time timestamptz not null,
    is_complete boolean not null default true,
    primary key (symbol, timeframe, bar_start)
);

create table if not exists public.equity_snapshots (
    snapshot_id text primary key,
    account_id text not null,
    timestamp timestamptz not null,
    net_liquidation numeric,
    cash numeric,
    buying_power numeric,
    realized_pnl numeric,
    unrealized_pnl numeric,
    source text not null
);

create table if not exists public.positions (
    position_id text primary key,
    account_id text not null,
    symbol text not null,
    quantity numeric not null,
    side text not null,
    avg_cost numeric,
    market_price numeric,
    unrealized_pnl numeric,
    updated_at timestamptz not null
);

create table if not exists public.strategy_annotations (
    annotation_id text primary key,
    account_id text not null,
    symbol text not null,
    strategy text not null,
    trading_day date not null,
    annotation_type text not null,
    key text not null,
    value_json jsonb not null,
    enabled boolean not null default true,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.operational_metrics (
    metric_name text not null,
    metric_value numeric not null,
    dimensions jsonb,
    timestamp timestamptz not null,
    created_at timestamptz default now(),
    primary key (metric_name, timestamp)
);

create index if not exists idx_trades_account_entry on public.trades(account_id, entry_time desc);
create index if not exists idx_order_events_account_time on public.order_events(account_id, occurred_at desc);
create index if not exists idx_price_bars_symbol_time on public.price_bars(symbol, timeframe, bar_start desc);
create index if not exists idx_equity_account_time on public.equity_snapshots(account_id, timestamp desc);
create index if not exists idx_positions_account_symbol on public.positions(account_id, symbol);
create index if not exists idx_metrics_timestamp on public.operational_metrics(timestamp desc);

alter table public.accounts enable row level security;
alter table public.trades enable row level security;
alter table public.order_events enable row level security;
alter table public.price_bars enable row level security;
alter table public.equity_snapshots enable row level security;
alter table public.positions enable row level security;
alter table public.strategy_annotations enable row level security;
alter table public.operational_metrics enable row level security;

-- Anon dashboard key: read-only access to dashboard-safe tables.
drop policy if exists "anon read accounts" on public.accounts;
drop policy if exists "anon read trades" on public.trades;
drop policy if exists "anon read order events" on public.order_events;
drop policy if exists "anon read price bars" on public.price_bars;
drop policy if exists "anon read equity snapshots" on public.equity_snapshots;
drop policy if exists "anon read positions" on public.positions;
drop policy if exists "anon read strategy annotations" on public.strategy_annotations;
drop policy if exists "anon read operational metrics" on public.operational_metrics;

create policy "anon read accounts" on public.accounts for select to anon using (true);
create policy "anon read trades" on public.trades for select to anon using (true);
create policy "anon read order events" on public.order_events for select to anon using (true);
create policy "anon read price bars" on public.price_bars for select to anon using (true);
create policy "anon read equity snapshots" on public.equity_snapshots for select to anon using (true);
create policy "anon read positions" on public.positions for select to anon using (true);
create policy "anon read strategy annotations" on public.strategy_annotations for select to anon using (enabled = true);
create policy "anon read operational metrics" on public.operational_metrics for select to anon using (true);

-- Bot writes use the Supabase service-role key. Do not expose it to the browser.