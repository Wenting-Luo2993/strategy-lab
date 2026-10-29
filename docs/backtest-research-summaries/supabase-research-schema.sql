-- P12 remote read model for summary-only research publications.
-- Run this in the Supabase SQL editor with an administrative role.

CREATE TABLE IF NOT EXISTS public.research_publications (
    idempotency_key     text PRIMARY KEY,
    aggregate_type     text NOT NULL
                       CHECK (aggregate_type IN (
                           'run_summary',
                           'registry_record',
                           'experiment_definition',
                           'experiment_membership',
                           'experiment_outcome',
                           'study_reliability',
                           'methodology_freeze',
                           'holdout_authorization',
                           'holdout_claim',
                           'holdout_terminal',
                           'experiment_archive'
                       )),
    aggregate_id       text NOT NULL,
    schema_version     integer NOT NULL CHECK (schema_version >= 1),
    publication_version integer NOT NULL CHECK (publication_version >= 1),
    payload             jsonb NOT NULL,
    payload_sha256      text NOT NULL CHECK (length(payload_sha256) = 64),
    acknowledged_at     timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS research_publications_aggregate
    ON public.research_publications (aggregate_type, aggregate_id);

ALTER TABLE public.research_publications
    DROP CONSTRAINT IF EXISTS research_publications_aggregate_type_check;
ALTER TABLE public.research_publications
    ADD CONSTRAINT research_publications_aggregate_type_check
    CHECK (aggregate_type IN (
        'run_summary',
        'registry_record',
        'experiment_definition',
        'experiment_membership',
        'experiment_outcome',
        'study_reliability',
        'methodology_freeze',
        'holdout_authorization',
        'holdout_claim',
        'holdout_terminal',
        'experiment_archive'
    ));

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

-- -------------------------------------------------------------------------
-- P13 flexible analysis read model
-- -------------------------------------------------------------------------
-- These rows are derived and replaceable.  The trusted publisher owns writes;
-- browsers receive only catalog SELECT and execution of the bounded read RPC.

CREATE TABLE IF NOT EXISTS public.research_analysis_runs (
    run_id                     text PRIMARY KEY,
    display_alias              text NOT NULL,
    strategy_id                text NOT NULL,
    methodology_version        text NOT NULL,
    execution_model_version    integer,
    metric_calculation_version integer,
    feature_set_version        integer,
    validation_state           text NOT NULL,
    trust_state                text NOT NULL
                               CHECK (trust_state IN (
                                   'current_validated',
                                   'validation_attention',
                                   'legacy'
                               )),
    is_legacy                  boolean NOT NULL,
    code_dirty                 boolean NOT NULL,
    lineage_id                 text NOT NULL,
    supported                  boolean NOT NULL DEFAULT true,
    published_at               timestamptz NOT NULL,
    source_payload_sha256      text NOT NULL
                               CHECK (length(source_payload_sha256) = 64)
);

CREATE TABLE IF NOT EXISTS public.research_analysis_dimensions (
    run_id       text NOT NULL REFERENCES public.research_analysis_runs(run_id)
                 ON DELETE CASCADE,
    field_id     text NOT NULL CHECK (
                     field_id ~ '^(identity|parameter|dataset|validation|lineage|trust)\.[a-zA-Z0-9_.-]+$'
                 ),
    string_value text,
    numeric_value double precision,
    boolean_value boolean,
    date_value   timestamptz,
    PRIMARY KEY (run_id, field_id),
    CHECK (num_nonnulls(string_value, numeric_value, boolean_value, date_value) <= 1)
);
CREATE INDEX IF NOT EXISTS research_analysis_dimensions_field
    ON public.research_analysis_dimensions (field_id, run_id);

CREATE TABLE IF NOT EXISTS public.research_analysis_metrics (
    run_id               text NOT NULL REFERENCES public.research_analysis_runs(run_id)
                         ON DELETE CASCADE,
    field_id             text NOT NULL CHECK (field_id ~ '^metric\.[a-zA-Z0-9_.-]+$'),
    calculation_version  integer NOT NULL,
    unit                  text,
    direction             text CHECK (direction IN ('higher', 'lower', 'neutral')),
    numeric_value         double precision,
    PRIMARY KEY (run_id, field_id, calculation_version)
);
CREATE INDEX IF NOT EXISTS research_analysis_metrics_field
    ON public.research_analysis_metrics (field_id, calculation_version, run_id);

CREATE TABLE IF NOT EXISTS public.research_analysis_field_catalog (
    field_id          text PRIMARY KEY CHECK (
                          field_id ~ '^(identity|parameter|dataset|validation|lineage|trust|metric)\.[a-zA-Z0-9_.-]+$'
                      ),
    label             text NOT NULL,
    data_type         text NOT NULL
                      CHECK (data_type IN ('string', 'number', 'integer', 'boolean', 'date')),
    unit              text,
    cardinality       integer NOT NULL DEFAULT 0 CHECK (cardinality >= 0),
    filter_operators  text[] NOT NULL,
    aggregates        text[] NOT NULL,
    groupable         boolean NOT NULL DEFAULT true,
    screenable        boolean NOT NULL DEFAULT true,
    trust_restricted  boolean NOT NULL DEFAULT false,
    refreshed_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.research_analysis_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_analysis_dimensions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_analysis_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_analysis_field_catalog ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS research_analysis_catalog_anonymous_read
    ON public.research_analysis_field_catalog;
CREATE POLICY research_analysis_catalog_anonymous_read
ON public.research_analysis_field_catalog
FOR SELECT TO anon, authenticated
USING (true);

GRANT SELECT ON public.research_analysis_field_catalog TO anon, authenticated;
REVOKE ALL ON public.research_analysis_runs,
              public.research_analysis_dimensions,
              public.research_analysis_metrics
    FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON public.research_analysis_field_catalog FROM anon, authenticated;

-- Value lookup never treats a client field as a SQL identifier.  A field
-- reference is matched to the catalog and then used only as a row value.
CREATE OR REPLACE FUNCTION public.research_analysis_value(
    p_row jsonb,
    p_field text
) RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT p_row -> p_field
$$;

CREATE OR REPLACE FUNCTION public.research_analysis_matches(
    p_row jsonb,
    p_filter jsonb,
    p_depth integer DEFAULT 1
) RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    v_op text := p_filter ->> 'op';
    v_actual jsonb;
    v_expected jsonb := p_filter -> 'value';
    v_child jsonb;
    v_number double precision;
    v_expected_number double precision;
BEGIN
    IF p_depth > 5 THEN
        RAISE EXCEPTION 'analysis filter depth exceeds 5';
    END IF;
    IF v_op IN ('and', 'or') THEN
        IF jsonb_typeof(p_filter -> 'children') <> 'array' THEN
            RAISE EXCEPTION 'analysis filter group requires children';
        END IF;
        IF v_op = 'and' THEN
            FOR v_child IN SELECT value FROM jsonb_array_elements(p_filter -> 'children')
            LOOP
                IF NOT public.research_analysis_matches(p_row, v_child, p_depth + 1) THEN
                    RETURN false;
                END IF;
            END LOOP;
            RETURN true;
        END IF;
        FOR v_child IN SELECT value FROM jsonb_array_elements(p_filter -> 'children')
        LOOP
            IF public.research_analysis_matches(p_row, v_child, p_depth + 1) THEN
                RETURN true;
            END IF;
        END LOOP;
        RETURN false;
    END IF;

    v_actual := public.research_analysis_value(p_row, p_filter ->> 'field');
    IF v_op = 'exists' THEN
        RETURN (v_actual IS NOT NULL AND v_actual <> 'null'::jsonb)
            = COALESCE((v_expected #>> '{}')::boolean, false);
    ELSIF v_op = 'eq' THEN
        RETURN v_actual = v_expected;
    ELSIF v_op = 'neq' THEN
        RETURN v_actual IS DISTINCT FROM v_expected;
    ELSIF v_op = 'in' THEN
        RETURN EXISTS (SELECT 1 FROM jsonb_array_elements(v_expected) item
                       WHERE item = v_actual);
    ELSIF v_op = 'not_in' THEN
        RETURN NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v_expected) item
                           WHERE item = v_actual);
    ELSIF v_op = 'contains' THEN
        RETURN lower(v_actual #>> '{}') LIKE
               '%' || lower(COALESCE(v_expected #>> '{}', '')) || '%';
    END IF;

    BEGIN
        v_number := (v_actual #>> '{}')::double precision;
        v_expected_number := (v_expected #>> '{}')::double precision;
    EXCEPTION WHEN invalid_text_representation THEN
        RETURN false;
    END;
    IF v_op = 'gt' THEN RETURN v_number > v_expected_number;
    ELSIF v_op = 'gte' THEN RETURN v_number >= v_expected_number;
    ELSIF v_op = 'lt' THEN RETURN v_number < v_expected_number;
    ELSIF v_op = 'lte' THEN RETURN v_number <= v_expected_number;
    ELSIF v_op = 'between' THEN
        RETURN v_number >= ((v_expected -> 0) #>> '{}')::double precision
           AND v_number <= ((v_expected -> 1) #>> '{}')::double precision;
    END IF;
    RAISE EXCEPTION 'unsupported analysis filter operator %', v_op;
END;
$$;

CREATE OR REPLACE FUNCTION public.research_analysis_aggregate(
    p_values jsonb,
    p_aggregate text
) RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    v_count integer;
    v_result double precision;
    v_fraction double precision;
BEGIN
    IF p_aggregate = 'count' THEN
        SELECT count(*) INTO v_count
        FROM jsonb_array_elements(p_values) value
        WHERE value <> 'null'::jsonb;
        RETURN to_jsonb(v_count);
    END IF;
    IF p_aggregate NOT IN (
        'mean', 'median', 'min', 'max', 'sum', 'stddev',
        'p10', 'p25', 'p75', 'p90'
    ) THEN
        RAISE EXCEPTION 'unsupported analysis aggregate %', p_aggregate;
    END IF;
    v_fraction := CASE p_aggregate
        WHEN 'median' THEN 0.5 WHEN 'p10' THEN 0.1 WHEN 'p25' THEN 0.25
        WHEN 'p75' THEN 0.75 WHEN 'p90' THEN 0.9 ELSE NULL END;
    IF v_fraction IS NOT NULL THEN
        SELECT percentile_cont(v_fraction) WITHIN GROUP (ORDER BY number)
        INTO v_result
        FROM (
            SELECT (value #>> '{}')::double precision AS number
            FROM jsonb_array_elements(p_values) value
            WHERE jsonb_typeof(value) = 'number'
        ) numbers;
    ELSE
        SELECT CASE p_aggregate
            WHEN 'mean' THEN avg(number)
            WHEN 'min' THEN min(number)
            WHEN 'max' THEN max(number)
            WHEN 'sum' THEN sum(number)
            WHEN 'stddev' THEN stddev_pop(number)
        END INTO v_result
        FROM (
            SELECT (value #>> '{}')::double precision AS number
            FROM jsonb_array_elements(p_values) value
            WHERE jsonb_typeof(value) = 'number'
        ) numbers;
    END IF;
    RETURN to_jsonb(v_result);
END;
$$;

-- Read-only RPC.  POST is used only because PostgREST RPC accepts structured
-- JSON in a request body; the function is STABLE and has no write privileges.
CREATE OR REPLACE FUNCTION public.research_analysis_query(
    p_query jsonb,
    p_estimate_only boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    c_max_candidate_rows constant integer := 5000;
    c_max_groups constant integer := 500;
    c_max_response_rows constant integer := 500;
    c_max_chart_marks constant integer := 300;
    c_max_response_bytes constant integer := 512000;
    c_max_filter_nodes constant integer := 40;
    c_max_screening_columns constant integer := 24;
    c_max_order_rules constant integer := 8;
    v_filter jsonb := COALESCE(p_query -> 'filters', '{"op":"and","children":[]}'::jsonb);
    v_group_by jsonb := COALESCE(p_query -> 'groupBy', '[]'::jsonb);
    v_measures jsonb := COALESCE(p_query -> 'measures', '[]'::jsonb);
    v_screening jsonb := COALESCE(p_query -> 'screeningColumns', '[]'::jsonb);
    v_order_by jsonb := COALESCE(p_query -> 'orderBy', '[]'::jsonb);
    v_include_legacy boolean := COALESCE((p_query #>> '{population,includeLegacy}')::boolean, false);
    v_trust_states text[] := ARRAY(
        SELECT jsonb_array_elements_text(
            COALESCE(p_query #> '{population,trustStates}', '["current_validated"]'::jsonb)
        )
    );
    v_candidates jsonb := '[]'::jsonb;
    v_output jsonb := '[]'::jsonb;
    v_groups jsonb := '{}'::jsonb;
    v_row jsonb;
    v_data jsonb;
    v_field text;
    v_group_key jsonb;
    v_group_id text;
    v_group jsonb;
    v_measure jsonb;
    v_measure_values jsonb;
    v_result_row jsonb;
    v_candidate_count integer := 0;
    v_group_count integer := 0;
    v_total_result_rows integer := 0;
    v_response_bytes integer := 2;
    v_truncated boolean := false;
    v_versions integer[];
BEGIN
    IF jsonb_array_length(v_screening) > c_max_screening_columns
       OR jsonb_array_length(v_order_by) > c_max_order_rules THEN
        RAISE EXCEPTION 'analysis query exceeds screening/order limits';
    END IF;
    IF (
        SELECT count(*) FROM jsonb_path_query(v_filter, 'strict $.** ? (@.type() == "object")')
    ) > c_max_filter_nodes THEN
        RAISE EXCEPTION 'analysis filter exceeds node limit';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM (
            SELECT jsonb_array_elements_text(v_group_by) field
            UNION ALL SELECT value ->> 'field' FROM jsonb_array_elements(v_measures) value
            UNION ALL SELECT jsonb_array_elements_text(v_screening)
        ) requested
        LEFT JOIN public.research_analysis_field_catalog catalog
          ON catalog.field_id = requested.field
        WHERE catalog.field_id IS NULL
    ) THEN
        RAISE EXCEPTION 'analysis query references an unknown field';
    END IF;

    FOR v_row IN
        SELECT to_jsonb(r.*) AS run
        FROM public.research_analysis_runs r
        WHERE r.supported
          AND (v_include_legacy OR NOT r.is_legacy)
          AND r.trust_state = ANY(v_trust_states)
        ORDER BY r.run_id
    LOOP
        v_data := jsonb_build_object(
            'identity.run_id', v_row ->> 'run_id',
            'identity.display_alias', v_row ->> 'display_alias',
            'identity.strategy_id', v_row ->> 'strategy_id',
            'identity.methodology_version', v_row ->> 'methodology_version',
            'identity.execution_model_version', v_row -> 'execution_model_version',
            'identity.metric_calculation_version', v_row -> 'metric_calculation_version',
            'identity.feature_set_version', v_row -> 'feature_set_version',
            'validation.state', v_row ->> 'validation_state',
            'lineage.id', v_row ->> 'lineage_id',
            'trust.state', v_row ->> 'trust_state',
            'trust.legacy', v_row -> 'is_legacy',
            'trust.current', to_jsonb(NOT (v_row ->> 'is_legacy')::boolean),
            'trust.code_dirty', v_row -> 'code_dirty'
        );
        SELECT v_data || COALESCE(jsonb_object_agg(
            d.field_id,
            COALESCE(
                to_jsonb(d.string_value),
                to_jsonb(d.numeric_value),
                to_jsonb(d.boolean_value),
                to_jsonb(d.date_value)
            )
        ), '{}'::jsonb)
        INTO v_data
        FROM public.research_analysis_dimensions d
        WHERE d.run_id = v_row ->> 'run_id';
        SELECT v_data || COALESCE(jsonb_object_agg(m.field_id, to_jsonb(m.numeric_value)), '{}'::jsonb)
        INTO v_data
        FROM public.research_analysis_metrics m
        WHERE m.run_id = v_row ->> 'run_id'
          AND m.calculation_version = (v_row ->> 'metric_calculation_version')::integer;
        IF public.research_analysis_matches(v_data, v_filter) THEN
            v_candidates := v_candidates || jsonb_build_array(v_data);
            v_candidate_count := v_candidate_count + 1;
            IF v_candidate_count > c_max_candidate_rows THEN
                RAISE EXCEPTION 'analysis candidate row limit exceeded; narrow filters';
            END IF;
        END IF;
    END LOOP;

    IF jsonb_array_length(v_group_by) = 0 THEN
        v_group_count := CASE WHEN v_candidate_count = 0 THEN 0 ELSE 1 END;
    ELSE
        FOR v_data IN SELECT value FROM jsonb_array_elements(v_candidates)
        LOOP
            v_group_key := '[]'::jsonb;
            FOR v_field IN SELECT jsonb_array_elements_text(v_group_by)
            LOOP
                v_group_key := v_group_key || jsonb_build_array(v_data -> v_field);
            END LOOP;
            v_groups := v_groups || jsonb_build_object(md5(v_group_key::text), true);
        END LOOP;
        v_group_count := jsonb_object_length(v_groups);
    END IF;
    IF v_group_count > c_max_groups THEN
        RAISE EXCEPTION 'analysis group limit exceeded; narrow filters or grouping';
    END IF;

    IF p_estimate_only THEN
        RETURN jsonb_build_object(
            'rows', '[]'::jsonb,
            'metadata', jsonb_build_object(
                'candidateRows', v_candidate_count,
                'estimatedGroups', v_group_count,
                'estimatedCost', v_candidate_count * GREATEST(1, jsonb_array_length(v_group_by))
                    + v_group_count * GREATEST(1, jsonb_array_length(v_measures)),
                'limits', jsonb_build_object(
                    'maxCandidateRows', c_max_candidate_rows,
                    'maxGroups', c_max_groups,
                    'maxResponseRows', c_max_response_rows,
                    'maxChartMarks', c_max_chart_marks,
                    'maxResponseBytes', c_max_response_bytes,
                    'maxFilterNodes', c_max_filter_nodes,
                    'maxFilterDepth', 5,
                    'maxScreeningColumns', c_max_screening_columns,
                    'maxOrderRules', c_max_order_rules,
                    'maxUrlStateBytes', 4096,
                    'maxLocalStateBytes', 16384
                ),
                'requiresAggregation', v_candidate_count > c_max_response_rows,
                'allowed', true
            )
        );
    END IF;

    SELECT array_agg(DISTINCT (value ->> 'identity.metric_calculation_version')::integer)
    INTO v_versions
    FROM jsonb_array_elements(v_candidates) value
    WHERE value ? 'identity.metric_calculation_version';
    IF cardinality(v_versions) > 1
       AND EXISTS (
           SELECT 1 FROM jsonb_array_elements(v_measures) measure
           WHERE measure ->> 'field' LIKE 'metric.%'
       )
       AND NOT (v_group_by @> '["identity.metric_calculation_version"]'::jsonb) THEN
        RAISE EXCEPTION 'metric aggregation spans incompatible calculation versions';
    END IF;

    IF jsonb_array_length(v_group_by) = 0 AND jsonb_array_length(v_measures) = 0 THEN
        FOR v_data IN SELECT value FROM jsonb_array_elements(v_candidates)
        LOOP
            v_result_row := '{}'::jsonb;
            FOR v_field IN SELECT jsonb_array_elements_text(v_screening)
            LOOP
                v_result_row := v_result_row || jsonb_build_object(v_field, v_data -> v_field);
            END LOOP;
            v_total_result_rows := v_total_result_rows + 1;
            IF v_total_result_rows <= c_max_response_rows
               AND v_response_bytes + octet_length(v_result_row::text) + 1 <= c_max_response_bytes THEN
                v_output := v_output || jsonb_build_array(v_result_row);
                v_response_bytes := v_response_bytes + octet_length(v_result_row::text) + 1;
            ELSE
                v_truncated := true;
            END IF;
        END LOOP;
    ELSE
        -- Build bounded groups as JSON.  At most 500 groups and 5,000
        -- candidates are admitted above, so this in-RPC aggregation is bounded.
        v_groups := '{}'::jsonb;
        FOR v_data IN SELECT value FROM jsonb_array_elements(v_candidates)
        LOOP
            v_group_key := '[]'::jsonb;
            FOR v_field IN SELECT jsonb_array_elements_text(v_group_by)
            LOOP
                v_group_key := v_group_key || jsonb_build_array(v_data -> v_field);
            END LOOP;
            v_group_id := md5(v_group_key::text);
            v_group := COALESCE(v_groups -> v_group_id, jsonb_build_object(
                'key', v_group_key, 'members', '[]'::jsonb
            ));
            v_group := jsonb_set(v_group, '{members}', (v_group -> 'members') || jsonb_build_array(v_data));
            v_groups := jsonb_set(v_groups, ARRAY[v_group_id], v_group, true);
        END LOOP;
        FOR v_group IN SELECT value FROM jsonb_each(v_groups) ORDER BY key
        LOOP
            v_result_row := '{}'::jsonb;
            FOR v_field, v_group_key IN
                SELECT field.value, key.value
                FROM jsonb_array_elements_text(v_group_by) WITH ORDINALITY field(value, ordinal)
                JOIN jsonb_array_elements(v_group -> 'key') WITH ORDINALITY key(value, ordinal)
                  USING (ordinal)
            LOOP
                v_result_row := v_result_row || jsonb_build_object(v_field, v_group_key);
            END LOOP;
            FOR v_measure IN SELECT value FROM jsonb_array_elements(v_measures)
            LOOP
                SELECT COALESCE(jsonb_agg(member -> (v_measure ->> 'field')), '[]'::jsonb)
                INTO v_measure_values
                FROM jsonb_array_elements(v_group -> 'members') member;
                v_result_row := v_result_row || jsonb_build_object(
                    (v_measure ->> 'aggregate') || ':' || (v_measure ->> 'field'),
                    public.research_analysis_aggregate(v_measure_values, v_measure ->> 'aggregate')
                );
            END LOOP;
            v_result_row := v_result_row || jsonb_build_object(
                'analysis.run_count', jsonb_array_length(v_group -> 'members')
            );
            v_total_result_rows := v_total_result_rows + 1;
            IF v_total_result_rows <= c_max_response_rows
               AND v_response_bytes + octet_length(v_result_row::text) + 1 <= c_max_response_bytes THEN
                v_output := v_output || jsonb_build_array(v_result_row);
                v_response_bytes := v_response_bytes + octet_length(v_result_row::text) + 1;
            ELSE
                v_truncated := true;
            END IF;
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'rows', v_output,
        'metadata', jsonb_build_object(
            'candidateRows', v_candidate_count,
            'estimatedGroups', v_group_count,
            'estimatedCost', v_candidate_count * GREATEST(1, jsonb_array_length(v_group_by))
                + v_group_count * GREATEST(1, jsonb_array_length(v_measures)),
            'limits', jsonb_build_object(
                'maxCandidateRows', c_max_candidate_rows,
                'maxGroups', c_max_groups,
                'maxResponseRows', c_max_response_rows,
                'maxChartMarks', c_max_chart_marks,
                'maxResponseBytes', c_max_response_bytes,
                'maxFilterNodes', c_max_filter_nodes,
                'maxFilterDepth', 5,
                'maxScreeningColumns', c_max_screening_columns,
                'maxOrderRules', c_max_order_rules,
                'maxUrlStateBytes', 4096,
                'maxLocalStateBytes', 16384
            ),
            'requiresAggregation', v_candidate_count > c_max_response_rows,
            'allowed', true,
            'returnedRows', jsonb_array_length(v_output),
            'totalResultRows', v_total_result_rows,
            'truncated', v_truncated,
            'sampled', false,
            'aggregated', jsonb_array_length(v_group_by) > 0
                          OR jsonb_array_length(v_measures) > 0,
            'chartMarks', LEAST(jsonb_array_length(v_output), c_max_chart_marks),
            'chartMarksTruncated', jsonb_array_length(v_output) > c_max_chart_marks
        )
    );
END;
$$;

REVOKE ALL ON FUNCTION public.research_analysis_query(jsonb, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.research_analysis_query(jsonb, boolean)
    TO anon, authenticated;
