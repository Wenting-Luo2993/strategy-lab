from __future__ import annotations

import sqlite3

import pytest

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

    assert schema.migrate(connection) == 16
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
        "research_publication_contracts",
    } <= tables
    authorization_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(final_holdout_authorizations)"
        )
    }
    assert {
        "reliability_policy_hash",
        "frozen_definition_hash",
        "reliability_report_hash",
    } <= authorization_columns
    triggers = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    }
    assert {
        "final_holdout_authorization_eligibility",
        "final_holdout_claim_eligibility",
    } <= triggers
    contracts = {
        row[0]
        for row in connection.execute(
            "SELECT aggregate_type FROM research_publication_contracts"
        )
    }
    assert {
        "experiment_definition",
        "experiment_membership",
        "experiment_outcome",
        "study_reliability",
        "methodology_freeze",
        "holdout_authorization",
        "holdout_claim",
        "holdout_terminal",
        "experiment_archive",
    } == contracts
    with pytest.raises(Exception, match="immutable"):
        connection.execute(
            """UPDATE research_publication_contracts
               SET schema_version = 2
               WHERE aggregate_type = 'experiment_definition'"""
        )
