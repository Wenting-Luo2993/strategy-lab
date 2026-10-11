-- P12 remote read model for summary-only research publications.
-- Run this in the Supabase SQL editor with an administrative role.

CREATE TABLE IF NOT EXISTS public.research_publications (
    idempotency_key     text PRIMARY KEY,
    aggregate_type     text NOT NULL
                       CHECK (aggregate_type IN ('run_summary', 'registry_record')),
    aggregate_id       text NOT NULL,
    schema_version     integer NOT NULL CHECK (schema_version >= 1),
    publication_version integer NOT NULL CHECK (publication_version >= 1),
    payload             jsonb NOT NULL,
    payload_sha256      text NOT NULL CHECK (length(payload_sha256) = 64),
    acknowledged_at     timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS research_publications_aggregate
    ON public.research_publications (aggregate_type, aggregate_id);

CREATE OR REPLACE FUNCTION public.guard_research_publication_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.idempotency_key <> OLD.idempotency_key
       OR NEW.aggregate_type <> OLD.aggregate_type
       OR NEW.aggregate_id <> OLD.aggregate_id
       OR NEW.schema_version <> OLD.schema_version
       OR NEW.payload_sha256 <> OLD.payload_sha256
       OR NEW.payload <> OLD.payload THEN
        RAISE EXCEPTION 'research publication identity and payload are immutable';
    END IF;
    IF NEW.publication_version < OLD.publication_version THEN
        RAISE EXCEPTION 'research publication version cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS guard_research_publication_identity
    ON public.research_publications;
CREATE TRIGGER guard_research_publication_identity
BEFORE UPDATE ON public.research_publications
FOR EACH ROW
EXECUTE FUNCTION public.guard_research_publication_identity();

ALTER TABLE public.research_publications ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS research_publications_anonymous_read
    ON public.research_publications;
CREATE POLICY research_publications_anonymous_read
ON public.research_publications
FOR SELECT
TO anon, authenticated
USING (true);

GRANT SELECT ON public.research_publications TO anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON public.research_publications FROM anon, authenticated;

-- Supabase service_role bypasses RLS. Keep that key only in trusted local
-- Python under RESEARCH_SUPABASE_SERVICE_ROLE_KEY; never expose it to a browser.
