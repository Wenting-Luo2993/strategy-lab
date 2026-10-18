from __future__ import annotations

import sqlite3

from vibe.research_pipeline.storage import schema


def test_forward_migration_from_v11_adds_decision_boundary_tables():
    connection = sqlite3.connect(":memory:")
    schema.configure_connection(connection)
    schema._ensure_migrations_table(connection)
    for version, ddl in schema._MIGRATIONS:
        if version > 11:
            break
        schema._execute_ddl(connection, ddl)
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, ?)",
            (version, "2026-10-17T00:00:00Z"),
        )
    connection.commit()

    assert schema.migrate(connection) == 13
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "engine_integrity_reports",
        "reliability_policies",
        "study_reliability_reports",
        "study_reliability_criteria",
        "robustness_evidence",
        "frozen_study_definitions",
        "final_holdout_executions",
        "retention_metadata",
        "final_holdout_authorizations",
    } <= tables
