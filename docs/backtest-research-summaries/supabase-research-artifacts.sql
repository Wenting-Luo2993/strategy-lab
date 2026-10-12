-- Private, immutable research artifact storage and metadata.
-- Deployment gate: run with an administrative role only after the
-- research-artifacts bucket and signed-preview Edge Function are approved.

INSERT INTO storage.buckets (
    id, name, public, file_size_limit, allowed_mime_types
)
VALUES (
    'research-artifacts', 'research-artifacts', false, 10000000,
    ARRAY[
        'image/png', 'image/jpeg', 'image/gif', 'image/webp',
        'text/markdown', 'text/csv', 'application/json', 'text/plain',
        'application/yaml', 'application/pdf'
    ]
)
ON CONFLICT (id) DO UPDATE
SET public = false,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

CREATE TABLE IF NOT EXISTS public.research_artifacts (
    artifact_id                  text PRIMARY KEY
                                 CHECK (artifact_id ~ '^artifact:[0-9a-f]{64}$'),
    owning_publication_key       text NOT NULL
                                 REFERENCES public.research_publications(idempotency_key),
    content_sha256               text NOT NULL
                                 CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes                   bigint NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10000000),
    media_type                   text NOT NULL CHECK (media_type IN (
                                     'image/png', 'image/jpeg', 'image/gif', 'image/webp',
                                     'text/markdown', 'text/csv', 'application/json',
                                     'text/plain', 'application/yaml', 'application/pdf'
                                 )),
    display_name                 text NOT NULL
                                 CHECK (display_name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,115}$'),
    artifact_class               text NOT NULL CHECK (artifact_class IN (
                                     'image', 'markdown', 'csv', 'json', 'text', 'pdf'
                                 )),
    object_key                   text NOT NULL,
    preview_policy               text NOT NULL CHECK (preview_policy IN (
                                     'inline', 'download', 'metadata_only', 'blocked'
                                 )),
    sanitizer_version            integer NOT NULL CHECK (sanitizer_version >= 1),
    manifest_version             integer NOT NULL CHECK (manifest_version >= 1),
    manifest_sha256              text NOT NULL UNIQUE
                                 CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    source_registry_checksum     text NOT NULL
                                 CHECK (source_registry_checksum ~ '^[0-9a-f]{64}$'),
    publication_state            text NOT NULL CHECK (publication_state IN (
                                     'published', 'revoked'
                                 )),
    published_at                 timestamptz NOT NULL,
    revoked_at                   timestamptz,
    CHECK (object_key = 'v1/' || content_sha256 || '/' || display_name),
    CHECK ((publication_state = 'revoked') = (revoked_at IS NOT NULL)),
    CHECK (artifact_class <> 'pdf' OR preview_policy <> 'inline')
);

CREATE INDEX IF NOT EXISTS research_artifacts_owner
    ON public.research_artifacts (
        owning_publication_key, published_at DESC, artifact_id
    );
CREATE INDEX IF NOT EXISTS research_artifacts_object
    ON public.research_artifacts (object_key);

CREATE OR REPLACE FUNCTION public.guard_research_artifact_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.artifact_id <> OLD.artifact_id
       OR NEW.owning_publication_key <> OLD.owning_publication_key
       OR NEW.content_sha256 <> OLD.content_sha256
       OR NEW.size_bytes <> OLD.size_bytes
       OR NEW.media_type <> OLD.media_type
       OR NEW.display_name <> OLD.display_name
       OR NEW.artifact_class <> OLD.artifact_class
       OR NEW.object_key <> OLD.object_key
       OR NEW.preview_policy <> OLD.preview_policy
       OR NEW.sanitizer_version <> OLD.sanitizer_version
       OR NEW.manifest_version <> OLD.manifest_version
       OR NEW.manifest_sha256 <> OLD.manifest_sha256
       OR NEW.source_registry_checksum <> OLD.source_registry_checksum
       OR NEW.published_at <> OLD.published_at THEN
        RAISE EXCEPTION 'research artifact identity and manifest are immutable';
    END IF;
    IF OLD.publication_state = 'revoked' THEN
        RAISE EXCEPTION 'revoked research artifacts are immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS guard_research_artifact_identity ON public.research_artifacts;
CREATE TRIGGER guard_research_artifact_identity
BEFORE UPDATE ON public.research_artifacts
FOR EACH ROW EXECUTE FUNCTION public.guard_research_artifact_identity();

CREATE OR REPLACE FUNCTION public.prevent_research_artifact_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'research artifact manifests cannot be deleted; revoke them';
END;
$$;

DROP TRIGGER IF EXISTS prevent_research_artifact_delete ON public.research_artifacts;
CREATE TRIGGER prevent_research_artifact_delete
BEFORE DELETE ON public.research_artifacts
FOR EACH ROW EXECUTE FUNCTION public.prevent_research_artifact_delete();

ALTER TABLE public.research_artifacts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.research_artifacts FROM anon, authenticated;

-- Browser-readable metadata deliberately excludes object_key, checksums that
-- could become addressing inputs, and every local path/staging field.
CREATE OR REPLACE VIEW public.research_artifact_metadata
WITH (security_barrier = true)
AS
SELECT artifact_id, owning_publication_key, size_bytes, media_type,
       display_name, artifact_class, preview_policy, sanitizer_version,
       manifest_version, published_at
FROM public.research_artifacts
WHERE publication_state = 'published';

GRANT SELECT ON public.research_artifact_metadata TO anon, authenticated;

-- The bucket has no browser policies. service_role bypasses RLS for the
-- trusted publisher and preview function; its credential must remain in
-- server-side secrets only.
DROP POLICY IF EXISTS research_artifacts_browser_select ON storage.objects;
DROP POLICY IF EXISTS research_artifacts_browser_insert ON storage.objects;
DROP POLICY IF EXISTS research_artifacts_browser_update ON storage.objects;
DROP POLICY IF EXISTS research_artifacts_browser_delete ON storage.objects;
-- Do not grant a policy scoped to bucket_id = 'research-artifacts'. Existing
-- storage.objects grants/policies for unrelated private buckets remain intact.

-- The signed-preview Edge Function contract is:
-- request:  POST JSON {"artifact_id":"artifact:<64 lowercase hex>"}
-- reject:   unknown fields, raw object_key/path/url, non-published rows,
--           blocked/metadata_only policy, or an identity that fails validation
-- response: {"artifact_id", "url", "expires_at", "media_type",
--            "preview_policy", "display_name", "size_bytes"}
-- The function must query research_artifacts by artifact_id using a
-- server-side service-role secret, sign that row's exact object_key for at
-- most 300 seconds, and return Cache-Control: no-store. It must never accept
-- or echo an object key supplied by the browser.
