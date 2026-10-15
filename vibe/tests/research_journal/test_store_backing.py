"""The journal is a facade over the research store, not a rival to it.

A target-architecture audit once concluded from the import graph alone that
``vibe/research_journal/`` was "a second, parallel research-recording system"
and recommended deleting fifteen files on that basis. It was wrong: P11 did
not leave the old writer in place, it rewired it onto
``SqliteResearchStore``. Reading the module and querying the store refuted the
claim in minutes.

These tests pin the property that makes the claim false, so the conclusion
cannot be reached again from the same bad evidence -- and so that nobody
reintroduces an independent writer without a failing test.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from vibe.research_journal.registry import ResearchRegistry
from vibe.research_pipeline.paths import research_db_path


@pytest.fixture
def isolated_root(tmp_path):
    """An explicit root outside a git tree gets its own compatibility DB."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "research"


def _registry_rows(db_path: Path, record_type: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM registry_records WHERE record_type = ?",
            (record_type,),
        ).fetchone()[0]
    finally:
        connection.close()


def test_journal_writes_land_in_the_research_store(isolated_root):
    """A hypothesis created through the journal is a row in the shared store.

    If this ever fails because the row went somewhere else -- a YAML tree, a
    private table -- then the journal really has become a second system of
    record and the audit's original recommendation applies.
    """
    registry = ResearchRegistry(isolated_root)
    try:
        db_path = Path(registry._sqlite_db_path)
        before = _registry_rows(db_path, "hypothesis")
        registry.create_hypothesis(
            title="Facade check",
            rationale="Recorded through the journal, stored by the pipeline.",
        )
        after = _registry_rows(db_path, "hypothesis")
    finally:
        registry.close()

    assert after == before + 1, (
        "the journal must persist through SqliteResearchStore; a write that "
        "does not reach registry_records means a parallel writer came back"
    )


def test_journal_reads_come_back_through_the_same_store(isolated_root):
    """Round-tripping proves reads are served by the store, not a cache."""
    registry = ResearchRegistry(isolated_root)
    try:
        created = registry.create_hypothesis(
            title="Round trip",
            rationale="Written once, then read back from the shared store.",
        )
        reopened = ResearchRegistry(isolated_root)
        try:
            fetched = reopened.get_hypothesis(created.id)
        finally:
            reopened.close()
    finally:
        registry.close()

    assert fetched.id == created.id
    assert fetched.title == "Round trip"


def test_production_registry_targets_the_main_research_database():
    """The default root resolves to the one real research DB.

    ``_compatibility_db_path`` only hands out a side file for an *explicit*
    root outside a git tree, which is what tests and import tools want. The
    production path -- ``research_root=None``, which is what
    ``optimize_strategy.py --journal`` passes -- must not be isolated, or a
    sweep would record into a database the viewer never opens.
    """
    resolved = ResearchRegistry._compatibility_db_path(
        ResearchRegistry.__new__(ResearchRegistry), None
    )

    assert resolved == research_db_path(create_parents=True)
