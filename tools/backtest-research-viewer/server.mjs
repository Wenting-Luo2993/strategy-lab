import { DatabaseSync } from "node:sqlite";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { basename, dirname, extname, isAbsolute, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir, platform } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = join(HERE, "public");
// Minimum, not exact. Pinning the viewer to one version meant every additive
// migration broke it outright: schema v9 added retained trade/equity tables and
// the viewer stopped opening the database at all rather than degrading. The
// real guard is assertSchemaContract, which verifies the specific tables and
// columns actually read, so a newer schema that only adds tables is safe while
// one that renames or drops something still fails loudly.
const MINIMUM_SCHEMA_VERSION = 9;
const MAX_DETAIL_ROWS = 5_000;
const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 4178;
const MAX_PAGE_SIZE = 100;
const MAX_SEARCH_LENGTH = 100;
const MAX_ANALYSIS_QUERY_LENGTH = 16_384;
const MAX_JSON_DEPTH = 12;
const MAX_JSON_ITEMS = 500;
const MAX_JSON_CHARS = 1_000_000;
const MAX_ARTIFACT_IMAGE_BYTES = 5_000_000;
const MAX_ARTIFACT_TEXT_BYTES = 1_000_000;
const ARTIFACT_FORMATS = new Map([
  [".png", { kind: "image", contentType: "image/png" }],
  [".jpg", { kind: "image", contentType: "image/jpeg" }],
  [".jpeg", { kind: "image", contentType: "image/jpeg" }],
  [".gif", { kind: "image", contentType: "image/gif" }],
  [".webp", { kind: "image", contentType: "image/webp" }],
  [".md", { kind: "text", contentType: "text/markdown" }],
  [".csv", { kind: "text", contentType: "text/csv" }],
  [".json", { kind: "text", contentType: "application/json" }],
  [".txt", { kind: "text", contentType: "text/plain" }],
  [".log", { kind: "text", contentType: "text/plain" }],
  [".yaml", { kind: "text", contentType: "application/yaml" }],
  [".yml", { kind: "text", contentType: "application/yaml" }],
  [".html", { kind: "text", contentType: "text/html" }],
  [".xml", { kind: "text", contentType: "application/xml" }],
]);
const DETAIL_LIMITS = Object.freeze({
  metrics: 250,
  findings: 250,
  transitions: 500,
  relationships: 250,
  artifacts: 250,
});
const STATIC_FILES = new Map([
  ["/", [join(PUBLIC_DIR, "index.html"), "text/html; charset=utf-8"]],
  ["/index.html", [join(PUBLIC_DIR, "index.html"), "text/html; charset=utf-8"]],
  ["/app.mjs", [join(PUBLIC_DIR, "app.mjs"), "text/javascript; charset=utf-8"]],
  ["/styles.css", [join(PUBLIC_DIR, "styles.css"), "text/css; charset=utf-8"]],
  [
    "/research-analysis-workspace.mjs",
    [
      resolve(HERE, "..", "..", "apps", "operational-metrics-dashboard", "src", "components", "research", "research-analysis-workspace.mjs"),
      "text/javascript; charset=utf-8",
    ],
  ],
  [
    "/research-analysis-workspace.css",
    [
      resolve(HERE, "..", "..", "apps", "operational-metrics-dashboard", "src", "components", "research", "research-analysis-workspace.css"),
      "text/css; charset=utf-8",
    ],
  ],
]);
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost"]);
const SYNC_MARKERS = [
  "onedrive",
  "dropbox",
  "google drive",
  "googledrive",
  "icloud drive",
  "icloud~",
  "box sync",
  "creative cloud files",
];
const SECRET_KEY = /(authorization|api[_-]?key|secret|password|passwd|token|credential|service[_-]?role|webhook)/i;
const SECRET_VALUE = /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b|(?:bearer\s+)[A-Za-z0-9._~+/-]{3,}|(?:authorization|api[_-]?key|secret|password|token|credential|service[_-]?role|webhook)\s*[:=]\s*\S+/i;
/**
 * Metrics shown directly in the run list.
 *
 * A study produces dozens of runs that share a strategy, a state and a set of
 * trust badges, so those columns cannot tell them apart. These four are the
 * smallest set that answers "which run is this, and is it worth opening?".
 * The keys are hardcoded constants and are interpolated into SQL; never build
 * this list from user input.
 */
const HEADLINE_METRICS = Object.freeze([
  "n_trades",
  "expectancy_r",
  "win_rate",
  "max_drawdown",
]);

const RUN_SORTS = Object.freeze({
  updated: "r.updated_at",
  created: "r.created_at",
  alias: "r.display_seq",
  strategy: "r.strategy_id",
  state: "r.state",
  return: "CAST(json_extract(e.payload_json, '$.net_pnl') AS REAL)",
  expectancy: `(SELECT m.metric_value FROM run_metrics m
     WHERE m.run_id = r.run_id AND m.metric_key = 'expectancy_r' ORDER BY m.id DESC LIMIT 1)`,
  drawdown: `(SELECT m.metric_value FROM run_metrics m
     WHERE m.run_id = r.run_id AND m.metric_key = 'max_drawdown' ORDER BY m.id DESC LIMIT 1)`,
  trades: "e.trade_count",
});
const REGISTRY_SORTS = Object.freeze({
  recorded: "rr.imported_at",
  id: "rr.legacy_id",
  type: "rr.record_type",
  status: "rr.status",
});
const PUBLICATION_SORTS = Object.freeze({
  enqueued: "po.enqueued_at",
  sequence: "po.sequence",
  status: "po.status",
  type: "po.aggregate_type",
});

export function minimumNodeVersionSatisfied(version = process.versions.node) {
  const [major, minor] = version.split(".").map(Number);
  return major > 22 || (major === 22 && minor >= 13);
}

export function isLoopbackAddress(value) {
  if (!value) return false;
  const normalized = value.toLowerCase().replace(/^::ffff:/, "").replace(/^\[|\]$/g, "");
  return LOOPBACK_HOSTS.has(normalized);
}

export function validateBindHost(host) {
  if (!isLoopbackAddress(host)) {
    throw new Error(`Refusing non-loopback bind address ${JSON.stringify(host)}.`);
  }
  return host;
}

export function validateHostHeader(value) {
  if (typeof value !== "string" || value.length === 0 || value.length > 255) return false;
  const match = value.match(/^(localhost|127\.0\.0\.1|\[::1\])(?::(\d{1,5}))?$/i);
  if (!match) return false;
  return !match[2] || (Number(match[2]) >= 1 && Number(match[2]) <= 65535);
}

export function resolveDefaultDbPath(env = process.env) {
  if (env.RESEARCH__DB_PATH) return resolve(env.RESEARCH__DB_PATH);
  if (env.RESEARCH__DATA_DIR) return resolve(env.RESEARCH__DATA_DIR, "research.sqlite3");
  if (platform() === "win32") {
    return resolve(env.LOCALAPPDATA || join(homedir(), "AppData", "Local"), "strategy-lab", "research.sqlite3");
  }
  if (platform() === "darwin") {
    return resolve(homedir(), "Library", "Application Support", "strategy-lab", "research.sqlite3");
  }
  return resolve(env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "strategy-lab", "research.sqlite3");
}

export function assertSafeDbPath(dbPath) {
  const absolute = resolve(dbPath);
  const loweredParts = absolute.split(/[\\/]+/).map((part) => part.toLowerCase());
  const unsafe = loweredParts.find((part) => SYNC_MARKERS.some((marker) => part.includes(marker)));
  if (unsafe) {
    throw new Error(`Refusing research database in cloud-synced path component ${JSON.stringify(unsafe)}.`);
  }
  if (!isAbsolute(absolute)) throw new Error("Research database path must resolve absolutely.");
  const info = statSync(absolute);
  if (!info.isFile()) throw new Error("Research database path is not a file.");
  return absolute;
}

export function parseArgs(argv = process.argv.slice(2), env = process.env) {
  let dbPath = null;
  let host = DEFAULT_HOST;
  let port = DEFAULT_PORT;
  let help = false;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      help = true;
    } else if (argument === "--db") {
      dbPath = requiredValue(argv, ++index, "--db");
    } else if (argument === "--host") {
      host = requiredValue(argv, ++index, "--host");
    } else if (argument === "--port") {
      port = parseInteger(requiredValue(argv, ++index, "--port"), "port", 1, 65535);
    } else {
      throw new Error(`Unknown argument ${JSON.stringify(argument)}.`);
    }
  }
  return {
    dbPath: resolve(dbPath || resolveDefaultDbPath(env)),
    host: validateBindHost(host),
    port,
    help,
  };
}

function requiredValue(argv, index, flag) {
  const value = argv[index];
  if (!value || value.startsWith("--")) throw new Error(`${flag} requires a value.`);
  return value;
}

function parseInteger(value, name, minimum, maximum) {
  if (!/^\d+$/.test(String(value))) throw new HttpError(400, `${name} must be an integer.`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new HttpError(400, `${name} must be between ${minimum} and ${maximum}.`);
  }
  return parsed;
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export function openReadonlyDatabase(dbPath) {
  const database = new DatabaseSync(dbPath, {
    open: true,
    readOnly: true,
    allowExtension: false,
    enableDoubleQuotedStringLiterals: false,
  });
  database.exec("PRAGMA query_only = ON");
  database.exec("PRAGMA trusted_schema = OFF");
  database.exec("PRAGMA foreign_keys = ON");
  database.exec("PRAGMA busy_timeout = 5000");
  return database;
}

function withDatabase(dbPath, callback) {
  const database = openReadonlyDatabase(dbPath);
  try {
    return callback(database);
  } finally {
    database.close();
  }
}

export function inspectDatabase(dbPath) {
  return withDatabase(dbPath, (database) => {
    const versions = database.prepare(
      "SELECT version FROM schema_migrations ORDER BY version",
    ).all().map((row) => Number(row.version));
    const contiguous = versions.every((version, index) => version === index + 1);
    if (!contiguous || versions.length < MINIMUM_SCHEMA_VERSION) {
      throw new Error(
        `Viewer requires contiguous research schema migrations 1-${MINIMUM_SCHEMA_VERSION} `
        + `or newer; found [${versions.join(", ")}].`,
      );
    }
    assertSchemaContract(database);
    const integrity = database.prepare("PRAGMA quick_check").all().map((row) => Object.values(row)[0]);
    if (integrity.length !== 1 || integrity[0] !== "ok") {
      throw new Error("Research database failed PRAGMA quick_check.");
    }
    const queryOnly = Number(Object.values(database.prepare("PRAGMA query_only").get())[0]);
    if (queryOnly !== 1) throw new Error("SQLite query_only enforcement is unavailable.");
    return { version: versions.length, integrity: "ok", queryOnly: true };
  });
}

function assertSchemaContract(database) {
  const required = {
    runs: ["run_id", "display_seq", "identity_json", "origin_device_id"],
    run_metrics: ["run_id", "metric_key", "metric_value", "calculation_version"],
    run_evidence: ["run_id", "trade_count", "retention_profile", "payload_json"],
    run_findings: ["run_id", "severity", "message"],
    validation_reports: ["run_id", "leakage_passed", "payload_json"],
    experiment_state_transitions: ["run_id", "to_state", "occurred_at"],
    registry_records: ["record_type", "legacy_id", "payload_json", "record_origin"],
    registry_relationships: ["record_type", "legacy_id", "target_type", "target_id"],
    registry_artifact_locations: ["record_type", "legacy_id", "repository_path", "external_uri"],
    publication_outbox: ["sequence", "idempotency_key", "aggregate_type", "aggregate_id", "claim_token"],
    publication_dead_letters: ["event_id", "dead_lettered_at", "replay_count"],
    publication_acknowledgements: ["idempotency_key", "remote_checksum", "last_reconciled_at"],
    artifact_publication_outbox: [
      "artifact_id", "owning_publication_key", "object_key", "staging_path",
      "manifest_sha256", "content_sha256", "preview_policy", "status", "claim_token",
    ],
    run_trades: [
      "run_id", "sequence", "symbol", "side", "entry_time", "exit_time",
      "entry_price", "exit_price", "quantity", "pnl", "exit_reason",
    ],
    run_equity_points: ["run_id", "sequence", "observed_at", "equity"],
  };
  for (const [table, columns] of Object.entries(required)) {
    const present = new Set(database.prepare(`PRAGMA table_info(${table})`).all().map((row) => row.name));
    const missing = columns.filter((column) => !present.has(column));
    if (missing.length) {
      throw new Error(`Research schema v${MINIMUM_SCHEMA_VERSION} contract is missing ${table}.${missing.join(`, ${table}.`)}.`);
    }
  }
}

function safeJsonParse(value, fallback = null) {
  if (typeof value !== "string" || value.length > MAX_JSON_CHARS) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

export function redactSecrets(value, depth = 0) {
  if (depth > MAX_JSON_DEPTH) return "[truncated]";
  if (typeof value === "string") return SECRET_VALUE.test(value) ? "[redacted]" : value;
  if (Array.isArray(value)) {
    return value.slice(0, MAX_JSON_ITEMS).map((item) => redactSecrets(item, depth + 1));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).slice(0, MAX_JSON_ITEMS).map(([key, item]) => [
        key,
        SECRET_KEY.test(key) ? "[redacted]" : redactSecrets(item, depth + 1),
      ]),
    );
  }
  return value;
}

function parsePage(searchParams) {
  const page = parseInteger(searchParams.get("page") || "1", "page", 1, 1_000_000);
  const pageSize = parseInteger(searchParams.get("pageSize") || "25", "pageSize", 1, MAX_PAGE_SIZE);
  return { page, pageSize, offset: (page - 1) * pageSize };
}

function parseOptional(searchParams, name, allowed = null) {
  const raw = searchParams.get(name);
  if (raw === null || raw === "") return null;
  if (raw.length > MAX_SEARCH_LENGTH) throw new HttpError(400, `${name} is too long.`);
  if (allowed && !allowed.has(raw)) throw new HttpError(400, `Unsupported ${name}.`);
  return raw;
}

function parseOrder(searchParams, sorts, defaultSort) {
  const sort = parseOptional(searchParams, "sort", new Set(Object.keys(sorts))) || defaultSort;
  const direction = parseOptional(searchParams, "direction", new Set(["asc", "desc"])) || "desc";
  return { column: sorts[sort], direction: direction.toUpperCase() };
}

function pageResponse(rows, total, page, pageSize) {
  return { items: rows, page, pageSize, total: Number(total) };
}

function publicationState(row) {
  if (!row) return "local";
  const status = typeof row === "string" ? row : row.status;
  if (status === "published") return "published";
  if (status === "dead_letter") return "dead_letter";
  if (status === "failed") return "failed";
  if (status === "publishing") return "publishing";
  return "pending";
}

function trustBadges(run, identity, evidence, validation, findingCounts) {
  const badges = [];
  const methodology = run.methodology_version || "Methodology unknown";
  badges.push({
    label: methodology,
    tone: methodology === "legacy-uncontrolled" ? "danger" : "good",
  });
  const executionVersion = identity?.execution_model_version;
  badges.push(executionVersion == null
    ? { label: "Execution model unknown", tone: "danger" }
    : Number(executionVersion) < 6
      ? { label: `Pre-P2b execution v${executionVersion}`, tone: "warning" }
      : { label: `Execution model v${executionVersion}`, tone: "good" });
  const survivorship = identity?.parameters?.survivorship_bias
    || identity?.parameters?.universe?.survivorship_bias
    || identity?.survivorship_bias;
  if (survivorship && survivorship !== "absent") {
    badges.push({ label: `Survivorship ${survivorship}`, tone: "warning" });
  }
  if (run.code_dirty) badges.push({ label: "Dirty code", tone: "warning" });
  const blocking = Number(findingCounts.blocking || 0);
  const review = Number(findingCounts.review || 0);
  badges.push(blocking > 0
    ? { label: `${blocking} blocking finding${blocking === 1 ? "" : "s"}`, tone: "danger" }
    : validation?.leakage_passed === 0
      ? { label: "Leakage check failed", tone: "danger" }
      : review > 0
        ? { label: `${review} review finding${review === 1 ? "" : "s"}`, tone: "warning" }
        : { label: "No blocking findings", tone: "good" });
  if (evidence?.retention_profile) {
    badges.push({
      label: `${evidence.retention_profile} retention`,
      tone: evidence.retention_profile === "summary" ? "neutral" : "warning",
    });
  }
  return badges;
}

function getOverview(database) {
  const runCounts = database.prepare(
    "SELECT state, COUNT(*) AS count FROM runs GROUP BY state ORDER BY state",
  ).all();
  const publicationCounts = database.prepare(
    "SELECT status, COUNT(*) AS count FROM publication_outbox GROUP BY status ORDER BY status",
  ).all();
  const registryCounts = database.prepare(
    `SELECT record_type, COUNT(*) AS count
     FROM registry_records
     GROUP BY record_type ORDER BY record_type`,
  ).all();
  const findings = database.prepare(
    "SELECT severity, COUNT(*) AS count FROM run_findings GROUP BY severity ORDER BY severity",
  ).all();
  return {
    runs: Object.fromEntries(runCounts.map((row) => [row.state, Number(row.count)])),
    publications: Object.fromEntries(publicationCounts.map((row) => [row.status, Number(row.count)])),
    registry: Object.fromEntries(registryCounts.map((row) => [row.record_type, Number(row.count)])),
    findings: Object.fromEntries(findings.map((row) => [row.severity, Number(row.count)])),
  };
}

function listRuns(database, searchParams) {
  const { page, pageSize, offset } = parsePage(searchParams);
  const state = parseOptional(searchParams, "state");
  const strategy = parseOptional(searchParams, "strategy");
  const search = parseOptional(searchParams, "search");
  const order = parseOrder(searchParams, RUN_SORTS, "updated");
  const clauses = [];
  const params = [];
  if (state) {
    clauses.push("r.state = ?");
    params.push(state);
  }
  if (strategy) {
    clauses.push("r.strategy_id = ?");
    params.push(strategy);
  }
  if (search) {
    clauses.push(
      "(r.run_id LIKE ? ESCAPE '\\' OR r.display_alias LIKE ? ESCAPE '\\' "
        + "OR r.fingerprint LIKE ? ESCAPE '\\' OR r.notes LIKE ? ESCAPE '\\')",
    );
    const pattern = `%${escapeLike(search)}%`;
    params.push(pattern, pattern, pattern, pattern);
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const total = database.prepare(`SELECT COUNT(*) AS count FROM runs r ${where}`).get(...params).count;
  const rows = database.prepare(`
    SELECT r.run_id, r.display_alias, r.strategy_id, r.methodology_version,
           r.state, r.notes, r.code_commit, r.code_dirty, r.created_at, r.updated_at,
           r.lineage_id, r.identity_json, r.origin_device_id,
           e.trade_count, e.equity_points, e.retention_profile,
           json_extract(e.payload_json, '$.first_session') AS first_session,
           json_extract(e.payload_json, '$.last_session') AS last_session,
           json_extract(e.payload_json, '$.net_pnl') AS net_pnl,
           json_extract(e.payload_json, '$.starting_equity') AS starting_equity,
           vr.leakage_passed,
           ${HEADLINE_METRICS.map(
             (key) => `(SELECT m.metric_value FROM run_metrics m
              WHERE m.run_id = r.run_id AND m.metric_key = '${key}'
              ORDER BY m.id DESC LIMIT 1) AS metric_${key}`,
           ).join(",\n           ")},
           (SELECT COUNT(*) FROM run_findings rf
            WHERE rf.run_id = r.run_id AND rf.severity = 'blocking') AS blocking_findings,
           (SELECT COUNT(*) FROM run_findings rf
            WHERE rf.run_id = r.run_id AND rf.severity = 'review') AS review_findings,
           po.status AS publication_status, po.attempts, po.last_error
    FROM runs r
    LEFT JOIN run_evidence e ON e.id = (
      SELECT e2.id FROM run_evidence e2 WHERE e2.run_id = r.run_id ORDER BY e2.id DESC LIMIT 1
    )
    LEFT JOIN validation_reports vr ON vr.run_id = r.run_id
    LEFT JOIN publication_outbox po ON po.idempotency_key = 'run:' || r.run_id
    ${where}
    ORDER BY ${order.column} ${order.direction}, r.display_seq DESC
    LIMIT ? OFFSET ?
  `).all(...params, pageSize, offset).map((row) => {
    const identity = safeJsonParse(row.identity_json, {});
    const evidence = { retention_profile: row.retention_profile };
    const validation = { leakage_passed: row.leakage_passed };
    const findingCounts = {
      blocking: Number(row.blocking_findings || 0),
      review: Number(row.review_findings || 0),
    };
    const metrics = {};
    for (const key of HEADLINE_METRICS) {
      metrics[key] = numberOrNull(row[`metric_${key}`]);
    }
    const startingEquity = numberOrNull(row.starting_equity);
    const netPnl = numberOrNull(row.net_pnl);
    return {
      runId: row.run_id,
      displayAlias: row.display_alias,
      strategyId: row.strategy_id,
      methodologyVersion: row.methodology_version,
      state: row.state,
      notes: row.notes || null,
      codeCommit: row.code_commit,
      codeDirty: Boolean(row.code_dirty),
      createdAt: row.created_at,
      updatedAt: row.updated_at,
      lineageId: row.lineage_id || row.run_id,
      originDeviceId: row.origin_device_id,
      executionModelVersion: identity.execution_model_version ?? null,
      retentionProfile: row.retention_profile,
      tradeCount: row.trade_count == null ? null : Number(row.trade_count),
      equityPointCount: row.equity_points == null ? null : Number(row.equity_points),
      symbol: identity?.parameters?.symbol ?? null,
      parameters: identity?.parameters ?? {},
      firstSession: row.first_session || null,
      lastSession: row.last_session || null,
      startingEquity,
      netPnl,
      netReturnPct:
        startingEquity && netPnl != null ? (netPnl / startingEquity) * 100 : null,
      metrics,
      publicationState: publicationState(row.publication_status),
      publicationAttempts: Number(row.attempts || 0),
      publicationError: row.last_error ? redactSecrets(row.last_error) : null,
      badges: trustBadges(row, identity, evidence, validation, findingCounts),
    };
  });
  return pageResponse(rows, total, page, pageSize);
}

function getRun(database, runId) {
  validateIdentity(runId, "run id");
  const run = database.prepare("SELECT * FROM runs WHERE run_id = ?").get(runId);
  if (!run) throw new HttpError(404, "Run not found.");
  const metrics = boundedRows(database, {
    countSql: "SELECT COUNT(*) AS count FROM run_metrics WHERE run_id = ?",
    listSql: `
    SELECT metric_key AS key, metric_value AS value,
           calculation_version AS calculationVersion, recorded_at AS recordedAt
    FROM run_metrics WHERE run_id = ? ORDER BY metric_key, id LIMIT ?
    `,
    params: [runId],
    limit: DETAIL_LIMITS.metrics,
  });
  const evidenceRow = database.prepare(
    "SELECT * FROM run_evidence WHERE run_id = ? ORDER BY id DESC LIMIT 1",
  ).get(runId);
  const validationRow = database.prepare(
    "SELECT * FROM validation_reports WHERE run_id = ?",
  ).get(runId);
  const findings = boundedRows(database, {
    countSql: "SELECT COUNT(*) AS count FROM run_findings WHERE run_id = ?",
    listSql: `
    SELECT code, category, severity, message, metric_key AS metricKey, recorded_at AS recordedAt
    FROM run_findings WHERE run_id = ? ORDER BY id LIMIT ?
    `,
    params: [runId],
    limit: DETAIL_LIMITS.findings,
  });
  const transitions = boundedRows(database, {
    countSql: "SELECT COUNT(*) AS count FROM experiment_state_transitions WHERE run_id = ?",
    listSql: `
    SELECT from_state AS fromState, to_state AS toState, reason, occurred_at AS occurredAt
    FROM experiment_state_transitions WHERE run_id = ? ORDER BY id LIMIT ?
    `,
    params: [runId],
    limit: DETAIL_LIMITS.transitions,
  });
  const outbox = database.prepare(`
    SELECT event_id AS eventId, idempotency_key AS idempotencyKey, status, attempts,
           last_error AS lastError, next_attempt_at AS nextAttemptAt,
           enqueued_at AS enqueuedAt, published_at AS publishedAt,
           payload_sha256 AS payloadSha256, remote_checksum AS remoteChecksum,
           publication_version AS publicationVersion
    FROM publication_outbox WHERE idempotency_key = ?
  `).get(`run:${runId}`);
  const acknowledgements = database.prepare(`
    SELECT remote_checksum AS remoteChecksum, publication_version AS publicationVersion,
           acknowledged_at AS acknowledgedAt, last_reconciled_at AS lastReconciledAt
    FROM publication_acknowledgements WHERE idempotency_key = ?
  `).get(`run:${runId}`);
  const identity = redactSecrets(safeJsonParse(run.identity_json, {}));
  const evidence = evidenceRow ? redactSecrets(safeJsonParse(evidenceRow.payload_json, {})) : null;
  const validation = validationRow ? {
    profileName: validationRow.profile_name,
    scope: validationRow.scope,
    targetState: validationRow.target_state,
    registryHashAtExecution: validationRow.registry_hash_at_execution,
    currentRegistryHash: validationRow.current_registry_hash,
    leakagePassed: Boolean(validationRow.leakage_passed),
    recordedAt: validationRow.recorded_at,
  } : null;
  const findingCounts = Object.fromEntries(
    database.prepare(
      "SELECT severity, COUNT(*) AS count FROM run_findings WHERE run_id = ? GROUP BY severity ORDER BY severity",
    ).all(runId).map((row) => [row.severity, Number(row.count)]),
  );
  return {
    run: {
      runId: run.run_id,
      displayAlias: run.display_alias,
      fingerprint: run.fingerprint,
      fingerprintVersion: Number(run.fingerprint_version),
      strategyId: run.strategy_id,
      methodologyVersion: run.methodology_version,
      state: run.state,
      notes: redactSecrets(run.notes),
      codeCommit: run.code_commit,
      codeDirty: Boolean(run.code_dirty),
      createdAt: run.created_at,
      updatedAt: run.updated_at,
      lineageId: run.lineage_id || run.run_id,
      originDeviceId: run.origin_device_id,
    },
    identity,
    metrics,
    evidence,
    validation,
    findings,
    findingCounts,
    transitions,
    publication: outbox ? {
      ...outbox,
      lastError: outbox.lastError ? redactSecrets(outbox.lastError) : null,
      state: publicationState(outbox),
      acknowledgement: acknowledgements || null,
      reconciliation: reconciliationState(outbox, acknowledgements),
    } : { state: "local", acknowledgement: null, reconciliation: "not_enqueued" },
    badges: trustBadges(run, identity, evidence, validationRow, findingCounts),
    localArtifacts: localArtifactAvailability(database, runId, evidenceRow),
  };
}

function getRunTrades(database, runId) {
  validateIdentity(runId, "run id");
  const run = database.prepare("SELECT run_id FROM runs WHERE run_id = ?").get(runId);
  if (!run) throw new HttpError(404, "Run not found.");

  const total = Number(Object.values(
    database.prepare("SELECT COUNT(*) FROM run_trades WHERE run_id = ?").get(runId),
  )[0]);
  const rows = database.prepare(`
    SELECT sequence, symbol, side, entry_time, exit_time, entry_price,
           exit_price, quantity, pnl, exit_reason
    FROM run_trades WHERE run_id = ? ORDER BY sequence LIMIT ?
  `).all(runId, MAX_DETAIL_ROWS);

  return {
    runId,
    total,
    truncated: total > rows.length,
    returned: rows.length,
    trades: rows.map((row) => ({
      sequence: Number(row.sequence),
      symbol: row.symbol,
      side: row.side,
      entryTime: row.entry_time,
      exitTime: row.exit_time,
      entryPrice: numberOrNull(row.entry_price),
      exitPrice: numberOrNull(row.exit_price),
      quantity: numberOrNull(row.quantity),
      pnl: numberOrNull(row.pnl),
      exitReason: row.exit_reason,
    })),
  };
}

/**
 * Return the equity curve with drawdown derived server-side.
 *
 * Drawdown is computed here rather than in the browser because it is the one
 * series a reader is most likely to misread: it must be measured against the
 * running peak, and recomputing it per-render against whatever subset the UI
 * happens to be showing would silently rebase the peak and understate every
 * drawdown after a truncation.
 */
function getRunEquity(database, runId) {
  validateIdentity(runId, "run id");
  const run = database.prepare("SELECT run_id FROM runs WHERE run_id = ?").get(runId);
  if (!run) throw new HttpError(404, "Run not found.");

  const total = Number(Object.values(
    database.prepare("SELECT COUNT(*) FROM run_equity_points WHERE run_id = ?").get(runId),
  )[0]);

  // Downsample the *returned* series by stride rather than by LIMIT: truncating
  // a long curve would show only its opening months and make an unfinished run
  // look complete, whereas a stride preserves the whole span and final value.
  const stride = total > MAX_DETAIL_ROWS ? Math.ceil(total / MAX_DETAIL_ROWS) : 1;

  // The running peak and the max drawdown are derived from *every* point, not
  // from the downsampled subset. Deriving them from the subset would let a
  // trough that falls between two strides vanish, understating the worst
  // drawdown -- the single number most likely to be read as a risk bound.
  const rows = database.prepare(`
    SELECT sequence, observed_at, equity
    FROM run_equity_points WHERE run_id = ? ORDER BY sequence
  `).all(runId);

  let peak = null;
  let maxDrawdown = 0;
  let maxDrawdownAt = null;
  const points = [];
  const lastSequence = rows.length ? Number(rows[rows.length - 1].sequence) : -1;

  // The intraday curve marks open positions to market, so its running peak is an
  // intraday high-water mark. That answers "how much heat did this take?" but it
  // is not the closed-equity drawdown most risk limits are written against, and
  // the two differ a lot for a strategy that is flat overnight. Track the last
  // observation of each session alongside it so both are reported, each labelled.
  const sessionCloses = new Map();

  for (const row of rows) {
    const equity = Number(row.equity);
    const sequence = Number(row.sequence);
    peak = peak === null ? equity : Math.max(peak, equity);
    const drawdown = peak > 0 ? (equity - peak) / peak : 0;
    if (drawdown < maxDrawdown) {
      maxDrawdown = drawdown;
      maxDrawdownAt = row.observed_at;
    }
    sessionCloses.set(String(row.observed_at).slice(0, 10), { equity, observedAt: row.observed_at });
    if (sequence % stride === 0 || sequence === lastSequence) {
      points.push({ sequence, observedAt: row.observed_at, equity, drawdown });
    }
  }

  let sessionPeak = null;
  let maxSessionDrawdown = 0;
  let maxSessionDrawdownAt = null;
  for (const close of sessionCloses.values()) {
    sessionPeak = sessionPeak === null ? close.equity : Math.max(sessionPeak, close.equity);
    const drawdown = sessionPeak > 0 ? (close.equity - sessionPeak) / sessionPeak : 0;
    if (drawdown < maxSessionDrawdown) {
      maxSessionDrawdown = drawdown;
      maxSessionDrawdownAt = close.observedAt;
    }
  }

  return {
    runId,
    total,
    returned: points.length,
    stride,
    downsampled: stride > 1,
    maxDrawdown,
    maxDrawdownAt,
    sessionCount: sessionCloses.size,
    maxSessionDrawdown,
    maxSessionDrawdownAt,
    startingEquity: rows.length ? Number(rows[0].equity) : null,
    endingEquity: rows.length ? Number(rows[rows.length - 1].equity) : null,
    points,
  };
}

function numberOrNull(value) {
  return value === null || value === undefined ? null : Number(value);
}

function reconciliationState(outbox, acknowledgement) {
  if (!outbox) return "not_enqueued";
  if (outbox.status === "dead_letter") return "dead_letter";
  if (outbox.status !== "published") return outbox.status;
  if (!acknowledgement) return "published_unacknowledged";
  if (outbox.payloadSha256 !== acknowledgement.remoteChecksum) return "checksum_mismatch";
  return "reconciled";
}

function localArtifactAvailability(database, runId, evidenceRow) {
  const profile = evidenceRow?.retention_profile || null;
  const tradeRows = Number(
    Object.values(
      database.prepare("SELECT COUNT(*) FROM run_trades WHERE run_id = ?").get(runId),
    )[0],
  );
  const equityRows = Number(
    Object.values(
      database.prepare("SELECT COUNT(*) FROM run_equity_points WHERE run_id = ?").get(runId),
    )[0],
  );

  // Availability is measured, not inferred from the profile. A run can declare
  // `full` retention and still have no rows if it was recorded before schema
  // v9 or if recording was interrupted, and reporting detail as present when
  // the tables are empty would send the UI looking for rows that do not exist.
  return {
    retentionProfile: profile,
    trades: {
      available: tradeRows > 0,
      rowCount: tradeRows,
      reason: tradeRows > 0
        ? null
        : profile === "summary"
          ? "Summary retention stores only aggregate trade counts and checksums."
          : "This run has no retained trade rows; it predates schema v9 or did not finish recording.",
    },
    equity: {
      available: equityRows > 0,
      rowCount: equityRows,
      reason: equityRows > 0
        ? null
        : profile !== "full"
          ? "Equity points require the full retention profile."
          : "This run has no retained equity points; it predates schema v9 or did not finish recording.",
    },
  };
}

function analysisRequest(dbPath, repositoryRoot, action, searchParams) {
  let query = {};
  if (action !== "catalog") {
    const raw = searchParams.get("query");
    if (!raw || raw.length > MAX_ANALYSIS_QUERY_LENGTH) {
      throw new HttpError(400, "Analysis query is missing or too large.");
    }
    try {
      query = JSON.parse(raw);
    } catch {
      throw new HttpError(400, "Analysis query must be valid JSON.");
    }
  }
  const python = process.env.PYTHON || (platform() === "win32" ? "python" : "python3");
  const bridge = join(HERE, "analysis_bridge.py");
  const result = spawnSync(
    python,
    [bridge, "--db", dbPath, "--action", action],
    {
      cwd: repositoryRoot,
      encoding: "utf8",
      input: JSON.stringify(query),
      maxBuffer: 2 * 1024 * 1024,
      timeout: 15_000,
      windowsHide: true,
    },
  );
  if (result.error) {
    throw new HttpError(500, "Local analysis process could not start.");
  }
  if (result.status !== 0) {
    const message = String(result.stderr || "").trim();
    if (result.status === 2) {
      throw new HttpError(400, message || "Local analysis query is invalid.");
    }
    throw new HttpError(500, "Local analysis failed.");
  }
  try {
    return JSON.parse(result.stdout);
  } catch {
    throw new HttpError(500, "Local analysis returned an invalid response.");
  }
}

function listRegistry(database, searchParams) {
  const { page, pageSize, offset } = parsePage(searchParams);
  const type = parseOptional(searchParams, "type");
  const status = parseOptional(searchParams, "status");
  const search = parseOptional(searchParams, "search");
  const order = parseOrder(searchParams, REGISTRY_SORTS, "recorded");
  const clauses = [];
  const params = [];
  if (type) {
    clauses.push("rr.record_type = ?");
    params.push(type);
  }

  if (status) {
    clauses.push("rr.status = ?");
    params.push(status);
  }
  if (search) {
    clauses.push("(rr.legacy_id LIKE ? ESCAPE '\\' OR rr.canonical_sha256 LIKE ? ESCAPE '\\')");
    const pattern = `%${escapeLike(search)}%`;
    params.push(pattern, pattern);
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const total = database.prepare(`SELECT COUNT(*) AS count FROM registry_records rr ${where}`).get(...params).count;
  const rows = database.prepare(`
    SELECT rr.record_type, rr.legacy_id, rr.canonical_sha256,
           rr.methodology_version, rr.status, rr.git_dirty, rr.imported_at,
           rr.record_origin, rr.execution_model_version,
           po.status AS publication_status
    FROM registry_records rr
    LEFT JOIN publication_outbox po
      ON po.sequence = (
        SELECT po2.sequence
        FROM publication_outbox po2
        WHERE po2.aggregate_type = 'registry_record'
          AND po2.aggregate_id = rr.record_type || ':' || rr.legacy_id
        ORDER BY po2.sequence DESC
        LIMIT 1
      )
    ${where}
    ORDER BY ${order.column} ${order.direction}, rr.record_type, rr.legacy_id
    LIMIT ? OFFSET ?
  `).all(...params, pageSize, offset).map((row) => ({
    recordType: row.record_type,
    recordId: row.legacy_id,
    canonicalSha256: row.canonical_sha256,
    methodologyVersion: row.methodology_version,
    status: row.status,
    gitDirty: Boolean(row.git_dirty),
    recordedAt: row.imported_at,
    recordOrigin: row.record_origin,
    executionModelVersion: row.execution_model_version,
    publicationState: publicationState(row.publication_status),
  }));
  return pageResponse(rows, total, page, pageSize);
}

function getRegistryRecord(database, recordType, recordId, repositoryRoot) {
  validateIdentity(recordType, "record type");
  validateIdentity(recordId, "record id");
  const row = database.prepare(`
    SELECT * FROM registry_records WHERE record_type = ? AND legacy_id = ?
  `).get(recordType, recordId);
  if (!row) throw new HttpError(404, "Registry record not found.");
  const payload = sanitizeRegistryPayload(safeJsonParse(row.payload_json, {}));
  const relationships = boundedRows(database, {
    countSql: `
      SELECT COUNT(*) AS count FROM registry_relationships
      WHERE record_type = ? AND legacy_id = ?
    `,
    listSql: `
    SELECT relation, target_type AS targetType, target_id AS targetId,
           ordinal, resolved
    FROM registry_relationships
    WHERE record_type = ? AND legacy_id = ?
    ORDER BY ordinal, relation, target_type, target_id
    LIMIT ?
    `,
    params: [recordType, recordId],
    limit: DETAIL_LIMITS.relationships,
    map: (relation) => ({ ...relation, resolved: Boolean(relation.resolved) }),
  });
  const artifacts = boundedRows(database, {
    countSql: `
      SELECT COUNT(*) AS count FROM registry_artifact_locations
      WHERE record_type = ? AND legacy_id = ?
    `,
    listSql: `
    SELECT ordinal, repository_path AS repositoryPath,
           external_uri AS externalUri
    FROM registry_artifact_locations
    WHERE record_type = ? AND legacy_id = ?
    ORDER BY ordinal
    LIMIT ?
    `,
    params: [recordType, recordId],
    limit: DETAIL_LIMITS.artifacts,
    map: (artifact) => ({
      ordinal: Number(artifact.ordinal),
      repositoryPath: safeRepositoryPath(artifact.repositoryPath),
      externalLocation: artifact.externalUri ? basename(String(artifact.externalUri).replaceAll("\\", sep)) : null,
      preview: artifactPreviewMetadata(
        repositoryRoot,
        artifact.repositoryPath,
        recordType,
        recordId,
        Number(artifact.ordinal),
      ),
    }),
  });
  const outbox = database.prepare(`
    SELECT status, attempts, last_error AS lastError, enqueued_at AS enqueuedAt,
           published_at AS publishedAt, payload_sha256 AS payloadSha256,
           remote_checksum AS remoteChecksum, publication_version AS publicationVersion
    FROM publication_outbox
    WHERE aggregate_type = 'registry_record' AND aggregate_id = ?
    ORDER BY sequence DESC LIMIT 1
  `).get(`${recordType}:${recordId}`);
  return {
    record: {
      recordType: row.record_type,
      recordId: row.legacy_id,
      canonicalSha256: row.canonical_sha256,
      methodologyVersion: row.methodology_version,
      status: row.status,
      gitDirty: Boolean(row.git_dirty),
      recordedAt: row.imported_at,
      recordOrigin: row.record_origin,
      executionModelVersion: row.execution_model_version,
      executionIdentity: redactSecrets(safeJsonParse(row.execution_identity_json, null)),
    },
    payload,
    relationships,
    artifacts,
    publication: outbox ? {
      ...outbox,
      lastError: outbox.lastError ? redactSecrets(outbox.lastError) : null,
      state: publicationState(outbox),
    } : { state: "local" },
  };
}

function boundedRows(database, { countSql, listSql, params, limit, map = (row) => row }) {
  const total = Number(database.prepare(countSql).get(...params).count);
  const items = database.prepare(listSql).all(...params, limit).map(map);
  return { items, total, limit, truncated: total > items.length };
}

function sanitizeRegistryPayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return {};
  const allowed = new Set([
    "id",
    "title",
    "rationale",
    "status",
    "tags",
    "created_at",
    "updated_at",
    "strategy_name",
    "strategy_version",
    "parameters",
    "dataset_config",
    "results_summary",
    "conclusion",
    "completed_at",
    "hypothesis_id",
    "parent_experiment_id",
    "content",
    "related_experiment_id",
    "idea",
    "reason_rejected",
    "evidence",
    "experiment_id",
    "artifact_type",
    "checksum",
    "size_bytes",
  ]);
  return redactSecrets(Object.fromEntries(
    Object.entries(payload).filter(([key]) => allowed.has(key)),
  ));
}

function listPublications(database, searchParams) {
  const { page, pageSize, offset } = parsePage(searchParams);
  const status = parseOptional(searchParams, "status", new Set(["pending", "publishing", "failed", "published", "dead_letter"]));
  const type = parseOptional(searchParams, "type", new Set(["run_summary", "registry_record"]));
  const order = parseOrder(searchParams, PUBLICATION_SORTS, "sequence");
  const clauses = [];
  const params = [];
  if (status) {
    clauses.push("po.status = ?");
    params.push(status);
  }
  if (type) {
    clauses.push("po.aggregate_type = ?");
    params.push(type);
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
  const total = database.prepare(`SELECT COUNT(*) AS count FROM publication_outbox po ${where}`).get(...params).count;
  const rows = database.prepare(`
    SELECT po.sequence, po.event_id AS eventId, po.idempotency_key AS idempotencyKey,
           po.aggregate_type AS aggregateType, po.aggregate_id AS aggregateId,
           po.parent_idempotency_key AS parentIdempotencyKey,
           po.schema_version AS schemaVersion,
           po.publication_version AS publicationVersion,
           po.payload_sha256 AS payloadSha256, po.status, po.attempts,
           po.last_error AS lastError, po.next_attempt_at AS nextAttemptAt,
           po.enqueued_at AS enqueuedAt, po.published_at AS publishedAt,
           po.remote_checksum AS remoteChecksum,
           pa.remote_checksum AS acknowledgedChecksum,
           pa.acknowledged_at AS acknowledgedAt,
           pa.last_reconciled_at AS lastReconciledAt,
           pdl.dead_lettered_at AS deadLetteredAt,
           pdl.replay_count AS replayCount
    FROM publication_outbox po
    LEFT JOIN publication_acknowledgements pa ON pa.idempotency_key = po.idempotency_key
    LEFT JOIN publication_dead_letters pdl ON pdl.event_id = po.event_id
    ${where}
    ORDER BY ${order.column} ${order.direction}, po.sequence DESC
    LIMIT ? OFFSET ?
  `).all(...params, pageSize, offset).map((row) => ({
    ...row,
    sequence: Number(row.sequence),
    schemaVersion: Number(row.schemaVersion),
    publicationVersion: Number(row.publicationVersion),
    attempts: Number(row.attempts),
    replayCount: row.replayCount == null ? null : Number(row.replayCount),
    lastError: row.lastError ? redactSecrets(row.lastError) : null,
    reconciliation: row.status === "published"
      ? row.acknowledgedChecksum === row.payloadSha256 ? "reconciled" : "published_unreconciled"
      : row.status,
  }));
  return pageResponse(rows, total, page, pageSize);
}

function escapeLike(value) {
  return value.replaceAll("\\", "\\\\").replaceAll("%", "\\%").replaceAll("_", "\\_");
}

function validateIdentity(value, label) {
  if (typeof value !== "string" || value.length < 1 || value.length > 160 || !/^[A-Za-z0-9._:-]+$/.test(value)) {
    throw new HttpError(400, `Invalid ${label}.`);
  }
}

function safeRepositoryPath(value) {
  if (!value) return null;
  const normalized = String(value).replaceAll("\\", "/");
  if (
    normalized.startsWith("/")
    || /^[A-Za-z]:\//.test(normalized)
    || normalized.split("/").includes("..")
  ) return null;
  return normalized;
}

function artifactPreviewMetadata(repositoryRoot, repositoryPath, recordType, recordId, ordinal) {
  try {
    const artifact = resolveArtifactFile(repositoryRoot, repositoryPath);
    return {
      available: true,
      fileName: basename(artifact.path),
      kind: artifact.format.kind,
      contentType: artifact.format.contentType,
      sizeBytes: artifact.size,
      endpoint: `/api/artifacts/${encodeURIComponent(recordType)}/${encodeURIComponent(recordId)}/${ordinal}`,
    };
  } catch (error) {
    return {
      available: false,
      reason: error instanceof HttpError ? error.message : "Artifact preview is unavailable.",
    };
  }
}

function resolveArtifactFile(repositoryRoot, repositoryPath) {
  if (typeof repositoryPath !== "string" || !repositoryPath.trim()) {
    throw new HttpError(404, "Only repository-relative artifact references can be previewed.");
  }
  if (isAbsolute(repositoryPath)) {
    throw new HttpError(403, "Absolute artifact paths cannot be previewed.");
  }
  const root = realpathSync(resolve(repositoryRoot));
  let artifactPath;
  try {
    artifactPath = realpathSync(resolve(root, repositoryPath));
  } catch {
    throw new HttpError(404, "Referenced artifact file is not available in this checkout.");
  }
  if (artifactPath !== root && !artifactPath.startsWith(`${root}${sep}`)) {
    throw new HttpError(403, "Artifact path escapes the repository root.");
  }
  const format = ARTIFACT_FORMATS.get(extname(artifactPath).toLowerCase());
  if (!format) throw new HttpError(415, "Artifact type is not supported for inline preview.");
  const info = statSync(artifactPath);
  if (!info.isFile()) throw new HttpError(404, "Referenced artifact is not a file.");
  const limit = format.kind === "image" ? MAX_ARTIFACT_IMAGE_BYTES : MAX_ARTIFACT_TEXT_BYTES;
  if (info.size > limit) {
    throw new HttpError(413, `Artifact exceeds the ${Math.floor(limit / 1_000_000)} MB preview limit.`);
  }
  return { path: artifactPath, format, size: info.size };
}

function getArtifactPreview(database, repositoryRoot, recordType, recordId, ordinalText) {
  validateIdentity(recordType, "record type");
  validateIdentity(recordId, "record id");
  const ordinal = parseInteger(ordinalText, "artifact ordinal", 0, DETAIL_LIMITS.artifacts - 1);
  const row = database.prepare(`
    SELECT repository_path AS repositoryPath
    FROM registry_artifact_locations
    WHERE record_type = ? AND legacy_id = ? AND ordinal = ?
  `).get(recordType, recordId, ordinal);
  if (!row) throw new HttpError(404, "Artifact reference not found.");
  const artifact = resolveArtifactFile(repositoryRoot, row.repositoryPath);
  const body = readFileSync(artifact.path);
  return {
    fileName: basename(artifact.path),
    kind: artifact.format.kind,
    contentType: artifact.format.contentType,
    sizeBytes: artifact.size,
    content: artifact.format.kind === "image"
      ? `data:${artifact.format.contentType};base64,${body.toString("base64")}`
      : body.toString("utf8"),
  };
}

function artifactOutboxRow(database, artifactId) {
  validateIdentity(artifactId, "artifact id");
  if (!/^artifact:[0-9a-f]{64}$/.test(artifactId)) {
    throw new HttpError(400, "Invalid artifact id.");
  }
  const row = database.prepare(`
    SELECT artifact_id, owning_publication_key, object_key, staging_path,
           manifest_json, content_sha256, size_bytes, media_type,
           preview_policy, status, published_at, enqueued_at
    FROM artifact_publication_outbox WHERE artifact_id = ?
  `).get(artifactId);
  if (!row || row.status === "dead_letter") throw new HttpError(404, "Artifact not found.");
  return row;
}

function artifactOutboxMetadata(row) {
  const manifest = safeJsonParse(row.manifest_json, {});
  return {
    artifactId: row.artifact_id,
    owningPublicationKey: row.owning_publication_key,
    sizeBytes: Number(row.size_bytes),
    mediaType: row.media_type,
    displayName: String(manifest.display_name || "artifact"),
    artifactClass: String(manifest.artifact_class || "text"),
    previewPolicy: row.preview_policy,
    sanitizerVersion: Number(manifest.sanitizer_version || 0),
    manifestVersion: Number(manifest.manifest_version || 0),
    publishedAt: row.published_at || row.enqueued_at,
  };
}

function listArtifactOutbox(database, searchParams) {
  const owner = parseOptional(searchParams, "owningPublicationKey");
  if (!owner) throw new HttpError(400, "owningPublicationKey is required.");
  const limit = parseInteger(searchParams.get("limit") || "50", "limit", 1, 100);
  const offset = parseInteger(searchParams.get("offset") || "0", "offset", 0, 1_000_000);
  return database.prepare(`
    SELECT artifact_id, owning_publication_key, object_key, staging_path,
           manifest_json, content_sha256, size_bytes, media_type,
           preview_policy, status, published_at, enqueued_at
    FROM artifact_publication_outbox
    WHERE owning_publication_key = ? AND status != 'dead_letter'
    ORDER BY enqueued_at DESC, artifact_id
    LIMIT ? OFFSET ?
  `).all(owner, limit, offset).map(artifactOutboxMetadata);
}

function resolveOutboxArtifactFile(stagingRoot, row) {
  let root;
  let artifactPath;
  try {
    root = realpathSync(resolve(stagingRoot));
    artifactPath = realpathSync(resolve(root, row.staging_path));
  } catch {
    throw new HttpError(404, "Staged artifact bytes are unavailable.");
  }
  if (artifactPath !== root && !artifactPath.startsWith(`${root}${sep}`)) {
    throw new HttpError(403, "Artifact staging path escapes the configured root.");
  }
  const info = statSync(artifactPath);
  if (!info.isFile() || info.size !== Number(row.size_bytes)) {
    throw new HttpError(409, "Staged artifact size does not match its manifest.");
  }
  const body = readFileSync(artifactPath);
  const checksum = createHash("sha256").update(body).digest("hex");
  if (checksum !== row.content_sha256) {
    throw new HttpError(409, "Staged artifact checksum does not match its manifest.");
  }
  const format = ARTIFACT_FORMATS.get(extname(artifactPath).toLowerCase());
  if (!format) throw new HttpError(415, "Artifact type is not supported for preview.");
  return { body, format };
}

function getArtifactIdentityPreview(database, stagingRoot, artifactId) {
  const row = artifactOutboxRow(database, artifactId);
  if (!["inline", "download"].includes(row.preview_policy)) {
    throw new HttpError(403, "Artifact policy does not permit preview.");
  }
  resolveOutboxArtifactFile(stagingRoot, row);
  return {
    ...artifactOutboxMetadata(row),
    url: `/api/artifacts/${encodeURIComponent(artifactId)}/content`,
    expiresAt: new Date(Date.now() + 5 * 60 * 1000).toISOString(),
  };
}

function getArtifactIdentityContent(database, stagingRoot, artifactId) {
  const row = artifactOutboxRow(database, artifactId);
  if (!["inline", "download"].includes(row.preview_policy)) {
    throw new HttpError(403, "Artifact policy does not permit preview.");
  }
  const artifact = resolveOutboxArtifactFile(stagingRoot, row);
  return {
    artifactId,
    fileName: artifactOutboxMetadata(row).displayName,
    kind: artifact.format.kind,
    contentType: row.media_type,
    sizeBytes: Number(row.size_bytes),
    content: artifact.format.kind === "image"
      ? `data:${row.media_type};base64,${artifact.body.toString("base64")}`
      : artifact.body.toString("utf8"),
  };
}

function safeDisplayPath(dbPath, env = process.env) {
  const normalized = resolve(dbPath);
  if (env.RESEARCH__DB_PATH && normalized === resolve(env.RESEARCH__DB_PATH)) {
    return "$RESEARCH__DB_PATH";
  }
  const candidates = [
    [env.RESEARCH__DATA_DIR, "$RESEARCH__DATA_DIR"],
    [env.LOCALAPPDATA, "%LOCALAPPDATA%"],
  ].filter(([prefix]) => prefix);
  for (const [prefix, label] of candidates) {
    const root = resolve(prefix);
    if (normalized === root || normalized.startsWith(`${root}${sep}`)) {
      return `${label}${normalized.slice(root.length)}`;
    }
  }
  return basename(normalized);
}

function displayHost(host) {
  return host.includes(":") ? `[${host}]` : host;
}

function routeApi(database, pathname, searchParams, repositoryRoot, artifactStagingRoot, dbPath) {
  if (pathname === "/api/meta") return null;
  if (pathname === "/api/overview") return getOverview(database);
  if (pathname === "/api/runs") return listRuns(database, searchParams);
  if (pathname === "/api/registry") return listRegistry(database, searchParams);
  if (pathname === "/api/publications") return listPublications(database, searchParams);
  if (pathname === "/api/artifacts") return listArtifactOutbox(database, searchParams);
  if (pathname === "/api/analysis/catalog") {
    return analysisRequest(dbPath, repositoryRoot, "catalog", searchParams);
  }
  if (pathname === "/api/analysis/estimate") {
    return analysisRequest(dbPath, repositoryRoot, "estimate", searchParams);
  }
  if (pathname === "/api/analysis/query") {
    return analysisRequest(dbPath, repositoryRoot, "query", searchParams);
  }
  const runMatch = pathname.match(/^\/api\/runs\/([^/]+)$/);
  if (runMatch) return getRun(database, decodeURIComponent(runMatch[1]));
  const tradesMatch = pathname.match(/^\/api\/runs\/([^/]+)\/trades$/);
  if (tradesMatch) return getRunTrades(database, decodeURIComponent(tradesMatch[1]));
  const equityMatch = pathname.match(/^\/api\/runs\/([^/]+)\/equity$/);
  if (equityMatch) return getRunEquity(database, decodeURIComponent(equityMatch[1]));
  const registryMatch = pathname.match(/^\/api\/registry\/([^/]+)\/([^/]+)$/);
  if (registryMatch) {
    return getRegistryRecord(
      database,
      decodeURIComponent(registryMatch[1]),
      decodeURIComponent(registryMatch[2]),
      repositoryRoot,
    );
  }
  const artifactMatch = pathname.match(/^\/api\/artifacts\/([^/]+)\/([^/]+)\/(\d+)$/);
  if (artifactMatch) {
    return getArtifactPreview(
      database,
      repositoryRoot,
      decodeURIComponent(artifactMatch[1]),
      decodeURIComponent(artifactMatch[2]),
      artifactMatch[3],
    );
  }
  const artifactIdentityMatch = pathname.match(/^\/api\/artifacts\/([^/]+)$/);
  if (artifactIdentityMatch) {
    return artifactOutboxMetadata(
      artifactOutboxRow(database, decodeURIComponent(artifactIdentityMatch[1])),
    );
  }
  const artifactPreviewMatch = pathname.match(/^\/api\/artifacts\/([^/]+)\/preview$/);
  if (artifactPreviewMatch) {
    return getArtifactIdentityPreview(
      database, artifactStagingRoot, decodeURIComponent(artifactPreviewMatch[1]),
    );
  }
  const artifactContentMatch = pathname.match(/^\/api\/artifacts\/([^/]+)\/content$/);
  if (artifactContentMatch) {
    return getArtifactIdentityContent(
      database, artifactStagingRoot, decodeURIComponent(artifactContentMatch[1]),
    );
  }
  throw new HttpError(404, "Endpoint not found.");
}

function securityHeaders(contentType, api = false) {
  return {
    "content-type": contentType,
    "content-security-policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    "cross-origin-resource-policy": "same-origin",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "cache-control": api ? "no-store" : "no-cache",
  };
}

function sendJson(response, status, body) {
  response.writeHead(status, securityHeaders("application/json; charset=utf-8", true));
  response.end(JSON.stringify(body));
}

function sendError(response, error) {
  const status = error instanceof HttpError ? error.status : 500;
  const message = status >= 500 ? "Local viewer request failed." : error.message;
  // The client message stays generic so internals never reach the response, but
  // an unexplained 500 is undiagnosable without the cause recorded somewhere.
  if (status >= 500) console.error("[viewer] request failed:", error);
  sendJson(response, status, { error: message });
}

export function createViewerServer({
  dbPath,
  host = DEFAULT_HOST,
  port = DEFAULT_PORT,
  repositoryRoot = resolve(HERE, "..", ".."),
  artifactStagingRoot = resolve(
    process.env.RESEARCH__ARTIFACT_STAGING_DIR || join(dirname(dbPath), "artifact-staging"),
  ),
}) {
  validateBindHost(host);
  const safeDbPath = assertSafeDbPath(dbPath);
  const inspection = inspectDatabase(safeDbPath);
  const meta = {
    apiVersion: 1,
    schemaVersion: inspection.version,
    database: {
      fileName: basename(safeDbPath),
      displayPath: safeDisplayPath(safeDbPath),
      mode: "read-only",
      queryOnly: true,
      immutableUri: false,
      immutableReason: "Disabled because the live WAL-backed database may change; every request opens a fresh read-only/query-only connection.",
    },
    localOnly: true,
    host,
  };
  const server = createServer((request, response) => {
    if (!isLoopbackAddress(request.socket.remoteAddress) || !validateHostHeader(request.headers.host)) {
      sendJson(response, 403, { error: "Loopback access with a loopback Host header is required." });
      return;
    }
    if (!["GET", "HEAD"].includes(request.method || "")) {
      response.setHeader("allow", "GET, HEAD");
      sendJson(response, 405, { error: "Method not allowed." });
      return;
    }
    let parsed;
    try {
      parsed = new URL(request.url || "/", "http://localhost");
    } catch {
      sendJson(response, 400, { error: "Invalid request URL." });
      return;
    }
    if (parsed.pathname.startsWith("/api/")) {
      try {
        const result = parsed.pathname === "/api/meta"
          ? meta
          : withDatabase(
            safeDbPath,
            (database) => routeApi(
              database,
              parsed.pathname,
              parsed.searchParams,
              repositoryRoot,
              artifactStagingRoot,
              safeDbPath,
            ),
          );
        if (request.method === "HEAD") {
          response.writeHead(200, securityHeaders("application/json; charset=utf-8", true));
          response.end();
        } else {
          sendJson(response, 200, result);
        }
      } catch (error) {
        sendError(response, error);
      }
      return;
    }
    const staticEntry = STATIC_FILES.get(parsed.pathname);
    if (!staticEntry) {
      sendJson(response, 404, { error: "Resource not found." });
      return;
    }
    try {
      const [filePath, contentType] = staticEntry;
      const body = readFileSync(filePath);
      response.writeHead(200, securityHeaders(contentType));
      response.end(request.method === "HEAD" ? undefined : body);
    } catch {
      sendJson(response, 500, { error: "Local viewer resource unavailable." });
    }
  });
  return {
    server,
    host,
    port,
    dbPath: safeDbPath,
    listen() {
      return new Promise((resolvePromise, rejectPromise) => {
        server.once("error", rejectPromise);
        server.listen(port, host, () => {
          server.off("error", rejectPromise);
          resolvePromise(server.address());
        });
      });
    },
    close() {
      return new Promise((resolvePromise, rejectPromise) => {
        server.closeAllConnections();
        server.close((error) => error ? rejectPromise(error) : resolvePromise());
      });
    },
  };
}

function usage() {
  return [
    "Local read-only backtest research viewer",
    "",
    "Usage:",
    "  node tools\\backtest-research-viewer\\server.mjs [--db PATH] [--port PORT]",
    "",
    "Defaults:",
    "  DB: RESEARCH__DB_PATH, RESEARCH__DATA_DIR, or OS-local strategy-lab storage",
    `  URL: http://${DEFAULT_HOST}:${DEFAULT_PORT}`,
    "",
    "Only loopback bind addresses are accepted. --db is read once at startup.",
  ].join("\n");
}

async function main() {
  if (!minimumNodeVersionSatisfied()) {
    throw new Error(`Node.js 22.13.0 or newer is required; found ${process.versions.node}.`);
  }
  const options = parseArgs();
  if (options.help) {
    console.log(usage());
    return;
  }
  const viewer = createViewerServer(options);
  const address = await viewer.listen();
  const actualPort = typeof address === "object" && address ? address.port : options.port;
  console.log(`Research viewer: http://${displayHost(options.host)}:${actualPort}`);
  console.log(`Database: ${safeDisplayPath(viewer.dbPath)}`);
  console.log("Mode: local-only, read-only, query-only");
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`Research viewer failed: ${redactSecrets(error instanceof Error ? error.message : String(error))}`);
    process.exitCode = 1;
  });
}
