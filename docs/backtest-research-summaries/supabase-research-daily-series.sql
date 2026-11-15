-- Derived daily aggregates only. Apply separately from immutable publications.
BEGIN;
CREATE TABLE IF NOT EXISTS public.research_experiment_daily_series (
    experiment_id text PRIMARY KEY,
    schema_version integer NOT NULL CHECK (schema_version = 1),
    payload jsonb NOT NULL CHECK (
        jsonb_typeof(payload) = 'object'
        AND payload->>'experimentId' = experiment_id
        AND (payload->>'version')::integer = schema_version
        AND jsonb_typeof(payload->'runs') = 'array'
        AND jsonb_array_length(payload->'runs') <= 200
        AND octet_length(payload::text) <= 3145728
    ),
    payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    published_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.research_experiment_daily_series ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.research_experiment_daily_series FROM anon, authenticated;
GRANT SELECT ON public.research_experiment_daily_series TO anon, authenticated;
GRANT ALL ON public.research_experiment_daily_series TO service_role;
DROP POLICY IF EXISTS research_daily_series_read ON public.research_experiment_daily_series;
CREATE POLICY research_daily_series_read ON public.research_experiment_daily_series
    FOR SELECT TO anon, authenticated USING (
        EXISTS (SELECT 1 FROM public.research_publications p
                WHERE p.aggregate_id = experiment_id
                  AND p.aggregate_type = 'experiment_outcome')
    );
COMMIT;
