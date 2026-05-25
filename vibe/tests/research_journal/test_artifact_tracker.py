"""
Tests for Artifact Tracking (Stage 7)

P0 Tests:
  - Register artifact with checksum computation
  - Verify artifact integrity (detect tampering)
  - List artifacts for experiment

P1 Tests:
  - Large file warnings (> 1MB)
  - Non-existent file handling
  - Artifact lookup
"""

import pytest
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import hashlib
from vibe.research_journal.artifact_tracker import ArtifactTracker
from vibe.research_journal.models import Experiment, ExperimentStatus
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.persistence import ensure_research_directories


@pytest.fixture
def temp_research_dir():
    """Create temporary research directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "research"


@pytest.fixture
def registry(temp_research_dir):
    """Create registry with sample experiment."""
    ensure_research_directories(temp_research_dir)
    reg = ResearchRegistry(temp_research_dir)
    
    hyp = reg.create_hypothesis(
        title="Test hypothesis",
        rationale="Testing artifact tracking",
        tags=["test"]
    )
    
    exp = reg.create_experiment(
        strategy_name="TestStrategy",
        strategy_version="1.0.0",
        parameters={},
        dataset_config={},
        hypothesis_id=hyp.id,
        tags=["test"]
    )
    
    return reg, exp


@pytest.fixture
def artifact_tracker(registry):
    """Create artifact tracker with registry."""
    reg, exp = registry
    return ArtifactTracker(reg), exp, reg


@pytest.fixture
def sample_file(temp_research_dir):
    """Create a sample file for testing."""
    artifact_dir = temp_research_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = artifact_dir / "sample.txt"
    file_path.write_text("Sample artifact content for testing")
    
    return file_path


class TestArtifactRegistration:
    """P0: Artifact registration tests."""
    
    def test_register_artifact(self, artifact_tracker, sample_file):
        """Test registering an artifact."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            experiment_id=exp.id,
            file_path=sample_file,
            artifact_type="backtest_report"
        )
        
        assert artifact_ref.id.startswith("ART-")
        # Path is stored relative to research root parent
        assert "artifacts" in artifact_ref.path and "sample.txt" in artifact_ref.path
        assert artifact_ref.artifact_type == "backtest_report"
        assert len(artifact_ref.checksum) == 64  # SHA256 hex
        assert artifact_ref.size_bytes > 0
    
    def test_register_artifact_computes_checksum(self, artifact_tracker, sample_file):
        """Test that checksum is correctly computed."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            experiment_id=exp.id,
            file_path=sample_file,
            artifact_type="report"
        )
        
        # Verify checksum matches file content
        expected_checksum = hashlib.sha256(sample_file.read_bytes()).hexdigest()
        assert artifact_ref.checksum == expected_checksum
    
    def test_register_artifact_sequential_ids(self, artifact_tracker, sample_file):
        """Test that artifact IDs are sequential."""
        tracker, exp, reg = artifact_tracker
        
        ref1 = tracker.register_artifact(exp.id, sample_file, "report")
        ref2 = tracker.register_artifact(exp.id, sample_file, "report")
        
        # Extract ID numbers
        id1_num = int(ref1.id.split("-")[1])
        id2_num = int(ref2.id.split("-")[1])
        
        assert id2_num > id1_num


class TestArtifactVerification:
    """P0: Artifact verification tests."""
    
    def test_verify_artifact_valid(self, artifact_tracker, sample_file):
        """Test verifying an unmodified artifact."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            exp.id, sample_file, "report"
        )
        
        is_valid = tracker.verify_artifact(artifact_ref)
        assert is_valid is True
    
    def test_verify_artifact_tampered(self, artifact_tracker, sample_file):
        """Test verification detects file tampering."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            exp.id, sample_file, "report"
        )
        
        # Tamper with file
        sample_file.write_text("MODIFIED CONTENT")
        
        is_valid = tracker.verify_artifact(artifact_ref)
        assert is_valid is False
    
    def test_verify_artifact_missing_file(self, artifact_tracker, sample_file):
        """Test verification fails for missing file."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            exp.id, sample_file, "report"
        )
        
        # Delete file
        sample_file.unlink()
        
        is_valid = tracker.verify_artifact(artifact_ref)
        assert is_valid is False


class TestArtifactListing:
    """P0: Artifact listing tests."""
    
    def test_list_artifacts_for_experiment(self, artifact_tracker, sample_file):
        """Test listing artifacts for an experiment."""
        tracker, exp, reg = artifact_tracker
        
        tracker.register_artifact(exp.id, sample_file, "report")
        tracker.register_artifact(exp.id, sample_file, "chart")
        
        artifacts = tracker.list_artifacts(exp.id)
        
        assert len(artifacts) == 2
        assert all(a.experiment_id == exp.id for a in artifacts)
    
    def test_list_artifacts_empty(self, artifact_tracker, sample_file):
        """Test listing artifacts when none exist."""
        tracker, exp, reg = artifact_tracker
        
        artifacts = tracker.list_artifacts(exp.id)
        
        assert len(artifacts) == 0
    
    def test_list_artifacts_multiple_experiments(self, artifact_tracker, sample_file):
        """Test that artifacts are isolated by experiment."""
        tracker, exp, reg = artifact_tracker
        
        # Create another experiment
        hyp = reg.create_hypothesis(
            title="Test 2",
            rationale="Another test hypothesis",
            tags=[]
        )
        exp2 = reg.create_experiment(
            strategy_name="Test",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
            hypothesis_id=hyp.id
        )
        
        # Register artifacts for both
        tracker.register_artifact(exp.id, sample_file, "report")
        tracker.register_artifact(exp2.id, sample_file, "chart")
        
        artifacts_exp1 = tracker.list_artifacts(exp.id)
        artifacts_exp2 = tracker.list_artifacts(exp2.id)
        
        assert len(artifacts_exp1) == 1
        assert len(artifacts_exp2) == 1
        assert artifacts_exp1[0].id != artifacts_exp2[0].id


class TestArtifactFileSizes:
    """P1: File size handling tests."""
    
    def test_large_file_warning(self, artifact_tracker, temp_research_dir, caplog):
        """Test that files > 1MB generate warnings."""
        tracker, exp, reg = artifact_tracker
        
        # Create 2MB file
        large_file = temp_research_dir / "artifacts" / "large.bin"
        with open(large_file, "wb") as f:
            f.write(b"x" * (2 * 1024 * 1024))  # 2MB
        
        tracker.register_artifact(exp.id, large_file, "report")
        
        # Check that warning was logged
        assert any("1 MB" in record.message for record in caplog.records 
                   if record.levelname == "WARNING")
    
    def test_small_file_no_warning(self, artifact_tracker, sample_file, caplog):
        """Test that files < 1MB don't generate warnings."""
        tracker, exp, reg = artifact_tracker
        
        caplog.clear()
        tracker.register_artifact(exp.id, sample_file, "report")
        
        # No warnings should be generated
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 0


class TestArtifactEdgeCases:
    """P1: Edge case tests."""
    
    def test_register_nonexistent_file(self, artifact_tracker, temp_research_dir):
        """Test registering a non-existent file."""
        tracker, exp, reg = artifact_tracker
        
        nonexistent = temp_research_dir / "artifacts" / "missing.txt"
        
        with pytest.raises(FileNotFoundError):
            tracker.register_artifact(exp.id, nonexistent, "report")
    
    def test_artifact_types(self, artifact_tracker, sample_file):
        """Test different artifact types."""
        tracker, exp, reg = artifact_tracker
        
        types = ["backtest_report", "chart", "data", "log", "config"]
        
        for art_type in types:
            artifact_ref = tracker.register_artifact(
                exp.id, sample_file, art_type
            )
            assert artifact_ref.artifact_type == art_type
    
    def test_artifact_metadata_captured(self, artifact_tracker, sample_file):
        """Test that artifact metadata is properly captured."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            exp.id, sample_file, "report"
        )
        
        # Verify all fields are populated
        assert artifact_ref.id is not None
        assert artifact_ref.experiment_id == exp.id
        assert artifact_ref.artifact_type == "report"
        assert artifact_ref.path is not None
        assert artifact_ref.checksum is not None
        assert artifact_ref.size_bytes is not None
        assert artifact_ref.created_at is not None
        assert artifact_ref.created_at.tzinfo is not None  # Timezone-aware
    
    def test_artifact_path_security(self, artifact_tracker, temp_research_dir):
        """Test that artifacts can't reference paths outside research dir."""
        tracker, exp, reg = artifact_tracker
        
        # Try to register artifact with .. in path
        unsafe_path = temp_research_dir / "artifacts" / ".." / ".." / "secret.txt"
        
        with pytest.raises(ValueError):
            tracker.register_artifact(exp.id, unsafe_path, "report")


class TestArtifactPersistence:
    """P1: Artifact persistence tests."""
    
    def test_get_artifact_by_id(self, artifact_tracker, sample_file):
        """Test retrieving artifact by ID."""
        tracker, exp, reg = artifact_tracker
        
        artifact_ref = tracker.register_artifact(
            exp.id, sample_file, "report"
        )
        
        retrieved = tracker.get_artifact(artifact_ref.id)
        
        assert retrieved.id == artifact_ref.id
        assert retrieved.checksum == artifact_ref.checksum
    
    def test_artifact_survives_registry_reload(self, temp_research_dir, sample_file):
        """Test that artifacts persist across registry reloads."""
        # Create first registry and register artifact
        reg1 = ResearchRegistry(temp_research_dir)
        hyp = reg1.create_hypothesis(
            title="Test",
            rationale="Testing persistence",
            tags=[]
        )
        exp = reg1.create_experiment(
            strategy_name="Test",
            strategy_version="1.0",
            parameters={},
            dataset_config={},
            hypothesis_id=hyp.id
        )
        
        tracker1 = ArtifactTracker(reg1)
        artifact_ref = tracker1.register_artifact(exp.id, sample_file, "report")
        artifact_id = artifact_ref.id
        
        # Create new registry and verify artifact exists
        reg2 = ResearchRegistry(temp_research_dir)
        tracker2 = ArtifactTracker(reg2)
        
        retrieved = tracker2.get_artifact(artifact_id)
        assert retrieved.id == artifact_id
        assert retrieved.checksum == artifact_ref.checksum
