"""SQLite research store (increment P7).

Public API for the local durable research store. Top-level package exports are
wired up separately by the lead agent; import from here directly for now.
"""

from vibe.research_pipeline.storage.schema import (
    SCHEMA_VERSION,
    connect,
    migrate,
    utc_now_iso,
)
from vibe.research_pipeline.storage.sqlite_store import (
    SqliteResearchStore,
    SqliteRunRecord,
)
from vibe.research_pipeline.storage.legacy_importer import (
    ImportVerification,
    LEGACY_METHODOLOGY_VERSION,
    LegacyImportError,
    LegacyParityError,
    import_legacy_tree,
    load_legacy_records,
    verify_legacy_import,
)

__all__ = [
    "SCHEMA_VERSION",
    "SqliteResearchStore",
    "SqliteRunRecord",
    "ImportVerification",
    "LEGACY_METHODOLOGY_VERSION",
    "LegacyImportError",
    "LegacyParityError",
    "connect",
    "import_legacy_tree",
    "load_legacy_records",
    "migrate",
    "utc_now_iso",
    "verify_legacy_import",
]
