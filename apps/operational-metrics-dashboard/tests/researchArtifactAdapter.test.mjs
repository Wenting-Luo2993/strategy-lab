import assert from "node:assert/strict";
import test from "node:test";

import {
  LocalResearchArtifactAdapter,
  SupabaseResearchArtifactAdapter,
} from "../src/data/researchArtifactAdapter.ts";

const ARTIFACT_ID = `artifact:${"a".repeat(64)}`;
const METADATA = {
  artifact_id: ARTIFACT_ID,
  owning_publication_key: "run:fixture",
  size_bytes: 42,
  media_type: "text/markdown",
  display_name: "summary.md",
  artifact_class: "markdown",
  preview_policy: "inline",
  sanitizer_version: 1,
  manifest_version: 1,
  published_at: "2026-10-12T12:00:00Z",
};

test("Supabase artifact metadata uses the safe view and never selects object keys", async () => {
  const requests = [];
  const adapter = new SupabaseResearchArtifactAdapter(
    "https://example.supabase.co",
    "anon-key",
    async (url, options) => {
      requests.push({ url: String(url), options });
      return new Response(JSON.stringify([METADATA]), { status: 200 });
    },
  );

  const artifacts = await adapter.listArtifacts("run:fixture");

  assert.equal(artifacts[0].artifactId, ARTIFACT_ID);
  assert.match(requests[0].url, /research_artifact_metadata/);
  assert.doesNotMatch(requests[0].url, /object_key/);
  assert.equal(requests[0].options.method, "GET");
  assert.equal("body" in requests[0].options, false);
});

test("signed preview request accepts only artifact identity", async () => {
  let request;
  const adapter = new SupabaseResearchArtifactAdapter(
    "https://example.supabase.co",
    "anon-key",
    async (url, options) => {
      request = { url: String(url), options };
      return new Response(JSON.stringify({
        artifact_id: ARTIFACT_ID,
        url: "https://example.supabase.co/storage/v1/object/sign/research-artifacts/signed",
        expires_at: new Date(Date.now() + 60_000).toISOString(),
        media_type: "text/markdown",
        preview_policy: "inline",
        display_name: "summary.md",
        size_bytes: 42,
      }), { status: 200 });
    },
  );

  const preview = await adapter.requestPreview(ARTIFACT_ID);

  assert.equal(preview.artifactId, ARTIFACT_ID);
  assert.deepEqual(JSON.parse(request.options.body), { artifact_id: ARTIFACT_ID });
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.body.includes("object_key"), false);
  assert.match(request.url, /research-artifact-preview$/);
  await assert.rejects(
    () => adapter.requestPreview("v1/raw/object-key"),
    /Artifact identity/,
  );
});

test("signed preview rejects overlong grants and non-previewable policies", async () => {
  const adapter = new SupabaseResearchArtifactAdapter(
    "https://example.supabase.co",
    "anon-key",
    async () => new Response(JSON.stringify({
      artifact_id: ARTIFACT_ID,
      url: "https://example.supabase.co/signed",
      expires_at: new Date(Date.now() + 301_000).toISOString(),
      media_type: "application/pdf",
      preview_policy: "metadata_only",
      display_name: "report.pdf",
      size_bytes: 42,
    }), { status: 200 }),
  );

  await assert.rejects(
    () => adapter.requestPreview(ARTIFACT_ID),
    /non-previewable policy/,
  );
});

test("local adapter uses identity-addressed GET preview contract", async () => {
  const requests = [];
  const adapter = new LocalResearchArtifactAdapter(
    "http://127.0.0.1:4178",
    async (url, options) => {
      requests.push({ url: String(url), options });
      return new Response(JSON.stringify({
        artifactId: ARTIFACT_ID,
        url: `/api/artifacts/${encodeURIComponent(ARTIFACT_ID)}/content`,
        expiresAt: new Date(Date.now() + 60_000).toISOString(),
        mediaType: "text/plain",
        previewPolicy: "inline",
        displayName: "summary.txt",
        sizeBytes: 42,
      }), { status: 200 });
    },
  );

  await adapter.requestPreview(ARTIFACT_ID);

  assert.equal(requests[0].options.method, "GET");
  assert.match(requests[0].url, /artifact%3A[a-f0-9]{64}\/preview$/);
});
