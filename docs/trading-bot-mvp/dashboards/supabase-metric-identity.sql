-- Post-rollout repair: all metric publishers must use on_conflict=metric_id.
-- Run after supabase-read-model.sql with a privileged migration identity.
-- No rows, metric IDs, timestamps or publication versions are changed.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
lock table public.operational_metrics in access exclusive mode;

do $$
declare
    legacy_columns smallint[];
    metric_column smallint;
    item record;
begin
    select array_agg(attnum order by attnum)
    into legacy_columns
    from pg_attribute
    where attrelid = 'public.operational_metrics'::regclass
      and attname in ('metric_name', 'timestamp') and not attisdropped;

    select attnum into metric_column
    from pg_attribute
    where attrelid = 'public.operational_metrics'::regclass
      and attname = 'metric_id' and not attisdropped;

    if cardinality(legacy_columns) is distinct from 2 or metric_column is null then
        raise exception 'Apply the base read-model migration first';
    end if;
    if exists (
        select 1 from public.operational_metrics
        where metric_id is null or btrim(metric_id) = ''
    ) or exists (
        select 1 from public.operational_metrics
        group by metric_id having count(*) > 1
    ) then
        raise exception 'Metric IDs require reviewed reconciliation before migration';
    end if;

    alter table public.operational_metrics alter column metric_id set not null;
    create unique index if not exists operational_metrics_metric_id_key
        on public.operational_metrics(metric_id);

    if not exists (
        select 1 from pg_index
        where indrelid = 'public.operational_metrics'::regclass
          and indisunique and indisvalid and indpred is null
          and indnkeyatts = 1 and indkey[0] = metric_column
    ) then
        raise exception 'No valid unique metric_id conflict target';
    end if;

    -- Match columns, not guessed constraint names. RESTRICT is deliberate:
    -- any dependent foreign key aborts the transaction instead of being dropped.
    for item in
        select conname
        from pg_constraint
        where conrelid = 'public.operational_metrics'::regclass
          and contype in ('p', 'u')
          and (select array_agg(k order by k) from unnest(conkey) k) = legacy_columns
    loop
        execute format(
            'alter table public.operational_metrics drop constraint %I restrict',
            item.conname
        );
    end loop;

    for item in
        select indexrelid::regclass as index_name
        from pg_index
        where indrelid = 'public.operational_metrics'::regclass
          and indisunique and indnkeyatts = 2 and indexprs is null
          and (select array_agg(k order by k)
               from unnest(indkey::smallint[]) with ordinality as keys(k, n)
               where n <= indnkeyatts) = legacy_columns
    loop
        execute format('drop index %s restrict', item.index_name);
    end loop;

    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.operational_metrics'::regclass and contype = 'p'
    ) then
        alter table public.operational_metrics
            add constraint operational_metrics_pkey primary key (metric_id);
    end if;
end;
$$;

-- Retain name/time lookup performance without forbidding distinct symbols.
create index if not exists operational_metrics_name_timestamp_idx
    on public.operational_metrics(metric_name, timestamp);
notify pgrst, 'reload schema';
commit;
