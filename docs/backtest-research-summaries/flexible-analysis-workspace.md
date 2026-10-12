# Flexible research analysis workspace

This implementation realizes the analysis/read-model portions of
`2026-10-12-shared-data-analysis-and-artifact-design.md`. It does **not**
implement dataset distribution or artifact upload.

## Contract

Local SQLite, dashboard fixtures, and Supabase use the same namespaced field
model:

- `identity.*`
- `parameter.*`
- `dataset.*`
- `validation.*`
- `lineage.*`
- `trust.*`
- `metric.*`

An analysis definition contains a population, nested typed filters, screening
columns, grouping fields, aggregate measures, ordering, and chart encodings.
Grouping is not a fixed 2–8 experiment selection. Any catalog field may be
selected; actual candidate and group cardinality determine whether execution is
allowed.

Supported filter operators are equality/inequality, inclusion/exclusion,
numeric ranges, contains, and existence. Supported aggregates are count, mean,
median, min, max, sum, population standard deviation, and p10/p25/p75/p90.
Metric aggregation rejects mixed calculation versions unless the version field
is grouped.

## Local SQLite path

`vibe.research_pipeline.analysis.SqliteAnalysisAdapter` opens the research
database read-only and projects terminal run summaries, flattened fingerprint
parameters, dataset identity, validation evidence, lineage, trust, and metrics.
It provides:

- `field_catalog()`
- `estimate(query)`
- `query(query)`

The default population is `current_validated`; legacy and validation-attention
states are explicit. The adapter reads summary tables only. It never reads or
returns event-level data.

## Remote Supabase path

`supabase-research-schema.sql` defines replaceable normalized tables:

- `research_analysis_runs`
- `research_analysis_dimensions`
- `research_analysis_metrics`
- `research_analysis_field_catalog`

The trusted publisher populates these derived rows. Browser roles can select
only the catalog and execute `research_analysis_query(jsonb, boolean)`. They
cannot select normalized run data directly and cannot insert, update, or delete
analysis rows. The RPC validates field IDs against the catalog and uses them as
values, never interpolated identifiers.

The dashboard uses the anonymous key for catalog `GET` and the read-only RPC.
RPC invocation is an HTTP `POST` because PostgREST receives structured
arguments in a body; it does not grant write behavior.

## Explicit policy

| Limit | Value |
| --- | ---: |
| Candidate runs | 5,000 |
| Groups | 500 |
| Result rows | 500 |
| Chart marks | 300 |
| Response bytes | 512,000 |
| Filter nodes / depth | 40 / 5 |
| Screening columns | 24 |
| Ordering rules | 8 |
| URL state | 4,096 bytes |
| Local state | 16,384 bytes |

The result reports estimates, applied limits, aggregation, sampling (always
false), result truncation, and chart-mark truncation. Queries over candidate or
group limits fail with a narrowing instruction. The table remains beneath the
chart so each returned mark has inspectable source data.

## Deployment gates

1. Apply and review the Supabase schema/RLS/RPC in a staging project.
2. Extend the trusted publisher transaction/outbox projection to populate the
   normalized analysis tables and refresh catalog cardinalities.
3. Backfill current supported terminal runs and reconcile counts/checksums with
   local SQLite, including current `RUN-000003` data.
4. Exercise the RPC with production-like cardinality and verify statement
   timeout, response bytes, and query plans.
5. Configure only `NEXT_PUBLIC_SUPABASE_URL` and
   `NEXT_PUBLIC_SUPABASE_ANON_KEY` in the dashboard deployment.
6. Obtain product/security review of trust labels, chart semantics, and
   anonymous summary-field exposure before enabling
   `NEXT_PUBLIC_RESEARCH_DATA_SOURCE=supabase`.
