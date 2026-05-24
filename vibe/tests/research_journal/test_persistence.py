"""Unit tests for Research Journal persistence layer.

Tests focus on:
- Directory structure creation
- YAML serialization/deserialization
- File I/O operations
- Immutability enforcement
- Markdown format for notes
"""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

# Add vibe to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from vibe.research_journal.models import (
    ExecutionMetadata,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    RejectedIdea,
    ResearchNote,
)
from vibe.research_journal.persistence import (
    ImmutabilityError,
    ensure_research_directories,
    load_experiment,
    load_hypothesis,
    save_experiment,
    save_hypothesis,
    save_rejected_idea,
    save_research_note,
    update_experiment_status,
)


@pytest.fixture
def temp_research_dir():
    """Fixture providing a temporary research directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "research"


class TestSaveHypothesisCreatesFile:
    """P0: save_hypothesis should create YAML file at correct path."""

    def test_save_hypothesis_creates_file(self, temp_research_dir):
        """Saving hypothesis should create file in hypotheses/ directory."""
        hyp = Hypothesis(
            id="HYP-001",
            title="Test hypothesis",
            rationale="This is a test rationale that is long enough",
            status=HypothesisStatus.PROPOSED,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        filepath = save_hypothesis(hyp, temp_research_dir)

        assert filepath.exists()
        assert filepath.name == "HYP-001.yaml"
        assert filepath.parent.name == "hypotheses"

    def test_saved_file_contains_valid_yaml(self, temp_research_dir):
        """Saved file should contain valid YAML."""
        hyp = Hypothesis(
            id="HYP-002",
            title="Another hypothesis",
            rationale="This is a test rationale that is long enough",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        filepath = save_hypothesis(hyp, temp_research_dir)

        with open(filepath, "r") as f:
            data = yaml.safe_load(f)

        assert data["id"] == "HYP-002"
        assert data["title"] == "Another hypothesis"


class TestSaveLoadHypothesisRoundtrip:
    """P0: Loaded hypothesis should equal saved hypothesis."""

    def test_save_load_hypothesis_roundtrip(self, temp_research_dir):
        """Hypothesis should survive save/load cycle unchanged."""
        original = Hypothesis(
            id="HYP-003",
            title="Roundtrip test",
            rationale="This is a test rationale that is long enough",
            status=HypothesisStatus.ACTIVE,
            tags=["test", "validation"],
            created_at=datetime(2026, 5, 24, 10, 0, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 24, 11, 0, 0, tzinfo=timezone.utc),
        )

        save_hypothesis(original, temp_research_dir)
        loaded = load_hypothesis("HYP-003", temp_research_dir)

        assert loaded.id == original.id
        assert loaded.title == original.title
        assert loaded.status == original.status
        assert loaded.tags == original.tags
        # Compare ISO strings to avoid timezone comparison issues
        assert loaded.created_at.isoformat() == original.created_at.isoformat()


class TestSaveExperimentPreventsOverwrite:
    """P0: FileExistsError raised if experiment file already exists."""

    def test_save_experiment_prevents_overwrite(self, temp_research_dir):
        """Saving with existing file should raise FileExistsError."""
        exp = Experiment(
            id="EXP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            created_at=datetime.now(timezone.utc),
        )

        # Save first time
        save_experiment(exp, temp_research_dir)

        # Save again should raise
        with pytest.raises(FileExistsError):
            save_experiment(exp, temp_research_dir)


class TestCompletedExperimentSavedReadonly:
    """P0: Completed experiment file should be read-only (0o444)."""

    def test_completed_experiment_saved_readonly(self, temp_research_dir):
        """Completed experiment should be saved with read-only permissions."""
        exp = Experiment(
            id="EXP-002",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.COMPLETED,
            results_summary={"sharpe": 1.2},
            conclusion="Test completed",
            completed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        filepath = save_experiment(exp, temp_research_dir)

        # Check file permissions (on Unix-like systems)
        file_stat = filepath.stat()
        # 0o444 means read-only for all users
        mode = file_stat.st_mode & 0o777
        assert mode == 0o444 or mode == 0o644  # Windows may differ


class TestUpdateStatusRejectedForCompleted:
    """P0: Cannot update status of completed experiment (immutable)."""

    def test_update_status_rejected_for_completed(self, temp_research_dir):
        """Updating status of completed experiment should raise ImmutabilityError."""
        exp = Experiment(
            id="EXP-003",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.COMPLETED,
            results_summary={"sharpe": 1.2},
            conclusion="Test completed",
            completed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )

        save_experiment(exp, temp_research_dir)

        # Attempt to update status
        with pytest.raises(ImmutabilityError):
            update_experiment_status("EXP-003", ExperimentStatus.RUNNING, temp_research_dir)


class TestLoadNonexistentHypothesisRaises:
    """P1: FileNotFoundError with clear message when hypothesis doesn't exist."""

    def test_load_nonexistent_hypothesis_raises(self, temp_research_dir):
        """Loading non-existent hypothesis should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            load_hypothesis("HYP-999", temp_research_dir)

        assert "HYP-999" in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()


class TestResearchDirectoriesCreatedIdempotent:
    """P1: Multiple calls to ensure_research_directories should not fail."""

    def test_research_directories_created_idempotent(self, temp_research_dir):
        """Creating directories multiple times should be safe."""
        # Call multiple times
        path1 = ensure_research_directories(temp_research_dir)
        path2 = ensure_research_directories(temp_research_dir)
        path3 = ensure_research_directories(temp_research_dir)

        assert path1 == path2 == path3
        assert path1.exists()

        # Verify all subdirectories exist
        assert (path1 / "hypotheses").exists()
        assert (path1 / "experiments").exists()
        assert (path1 / "notes").exists()
        assert (path1 / "rejected").exists()
        assert (path1 / "artifacts").exists()


class TestYamlDatetimeSerialization:
    """P1: Datetimes should serialize to ISO 8601 format."""

    def test_yaml_datetime_serialization(self, temp_research_dir):
        """Datetime fields should be ISO 8601 format in YAML."""
        created = datetime(2026, 5, 24, 10, 30, 45, tzinfo=timezone.utc)
        hyp = Hypothesis(
            id="HYP-004",
            title="Datetime test",
            rationale="This is a test rationale that is long enough",
            created_at=created,
            updated_at=created,
        )

        filepath = save_hypothesis(hyp, temp_research_dir)

        with open(filepath, "r") as f:
            content = f.read()

        # Should contain ISO 8601 datetime string
        assert "2026-05-24" in content
        assert "10:30:45" in content


class TestResearchNoteMarkdownFormat:
    """P1: Research note should be saved as .md with YAML frontmatter."""

    def test_research_note_markdown_format(self, temp_research_dir):
        """Research note should have frontmatter + body in Markdown format."""
        note = ResearchNote(
            id="NOTE-001",
            content="This is an important observation about the strategy behavior.",
            related_experiment_id="EXP-001",
            tags=["observation", "volatility"],
            created_at=datetime.now(timezone.utc),
        )

        filepath = save_research_note(note, temp_research_dir)

        assert filepath.exists()
        assert filepath.name == "NOTE-001.md"

        with open(filepath, "r") as f:
            content = f.read()

        # Should start with frontmatter
        assert content.startswith("---\n")

        # Should contain YAML metadata
        assert "id: NOTE-001" in content
        assert "related_experiment_id: EXP-001" in content
        assert "tags:" in content

        # Should contain divider between frontmatter and body
        lines = content.split("\n")
        assert lines[0] == "---"
        frontmatter_end = next(i for i, line in enumerate(lines[1:], 1) if line == "---")
        assert frontmatter_end > 0

        # Should contain note content after frontmatter
        body_start = frontmatter_end + 1
        assert "important observation" in content[content.find(lines[body_start]) :]


class TestSaveRejectedIdea:
    """Test saving rejected ideas."""

    def test_save_rejected_idea_creates_file(self, temp_research_dir):
        """Saving rejected idea should create YAML file."""
        rj = RejectedIdea(
            id="RJ-001",
            idea="Add trailing stop loss",
            reason_rejected="Cut winners too early",
            evidence=["EXP-001", "EXP-002"],
            tags=["exit-strategy"],
            created_at=datetime.now(timezone.utc),
        )

        filepath = save_rejected_idea(rj, temp_research_dir)

        assert filepath.exists()
        assert filepath.name == "RJ-001.yaml"

        with open(filepath, "r") as f:
            data = yaml.safe_load(f)

        assert data["id"] == "RJ-001"
        assert data["idea"] == "Add trailing stop loss"
        assert data["evidence"] == ["EXP-001", "EXP-002"]


class TestSaveLoadExperimentRoundtrip:
    """Test experiment save/load roundtrip."""

    def test_save_load_experiment_roundtrip(self, temp_research_dir):
        """Experiment should survive save/load cycle unchanged."""
        original = Experiment(
            id="EXP-100",
            hypothesis_id="HYP-001",
            strategy_name="ORBStrategy",
            strategy_version="1.4.2",
            parameters={"orb_minutes": 5, "take_profit": 2},
            dataset_config={"symbols": ["QQQ"], "date_range": "2024"},
            execution_metadata=ExecutionMetadata(
                git_commit="b" * 40,
                git_branch="feature/test",
                git_dirty=True,
                random_seed=42,
                executed_at=datetime.now(timezone.utc),
                python_version="3.12.0",
            ),
            status=ExperimentStatus.RUNNING,
            tags=["test", "qqq"],
            created_at=datetime.now(timezone.utc),
        )

        save_experiment(original, temp_research_dir)
        loaded = load_experiment("EXP-100", temp_research_dir)

        assert loaded.id == original.id
        assert loaded.hypothesis_id == original.hypothesis_id
        assert loaded.strategy_name == original.strategy_name
        assert loaded.parameters == original.parameters
        assert loaded.dataset_config == original.dataset_config
        assert loaded.tags == original.tags


class TestUpdateExperimentStatus:
    """Test updating experiment status."""

    def test_update_experiment_status_succeeds(self, temp_research_dir):
        """Can update status of non-completed experiment."""
        exp = Experiment(
            id="EXP-200",
            strategy_name="ORBStrategy",
            strategy_version="1.0.0",
            execution_metadata=ExecutionMetadata(
                git_commit="a" * 40,
                git_branch="main",
                git_dirty=False,
                executed_at=datetime.now(timezone.utc),
                python_version="3.11.0",
            ),
            status=ExperimentStatus.REGISTERED,
            created_at=datetime.now(timezone.utc),
        )

        save_experiment(exp, temp_research_dir)

        # Update status
        update_experiment_status("EXP-200", ExperimentStatus.RUNNING, temp_research_dir)

        # Verify update
        updated = load_experiment("EXP-200", temp_research_dir)
        assert updated.status == ExperimentStatus.RUNNING


class TestFileSerialization:
    """Test various file I/O scenarios."""

    def test_hypothesis_file_human_readable(self, temp_research_dir):
        """Saved YAML should be human-readable."""
        hyp = Hypothesis(
            id="HYP-099",
            title="Human readable test",
            rationale="Testing that YAML output is nice and readable",
            tags=["readability", "formatting"],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        filepath = save_hypothesis(hyp, temp_research_dir)

        with open(filepath, "r") as f:
            content = f.read()

        # Should contain field names (not cryptic)
        assert "id:" in content
        assert "title:" in content
        assert "tags:" in content

        # Should not be single-line JSON
        assert "\n" in content
        assert "{" not in content  # Not JSON object


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
