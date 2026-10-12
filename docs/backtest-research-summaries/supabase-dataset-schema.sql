-- Private approved-dataset metadata and Storage policy.
-- Run as an administrative role. The service-role credential remains only on
-- trusted owner-controlled devices and bypasses RLS; browsers receive no
-- policy granting access to research-datasets or these metadata tables.

INSERT INTO storage.buckets (id, name, public, file_size_limit)
VALUES ('research-datasets', 'research-datasets', false, 1073741824)
ON CONFLICT (id) DO UPDATE SET public = false;

CREATE TABLE IF NOT EXISTS public.research_datasets (
    dataset_id text PRIMARY KEY
        CHECK (dataset_id ~ '^sha256:[0-9a-f]{64}$'),
    status text NOT NULL
        CHECK (status IN ('candidate', 'approved', 'retired', 'revoked')),
    manifest_sha256 text NOT NULL
        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    manifest jsonb NOT NULL,
    license_classification text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.research_dataset_objects (
    dataset_id text NOT NULL REFERENCES public.research_datasets(dataset_id),
    symbol text NOT NULL,
    object_key text NOT NULL UNIQUE,
    object_sha256 text NOT NULL
        CHECK (object_sha256 ~ '^[0-9a-f]{64}$'),
    object_bytes bigint NOT NULL CHECK (object_bytes > 0),
    format text NOT NULL CHECK (format = 'parquet'),
    object_version integer NOT NULL CHECK (object_version >= 1),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, symbol, object_version)
);

CREATE OR REPLACE FUNCTION public.guard_research_dataset_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.dataset_id <> OLD.dataset_id
       OR (NEW.manifest - ARRAY['status', 'license'])
          <> (OLD.manifest - ARRAY['status', 'license'])
       OR NEW.license_classification <> OLD.license_classification THEN
        RAISE EXCEPTION 'research dataset identity and manifest are immutable';
    END IF;
    IF NEW.manifest->>'status' <> NEW.status THEN
        RAISE EXCEPTION 'research dataset status and manifest disagree';
    END IF;
    IF NOT (
        NEW.status = OLD.status
        OR (OLD.status = 'candidate' AND NEW.status IN ('approved', 'revoked'))
        OR (OLD.status = 'approved' AND NEW.status IN ('retired', 'revoked'))
        OR (OLD.status = 'retired' AND NEW.status = 'revoked')
    ) THEN
        RAISE EXCEPTION 'illegal research dataset lifecycle transition';
    END IF;
    IF NEW.manifest->'license' <> OLD.manifest->'license'
       AND NOT (OLD.status = 'candidate' AND NEW.status = 'approved') THEN
        RAISE EXCEPTION 'license evidence may only change at approval';
    END IF;
    IF NEW.status = 'approved' AND (
        coalesce(NEW.manifest->'license'->>'approved_by', '') = ''
        OR coalesce(NEW.manifest->'license'->>'approved_at', '') = ''
        OR coalesce(NEW.manifest->'license'->>'evidence_reference', '') = ''
    ) THEN
        RAISE EXCEPTION 'approved datasets require license evidence';
    END IF;
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS guard_research_dataset_update
    ON public.research_datasets;
CREATE TRIGGER guard_research_dataset_update
BEFORE UPDATE ON public.research_datasets
FOR EACH ROW EXECUTE FUNCTION public.guard_research_dataset_update();

CREATE OR REPLACE FUNCTION public.reject_research_dataset_object_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'research dataset objects are immutable';
END;
$$;

DROP TRIGGER IF EXISTS reject_research_dataset_object_update
    ON public.research_dataset_objects;
CREATE TRIGGER reject_research_dataset_object_update
BEFORE UPDATE OR DELETE ON public.research_dataset_objects
FOR EACH ROW EXECUTE FUNCTION public.reject_research_dataset_object_update();

ALTER TABLE public.research_datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_dataset_objects ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.research_datasets FROM anon, authenticated;
REVOKE ALL ON public.research_dataset_objects FROM anon, authenticated;

-- Remove any accidental browser-facing policies. No replacement SELECT or
-- Storage policy is created: service_role bypasses RLS for trusted publisher
-- and downloader processes, while anon/authenticated have no access.
DROP POLICY IF EXISTS research_datasets_anonymous_read
    ON public.research_datasets;
DROP POLICY IF EXISTS research_dataset_objects_anonymous_read
    ON public.research_dataset_objects;
DROP POLICY IF EXISTS research_datasets_storage_read
    ON storage.objects;
DROP POLICY IF EXISTS research_datasets_storage_write
    ON storage.objects;
