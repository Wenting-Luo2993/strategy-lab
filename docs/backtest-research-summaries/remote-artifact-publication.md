# Remote research artifact publication

Remote artifact publication is disabled unless a caller constructs an
`ArtifactPublicationRequest`, stages it with `ArtifactStager`, and durably
enqueues the resulting manifest in `SqliteArtifactOutbox`. Registry references
alone never trigger publication.

The default policy accepts only bounded PNG/JPEG/GIF/WebP, Markdown, CSV, JSON,
plain text/log/YAML/XML/HTML, and PDF files. Repository-relative references are
resolved after symlink expansion and must remain beneath the checkout.
Datasets, Parquet/Arrow/database files, bars, fills, trades, ledgers,
event-shaped JSON/CSV, credential-shaped filenames, and byte-visible secrets
are rejected. Policy checks run against the immutable staged copy rather than
the mutable source path. HTML/XML is stored as `text/plain`; all
log/YAML/textual previews are escaped. Image/PDF signatures are verified and
PDF is download-only. Object keys are immutable:
`v1/<content-sha256>/<sanitized-name>`.

The local outbox records a repository-free manifest and a staging-root-relative
path, uses lease claim tokens, resumes interrupted object/manifest operations
from the last confirmed phase, and never treats checksum divergence as
repairable. Reconciliation may requeue missing objects/manifests. Garbage
collection requires an aged remote staging record and a destination-side
conditional delete that rechecks the object remains unreferenced.

## External deployment gates

No code in this repository uploads an artifact until all of these gates are
completed:

1. Apply `supabase-research-schema.sql` and then
   `supabase-research-artifacts.sql` to the private research Supabase project.
2. Verify `research-artifacts` is private and that `anon`/`authenticated`
   cannot list or read `storage.objects`.
3. Deploy and independently review the `research-artifact-preview` Edge
   Function against the SQL-file contract. It must accept only `artifact_id`,
   look up the immutable row server-side, and issue an exact-object URL for no
   more than 300 seconds with `Cache-Control: no-store`.
4. Store the research service-role key only in trusted publisher/function
   secrets. Never use a `NEXT_PUBLIC_*` variable or browser bundle.
5. Run an interrupted-upload/reconciliation test in a non-production project,
   including duplicate delivery, object-without-manifest recovery, manifest
   insertion failure, checksum divergence, and unreferenced-stage cleanup.
6. Confirm CSP still has `object-src 'none'` and limits `img-src`/`connect-src`
   to the dashboard and the approved Supabase project.

Licensed datasets remain in the separate `research-datasets` design and are
never eligible for this artifact pipeline.
