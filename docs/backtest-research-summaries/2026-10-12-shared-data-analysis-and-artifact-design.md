# Shared research data, analysis, and artifact design

**Status:** Proposed  
**Date:** 2026-10-12  
**Scope:** Approved market-data snapshots, flexible research analysis, and
remote artifact previews for the P8/P13 research surfaces.

## Decisions

1. A market-data file is not approved merely because it can be converted.
   Approval requires an immutable manifest, source and output hashes, converter
   identity, validation evidence, coverage, and a licensing decision.
2. Licensed market data and published research artifacts use separate private
   Supabase Storage buckets and separate policies. Raw bars are never exposed
   to the browser or anonymous role.
3. The dashboard analyzer is field-catalog driven. It does not hardcode an
   experiment-family size or a fixed set of ORB parameters.
4. Local SQLite remains the execution authority. Supabase remains a read model
   and immutable object distributor, not a remote execution database.
5. Legacy/pre-P2b results are excluded from current validated analysis by
   default. They may be added as a visibly distinct overlay.

## Candidate QQQ snapshot

The archive at
`D:\development\databento-data-download\databento.zip` contains:

| Property | Value |
| --- | --- |
| Databento job | `XNAS-20260429-XAVC4MHWBR` |
| Dataset/schema | `XNAS.ITCH` / `ohlcv-1m` |
| Source file | `xnas-itch-20180501-20260428.ohlcv-1m.QQQ.csv.zst` |
| Source bytes | `24,648,396` |
| Source SHA-256 | `86e447e025fd830962535176ff4da031bc018fb0042bf995681d17e7d8df2b19` |
| Converted rows | `781,508` regular-session one-minute bars |
| Converted coverage | `2018-05-01 09:30 America/New_York` through `2026-04-27 15:59 America/New_York` |
| Trading sessions | `2,008` |
| Columns | `open`, `high`, `low`, `close`, `volume` |
| Null/duplicate timestamps | `0` / `0` |
| Parquet bytes | `14,885,247` |
| Parquet SHA-256 | `9624638a8c6fe4009e14b7f93acd0d350c037219e0701fe761b1f7eb80890f48` |

The source hash matches Databento's manifest and the existing converter reports
the dataset clean. This is an **approval candidate**, not yet a globally
approved snapshot:

- It cannot reproduce registry windows beginning `2018-01-01`; the first bar
  is `2018-05-01`.
- Parquet bytes can vary with converter, pandas, PyArrow, or compression
  versions even when logical rows do not. Both a logical snapshot ID and the
  physical object hash are required.
- Databento identifies redistribution as a separately licensed use. Private
  cloud storage shared only by entitled users may be acceptable, but project
  entitlement must be confirmed before upload. The dataset must never use an
  anonymous/public bucket.

## Approved dataset manifest

An approval command should produce a canonical JSON manifest before upload:

```json
{
  "manifest_version": 1,
  "dataset_id": "sha256:<canonical-manifest-hash>",
  "status": "approved",
  "vendor": "databento",
  "vendor_job_id": "XNAS-20260429-XAVC4MHWBR",
  "source_dataset": "XNAS.ITCH",
  "source_schema": "ohlcv-1m",
  "symbol": "QQQ",
  "bar_interval": "1m",
  "session": "regular",
  "timezone": "America/New_York",
  "first_timestamp": "2018-05-01T09:30:00-04:00",
  "last_timestamp": "2026-04-27T15:59:00-04:00",
  "row_count": 781508,
  "source_sha256": "86e447e025fd830962535176ff4da031bc018fb0042bf995681d17e7d8df2b19",
  "object_sha256": "9624638a8c6fe4009e14b7f93acd0d350c037219e0701fe761b1f7eb80890f48",
  "converter": {
    "path": "scripts/convert_databento.py",
    "code_commit": "<clean-commit>",
    "pandas": "<version>",
    "pyarrow": "<version>",
    "zstandard": "<version>",
    "compression": "snappy"
  },
  "validation": {
    "null_values": 0,
    "duplicate_timestamps": 0,
    "hard_errors": [],
    "warnings": []
  },
  "license": {
    "classification": "private-internal-use",
    "approved_by": "<owner>",
    "approved_at": "<timestamp>",
    "evidence_reference": "<non-secret reference>"
  }
}
```

`RunFingerprint.data_snapshot_id` uses `dataset_id`, not a filename, mutable
alias, signed URL, or local path. The split manifest remains a separate hash.

## Supabase dataset distribution

### Storage

- Private bucket: `research-datasets`
- Object key:
  `v1/<dataset_id>/<symbol>/<object_sha256>.parquet`
- Immutable objects: publishing an existing key with different bytes is an
  error. Corrections produce a new dataset ID and object key.
- The browser anonymous key has no `storage.objects` access to this bucket.
- Dataset downloads require a trusted authenticated device. Phase 1 may reuse
  the existing trusted publisher service-role environment on owner-controlled
  devices; the key remains outside browsers and logs. Phase 2 should replace
  broad service-role downloads with short-lived, dataset-scoped signed URLs
  issued by a trusted function.

### Metadata tables

`research_datasets` stores the canonical manifest, lifecycle state
(`candidate`, `approved`, `retired`, `revoked`), manifest hash, license
classification, and timestamps.

`research_dataset_objects` stores dataset ID, symbol, object key, SHA-256,
bytes, format, and object version. Row-level policy denies anonymous access.

### Device synchronization

`python -m vibe.research_pipeline.datasets sync <dataset_id>` should:

1. Fetch the approved manifest.
2. Refuse candidate, retired, revoked, or unauthorized datasets.
3. Reuse a local content-addressed cache when the object hash matches.
4. Download to a temporary file, hash while streaming, then atomically rename.
5. Validate Parquet schema, logical row count, coverage, timezone, and null/
   duplicate constraints.
6. Materialize `<BACKTEST__DATA_DIR>/<symbol>.parquet` by link or atomic copy.
7. Record the manifest and local verification time beside the cache.

Research execution refuses a dataset alias whose verified manifest ID does not
match the run fingerprint.

## Flexible analysis workspace

The analysis unit is a validated run summary, represented identically by the
local and remote adapters:

```ts
type AnalysisRow = {
  runId: string;
  trust: Record<string, string | number | boolean | null>;
  dimensions: Record<string, string | number | boolean | null>;
  metrics: Record<string, number | null>;
};
```

Fields are namespaced rather than hardcoded:

- `identity.*`: strategy, methodology, execution and metric versions
- `parameter.*`: flattened resolved strategy parameters
- `dataset.*`: snapshot ID, symbol/universe, interval, date bounds, split
- `validation.*`: state, leakage result, finding counts, dirty-code flag
- `lineage.*`: parent, family, hypothesis
- `metric.*`: aggregate result values with calculation version and unit

The adapter also returns a field catalog describing data type, unit,
cardinality, filter operators, aggregation support, and trust restrictions.
Unknown fields remain queryable after publication without a dashboard release.

### Query model

```ts
type AnalysisQuery = {
  population: {
    includeLegacy: boolean;
    trustStates: string[];
  };
  filters: FilterExpression;
  groupBy: FieldRef[];
  measures: Array<{
    field: FieldRef;
    aggregate: "count" | "mean" | "median" | "min" | "max"
      | "sum" | "stddev" | "p10" | "p25" | "p75" | "p90";
  }>;
  orderBy: Array<{ field: FieldRef; direction: "asc" | "desc" }>;
  chart: {
    kind: "auto" | "table" | "scatter" | "line" | "bar" | "box"
      | "heatmap" | "parallel_coordinates";
    x?: FieldRef;
    y?: FieldRef;
    color?: FieldRef;
    size?: FieldRef;
    rowFacet?: FieldRef;
    columnFacet?: FieldRef;
  };
};
```

`FilterExpression` supports nested AND/OR groups and typed operators:
equals/not-equals, inclusion, numeric/date ranges, contains, exists, validation
state, and lineage membership.

There is no product rule limiting an analysis to 2–8 experiments. The full
filtered population is eligible. Operational safeguards are explicit and
data-dependent:

- estimate rows and group cardinality before execution;
- aggregate server-side when raw result rows exceed the response budget;
- cap response bytes, chart marks, and group cardinality;
- return `truncated`, sampled, and aggregation metadata prominently;
- never silently drop records or coerce incompatible metric versions;
- require users to narrow or aggregate a query whose cost exceeds policy.

### User workflow

1. Choose a population: current validated runs by default, with explicit
   legacy/pre-P2b overlay.
2. Build filters from the field catalog.
3. Add screening columns and sort rules.
4. Add any number of grouping fields subject to estimated cardinality.
5. Select measures and aggregations.
6. Let `auto` recommend a chart or assign chart encodings directly.
7. Save the analysis definition in the URL/local storage initially; a later
   `research_saved_analyses` table can support named, cross-device views.

The result table always remains available beneath a chart so every visual mark
has inspectable source groups and run lineage.

## Remote analysis read model

The current `research_publications` payload lacks flattened resolved
parameters and dataset configuration, so it cannot answer parameter-effect
questions safely. Add a derived, replaceable read model:

- `research_analysis_runs`: one row per supported terminal run and its trust
  identity;
- `research_analysis_dimensions`: `(run_id, namespace, key, typed_value...)`;
- `research_analysis_metrics`: `(run_id, key, calculation_version, unit,
  direction, numeric_value)`;
- `research_analysis_field_catalog`: available fields and capabilities.

The trusted publisher writes these rows from the same local transaction/outbox
identity as `run_summary`. Anonymous users receive SELECT-only access to
approved summary fields. A Postgres RPC accepts the typed `AnalysisQuery`,
validates field references as values (never interpolated SQL identifiers), and
returns bounded aggregate results plus cost/truncation metadata.

The local adapter implements the same contract against SQLite. The React
analyzer is therefore source-agnostic.

## Remote artifact publication and preview

### Separation from datasets

- Private bucket: `research-artifacts`
- Datasets, bars, ledgers, fills, equity-point rows, database files, secrets,
  and arbitrary local paths are forbidden.
- Artifact publication is explicit opt-in; registry references are not
  automatically uploaded.

### Manifest and object identity

`research_artifacts` stores:

- artifact ID and owning publication idempotency key;
- immutable SHA-256, bytes, media type, original display name;
- artifact class (`image`, `markdown`, `csv`, `json`, `text`, `pdf`);
- object key `v1/<sha256>/<sanitized-name>`;
- preview policy (`inline`, `download`, `metadata_only`, `blocked`);
- sanitization/version metadata and publication state;
- source registry checksum, without exposing an absolute local path.

The publisher:

1. resolves only repository-contained registry references;
2. rejects symlink escapes, external URIs, secrets, unsupported types, and
   configured size violations;
3. hashes bytes before upload;
4. uploads with an immutable content type and no overwrite;
5. inserts the manifest only after Storage confirms the object hash;
6. reconciles interrupted upload/manifest operations and garbage-collects only
   unreferenced staged objects.

### Browser access

The dashboard reads artifact metadata anonymously but cannot list the bucket.
For an `inline` artifact, a trusted Edge Function validates the artifact
manifest and returns a short-lived signed URL for that exact object. It never
accepts a raw object key or local path from the browser.

Preview behavior:

- PNG/JPEG/GIF/WebP: bounded image preview.
- Markdown: raw HTML disabled; render a conservative Markdown subset.
- CSV: bounded rows/columns with download available when policy permits.
- JSON/YAML/text/XML/HTML/logs: escaped text; HTML is never executed.
- PDF: sandboxed/download-only until a reviewed viewer is added.
- Unsupported or blocked artifacts: metadata only.

Content Security Policy keeps `object-src 'none'`, restricts images/connects to
the Supabase project, and does not permit arbitrary remote origins.

## Rollout

1. **Dataset approval tooling**
   - Add manifest generation/verification and declare converter dependencies.
   - Decide licensing for private multi-device storage.
   - Approve or reject the QQQ candidate; do not upload before this gate.
2. **Private dataset synchronization**
   - Add Storage schema/policies, publisher, downloader, atomic cache, and
     cross-device checksum test.
3. **Analysis contract/read model**
   - Add field catalog, normalized dimensions/metrics, query RPC, and matching
     local adapter.
4. **Analyzer UI**
   - Add filter builder, screening table, grouping/measures, chart encodings,
     URL state, trust overlays, and query-cost feedback.
5. **Artifact publisher**
   - Add manifest/outbox/storage reconciliation and signed-preview function.
6. **Remote preview UI**
   - Reuse the local preview components against signed remote artifacts.

P14 remains paused. None of these changes justify retiring the legacy YAML
compatibility surface until deployed read-model, storage, and reconciliation
behavior are independently validated.
