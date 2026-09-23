-- Live Trading Dashboard Phase 1 Supabase read model.
-- Run with a privileged Supabase role. Browser clients should use anon read-only policies only.
begin;

create table if not exists public.accounts (
    account_id text primary key,
    broker text not null,
    display_name text not null,
    currency text,
    mode text not null,
    code_version text,
    git_commit text,
    deployment_id text,
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
    pnl_currency text,
    strategy text,
    exit_reason text,
    broker_order_id text,
    code_version text,
    git_commit text,
    deployment_id text,
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
    execution_id text,
    permanent_order_id text,
    trade_currency text,
    commission numeric,
    commission_currency text,
    decision_at timestamptz,
    submitted_at timestamptz,
    filled_at timestamptz,
    benchmark_type text,
    benchmark_price numeric,
    quote_bid numeric,
    quote_ask numeric,
    quote_midpoint numeric,
    stop_price numeric,
    limit_price numeric,
    decision_to_submission_latency_ms numeric,
    submission_to_fill_latency_ms numeric,
    slippage_amount numeric,
    slippage_version integer not null default 1,
    slippage_valid boolean not null default false,
    occurred_at timestamptz not null,
    raw_status text,
    code_version text,
    git_commit text,
    deployment_id text
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
    code_version text,
    git_commit text,
    deployment_id text,
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
    base_currency text,
    net_liquidation_currency text,
    cash_currency text,
    buying_power_currency text,
    realized_pnl_currency text,
    unrealized_pnl_currency text,
    pnl_provenance text,
    realized_pnl_provenance text,
    unrealized_pnl_provenance text,
    pnl_version integer,
    local_realized_pnl numeric,
    local_realized_pnl_currency text,
    granularity text not null default 'raw',
    period_start timestamptz,
    event_type text,
    source text not null,
    code_version text,
    git_commit text,
    deployment_id text
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
    instrument_currency text,
    unrealized_pnl_currency text,
    updated_at timestamptz not null,
    code_version text,
    git_commit text,
    deployment_id text
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
    code_version text,
    git_commit text,
    deployment_id text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.operational_metrics (
    metric_id text primary key,
    metric_name text not null,
    metric_value numeric not null,
    dimensions jsonb,
    timestamp timestamptz not null,
    code_version text,
    git_commit text,
    deployment_id text,
    created_at timestamptz default now()
);

-- Backward-compatible additions for deployments created from an earlier version
-- of this read model. Existing execution rows remain version 1 / invalid.
do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'accounts', 'trades', 'order_events', 'price_bars',
        'equity_snapshots', 'positions', 'operational_metrics',
        'strategy_annotations'
    ]
    loop
        execute format(
            'alter table public.%I add column if not exists code_version text',
            table_name
        );
        execute format(
            'alter table public.%I add column if not exists git_commit text',
            table_name
        );
        execute format(
            'alter table public.%I add column if not exists deployment_id text',
            table_name
        );
    end loop;
end;
$$;
alter table public.order_events add column if not exists execution_id text;
alter table public.accounts alter column currency drop not null;
alter table public.accounts alter column currency drop default;
alter table public.trades add column if not exists pnl_currency text;
alter table public.order_events add column if not exists permanent_order_id text;
alter table public.order_events add column if not exists trade_currency text;
alter table public.order_events add column if not exists commission numeric;
alter table public.order_events add column if not exists commission_currency text;
alter table public.order_events add column if not exists decision_at timestamptz;
alter table public.order_events add column if not exists submitted_at timestamptz;
alter table public.order_events add column if not exists filled_at timestamptz;
alter table public.order_events add column if not exists benchmark_type text;
alter table public.order_events add column if not exists benchmark_price numeric;
alter table public.order_events add column if not exists quote_bid numeric;
alter table public.order_events add column if not exists quote_ask numeric;
alter table public.order_events add column if not exists quote_midpoint numeric;
alter table public.order_events add column if not exists stop_price numeric;
alter table public.order_events add column if not exists limit_price numeric;
alter table public.order_events add column if not exists decision_to_submission_latency_ms numeric;
alter table public.order_events add column if not exists submission_to_fill_latency_ms numeric;
alter table public.order_events add column if not exists slippage_amount numeric;
alter table public.order_events add column if not exists slippage_version integer not null default 1;
alter table public.order_events add column if not exists slippage_valid boolean not null default false;

alter table public.equity_snapshots add column if not exists base_currency text;
alter table public.equity_snapshots add column if not exists net_liquidation_currency text;
alter table public.equity_snapshots add column if not exists cash_currency text;
alter table public.equity_snapshots add column if not exists buying_power_currency text;
alter table public.equity_snapshots add column if not exists realized_pnl_currency text;
alter table public.equity_snapshots add column if not exists unrealized_pnl_currency text;
alter table public.equity_snapshots add column if not exists pnl_provenance text;
alter table public.equity_snapshots add column if not exists realized_pnl_provenance text;
alter table public.equity_snapshots add column if not exists unrealized_pnl_provenance text;
alter table public.equity_snapshots add column if not exists pnl_version integer;
alter table public.equity_snapshots add column if not exists local_realized_pnl numeric;
alter table public.equity_snapshots add column if not exists local_realized_pnl_currency text;
alter table public.equity_snapshots add column if not exists granularity text not null default 'raw';
alter table public.equity_snapshots add column if not exists period_start timestamptz;
alter table public.equity_snapshots add column if not exists event_type text;

alter table public.positions add column if not exists instrument_currency text;
alter table public.positions add column if not exists unrealized_pnl_currency text;
alter table public.accounts add column if not exists publication_version bigint not null default 0;
alter table public.trades add column if not exists publication_version bigint not null default 0;
alter table public.order_events add column if not exists publication_version bigint not null default 0;
alter table public.price_bars add column if not exists publication_version bigint not null default 0;
alter table public.equity_snapshots add column if not exists publication_version bigint not null default 0;
alter table public.positions add column if not exists publication_version bigint not null default 0;
alter table public.operational_metrics add column if not exists publication_version bigint not null default 0;
alter table public.strategy_annotations add column if not exists publication_version bigint not null default 0;

create or replace function public.reject_stale_dashboard_publication()
returns trigger
language plpgsql
as $$
begin
    if new.publication_version < old.publication_version then
        return old;
    end if;
    return new;
end;
$$;

do $$
declare
    table_name text;
begin
    foreach table_name in array array[
        'accounts', 'trades', 'order_events', 'price_bars',
        'equity_snapshots', 'positions', 'operational_metrics',
        'strategy_annotations'
    ]
    loop
        execute format(
            'drop trigger if exists reject_stale_dashboard_publication on public.%I',
            table_name
        );
        execute format(
            'create trigger reject_stale_dashboard_publication
             before update on public.%I
             for each row execute function public.reject_stale_dashboard_publication()',
            table_name
        );
    end loop;
end;
$$;
alter table public.operational_metrics add column if not exists metric_id text;
-- Give every legacy row a durable discriminator before deriving metric_id.
-- Prefer the old `id` when it exists, while retaining a migration-owned
-- identity so same-name/same-timestamp rows and concurrent inserts stay unique.
alter table public.operational_metrics
    add column if not exists legacy_row_id bigint generated by default as identity;
create sequence if not exists public.operational_metrics_legacy_metric_id_seq;
alter table public.operational_metrics
    alter column metric_id set default (
        'legacy:new:' ||
        nextval('public.operational_metrics_legacy_metric_id_seq')::text
    );

do $$
declare
    has_legacy_id boolean;
begin
    select exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'operational_metrics'
          and column_name = 'id'
    ) into has_legacy_id;

    if has_legacy_id then
        execute $sql$
            update public.operational_metrics
            set metric_id =
                'legacy:id:' || id::text || ':row:' || legacy_row_id::text
            where metric_id is null or btrim(metric_id) = ''
        $sql$;
    else
        update public.operational_metrics
        set metric_id = 'legacy:row:' || legacy_row_id::text
        where metric_id is null or btrim(metric_id) = '';
    end if;
end $$;

-- Preserve every metric if a prior interrupted/custom migration produced
-- duplicate IDs: deterministically re-key only the duplicate copies.
with ranked_metrics as (
    select
        legacy_row_id,
        metric_id,
        row_number() over (
            partition by metric_id
            order by legacy_row_id
        ) as duplicate_ordinal
    from public.operational_metrics
)
update public.operational_metrics as metric
set metric_id =
    metric.metric_id || ':dedup:' || metric.legacy_row_id::text
from ranked_metrics as ranked
where metric.legacy_row_id = ranked.legacy_row_id
  and ranked.duplicate_ordinal > 1;

do $$
begin
    if exists (
        select 1
        from public.operational_metrics
        where metric_id is null or btrim(metric_id) = ''
    ) then
        raise exception 'Null/blank operational metric IDs remain after backfill';
    end if;
    if exists (
        select 1
        from public.operational_metrics
        group by metric_id
        having count(*) > 1
    ) then
        raise exception 'Duplicate operational metric IDs remain after safe re-key';
    end if;
end $$;

alter table public.operational_metrics alter column metric_id set not null;
-- Mixed-version rollout compatibility:
-- * current publishers use metric_id as the durable conflict target;
-- * origin/main and older publishers still use (metric_name, timestamp).
-- Keep both unique identities until every publisher has been upgraded. A later,
-- separately reviewed cleanup migration may remove the legacy index and replace
-- a legacy composite primary key; do not drop either during this rollout.
create unique index if not exists operational_metrics_metric_id_key
    on public.operational_metrics(metric_id);
create unique index if not exists operational_metrics_legacy_name_timestamp_key
    on public.operational_metrics(metric_name, timestamp);

do $$
begin
    if exists (
        select 1 from public.order_events
        where execution_id is not null
        group by execution_id having count(*) > 1
    ) then
        raise exception 'Duplicate execution_id values require reviewed reconciliation before migration';
    end if;
end $$;

create index if not exists idx_trades_account_entry on public.trades(account_id, entry_time desc);
create index if not exists idx_order_events_account_time on public.order_events(account_id, occurred_at desc);
create index if not exists idx_price_bars_symbol_time on public.price_bars(symbol, timeframe, bar_start desc);
create index if not exists idx_equity_account_time on public.equity_snapshots(account_id, timestamp desc);
create index if not exists idx_equity_retention on public.equity_snapshots(account_id, granularity, timestamp);
create index if not exists idx_positions_account_symbol on public.positions(account_id, symbol);
create unique index if not exists idx_order_events_execution
    on public.order_events(execution_id) where execution_id is not null;
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
commit;