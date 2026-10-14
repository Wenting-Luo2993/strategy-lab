"""Adapter-neutral, bounded analysis of validated research run summaries.

The local SQLite store is the execution authority.  This module deliberately
operates on summary fields only: it never reads trades, fills, bars, ledgers, or
equity points.  The JSON-compatible query/result contract is mirrored by the
dashboard's Supabase adapter and the remote ``research_analysis_query`` RPC.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from vibe.research_pipeline.storage import schema

Scalar = str | int | float | bool | None

AGGREGATES = {
    "count",
    "mean",
    "median",
    "min",
    "max",
    "sum",
    "stddev",
    "p10",
    "p25",
    "p75",
    "p90",
}
FILTER_OPERATORS = {
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "contains",
    "exists",
}
TERMINAL_ANALYSIS_STATES = {
    "completed",
    "review_required",
    "validation_failed",
    "inconclusive",
    "failed",
    "cancelled",
    "archived",
}

# A recorded backtest comes to rest in `validating`, not in a terminal state:
# promoting it to `completed` requires a real leakage report and a bar-measured
# integrity summary, which recording alone cannot produce. Reading only
# terminal states therefore made the workspace blind to every run a study
# produces -- an empty field catalog and a dead UI no matter how many runs the
# store held.
#
# These rows are admitted to the *catalog* so the workspace has something to
# describe, but `_project_row` still classifies them as `validation_attention`
# rather than `current_validated`. The default population is unchanged, so an
# unvalidated number can never be mistaken for validated evidence; it appears
# only when the reader explicitly asks for runs needing attention.
ANALYSABLE_RUN_STATES = TERMINAL_ANALYSIS_STATES | {"validating"}


class AnalysisQueryError(ValueError):
    """The query is malformed or references unsupported fields."""


class AnalysisCostExceeded(AnalysisQueryError):
    """The query exceeds an explicit analysis policy limit."""


@dataclass(frozen=True)
class AnalysisPolicy:
    """Operational limits shared with the remote RPC and browser state."""

    max_candidate_rows: int = 5_000
    max_groups: int = 500
    max_response_rows: int = 500
    max_chart_marks: int = 300
    max_response_bytes: int = 512_000
    max_filter_nodes: int = 40
    max_filter_depth: int = 5
    max_screening_columns: int = 24
    max_order_rules: int = 8


@dataclass(frozen=True)
class AnalysisRow:
    run_id: str
    trust: Mapping[str, Scalar]
    dimensions: Mapping[str, Scalar]
    metrics: Mapping[str, float | None]

    def value(self, field: str) -> Scalar:
        if field == "identity.run_id":
            return self.run_id
        if field.startswith("trust."):
            return self.trust.get(field)
        if field.startswith("metric."):
            return self.metrics.get(field)
        return self.dimensions.get(field)


class SqliteAnalysisAdapter:
    """Read-only analysis adapter over the current research SQLite schema."""

    source = "sqlite"

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        policy: AnalysisPolicy | None = None,
    ) -> None:
        if (db_path is None) == (connection is None):
            raise ValueError("Provide exactly one of db_path or connection")
        self._owns_connection = connection is None
        if connection is None:
            connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        self._conn = connection
        self.policy = policy or AnalysisPolicy()

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    def rows(self) -> list[AnalysisRow]:
        """Project terminal run summaries into the namespaced analysis shape."""
        placeholders = ",".join("?" for _ in ANALYSABLE_RUN_STATES)
        run_rows = self._conn.execute(
            f"""
            SELECT r.*, vr.profile_name, vr.scope, vr.target_state,
                   vr.registry_hash_at_execution, vr.current_registry_hash,
                   vr.leakage_passed, vr.payload_json AS validation_payload
            FROM runs r
            LEFT JOIN validation_reports vr ON vr.run_id = r.run_id
            WHERE r.state IN ({placeholders})
            ORDER BY r.display_seq
            """,
            tuple(sorted(ANALYSABLE_RUN_STATES)),
        ).fetchall()
        if not run_rows:
            return []
        run_ids = [row["run_id"] for row in run_rows]
        marks = ",".join("?" for _ in run_ids)
        metric_rows = self._conn.execute(
            f"""
            SELECT run_id, metric_key, metric_value, calculation_version
            FROM run_metrics WHERE run_id IN ({marks})
            ORDER BY id
            """,
            run_ids,
        ).fetchall()
        metrics: dict[str, dict[str, float]] = {run_id: {} for run_id in run_ids}
        for metric in metric_rows:
            metrics[metric["run_id"]][f"metric.{metric['metric_key']}"] = float(
                metric["metric_value"]
            )
        return [self._project_row(row, metrics[row["run_id"]]) for row in run_rows]

    def field_catalog(self) -> list[dict[str, Any]]:
        """Return capabilities inferred from available rows, not a fixed UI list."""
        rows = self.rows()
        fields: dict[str, list[Scalar]] = {}
        for row in rows:
            for mapping in (row.trust, row.dimensions, row.metrics):
                for key, value in mapping.items():
                    fields.setdefault(key, []).append(value)
            fields.setdefault("identity.run_id", []).append(row.run_id)
        catalog = []
        for field, values in sorted(fields.items()):
            non_null = [value for value in values if value is not None]
            data_type = _data_type(non_null)
            catalog.append(
                {
                    "field": field,
                    "label": field.split(".", 1)[-1].replace("_", " ").title(),
                    "dataType": data_type,
                    "unit": _metric_unit(field),
                    "cardinality": len({_hashable(value) for value in non_null}),
                    "filterOperators": _operators_for(data_type),
                    "aggregates": sorted(AGGREGATES)
                    if data_type in {"integer", "number"}
                    else ["count"],
                    "groupable": True,
                    "screenable": True,
                    "trustRestricted": field.startswith("trust."),
                }
            )
        return catalog

    def estimate(self, query: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_query(query)
        rows = self._population(self.rows(), normalized["population"])
        rows = [
            row for row in rows if _matches(row, normalized["filters"], self.policy)
        ]
        cardinality = _group_cardinality(rows, normalized["groupBy"])
        cost = len(rows) * max(1, len(normalized["groupBy"])) + cardinality * max(
            1, len(normalized["measures"])
        )
        return {
            "candidateRows": len(rows),
            "estimatedGroups": cardinality,
            "estimatedCost": cost,
            "limits": _policy_dict(self.policy),
            "requiresAggregation": len(rows) > self.policy.max_response_rows,
            "allowed": len(rows) <= self.policy.max_candidate_rows
            and cardinality <= self.policy.max_groups,
        }

    def query(self, query: Mapping[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_query(query)
        estimate = self.estimate(normalized)
        if estimate["candidateRows"] > self.policy.max_candidate_rows:
            raise AnalysisCostExceeded(
                f"Candidate population {estimate['candidateRows']} exceeds "
                f"limit {self.policy.max_candidate_rows}; narrow the filters."
            )
        if estimate["estimatedGroups"] > self.policy.max_groups:
            raise AnalysisCostExceeded(
                f"Estimated groups {estimate['estimatedGroups']} exceeds "
                f"limit {self.policy.max_groups}; narrow or remove grouping fields."
            )
        rows = self._population(self.rows(), normalized["population"])
        rows = [
            row for row in rows if _matches(row, normalized["filters"], self.policy)
        ]
        self._assert_metric_versions(rows, normalized["measures"])
        result_rows = (
            _aggregate_rows(rows, normalized["groupBy"], normalized["measures"])
            if normalized["groupBy"] or normalized["measures"]
            else _screen_rows(rows, normalized["screeningColumns"])
        )
        result_rows = _order_rows(result_rows, normalized["orderBy"])
        total = len(result_rows)
        truncated = total > self.policy.max_response_rows
        result_rows = result_rows[: self.policy.max_response_rows]
        result_rows, byte_truncated = _fit_byte_budget(
            result_rows, self.policy.max_response_bytes
        )
        truncated = truncated or byte_truncated
        chart_marks = min(len(result_rows), self.policy.max_chart_marks)
        return {
            "rows": result_rows,
            "metadata": {
                **estimate,
                "returnedRows": len(result_rows),
                "totalResultRows": total,
                "truncated": truncated,
                "sampled": False,
                "aggregated": bool(
                    normalized["groupBy"] or normalized["measures"]
                ),
                "chartMarks": chart_marks,
                "chartMarksTruncated": len(result_rows) > chart_marks,
            },
        }

    def _project_row(
        self, row: sqlite3.Row, metrics: Mapping[str, float]
    ) -> AnalysisRow:
        identity = _json_object(row["identity_json"])
        parameters = identity.get("parameters")
        if not isinstance(parameters, Mapping):
            parameters = {}
        validation_payload = _json_object(row["validation_payload"])
        findings = validation_payload.get("findings")
        if not isinstance(findings, Sequence):
            findings = []
        finding_counts = {"blocking": 0, "review": 0, "advisory": 0}
        for finding in findings:
            if isinstance(finding, Mapping):
                severity = str(finding.get("severity", "")).lower()
                if severity in finding_counts:
                    finding_counts[severity] += 1
        legacy = (
            str(row["methodology_version"]).startswith("legacy")
            or row["identity_json"] is None
            or identity.get("execution_model_version") is None
        )
        validated = (
            row["state"] == "completed"
            and row["leakage_passed"] == 1
            and finding_counts["blocking"] == 0
            and not legacy
        )
        dimensions: dict[str, Scalar] = {
            "identity.display_alias": row["display_alias"],
            "identity.strategy_id": row["strategy_id"],
            "identity.methodology_version": row["methodology_version"],
            "identity.execution_model_version": identity.get(
                "execution_model_version"
            ),
            "identity.metric_calculation_version": identity.get(
                "metric_calculation_version"
            ),
            "identity.feature_set_version": identity.get("feature_set_version"),
            "dataset.snapshot_id": identity.get("data_snapshot_id"),
            "dataset.universe_hash": identity.get("universe_hash"),
            "dataset.split_manifest_hash": identity.get("split_manifest_hash"),
            "validation.state": row["state"],
            "validation.profile": row["profile_name"],
            "validation.leakage_passed": _optional_bool(row["leakage_passed"]),
            "validation.blocking_findings": finding_counts["blocking"],
            "validation.review_findings": finding_counts["review"],
            "validation.advisory_findings": finding_counts["advisory"],
            "lineage.id": row["lineage_id"] or row["run_id"],
        }
        for key, value in _flatten(parameters).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                dimensions[f"parameter.{key}"] = value
        trust: dict[str, Scalar] = {
            "trust.state": (
                "current_validated"
                if validated
                else "legacy"
                if legacy
                else "validation_attention"
            ),
            "trust.legacy": legacy,
            "trust.current": not legacy,
            "trust.code_dirty": bool(row["code_dirty"]),
        }
        return AnalysisRow(row["run_id"], trust, dimensions, metrics)

    @staticmethod
    def _population(
        rows: Iterable[AnalysisRow], population: Mapping[str, Any]
    ) -> list[AnalysisRow]:
        include_legacy = bool(population.get("includeLegacy", False))
        trust_states = set(population.get("trustStates") or ["current_validated"])
        return [
            row
            for row in rows
            if (include_legacy or row.value("trust.legacy") is not True)
            and row.value("trust.state") in trust_states
        ]

    def _normalize_query(self, query: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(query, Mapping):
            raise AnalysisQueryError("Analysis query must be an object")
        catalog = {item["field"]: item for item in self.field_catalog()}
        population = query.get("population") or {}
        if not isinstance(population, Mapping):
            raise AnalysisQueryError("population must be an object")
        trust_states = population.get("trustStates") or ["current_validated"]
        if not isinstance(trust_states, list) or not all(
            isinstance(item, str) for item in trust_states
        ):
            raise AnalysisQueryError("population.trustStates must be strings")
        filters = query.get("filters") or {"op": "and", "children": []}
        _validate_filter(filters, catalog, self.policy)
        group_by = _field_list(query.get("groupBy") or [], "groupBy", catalog)
        screening = _field_list(
            query.get("screeningColumns")
            or ["identity.display_alias", "identity.strategy_id", "validation.state"],
            "screeningColumns",
            catalog,
        )
        if len(screening) > self.policy.max_screening_columns:
            raise AnalysisQueryError(
                f"screeningColumns exceeds limit {self.policy.max_screening_columns}"
            )
        measures = query.get("measures") or []
        if not isinstance(measures, list):
            raise AnalysisQueryError("measures must be an array")
        normalized_measures = []
        for measure in measures:
            if not isinstance(measure, Mapping):
                raise AnalysisQueryError("Each measure must be an object")
            field = _field_name(measure.get("field"))
            aggregate = measure.get("aggregate")
            if field not in catalog:
                raise AnalysisQueryError(f"Unknown measure field {field!r}")
            if aggregate not in catalog[field]["aggregates"]:
                raise AnalysisQueryError(
                    f"Aggregate {aggregate!r} is not supported for {field!r}"
                )
            normalized_measures.append({"field": field, "aggregate": aggregate})
        order_by = query.get("orderBy") or []
        if not isinstance(order_by, list) or len(order_by) > self.policy.max_order_rules:
            raise AnalysisQueryError(
                f"orderBy must have at most {self.policy.max_order_rules} rules"
            )
        normalized_order = []
        result_names = set(group_by) | {
            _measure_name(item) for item in normalized_measures
        } | set(screening)
        for order in order_by:
            if not isinstance(order, Mapping):
                raise AnalysisQueryError("Each order rule must be an object")
            field = _field_name(order.get("field"))
            direction = order.get("direction", "asc")
            if field not in result_names or direction not in {"asc", "desc"}:
                raise AnalysisQueryError(f"Invalid order rule for {field!r}")
            normalized_order.append({"field": field, "direction": direction})
        return {
            "population": {
                "includeLegacy": bool(population.get("includeLegacy", False)),
                "trustStates": trust_states,
            },
            "filters": filters,
            "groupBy": group_by,
            "screeningColumns": screening,
            "measures": normalized_measures,
            "orderBy": normalized_order,
            "chart": query.get("chart") or {"kind": "auto"},
        }

    @staticmethod
    def _assert_metric_versions(
        rows: Sequence[AnalysisRow], measures: Sequence[Mapping[str, str]]
    ) -> None:
        if not any(item["field"].startswith("metric.") for item in measures):
            return
        versions = {
            row.value("identity.metric_calculation_version")
            for row in rows
            if row.value("identity.metric_calculation_version") is not None
        }
        if len(versions) > 1:
            raise AnalysisQueryError(
                "Metric aggregation spans incompatible calculation versions; "
                "filter or group by identity.metric_calculation_version."
            )


def _validate_filter(
    expression: Any,
    catalog: Mapping[str, Mapping[str, Any]],
    policy: AnalysisPolicy,
    *,
    depth: int = 1,
    count: list[int] | None = None,
) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > policy.max_filter_nodes or depth > policy.max_filter_depth:
        raise AnalysisQueryError("Filter expression exceeds depth/node limits")
    if not isinstance(expression, Mapping):
        raise AnalysisQueryError("Filter expression must be an object")
    op = expression.get("op")
    if op in {"and", "or"}:
        children = expression.get("children")
        if not isinstance(children, list):
            raise AnalysisQueryError("Filter groups require a children array")
        for child in children:
            _validate_filter(child, catalog, policy, depth=depth + 1, count=count)
        return
    if op not in FILTER_OPERATORS:
        raise AnalysisQueryError(f"Unsupported filter operator {op!r}")
    field = _field_name(expression.get("field"))
    if field not in catalog:
        raise AnalysisQueryError(f"Unknown filter field {field!r}")
    if op not in catalog[field]["filterOperators"]:
        raise AnalysisQueryError(f"Operator {op!r} is invalid for {field!r}")
    value = expression.get("value")
    if op in {"in", "not_in", "between"} and not isinstance(value, list):
        raise AnalysisQueryError(f"Operator {op!r} requires an array value")
    if op == "between" and len(value) != 2:
        raise AnalysisQueryError("between requires exactly two values")


def _matches(row: AnalysisRow, expression: Mapping[str, Any], policy: AnalysisPolicy) -> bool:
    op = expression["op"]
    if op == "and":
        return all(_matches(row, child, policy) for child in expression["children"])
    if op == "or":
        return any(_matches(row, child, policy) for child in expression["children"])
    actual = row.value(_field_name(expression["field"]))
    expected = expression.get("value")
    if op == "exists":
        return (actual is not None) is bool(expected)
    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op == "contains":
        return isinstance(actual, str) and str(expected).casefold() in actual.casefold()
    if actual is None:
        return False
    try:
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "between":
            return expected[0] <= actual <= expected[1]
    except TypeError:
        return False
    return False


def _aggregate_rows(
    rows: Sequence[AnalysisRow],
    group_by: Sequence[str],
    measures: Sequence[Mapping[str, str]],
) -> list[dict[str, Scalar]]:
    groups: dict[tuple[Scalar, ...], list[AnalysisRow]] = {}
    for row in rows:
        groups.setdefault(tuple(row.value(field) for field in group_by), []).append(row)
    if not group_by and not groups:
        groups[()] = []
    result = []
    for key, members in groups.items():
        output: dict[str, Scalar] = dict(zip(group_by, key))
        for measure in measures:
            name = _measure_name(measure)
            values = [member.value(measure["field"]) for member in members]
            output[name] = _aggregate(values, measure["aggregate"])
        output["analysis.run_count"] = len(members)
        result.append(output)
    return result


def _aggregate(values: Sequence[Scalar], aggregate: str) -> int | float | None:
    clean = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))]
    if aggregate == "count":
        return sum(value is not None for value in values)
    if not clean:
        return None
    if aggregate == "sum":
        return sum(clean)
    if aggregate == "mean":
        return statistics.fmean(clean)
    if aggregate == "median":
        return statistics.median(clean)
    if aggregate == "min":
        return min(clean)
    if aggregate == "max":
        return max(clean)
    if aggregate == "stddev":
        return statistics.pstdev(clean)
    percentile = {"p10": 0.1, "p25": 0.25, "p75": 0.75, "p90": 0.9}.get(aggregate)
    if percentile is not None:
        return _percentile(clean, percentile)
    raise AnalysisQueryError(f"Unsupported aggregate {aggregate!r}")


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _screen_rows(
    rows: Sequence[AnalysisRow], columns: Sequence[str]
) -> list[dict[str, Scalar]]:
    return [
        {field: row.value(field) for field in columns}
        for row in rows
    ]


def _order_rows(
    rows: list[dict[str, Scalar]], rules: Sequence[Mapping[str, str]]
) -> list[dict[str, Scalar]]:
    for rule in reversed(rules):
        field = rule["field"]
        rows.sort(
            key=lambda row: (row.get(field) is None, str(row.get(field))),
            reverse=rule["direction"] == "desc",
        )
    return rows


def _fit_byte_budget(
    rows: list[dict[str, Scalar]], budget: int
) -> tuple[list[dict[str, Scalar]], bool]:
    output = []
    size = 2
    for row in rows:
        encoded = json.dumps(row, separators=(",", ":"), ensure_ascii=True)
        if size + len(encoded.encode("utf-8")) + 1 > budget:
            return output, True
        output.append(row)
        size += len(encoded.encode("utf-8")) + 1
    return output, False


def _group_cardinality(rows: Sequence[AnalysisRow], fields: Sequence[str]) -> int:
    if not fields:
        return 1 if rows else 0
    return len({tuple(row.value(field) for field in fields) for row in rows})


def _field_list(
    value: Any, label: str, catalog: Mapping[str, Any]
) -> list[str]:
    if not isinstance(value, list):
        raise AnalysisQueryError(f"{label} must be an array")
    result = [_field_name(item) for item in value]
    unknown = [field for field in result if field not in catalog]
    if unknown:
        raise AnalysisQueryError(f"Unknown {label} fields: {', '.join(unknown)}")
    if len(set(result)) != len(result):
        raise AnalysisQueryError(f"{label} cannot contain duplicates")
    return result


def _field_name(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("field")
    if not isinstance(value, str) or "." not in value or len(value) > 128:
        raise AnalysisQueryError(f"Invalid field reference {value!r}")
    return value


def _measure_name(measure: Mapping[str, str]) -> str:
    return f"{measure['aggregate']}:{measure['field']}"


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            flattened.update(_flatten(item, path))
        elif not isinstance(item, (list, tuple, dict)):
            flattened[path] = item
    return flattened


def _data_type(values: Sequence[Scalar]) -> str:
    if values and all(isinstance(value, bool) for value in values):
        return "boolean"
    if values and all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return "number"
    return "string"


def _operators_for(data_type: str) -> list[str]:
    common = ["eq", "neq", "in", "not_in", "exists"]
    if data_type in {"integer", "number"}:
        return common + ["gt", "gte", "lt", "lte", "between"]
    if data_type == "string":
        return common + ["contains"]
    return common


def _metric_unit(field: str) -> str | None:
    lowered = field.lower()
    if any(token in lowered for token in ("pnl", "equity", "profit", "cost")):
        return "currency"
    if any(token in lowered for token in ("rate", "ratio", "drawdown", "percent")):
        return "ratio"
    if field.startswith("metric."):
        return "number"
    return None


def _hashable(value: Scalar) -> Scalar:
    return value


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _optional_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _policy_dict(policy: AnalysisPolicy) -> dict[str, int]:
    return {
        "maxCandidateRows": policy.max_candidate_rows,
        "maxGroups": policy.max_groups,
        "maxResponseRows": policy.max_response_rows,
        "maxChartMarks": policy.max_chart_marks,
        "maxResponseBytes": policy.max_response_bytes,
        "maxFilterNodes": policy.max_filter_nodes,
        "maxFilterDepth": policy.max_filter_depth,
        "maxScreeningColumns": policy.max_screening_columns,
        "maxOrderRules": policy.max_order_rules,
    }
