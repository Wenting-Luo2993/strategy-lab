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
import {
  isGroupingField,
  isReviewField,
} from "../../../apps/operational-metrics-dashboard/src/components/research/research-analysis-workspace.mjs";

function createFixture() {
  const directory = mkdtempSync(join(tmpdir(), "research-viewer-"));
  const dbPath = join(directory, "research.sqlite3");
  const database = new DatabaseSync(dbPath);
  database.exec(`
    CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    INSERT INTO schema_migrations VALUES
      (1, '2026-01-01'), (2, '2026-01-01'), (3, '2026-01-01'),
      (4, '2026-01-01'), (5, '2026-01-01'), (6, '2026-01-01'),
      (7, '2026-01-01'), (8, '2026-01-01'), (9, '2026-01-01'),
      (10, '2026-01-01');
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
      origin_device_id TEXT,
      segment_role TEXT
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
    CREATE TABLE run_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,
      quantity REAL NOT NULL,
      entry_price REAL NOT NULL,
      exit_price REAL,
      entry_time TEXT NOT NULL,
      exit_time TEXT,
      pnl REAL,
      commission REAL,
      initial_risk REAL,
      exit_reason TEXT,
      recorded_at TEXT NOT NULL,
      UNIQUE (run_id, sequence)
    );
    CREATE TABLE run_equity_points (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_id TEXT NOT NULL,
      sequence INTEGER NOT NULL,
      observed_at TEXT NOT NULL,
      equity REAL NOT NULL,
      recorded_at TEXT NOT NULL,
      UNIQUE (run_id, sequence)
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
      created_at, updated_at, lineage_id, identity_json, origin_device_id,
      segment_role
    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
      // Run 1 is out-of-sample, run 2 is in-sample, run 3 predates the role
      // column entirely, run 4 is a train segment.
      index === 1 ? "test" : index === 2 ? "validation" : index === 4 ? "train" : null,
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
      index === 4 ? "full" : "summary",
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
        retention_profile: index === 4 ? "full" : "summary",
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
    if (index === 4) {
      // Run 4 carries full retention, so the viewer must report its rows as
      // available. Equity dips at sequence 1 and recovers, giving the drawdown
      // scan something real to find.
      const insertTrade = database.prepare(`
        INSERT INTO run_trades (
          run_id, sequence, symbol, side, quantity, entry_price, exit_price,
          entry_time, exit_time, pnl, commission, initial_risk, exit_reason, recorded_at
        ) VALUES (?, ?, 'SPY', 'long', 10, 100, ?, ?, ?, ?, 1.5, 50, 'target', ?)
      `);
      for (let seq = 0; seq < 3; seq += 1) {
        insertTrade.run(
          runId, seq, 100 + seq, timestamp, timestamp, seq * 25, timestamp,
        );
      }
      const insertPoint = database.prepare(`
        INSERT INTO run_equity_points (run_id, sequence, observed_at, equity, recorded_at)
        VALUES (?, ?, ?, ?, ?)
      `);
      const equitySeries = [100000, 95000, 101000, 102000];
      equitySeries.forEach((equity, seq) => {
        insertPoint.run(runId, seq, `2026-01-0${seq + 1}T00:00:00+00:00`, equity, timestamp);
      });
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

async function startIntegrityFixtureServer() {
  const fixture = createFixture();
  const database = new DatabaseSync(fixture.dbPath);
  database.exec(`
    INSERT INTO schema_migrations VALUES
      (11, '2026-10-17'), (12, '2026-10-18');
    CREATE TABLE engine_integrity_reports (
      run_id TEXT PRIMARY KEY,
      status TEXT NOT NULL,
      engine_version INTEGER NOT NULL,
      metric_calculation_version INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      recorded_at TEXT NOT NULL
    );
  `);
  database.prepare(`
    INSERT INTO engine_integrity_reports VALUES (?, 'pass', 1, 2, ?, ?)
  `).run(
    "00000000-0000-7000-8000-000000000001",
    JSON.stringify({
      status: "pass",
      findings: [{
        check_id: "ENG-CROSS-001",
        category: "cross_metric_consistency",
        passed: true,
        observed: 12,
        expected: 12,
        residual: 0,
        applicable: true,
        context: { source: "ledger" },
      }],
    }),
    "2026-10-18T00:00:00Z",
  );
  database.close();
  const viewer = createViewerServer({
    dbPath: fixture.dbPath,
    host: "127.0.0.1",
    port: 0,
    repositoryRoot: fixture.directory,
    artifactStagingRoot: fixture.artifactStagingRoot,
  });
  const address = await viewer.listen();
  return {
    ...fixture,
    viewer,
    origin: `http://127.0.0.1:${address.port}`,
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

test("curates review and grouping fields semantically", () => {
  assert.equal(isReviewField("metric.expectancy_r"), true);
  assert.equal(isReviewField("identity.strategy_id"), true);
  assert.equal(isReviewField("identity.run_id"), false);
  assert.equal(isReviewField("dataset.split_manifest_hash"), false);
  assert.equal(isGroupingField({ field: "identity.strategy_id", cardinality: 8 }), true);
  assert.equal(isGroupingField({ field: "parameter.opening_range_minutes", cardinality: 12 }), true);
  assert.equal(isGroupingField({ field: "metric.expectancy_r", cardinality: 8 }), false);
  assert.equal(isGroupingField({ field: "identity.run_id", cardinality: 8 }), false);
  assert.equal(isGroupingField({ field: "parameter.threshold", cardinality: 101 }), false);
});

test("serves endpoint shapes, deterministic pagination, and refreshed rows", async () => {
  const fixture = await startFixtureServer();
  try {
    const meta = await rawRequest(fixture.origin, "/api/meta");
    assert.equal(meta.status, 200);
    assert.equal(meta.json.schemaVersion, 10);
    assert.equal(meta.json.database.mode, "read-only");
    assert.equal(meta.json.database.queryOnly, true);
    assert.equal(meta.json.database.immutableUri, false);
    assert.equal(meta.json.database.displayPath.includes(fixture.directory), false);

    const catalog = await rawRequest(fixture.origin, "/api/analysis/catalog");
    assert.equal(catalog.status, 200, catalog.body);
    assert.ok(catalog.json.some((field) => field.field === "identity.display_alias"));
    assert.ok(catalog.json.some((field) => field.field === "metric.expectancy_r"));
    const analysisQuery = {
      population: {
        includeLegacy: false,
        trustStates: ["current_validated", "validation_attention"],
      },
      filters: { op: "and", children: [] },
      screeningColumns: ["identity.display_alias", "metric.expectancy_r"],
      groupBy: [],
      measures: [],
      orderBy: [],
      chart: { kind: "table" },
    };
    const analysis = await rawRequest(
      fixture.origin,
      `/api/analysis/query?query=${encodeURIComponent(JSON.stringify(analysisQuery))}`,
    );
    assert.equal(analysis.status, 200);
    assert.equal(analysis.json.metadata.sampled, false);
    assert.ok(analysis.json.rows.length > 0);
    assert.deepEqual(
      Object.keys(analysis.json.rows[0]),
      ["identity.display_alias", "metric.expectancy_r"],
    );
    const legacyRequest = structuredClone(analysisQuery);
    legacyRequest.population.includeLegacy = true;
    const legacyResponse = await rawRequest(
      fixture.origin,
      `/api/analysis/query?query=${encodeURIComponent(JSON.stringify(legacyRequest))}`,
    );
    assert.equal(legacyResponse.status, 200);
    assert.equal(
      legacyResponse.json.rows.some((row) => row["identity.display_alias"] === "RUN-000003"),
      false,
    );
    assert.equal(
      (await rawRequest(fixture.origin, "/api/analysis/query?query=%7Bbad")).status,
      400,
    );
    assert.ok(
      [400, 431].includes(
        (
          await rawRequest(
            fixture.origin,
            `/api/analysis/query?query=${"x".repeat(16_385)}`,
          )
        ).status,
      ),
    );

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

test("filters runs by the sample role they actually ran on", async () => {
  const fixture = await startFixtureServer();
  try {
    const aliases = async (query) => {
      const response = await rawRequest(fixture.origin, `/api/runs?pageSize=50&${query}`);
      assert.equal(response.status, 200);
      return response.json.items.map((item) => item.displayAlias).sort();
    };

    assert.deepEqual(await aliases("segmentRole=test"), ["RUN-000001"]);
    assert.deepEqual(await aliases("segmentRole=validation"), ["RUN-000002"]);
    assert.deepEqual(await aliases("segmentRole=train"), ["RUN-000004"]);

    // A run recorded before the column existed is unlabelled, not in-sample
    // and not out-of-sample. Folding it into either bucket would hand a
    // reviewer in-sample numbers as out-of-sample evidence.
    assert.deepEqual(await aliases("segmentRole=unlabelled"), ["RUN-000003"]);

    const all = await aliases("");
    assert.equal(all.length, 4);
  } finally {
    await fixture.cleanupAll();
  }
});

test("surfaces the sample role on each run so it cannot be mistaken", async () => {
  const fixture = await startFixtureServer();
  try {
    const response = await rawRequest(fixture.origin, "/api/runs?pageSize=50");
    const byAlias = Object.fromEntries(
      response.json.items.map((item) => [item.displayAlias, item]),
    );

    assert.equal(byAlias["RUN-000001"].segmentRole, "test");
    assert.equal(byAlias["RUN-000003"].segmentRole, null);

    const labels = (alias) => byAlias[alias].badges.map((badge) => badge.label);
    assert.ok(labels("RUN-000001").includes("Out-of-sample (test)"));
    assert.ok(labels("RUN-000002").includes("In-sample (validation)"));
    assert.ok(labels("RUN-000003").includes("Sample role unlabelled"));

    const detail = await rawRequest(
      fixture.origin,
      "/api/runs/00000000-0000-7000-8000-000000000001",
    );
    assert.equal(detail.json.run.segmentRole, "test");
  } finally {
    await fixture.cleanupAll();
  }
});

test("surfaces engine integrity while historical decisions remain not assessed", async () => {
  const fixture = await startIntegrityFixtureServer();
  try {
    const list = await rawRequest(fixture.origin, "/api/runs?page=1&pageSize=10");
    const assessed = list.json.items.find((item) => item.displayAlias === "RUN-000001");
    const historical = list.json.items.find((item) => item.displayAlias === "RUN-000002");
    assert.equal(assessed.engineIntegrity, "pass");
    assert.equal(historical.engineIntegrity, "not_assessed");

    const detail = await rawRequest(
      fixture.origin,
      "/api/runs/00000000-0000-7000-8000-000000000001",
    );
    assert.equal(detail.json.engineIntegrity.status, "pass");
    assert.equal(
      detail.json.engineIntegrity.report.findings[0].check_id,
      "ENG-CROSS-001",
    );
    assert.deepEqual(
      detail.json.engineIntegrity.report.findings[0].context,
      { source: "ledger" },
    );
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

test("reports retained rows as available only when the rows are actually present", async () => {
  const fixture = await startFixtureServer();
  try {
    // Run 4 declares full retention AND has rows: available.
    const full = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-000000000004");
    assert.equal(full.status, 200);
    assert.equal(full.json.localArtifacts.trades.available, true);
    assert.equal(full.json.localArtifacts.trades.rowCount, 3);
    assert.equal(full.json.localArtifacts.trades.reason, null);
    assert.equal(full.json.localArtifacts.equity.available, true);
    assert.equal(full.json.localArtifacts.equity.rowCount, 4);

    // Availability must be measured, not inferred from the profile: deleting the
    // rows while the profile still says "full" has to flip availability to false.
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("DELETE FROM run_trades WHERE run_id = ?")
      .run("00000000-0000-7000-8000-000000000004");
    writer.close();
    const emptied = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-000000000004");
    assert.equal(emptied.json.localArtifacts.trades.available, false);
    assert.equal(emptied.json.localArtifacts.trades.rowCount, 0);
    assert.match(emptied.json.localArtifacts.trades.reason, /no retained trade rows/i);
  } finally {
    await fixture.cleanupAll();
  }
});

test("exposes finding evidence with recursive redaction and tolerates legacy payloads", async () => {
  const fixture = await startFixtureServer();
  try {
    const runId = "00000000-0000-7000-8000-000000000001";
    const writer = new DatabaseSync(fixture.dbPath);
    const insert = writer.prepare(`
      INSERT INTO run_findings
        (run_id, code, category, severity, message, payload_json, recorded_at)
      VALUES (?, ?, 'plausibility', 'review', 'Review session returns', ?, '2026-01-01')
    `);
    insert.run(runId, "PLAUS-006", JSON.stringify({
      observed: 0.9,
      expected: "<= 0.5",
      context: {
        full_sample_sharpe: 4,
        sharpe_without_largest_session: 0.1,
        api_key: "do-not-leak",
      },
      privateField: "not-exposed",
    }));
    insert.run(runId, "LEGACY", "null");
    insert.run(runId, "MALFORMED", "{bad");
    writer.close();
    const response = await rawRequest(fixture.origin, `/api/runs/${runId}`);
    assert.equal(response.status, 200);
    const finding = response.json.findings.items.find((item) => item.code === "PLAUS-006");
    assert.equal(finding.observed, 0.9);
    assert.equal(finding.expected, "<= 0.5");
    assert.deepEqual(finding.context, {
      full_sample_sharpe: 4,
      sharpe_without_largest_session: 0.1,
      api_key: "[redacted]",
    });

    assert.equal("payloadJson" in finding, false);
    assert.equal("privateField" in finding, false);
    for (const code of ["LEGACY", "MALFORMED"]) {
      const legacy = response.json.findings.items.find((item) => item.code === code);
      assert.equal(legacy.observed, null);
      assert.equal(legacy.expected, null);
      assert.deepEqual(legacy.context, {});
    }
  } finally {
    await fixture.cleanupAll();
  }
});

test("experiment endpoints preserve explicit membership and gracefully support v10 history", async () => {
  const fixture = await startFixtureServer();
  try {
    const before = await rawRequest(fixture.origin, "/api/experiments");
    assert.equal(before.status, 200);
    assert.equal(before.json.available, false);
    assert.equal(before.json.total, 0);
    assert.ok(before.json.ungroupedRuns > 0);
    const writer = new DatabaseSync(fixture.dbPath);
    writer.exec(`
      INSERT INTO schema_migrations VALUES (11, '2026-10-16');
      CREATE TABLE research_experiments (
        experiment_id TEXT PRIMARY KEY, title TEXT, state TEXT, definition_json TEXT,
        result_json TEXT, created_at TEXT, updated_at TEXT
      );
      CREATE TABLE research_experiment_runs (
        experiment_id TEXT, fold_index INTEGER, candidate_id TEXT, segment_role TEXT, run_id TEXT
      );
    `);
    const insert = writer.prepare("INSERT INTO research_experiments VALUES (?, ?, ?, ?, ?, ?, ?)");
    insert.run(
      "study-1", "Controlled ORB comparison", "completed",
      JSON.stringify({
        symbol: "QQQ", axes: [{ name: "orb_minutes", values: [5, 15, 30] }],
        plan: { folds: [{}] }, reference_configuration: { api_key: "do-not-leak" },
      }),
      JSON.stringify({ n_folds_completed: 1, recording_errors: [] }),
      "2026-10-16", "2026-10-16",
    );
    insert.run("study-large", "Too many candidates for a complete preview", "running",
      JSON.stringify({ candidates: Array.from({ length: 501 }, (_, id) => ({ id })) }),
      null, "2026-10-15", "2026-10-15");
    writer.prepare("INSERT INTO research_experiment_runs VALUES (?, ?, ?, ?, ?)").run(
      "study-1", 0, "candidate-15", "test", "00000000-0000-7000-8000-000000000004",
    );
    writer.prepare("DELETE FROM validation_reports WHERE run_id = ?")
      .run("00000000-0000-7000-8000-000000000004");
    writer.close();
    const list = await rawRequest(fixture.origin, "/api/experiments");
    assert.equal(list.json.available, true);
    assert.equal(list.json.items[0].linkedRuns, 1);
    assert.equal(list.json.items[0].expectedFolds, 1);
    assert.equal(list.json.ungroupedRuns, before.json.ungroupedRuns - 1);
    assert.equal(list.json.items[1].payloadUnavailable, true);
    assert.equal(list.json.items[1].expectedFolds, null);
    const detail = await rawRequest(fixture.origin, "/api/experiments/study-1");
    assert.equal(detail.status, 200);
    assert.equal(detail.json.runs.items[0].displayAlias, "RUN-000004");
    assert.equal(detail.json.runs.items[0].foldIndex, 0);
    assert.equal(detail.json.runs.items[0].segmentRole, "test");
    assert.equal(detail.json.definition.reference_configuration.api_key, "[redacted]");
    const large = await rawRequest(fixture.origin, "/api/experiments/study-large");
    assert.equal(large.json.payloadUnavailable, true);
    assert.equal((await rawRequest(fixture.origin, "/api/experiments/missing")).status, 404);
    assert.equal((await rawRequest(fixture.origin, "/api/experiments?pageSize=101")).status, 400);
    const run = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-000000000004");
    assert.ok(run.json.badges.some((item) => item.label === "Not validated"));
  } finally {
    await fixture.cleanupAll();
  }
});

test("serves retained trade rows and 404s for an unknown run", async () => {
  const fixture = await startFixtureServer();
  try {
    const response = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-000000000004/trades");
    assert.equal(response.status, 200);
    assert.equal(response.json.total, 3);
    assert.equal(response.json.returned, 3);
    assert.equal(response.json.truncated, false);
    assert.equal(response.json.trades[0].symbol, "SPY");
    assert.equal(response.json.trades[0].sequence, 0);
    assert.equal(response.json.trades[2].pnl, 50);

    const missing = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-00000000009f/trades");
    assert.equal(missing.status, 404);
  } finally {
    await fixture.cleanupAll();
  }
});

test("serves the equity curve with drawdown derived from the full series", async () => {
  const fixture = await startFixtureServer();
  try {
    const response = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-000000000004/equity");
    assert.equal(response.status, 200);
    assert.equal(response.json.total, 4);
    assert.equal(response.json.stride, 1);
    assert.equal(response.json.downsampled, false);
    assert.equal(response.json.startingEquity, 100000);
    assert.equal(response.json.endingEquity, 102000);
    assert.equal(response.json.startingCapital, 100000);
    assert.equal(response.json.sessionReturns.length, 4);
    assert.equal(response.json.sessionReturns[0].return, 0);
    assert.ok(Math.abs(response.json.sessionReturns[1].return + 0.05) < 1e-9);
    // Peak 100000 -> trough 95000 is a 5% drawdown.
    assert.ok(Math.abs(response.json.maxDrawdown + 0.05) < 1e-9);
    assert.equal(response.json.maxDrawdownAt, "2026-01-02T00:00:00+00:00");

    const missing = await rawRequest(fixture.origin, "/api/runs/00000000-0000-7000-8000-00000000009f/equity");
    assert.equal(missing.status, 404);
  } finally {
    await fixture.cleanupAll();
  }
});

test("session returns use full-curve closes, even when a close is skipped by the display stride", async () => {
  const fixture = await startFixtureServer();
  const runId = "00000000-0000-7000-8000-000000000004";
  try {
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("DELETE FROM run_equity_points WHERE run_id = ?").run(runId);
    const insert = writer.prepare(`
      INSERT INTO run_equity_points (run_id, sequence, observed_at, equity, recorded_at)
      VALUES (?, ?, ?, ?, '2026-01-01')
    `);
    writer.exec("BEGIN");
    for (let index = 0; index < 5001; index += 1) {
      insert.run(runId, index,
        index < 2500 ? "2026-01-02T16:00:00-05:00" : "2026-01-05T16:00:00-05:00",
        index === 2499 ? 90000 : 100000);
    }
    writer.exec("COMMIT");
    writer.close();
    const response = await rawRequest(fixture.origin, `/api/runs/${runId}/equity`);
    assert.equal(response.status, 200);
    assert.equal(response.json.downsampled, true);
    assert.equal(response.json.points.some((point) => point.sequence === 2499), false);
    assert.equal(response.json.sessionReturns.length, 2);
    assert.equal(response.json.sessionReturns[0].return, -0.1);
    assert.ok(Math.abs(response.json.sessionReturns[1].return - 1 / 9) < 1e-9);
  } finally {
    await fixture.cleanupAll();
  }
});

test("missing starting capital is explicit rather than inferred from the first equity mark", async () => {
  const fixture = await startFixtureServer();
  const runId = "00000000-0000-7000-8000-000000000004";
  try {
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("UPDATE run_evidence SET payload_json = '{}' WHERE run_id = ?").run(runId);
    writer.close();
    const response = await rawRequest(fixture.origin, `/api/runs/${runId}/equity`);
    assert.equal(response.status, 200);
    assert.equal(response.json.startingCapital, null);
    assert.equal(response.json.sessionReturns[0].return, null);
    assert.equal(response.json.startingEquity, 100000);
  } finally {
    await fixture.cleanupAll();
  }
});

test("first session return uses recorded capital, not the first equity observation", async () => {
  const fixture = await startFixtureServer();
  const runId = "00000000-0000-7000-8000-000000000004";
  try {
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("UPDATE run_evidence SET payload_json = ? WHERE run_id = ?").run(
      JSON.stringify({ starting_equity: 125000 }), runId,
    );
    writer.close();
    const response = await rawRequest(fixture.origin, `/api/runs/${runId}/equity`);
    assert.equal(response.status, 200);
    assert.equal(response.json.startingCapital, 125000);
    assert.equal(response.json.startingEquity, 100000);
    assert.equal(response.json.sessionReturns[0].return, -0.2);
    assert.equal(response.json.points[0].drawdown, -0.2);
    assert.equal(response.json.maxDrawdown, -0.24);
    assert.equal(response.json.maxSessionDrawdown, -0.24);
  } finally {
    await fixture.cleanupAll();
  }
});

test("keeps a trough that falls between strides out of the returned points but inside the drawdown", async () => {
  const fixture = await startFixtureServer();
  try {
    const runId = "00000000-0000-7000-8000-000000000004";
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("DELETE FROM run_equity_points WHERE run_id = ?").run(runId);
    const insert = writer.prepare(`
      INSERT INTO run_equity_points (run_id, sequence, observed_at, equity, recorded_at)
      VALUES (?, ?, ?, ?, '2026-01-01')
    `);
    writer.exec("BEGIN");
    // 10_001 points forces stride 3 against MAX_DETAIL_ROWS = 5_000. The curve
    // is flat except for one deep trough at sequence 5_000, which is not
    // divisible by 3 and so can never appear in the returned points.
    const troughSequence = 5_000;
    const totalPoints = 10_001;
    for (let seq = 0; seq < totalPoints; seq += 1) {
      insert.run(runId, seq, `2026-01-01T00:00:00+00:00`, seq === troughSequence ? 80_000 : 100_000);
    }
    writer.exec("COMMIT");
    writer.close();

    const response = await rawRequest(fixture.origin, `/api/runs/${runId}/equity`);
    assert.equal(response.status, 200);
    assert.equal(response.json.total, totalPoints);
    assert.equal(response.json.stride, 3);
    assert.equal(response.json.downsampled, true);

    // The trough is genuinely absent from the returned series...
    const returnedTrough = response.json.points.find((point) => point.sequence === troughSequence);
    assert.equal(returnedTrough, undefined);
    const subsetDrawdown = response.json.points.reduce((worst, point) => {
      return Math.min(worst, (point.equity - 100_000) / 100_000);
    }, 0);
    assert.equal(subsetDrawdown, 0);

    // ...yet the reported drawdown still finds it. This is the assertion that
    // fails if the drawdown is ever derived from the downsampled subset.
    assert.ok(Math.abs(response.json.maxDrawdown + 0.2) < 1e-9);
  } finally {
    await fixture.cleanupAll();
  }
});

test("separates intraday drawdown from drawdown measured on session closes", async () => {
  const fixture = await startFixtureServer();
  try {
    const runId = "00000000-0000-7000-8000-000000000004";
    const writer = new DatabaseSync(fixture.dbPath);
    writer.prepare("DELETE FROM run_equity_points WHERE run_id = ?").run(runId);
    const insert = writer.prepare(`
      INSERT INTO run_equity_points (run_id, sequence, observed_at, equity, recorded_at)
      VALUES (?, ?, ?, ?, '2026-01-01')
    `);
    // Three sessions, each dipping hard intraday but closing at a new high. The
    // intraday drawdown is real heat; the closed-equity drawdown is zero.
    const sessions = [
      ["2026-01-05", [100_000, 90_000, 101_000]],
      ["2026-01-06", [101_000, 91_000, 102_000]],
      ["2026-01-07", [102_000, 92_000, 103_000]],
    ];
    let sequence = 0;
    for (const [date, series] of sessions) {
      series.forEach((equity, index) => {
        insert.run(runId, sequence, `${date}T1${index}:00:00-05:00`, equity);
        sequence += 1;
      });
    }
    writer.close();

    const response = await rawRequest(fixture.origin, `/api/runs/${runId}/equity`);
    assert.equal(response.status, 200);
    assert.equal(response.json.sessionCount, 3);
    // Deepest intraday trough is the first session: 100_000 -> 90_000. Later
    // troughs are deeper in dollars but shallower against their higher peaks.
    assert.ok(Math.abs(response.json.maxDrawdown + 0.1) < 1e-9);
    assert.equal(response.json.maxDrawdownAt, "2026-01-05T11:00:00-05:00");
    // Every session closes higher than the last, so closed equity never retreats.
    assert.equal(response.json.maxSessionDrawdown, 0);
    assert.equal(response.json.maxSessionDrawdownAt, null);
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
    assert.match(response.body, /<dialog id="experiment-dialog"[^>]*aria-labelledby="experiment-dialog-title"/);
    assert.match(response.body, /aria-label="Close experiment"/);
    assert.doesNotMatch(response.body, /research-analysis-workspace|analysis-workspace/);
    assert.equal(response.body.includes("Screening columns"), false);
    const workspaceScript = await rawRequest(
      fixture.origin,
      "/research-analysis-workspace.mjs",
    );
    assert.equal(workspaceScript.status, 404);
    const workspaceStyles = await rawRequest(
      fixture.origin,
      "/research-analysis-workspace.css",
    );
    assert.equal(workspaceStyles.status, 404);
    const charts = await rawRequest(fixture.origin, "/run-charts.mjs");
    assert.equal(charts.status, 200);
    assert.match(charts.headers["content-type"], /text\/javascript/);
    assert.match(charts.body, /export function mountPerformanceCharts/);
    const app = await rawRequest(fixture.origin, "/app.mjs");
    assert.equal(app.status, 200);
    assert.match(app.body, /import \{ mountPerformanceCharts \} from "\.\/run-charts\.mjs"/);
    assert.doesNotMatch(app.body, /initializeAnalysisWorkspace|workspace\.adapter/);
    const styles = await rawRequest(fixture.origin, "/styles.css");
    assert.match(styles.body, /\.experiment-dialog\s*\{[^}]*width: 90vw;[^}]*height: 90dvh;/);
    assert.equal((await rawRequest(fixture.origin, "/public/../server.mjs")).status, 404);
  } finally {
    await fixture.cleanupAll();
  }
});
