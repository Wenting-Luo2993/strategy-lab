# Local Backtest Research Viewer

Dependency-light, local-only inspection for the schema v7 research SQLite
database. The server uses Node's built-in `node:sqlite`; the browser receives
only allowlisted JSON views and static HTML/CSS/MJS.

## Runtime

Use **Node.js 22.13.0 or newer**. `tools/backtest-research-viewer/.node-version`
pins the minimum LTS baseline. Node 22.13.0 is where `node:sqlite` stopped
requiring the experimental command-line flag and includes the `DatabaseSync`
read-only API used here. Node still labels the module a release candidate
(Stability 1.2), so the viewer deliberately uses only this small, tested API
surface. The tool's `package.json` enforces the minimum. Prefer an LTS release
(Node 22.13+ or Node 24+); newer non-LTS releases also satisfy the API floor.

## Run

From the repository root:

```powershell
node tools\backtest-research-viewer\server.mjs
```

The database resolves exactly like the Python research store:

1. `RESEARCH__DB_PATH`
2. `RESEARCH__DATA_DIR\research.sqlite3`
3. OS-local application data (`%LOCALAPPDATA%\strategy-lab\research.sqlite3`
   on Windows)

Choose a different database only when the process starts:

```powershell
node tools\backtest-research-viewer\server.mjs --db C:\local\research.sqlite3
```

Open `http://127.0.0.1:4178`. `--port` may select another local port.
`--host` accepts loopback values only.

## Security boundary

- Opens one fresh `DatabaseSync` connection per request with `readOnly: true`.
- Enforces `PRAGMA query_only=ON`, `trusted_schema=OFF`, foreign keys, and a
  bounded busy timeout.
- Does **not** use SQLite's `immutable=1` URI flag: this database is WAL-backed
  and may receive newly committed runs while the viewer is open. Claiming it is
  immutable can ignore WAL changes and contradict refresh-on-demand. Fresh
  read-only/query-only connections preserve both safety and current reads.
- Binds to `127.0.0.1` by default, rejects non-loopback bind addresses,
  non-loopback peers, and non-loopback/malformed Host headers.
- Accepts only `GET` and `HEAD`.
- Has no arbitrary SQL, database picker, upload, file-reading, mutation, or
  dynamic table/column endpoint.
- Uses parameter binding for every user filter and allowlists all sort fields,
  endpoint paths, and static assets.
- Bounds page sizes to 100 and search/filter values to 100 characters.
- Bounds detail collections (metrics/findings/lineage/artifacts) and reports
  their total/truncation state rather than materializing unbounded rows.
- Redacts credential-shaped keys and JWT/Bearer-shaped values from returned
  registry/configuration data and error surfaces. Browser-visible database and
  artifact paths are home/environment-relative or basename-only.

Schema v7 durably stores aggregate evidence counts and checksums but does not
define local `trades` or `equity_points` tables. The UI therefore reports those
details as unavailable instead of implying that summary-retained rows exist.
Artifact previews are available only for database-referenced,
repository-relative files that resolve beneath the checkout after symlink
resolution. Images are limited to PNG/JPEG/GIF/WebP and 5 MB; Markdown, CSV,
JSON, text, YAML, HTML, XML, and logs are rendered as escaped text and limited
to 1 MB. External, absolute, missing, oversized, and unsupported files remain
references only.

## Test

```powershell
node --test tools\backtest-research-viewer\tests\*.test.mjs
node --check tools\backtest-research-viewer\server.mjs
node --check tools\backtest-research-viewer\public\app.mjs
```
