import type {
  ResearchArtifactAdapter,
  ResearchArtifactClass,
  ResearchArtifactMetadata,
  ResearchArtifactPreview,
  ResearchArtifactPreviewPolicy,
} from "./researchArtifactTypes.ts";

type Fetch = typeof fetch;

const ARTIFACT_ID = /^artifact:[0-9a-f]{64}$/;
const MAX_SIGNED_URL_SECONDS = 300;

export class LocalResearchArtifactAdapter implements ResearchArtifactAdapter {
  readonly source = "local" as const;
  readonly #baseUrl: string;
  readonly #fetch: Fetch;

  constructor(baseUrl = "", fetchImplementation: Fetch = fetch) {
    this.#baseUrl = baseUrl.replace(/\/$/, "");
    this.#fetch = (input, init) => fetchImplementation.call(globalThis, input, init);
  }

  async listArtifacts(
    owningPublicationKey: string,
    page: { limit?: number; offset?: number } = {},
  ): Promise<ResearchArtifactMetadata[]> {
    const { limit, offset } = boundedPage(page);
    const query = new URLSearchParams({
      owningPublicationKey,
      limit: String(limit),
      offset: String(offset),
    });
    return this.#get(`/api/artifacts?${query}`);
  }

  async getArtifact(artifactId: string): Promise<ResearchArtifactMetadata | null> {
    validateArtifactId(artifactId);
    const response = await this.#fetch(
      `${this.#baseUrl}/api/artifacts/${encodeURIComponent(artifactId)}`,
      { method: "GET", headers: { Accept: "application/json" } },
    );
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`Local artifact query failed with status ${response.status}.`);
    return decodeMetadata(await response.json());
  }

  async requestPreview(artifactId: string): Promise<ResearchArtifactPreview> {
    validateArtifactId(artifactId);
    return this.#get(`/api/artifacts/${encodeURIComponent(artifactId)}/preview`);
  }

  async #get<T>(path: string): Promise<T> {
    const response = await this.#fetch(`${this.#baseUrl}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`Local artifact query failed with status ${response.status}.`);
    const value = await response.json();
    return (Array.isArray(value) ? value.map(decodeMetadata) : decodePreview(value)) as T;
  }
}

export class SupabaseResearchArtifactAdapter implements ResearchArtifactAdapter {
  readonly source = "supabase" as const;
  readonly #url: string;
  readonly #anonKey: string;
  readonly #fetch: Fetch;

  constructor(url: string, anonKey: string, fetchImplementation: Fetch = fetch) {
    if (!url || !anonKey) throw new Error("Research Supabase URL and anonymous key are required.");
    this.#url = url.replace(/\/$/, "");
    this.#anonKey = anonKey;
    this.#fetch = (input, init) => fetchImplementation.call(globalThis, input, init);
  }

  async listArtifacts(
    owningPublicationKey: string,
    page: { limit?: number; offset?: number } = {},
  ): Promise<ResearchArtifactMetadata[]> {
    const { limit, offset } = boundedPage(page);
    const query = new URLSearchParams({
      select: "*",
      owning_publication_key: `eq.${owningPublicationKey}`,
      order: "published_at.desc,artifact_id.asc",
      limit: String(limit),
      offset: String(offset),
    });
    const response = await this.#metadataRequest(query);
    return ((await response.json()) as unknown[]).map(decodeMetadata);
  }

  async getArtifact(artifactId: string): Promise<ResearchArtifactMetadata | null> {
    validateArtifactId(artifactId);
    const query = new URLSearchParams({
      select: "*",
      artifact_id: `eq.${artifactId}`,
      limit: "1",
    });
    const response = await this.#metadataRequest(query);
    const rows = await response.json() as unknown[];
    return rows[0] ? decodeMetadata(rows[0]) : null;
  }

  async requestPreview(artifactId: string): Promise<ResearchArtifactPreview> {
    validateArtifactId(artifactId);
    const response = await this.#fetch(
      `${this.#url}/functions/v1/research-artifact-preview`,
      {
        method: "POST",
        headers: {
          apikey: this.#anonKey,
          Authorization: `Bearer ${this.#anonKey}`,
          "Content-Type": "application/json",
          Accept: "application/json",
        },
        body: JSON.stringify({ artifact_id: artifactId }),
      },
    );
    if (!response.ok) {
      throw new Error(`Research artifact preview request failed with status ${response.status}.`);
    }
    const preview = decodePreview(await response.json());
    if (preview.artifactId !== artifactId) {
      throw new Error("Signed preview response identity does not match the request.");
    }
    const signedUrl = new URL(preview.url);
    if (signedUrl.protocol !== "https:" || signedUrl.origin !== new URL(this.#url).origin) {
      throw new Error("Signed preview URL must use the configured Supabase HTTPS origin.");
    }
    return preview;
  }

  async #metadataRequest(query: URLSearchParams): Promise<Response> {
    const response = await this.#fetch(
      `${this.#url}/rest/v1/research_artifact_metadata?${query}`,
      {
        method: "GET",
        headers: {
          apikey: this.#anonKey,
          Authorization: `Bearer ${this.#anonKey}`,
          Accept: "application/json",
        },
      },
    );
    if (!response.ok) {
      throw new Error(`Research artifact metadata query failed with status ${response.status}.`);
    }
    return response;
  }
}

function validateArtifactId(value: string): void {
  if (!ARTIFACT_ID.test(value)) {
    throw new Error("Artifact identity must be artifact:<64 lowercase hex>.");
  }
}

function decodeMetadata(value: unknown): ResearchArtifactMetadata {
  const row = requireRecord(value);
  const artifactId = String(row.artifact_id ?? row.artifactId ?? "");
  validateArtifactId(artifactId);
  return {
    artifactId,
    owningPublicationKey: String(row.owning_publication_key ?? row.owningPublicationKey ?? ""),
    sizeBytes: requireNonNegativeInteger(row.size_bytes ?? row.sizeBytes, "sizeBytes"),
    mediaType: String(row.media_type ?? row.mediaType ?? ""),
    displayName: String(row.display_name ?? row.displayName ?? ""),
    artifactClass: requireEnum(
      row.artifact_class ?? row.artifactClass,
      ["image", "markdown", "csv", "json", "text", "pdf"],
      "artifactClass",
    ) as ResearchArtifactClass,
    previewPolicy: requireEnum(
      row.preview_policy ?? row.previewPolicy,
      ["inline", "download", "metadata_only", "blocked"],
      "previewPolicy",
    ) as ResearchArtifactPreviewPolicy,
    sanitizerVersion: requireNonNegativeInteger(
      row.sanitizer_version ?? row.sanitizerVersion,
      "sanitizerVersion",
    ),
    manifestVersion: requireNonNegativeInteger(
      row.manifest_version ?? row.manifestVersion,
      "manifestVersion",
    ),
    publishedAt: String(row.published_at ?? row.publishedAt ?? ""),
  } as ResearchArtifactMetadata;
}

function decodePreview(value: unknown): ResearchArtifactPreview {
  const row = requireRecord(value);
  const artifactId = String(row.artifact_id ?? row.artifactId ?? "");
  validateArtifactId(artifactId);
  const previewPolicy = row.preview_policy ?? row.previewPolicy;
  if (previewPolicy !== "inline" && previewPolicy !== "download") {
    throw new Error("Signed preview response has a non-previewable policy.");
  }
  const expiresAt = String(row.expires_at ?? row.expiresAt ?? "");
  const expiry = Date.parse(expiresAt);
  const lifetime = expiry - Date.now();
  if (!Number.isFinite(expiry) || lifetime <= 0 || lifetime > MAX_SIGNED_URL_SECONDS * 1000) {
    throw new Error("Signed preview expiry is invalid or exceeds five minutes.");
  }
  return {
    artifactId,
    url: String(row.url ?? ""),
    expiresAt,
    mediaType: String(row.media_type ?? row.mediaType ?? ""),
    previewPolicy,
    displayName: String(row.display_name ?? row.displayName ?? ""),
    sizeBytes: requireNonNegativeInteger(row.size_bytes ?? row.sizeBytes, "sizeBytes"),
  };
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Artifact response must be an object.");
  }
  return value as Record<string, unknown>;
}

function requireNonNegativeInteger(value: unknown, name: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new Error(`Artifact ${name} must be a non-negative integer.`);
  }
  return parsed;
}

function requireEnum(value: unknown, allowed: readonly string[], name: string): string {
  if (typeof value !== "string" || !allowed.includes(value)) {
    throw new Error(`Artifact ${name} is unsupported.`);
  }
  return value;
}

function boundedPage(page: { limit?: number; offset?: number }): {
  limit: number;
  offset: number;
} {
  const limit = page.limit ?? 50;
  const offset = page.offset ?? 0;
  if (
    !Number.isSafeInteger(limit)
    || limit < 1
    || limit > 100
    || !Number.isSafeInteger(offset)
    || offset < 0
  ) {
    throw new Error("Artifact pagination requires limit 1-100 and non-negative offset.");
  }
  return { limit, offset };
}
