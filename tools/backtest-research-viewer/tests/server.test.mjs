import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { request } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import {
  createViewerServer,
  isLoopbackAddress,
  minimumNodeVersionSatisfied,
  openReadonlyDatabase,
  parseArgs,
  redactSecrets,
  validateBindHost,
  validateHostHeader,
} from "../server.mjs";

function createFixture() {
  const directory = mkdtempSync(join(tmpdir(), "research-viewer-"));
  const dbPath = join(directory, "research.sqlite3");
  const database = new DatabaseSync(dbPath);
  database.exec(`
    CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    INSERT INTO schema_migrations VALUES
      (1, '2026-01-01'), (2, '2026-01-01'), (3, '2026-01-01'),
      (4, '2026-01-01'), (5, '2026-01-01'), (6, '2026-01-01'),
      (7, '2026-01-01'), (8, '2026-01-01');
    CREATE TABLE runs (
      run_id TEXT PRIMARY KEY,
      display_seq INTEGER NOT NULL UNIQUE,
      display_alias TEXT NOT NULL UNIQUE,
      fingerprint TEXT NOT NULL UNIQUE,
      fingerprint_version INTEGER NOT NULL,
      strategy_id TEXT NOT NULL,
      methodology_version TEXT NOT NULL,
      state TEXT NOT NULL,
      notes TEXT,
      code_commit TEXT,
      code_dirty INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      lease_owner TEXT,
      lease_token TEXT,
      lease_expires_at TEXT,
      lineage_id TEXT,
      identity_json TEXT,
      origin_device_id TEXT
    );
    CREATE TABLE run_metrics (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      metric_key TEXT NOT NULL,
      metric_value REAL NOT NULL,
      calculation_version INTEGER NOT NULL,
      recorded_at TEXT NOT NULL
    );
    CREATE TABLE run_evidence (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      trade_count INTEGER NOT NULL,
      trade_ledger_sha256 TEXT NOT NULL,
      equity_curve_sha256 TEXT NOT NULL,
      equity_points INTEGER NOT NULL,
      retention_profile TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      recorded_at TEXT NOT NULL
    );
    CREATE TABLE run_findings (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      code TEXT NOT NULL,
      category TEXT NOT NULL,
      severity TEXT NOT NULL,
      message TEXT NOT NULL,
      metric_key TEXT,
      payload_json TEXT NOT NULL,
      recorded_at TEXT NOT NULL
    );
    CREATE TABLE validation_reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL UNIQUE,
      profile_name TEXT NOT NULL,
      scope TEXT NOT NULL,
      target_state TEXT NOT NULL,
      registry_hash_at_execution TEXT NOT NULL,
      current_registry_hash TEXT NOT NULL,
      leakage_passed INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      recorded_at TEXT NOT NULL
    );
    CREATE TABLE experiment_state_transitions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      from_state TEXT,
      to_state TEXT NOT NULL,
      reason TEXT,
      occurred_at TEXT NOT NULL
    );
    CREATE TABLE registry_records (
      record_type TEXT NOT NULL,
      legacy_id TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      canonical_sha256 TEXT NOT NULL,
      methodology_version TEXT NOT NULL,
      status TEXT,
      git_dirty INTEGER NOT NULL DEFAULT 0,
      source_path TEXT,
      imported_at TEXT NOT NULL,
      record_origin TEXT NOT NULL,
      execution_model_version INTEGER,
      execution_identity_json TEXT,
      PRIMARY KEY (record_type, legacy_id)
    );
    CREATE TABLE registry_relationships (
      record_type TEXT NOT NULL,
      legacy_id TEXT NOT NULL,
      relation TEXT NOT NULL,
      target_type TEXT NOT NULL,
      target_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL DEFAULT 0,
      resolved INTEGER NOT NULL,
      PRIMARY KEY (record_type, legacy_id, relation, ordinal)
    );
    CREATE TABLE registry_artifact_locations (
      record_type TEXT NOT NULL,
      legacy_id TEXT NOT NULL,
      ordinal INTEGER NOT NULL,
      original_location TEXT NOT NULL,
      repository_path TEXT,
      external_uri TEXT,
      PRIMARY KEY (record_type, legacy_id, ordinal)
    );
    CREATE TABLE publication_outbox (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL UNIQUE,
      idempotency_key TEXT NOT NULL UNIQUE,
      aggregate_type TEXT NOT NULL,
      aggregate_id TEXT NOT NULL,
      parent_idempotency_key TEXT,
      schema_version INTEGER NOT NULL,
      publication_version INTEGER NOT NULL DEFAULT 1,
      payload_json TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL,
      status TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      next_attempt_at TEXT,
      claimed_by TEXT,
      claimed_at TEXT,
      lease_expires_at TEXT,
      enqueued_at TEXT NOT NULL,
      published_at TEXT,
      remote_checksum TEXT,
      claim_token TEXT
    );
    CREATE TABLE publication_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL,
      attempt_number INTEGER NOT NULL,
      outcome TEXT NOT NULL,
      error TEXT,
      attempted_at TEXT NOT NULL
    );
    CREATE TABLE publication_dead_letters (
      event_id TEXT PRIMARY KEY,
      idempotency_key TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL,
      attempts INTEGER NOT NULL,
      last_error TEXT NOT NULL,
      dead_lettered_at TEXT NOT NULL,
      replay_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE publication_acknowledgements (
      idempotency_key TEXT PRIMARY KEY,
      remote_checksum TEXT NOT NULL,
      publication_version INTEGER NOT NULL,
      acknowledged_at TEXT NOT NULL,
      last_reconciled_at TEXT NOT NULL
    );
    CREATE TABLE artifact_publication_outbox (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      artifact_id TEXT NOT NULL UNIQUE,
      owning_publication_key TEXT NOT NULL,
      object_key TEXT NOT NULL UNIQUE,
      staging_path TEXT NOT NULL,
      manifest_json TEXT NOT NULL,
      manifest_sha256 TEXT NOT NULL,
      content_sha256 TEXT NOT NULL,
      size_bytes INTEGER NOT NULL,
      media_type TEXT NOT NULL,
      preview_policy TEXT NOT NULL,
      status TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT,
      next_attempt_at TEXT,
      claimed_by TEXT,
      claimed_at TEXT,
      lease_expires_at TEXT,
      claim_token TEXT,
      remote_object_sha256 TEXT,
      enqueued_at TEXT NOT NULL,
      updated_at TEXT,
      published_at TEXT
    );
  `);
  const insertRun = database.prepare(`
    INSERT INTO runs (
      run_id, display_seq, display_alias, fingerprint, fingerprint_version,
      strategy_id, methodology_version, state, notes, code_commit, code_dirty,
      created_at, updated_at, lineage_id, identity_json, origin_device_id
    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);
  for (let index = 1; index <= 4; index += 1) {
    const runId = `00000000-0000-7000-8000-${String(index).padStart(12, "0")}`;
    const timestamp = `2026-01-0${index}T12:00:00+00:00`;
    const identity = JSON.stringify({
      strategy_id: index === 4 ? "mean-reversion" : "orb",
      parameters: index === 1
        ? { opening_range_minutes: 15, api_key: "top-secret", survivorship_bias: "present" }
        : { opening_range_minutes: 5 * index },
      data_snapshot_id: `fixture-${index}`,
      split_manifest_hash: String(index).repeat(64),
      feature_set_version: 3,
      execution_model_version: index === 2 ? 5 : 6,
      metric_calculation_version: 2,
    });
    insertRun.run(
      runId,
      index,
      `RUN-${String(index).padStart(6, "0")}`,
      String(index).repeat(64),
      index === 4 ? "mean-reversion" : "orb",
      index === 3 ? "legacy-uncontrolled" : "research-store-v1",
      index === 2 ? "validation_failed" : "completed",
      index === 1 ? "authorization=Bearer abc.def.ghi" : null,
      "1234567abcdef",
      index === 3 ? 1 : 0,
      timestamp,
      timestamp,
      `lineage-${index}`,
      identity,
      "fixture-device",
    );
    database.prepare(`
      INSERT INTO run_metrics (run_id, metric_key, metric_value, calculation_version, recorded_at)
      VALUES (?, 'expectancy_r', ?, 2, ?)
    `).run(runId, index / 10, timestamp);
    database.prepare(`
      INSERT INTO run_evidence (
        run_id, trade_count, trade_ledger_sha256, equity_curve_sha256,
        equity_points, retention_profile, payload_json, recorded_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      runId,
      index * 10,
      "a".repeat(64),
      "b".repeat(64),
      index * 20,
      index === 4 ? "diagnostic" : "summary",
      JSON.stringify({
        trade_count: index * 10,
        trade_ledger_sha256: "a".repeat(64),
        equity_curve_sha256: "b".repeat(64),
        equity_points: index * 20,
        first_session: "2026-01-01",
        last_session: "2026-01-31",
        starting_equity: 100000,
        ending_equity: 100100,
        gross_pnl: 150,
        total_costs: 50,
        net_pnl: 100,
        max_observed_leverage: 1.2,
        min_cash: 50000,
        retention_profile: index === 4 ? "diagnostic" : "summary",
      }),
      timestamp,
    );
    database.prepare(`
      INSERT INTO validation_reports (
        run_id, profile_name, scope, target_state, registry_hash_at_execution,
        current_registry_hash, leakage_passed, payload_json, recorded_at
      ) VALUES (?, 'standard', 'run', ?, ?, ?, ?, '{}', ?)
    `).run(
      runId,
      index === 2 ? "validation_failed" : "completed",
      "c".repeat(64),
      "c".repeat(64),
      index === 2 ? 0 : 1,
      timestamp,
    );
    database.prepare(`
      INSERT INTO experiment_state_transitions (run_id, from_state, to_state, reason, occurred_at)
      VALUES (?, NULL, 'registered', 'registered', ?)
    `).run(runId, timestamp);
    const publicationStatus = ["published", "failed", "dead_letter", "pending"][index - 1];
    const payloadSha = `${index}`.repeat(64);
    database.prepare(`
      INSERT INTO publication_outbox (
        event_id, idempotency_key, aggregate_type, aggregate_id, schema_version,
        payload_json, payload_sha256, status, attempts, last_error, enqueued_at,
        published_at, remote_checksum
      ) VALUES (?, ?, 'run_summary', ?, 1, '{}', ?, ?, ?, ?, ?, ?, ?)
    `).run(
      `event-${index}`,
      `run:${runId}`,
      runId,
      payloadSha,
      publicationStatus,
      index,
      index === 2 ? "service_role_key=do-not-leak" : null,
      timestamp,
      index === 1 ? timestamp : null,
      index === 1 ? payloadSha : null,
    );
    if (index === 1) {
      database.prepare(`
        INSERT INTO publication_acknowledgements
        VALUES (?, ?, 1, ?, ?)
      `).run(`run:${runId}`, payloadSha, timestamp, timestamp);
    }
    if (index === 3) {
      database.prepare(`
        INSERT INTO publication_dead_letters
        VALUES (?, ?, ?, 3, 'permanent', ?, 0)
      `).run(`event-${index}`, `run:${runId}`, payloadSha, timestamp);
    }
  }
  const registryPayload = JSON.stringify({
    id: "EXP-001",
    strategy_name: "ORB",
    parameters: { api_key: "registry-secret", opening_range_minutes: 15 },
    conclusion: "Useful result",
    path: "C:\\private\\raw-output.json",
    raw_blob: "not part of the viewer contract",
  });
  database.prepare(`
    INSERT INTO registry_records (
      record_type, legacy_id, payload_json, canonical_sha256,
      methodology_version, status, git_dirty, source_path, imported_at,
      record_origin, execution_model_version, execution_identity_json
    ) VALUES ('experiment', 'EXP-001', ?, ?, 'legacy-uncontrolled',
              'completed', 1, 'legacy/EXP-001.yaml', '2026-01-01',
              'legacy_yaml', 5, '{}')
  `).run(registryPayload, "d".repeat(64));
  database.prepare(`
    INSERT INTO registry_relationships
    VALUES ('experiment', 'EXP-001', 'hypothesis', 'hypothesis', 'HYP-001', 0, 1)
  `).run();
  database.prepare(`
    INSERT INTO registry_artifact_locations
    VALUES ('experiment', 'EXP-001', 0, 'C:\\secret\\report.html', 'C:\\secret\\report.html', NULL)
  `).run();
  database.prepare(`
    INSERT INTO registry_artifact_locations
    VALUES ('experiment', 'EXP-001', 1, 'file:///C:/secret/chart.png', NULL, 'file:///C:/secret/chart.png')
  `).run();
  writeFileSync(join(directory, "preview.md"), "# Safe preview\n\n<script>alert('no')</script>\n");
  database.prepare(`
    INSERT INTO registry_artifact_locations
    VALUES ('experiment', 'EXP-001', 2, 'preview.md', 'preview.md', NULL)
  `).run();
  database.prepare(`
    INSERT INTO publication_outbox (
      event_id, idempotency_key, aggregate_type, aggregate_id, schema_version,
      payload_json, payload_sha256, status, attempts, enqueued_at, published_at,
      remote_checksum
    ) VALUES ('registry-event', 'registry:experiment:EXP-001:hash',
              'registry_record', 'experiment:EXP-001', 1, '{}', ?, 'published',
              1, '2026-01-01', '2026-01-01', ?)
  `).run("f".repeat(64), "f".repeat(64));
  const artifactId = `artifact:${"a".repeat(64)}`;
  const artifactStagingRoot = join(directory, "artifact-staging");
  const artifactBody = Buffer.from("# Published summary\n\nAggregate only.\n");
  const artifactSha = createHash("sha256").update(artifactBody).digest("hex");
  const actualRelativePath = join("v1", artifactSha, "summary.md");
  const actualArtifactPath = join(artifactStagingRoot, actualRelativePath);
  mkdirSync(join(artifactStagingRoot, "v1", artifactSha), { recursive: true });
  writeFileSync(actualArtifactPath, artifactBody, { flush: true });
  const artifactManifest = JSON.stringify({
    artifact_id: artifactId,
    owning_publication_key: "run:fixture",
    sha256: artifactSha,
    size_bytes: artifactBody.length,
    media_type: "text/markdown",
    display_name: "summary.md",
    artifact_class: "markdown",
    object_key: `v1/${artifactSha}/summary.md`,
    preview_policy: "inline",
    sanitizer_version: 1,
    manifest_version: 1,
    source_registry_checksum: "c".repeat(64),
  });
  database.prepare(`
    INSERT INTO artifact_publication_outbox (
      artifact_id, owning_publication_key, object_key, staging_path,
      manifest_json, manifest_sha256, content_sha256, size_bytes, media_type,
      preview_policy, status, enqueued_at, published_at
    ) VALUES (?, 'run:fixture', ?, ?, ?, ?, ?, ?, 'text/markdown',
              'inline', 'published', '2026-01-01', '2026-01-01')
  `).run(
    artifactId,
    `v1/${artifactSha}/summary.md`,
    actualRelativePath.replaceAll("\\", "/"),
    artifactManifest,
    createHash("sha256").update(artifactManifest).digest("hex"),
    artifactSha,
    artifactBody.length,
  );
  database.close();
  return {
    directory,
    dbPath,
    artifactId,
    artifactStagingRoot,
    cleanup() {
      rmSync(directory, { recursive: true, force: true });
    },
  };
}

async function startFixtureServer() {
  const fixture = createFixture();
  const viewer = createViewerServer({
    dbPath: fixture.dbPath,
    host: "127.0.0.1",
    port: 0,
    repositoryRoot: fixture.directory,
    artifactStagingRoot: fixture.artifactStagingRoot,
  });
  const address = await viewer.listen();
  const origin = `http://127.0.0.1:${address.port}`;
  return {
    ...fixture,
    viewer,
    origin,
    async cleanupAll() {
      await viewer.close();
      fixture.cleanup();
    },
  };
}

function rawRequest(origin, path, options = {}) {
  const target = new URL(path, origin);
  return new Promise((resolvePromise, rejectPromise) => {
    const req = request({
      hostname: target.hostname,
      port: target.port,
      path: `${target.pathname}${target.search}`,
      method: options.method || "GET",
      headers: { Host: options.host || target.host },
    }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => {
        let json = null;
        if (body && response.headers["content-type"]?.startsWith("application/json")) {
          json = JSON.parse(body);
        }
        resolvePromise({
          status: response.statusCode,
          headers: response.headers,
          body,
          json,
        });
      });
    });
    req.on("error", rejectPromise);
    req.end();
  });
}

test("pins the node:sqlite API floor at Node 22.13", () => {
  assert.equal(minimumNodeVersionSatisfied("22.12.0"), false);
  assert.equal(minimumNodeVersionSatisfied("22.13.0"), true);
  assert.equal(minimumNodeVersionSatisfied("24.0.0"), true);
});

test("enforces loopback bind and Host values", () => {
  assert.equal(isLoopbackAddress("127.0.0.1"), true);
  assert.equal(isLoopbackAddress("::ffff:127.0.0.1"), true);
  assert.equal(validateHostHeader("localhost:4178"), true);
  assert.equal(validateHostHeader("[::1]:4178"), true);
  assert.equal(validateHostHeader("research.example.com"), false);
  assert.equal(validateHostHeader("localhost/path"), false);
  assert.equal(validateHostHeader("localhost?query"), false);
  assert.equal(validateHostHeader("localhost:99999"), false);
  assert.throws(() => validateBindHost("0.0.0.0"), /non-loopback/);
  assert.throws(() => parseArgs(["--host", "192.168.1.10"]), /non-loopback/);
});

test("opens the database read-only and query-only", () => {
  const fixture = createFixture();
  try {
    const database = openReadonlyDatabase(fixture.dbPath);
    assert.equal(Object.values(database.prepare("PRAGMA query_only").get())[0], 1);
    assert.throws(() => database.exec("CREATE TABLE forbidden (id INTEGER)"), /readonly|read-only/i);
    assert.throws(() => database.exec("DELETE FROM runs"), /readonly|read-only/i);
    database.close();
  } finally {
    fixture.cleanup();
  }
});

test("redacts credential-shaped keys and values recursively", () => {
  assert.deepEqual(
    redactSecrets({
      api_key: "abc",
      nested: { Authorization: "Bearer abcdefghijklmnop", safe: "ok" },
      jwt: "eyJabcdefghijk.abcdefghijk.abcdefghijk",
    }),
    {
      api_key: "[redacted]",
      nested: { Authorization: "[redacted]", safe: "ok" },
      jwt: "[redacted]",
    },
  );
});

test("serves endpoint shapes, deterministic pagination, and refreshed rows", async () => {
  const fixture = await startFixtureServer();
  try {
    const meta = await rawRequest(fixture.origin, "/api/meta");
    assert.equal(meta.status, 200);
    assert.equal(meta.json.schemaVersion, 8);
    assert.equal(meta.json.database.mode, "read-only");
    assert.equal(meta.json.database.queryOnly, true);
    assert.equal(meta.json.database.immutableUri, false);
    assert.equal(meta.json.database.displayPath.includes(fixture.directory), false);

    const firstPage = await rawRequest(fixture.origin, "/api/runs?page=1&pageSize=2&sort=alias&direction=asc");
    const secondPage = await rawRequest(fixture.origin, "/api/runs?page=2&pageSize=2&sort=alias&direction=asc");
    assert.equal(firstPage.status, 200);
    assert.equal(firstPage.json.total, 4);
    assert.deepEqual(firstPage.json.items.map((item) => item.displayAlias), ["RUN-000001", "RUN-000002"]);
    assert.deepEqual(secondPage.json.items.map((item) => item.displayAlias), ["RUN-000003", "RUN-000004"]);
    assert.equal(firstPage.json.items[0].publicationState, "published");
    assert.equal(firstPage.json.items[1].publicationState, "failed");

    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare(`
      INSERT INTO runs (
        run_id, display_seq, display_alias, fingerprint, fingerprint_version,
        strategy_id, methodology_version, state, code_commit, code_dirty,
        created_at, updated_at, identity_json, origin_device_id
      ) VALUES ('new-run', 5, 'RUN-000005', ?, 1, 'orb', 'v1', 'registered',
                '1234567', 0, '2026-01-05', '2026-01-05', '{}', 'device')
    `).run("e".repeat(64));
    writer.close();
    const refreshed = await rawRequest(fixture.origin, "/api/runs?page=1&pageSize=10");
    assert.equal(refreshed.json.total, 5);
  } finally {
    await fixture.cleanupAll();
  }
});

test("returns bounded run detail without secrets and labels unavailable retained rows", async () => {
  const fixture = await startFixtureServer();
  try {
    const runId = "00000000-0000-7000-8000-000000000001";
    const writer = new DatabaseSync(fixture.dbPath);
    const insertMetric = writer.prepare(`
      INSERT INTO run_metrics (run_id, metric_key, metric_value, calculation_version, recorded_at)
      VALUES (?, ?, ?, 2, '2026-01-01')
    `);
    for (let index = 0; index < 255; index += 1) {
      insertMetric.run(runId, `bounded_${String(index).padStart(3, "0")}`, index);
    }
    writer.close();
    const response = await rawRequest(fixture.origin, `/api/runs/${runId}`);
    assert.equal(response.status, 200);
    assert.equal(response.json.run.displayAlias, "RUN-000001");
    assert.equal(response.json.identity.parameters.api_key, "[redacted]");
    assert.equal(response.json.run.notes, "[redacted]");
    assert.equal(response.json.publication.reconciliation, "reconciled");
    assert.equal(response.json.metrics.items.length, 250);
    assert.equal(response.json.metrics.total, 256);
    assert.equal(response.json.metrics.truncated, true);
    assert.equal(response.json.localArtifacts.trades.available, false);
    assert.match(response.json.localArtifacts.trades.reason, /Summary retention/);
    assert.equal(JSON.stringify(response.json).includes("top-secret"), false);
  } finally {
    await fixture.cleanupAll();
  }
});

test("returns registry notes, lineage, and artifact references with redaction", async () => {
  const fixture = await startFixtureServer();
  try {
    const list = await rawRequest(fixture.origin, "/api/registry");
    assert.equal(list.json.items[0].publicationState, "published");
    const response = await rawRequest(fixture.origin, "/api/registry/experiment/EXP-001");
    assert.equal(response.status, 200);
    assert.equal(response.json.payload.parameters.api_key, "[redacted]");
    assert.equal("path" in response.json.payload, false);
    assert.equal("raw_blob" in response.json.payload, false);
    assert.equal(response.json.relationships.items[0].targetId, "HYP-001");
    assert.equal(response.json.artifacts.items[0].repositoryPath, null);
    assert.equal(response.json.artifacts.items[1].externalLocation, "chart.png");
    assert.equal(response.json.artifacts.items[2].preview.available, true);
    assert.equal(response.json.artifacts.items[2].preview.kind, "text");
    assert.equal(response.json.publication.state, "published");
    assert.equal(JSON.stringify(response.json).includes("C:\\secret"), false);

    const preview = await rawRequest(fixture.origin, "/api/artifacts/experiment/EXP-001/2");
    assert.equal(preview.status, 200);
    assert.equal(preview.json.contentType, "text/markdown");
    assert.match(preview.json.content, /<script>alert\('no'\)<\/script>/);
    assert.equal((await rawRequest(fixture.origin, "/api/artifacts/experiment/EXP-001/0")).status, 403);
    assert.equal((await rawRequest(fixture.origin, "/api/artifacts/experiment/EXP-001/1")).status, 404);
    assert.equal((await rawRequest(fixture.origin, "/api/artifacts/experiment/EXP-001/99")).status, 404);
  } finally {
    await fixture.cleanupAll();
  }
});

test("serves identity-addressed local artifact metadata and safe preview", async () => {
  const fixture = await startFixtureServer();
  try {
    const encoded = encodeURIComponent(fixture.artifactId);
    const list = await rawRequest(
      fixture.origin,
      "/api/artifacts?owningPublicationKey=run%3Afixture&limit=10&offset=0",
    );
    assert.equal(list.status, 200);
    assert.equal(list.json[0].artifactId, fixture.artifactId);
    assert.equal(JSON.stringify(list.json).includes("objectKey"), false);
    assert.equal(JSON.stringify(list.json).includes("staging"), false);

    const metadata = await rawRequest(fixture.origin, `/api/artifacts/${encoded}`);
    assert.equal(metadata.status, 200);
    assert.equal(metadata.json.displayName, "summary.md");

    const preview = await rawRequest(
      fixture.origin,
      `/api/artifacts/${encoded}/preview`,
    );
    assert.equal(preview.status, 200);
    assert.equal(preview.json.artifactId, fixture.artifactId);
    assert.match(preview.json.url, /\/content$/);

    const content = await rawRequest(fixture.origin, preview.json.url);
    assert.equal(content.status, 200);
    assert.match(content.json.content, /Published summary/);
  } finally {
    await fixture.cleanupAll();
  }
});

test("rejects incomplete schema migration history before serving", () => {
  const fixture = createFixture();
  try {
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("DELETE FROM schema_migrations WHERE version = 3").run();
    writer.close();
    assert.throws(
      () => createViewerServer({ dbPath: fixture.dbPath }),
      /contiguous research schema migrations/,
    );
  } finally {
    fixture.cleanup();
  }
});

test("rejects unsafe hosts, methods, traversal, arbitrary SQL, and bad bounds", async () => {
  const fixture = await startFixtureServer();
  try {
    assert.equal((await rawRequest(fixture.origin, "/api/overview", { host: "evil.example" })).status, 403);
    assert.equal((await rawRequest(fixture.origin, "/api/overview", { method: "POST" })).status, 405);
    assert.equal((await rawRequest(fixture.origin, "/../server.mjs")).status, 404);
    assert.equal((await rawRequest(fixture.origin, "/api/sql?query=DELETE%20FROM%20runs")).status, 404);
    assert.equal((await rawRequest(fixture.origin, "/api/runs?pageSize=101")).status, 400);
    assert.equal((await rawRequest(fixture.origin, "/api/runs?page=0")).status, 400);
    assert.equal((await rawRequest(fixture.origin, "/api/runs?sort=updated%3BDELETE%20FROM%20runs")).status, 400);
  } finally {
    await fixture.cleanupAll();
  }
});

test("SQL injection strings are values and do not alter data", async () => {
  const fixture = await startFixtureServer();
  try {
    const injection = encodeURIComponent(`' OR 1=1; DELETE FROM runs; --`);
    const response = await rawRequest(fixture.origin, `/api/runs?search=${injection}`);
    assert.equal(response.status, 200);
    assert.equal(response.json.total, 0);
    const overview = await rawRequest(fixture.origin, "/api/overview");
    assert.equal(Object.values(overview.json.runs).reduce((sum, count) => sum + count, 0), 4);
  } finally {
    await fixture.cleanupAll();
  }
});

test("static UI has strict security headers and local-only copy", async () => {
  const fixture = await startFixtureServer();
  try {
    const response = await rawRequest(fixture.origin, "/");
    assert.equal(response.status, 200);
    assert.match(response.headers["content-security-policy"], /default-src 'self'/);
    assert.equal(response.headers["x-frame-options"], "DENY");
    assert.match(response.body, /LOCAL SQLITE/);
    assert.match(response.body, /never uploads or switches databases/);
    assert.equal((await rawRequest(fixture.origin, "/public/../server.mjs")).status, 404);
  } finally {
    await fixture.cleanupAll();
  }
});
