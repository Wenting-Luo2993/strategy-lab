"""
Artifact Tracking for Research Journal (Stage 7)

Manages registration, verification, and tracking of experiment output files.
Provides integrity verification via SHA256 checksums.
"""

import hashlib
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone
from vibe.research_journal.models import ArtifactReference
from vibe.research_journal.registry import ResearchRegistry
from vibe.research_journal.persistence import load_artifact_reference, save_artifact_reference

logger = logging.getLogger(__name__)

# File size threshold for warnings (1MB)
LARGE_FILE_THRESHOLD = 1024 * 1024


class ArtifactTracker:
    """Manages artifacts (large output files) for experiments.
    
    Features:
    - Register artifacts with automatic checksum computation
    - Verify artifact integrity (detect tampering)
    - List artifacts by experiment
    - Warn for large files (> 1MB)
    - Track artifact metadata
    
    Usage:
        tracker = ArtifactTracker(registry)
        artifact_ref = tracker.register_artifact(
            experiment_id="EXP-001",
            file_path=Path("reports/backtest.html"),
            artifact_type="backtest_report"
        )
        is_valid = tracker.verify_artifact(artifact_ref)
    """
    
    def __init__(self, registry: ResearchRegistry):
        """Initialize artifact tracker.
        
        Args:
            registry: ResearchRegistry instance
        """
        self.registry = registry
        self._artifact_cache = {}  # Cache for loaded artifacts
    
    def register_artifact(
        self,
        experiment_id: str,
        file_path: Path,
        artifact_type: str
    ) -> ArtifactReference:
        """Register an artifact for an experiment.
        
        Args:
            experiment_id: Experiment ID (EXP-NNN format)
            file_path: Path to artifact file (relative or absolute)
            artifact_type: Type of artifact (e.g., 'backtest_report', 'chart')
            
        Returns:
            ArtifactReference with computed checksum and metadata
            
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If path contains unsafe sequences (..)
        """
        file_path = Path(file_path)
        
        # Security: prevent path traversal
        try:
            file_path.resolve().relative_to(
                self.registry.research_root.resolve()
            )
        except ValueError:
            # Path is outside research directory or has .. in it
            if ".." in str(file_path):
                raise ValueError(f"Path traversal not allowed: {file_path}")
        
        if not file_path.exists():
            raise FileNotFoundError(f"Artifact file not found: {file_path}")
        
        # Compute SHA256 checksum
        checksum = self._compute_sha256(file_path)
        
        # Get file size
        size_bytes = file_path.stat().st_size
        
        # Warn if file is large
        if size_bytes > LARGE_FILE_THRESHOLD:
            size_mb = size_bytes / (1024 * 1024)
            logger.warning(
                f"Large artifact registered ({size_mb:.1f} MB > 1 MB): {file_path}"
            )
        
        # Generate artifact ID
        artifact_id = self._next_artifact_id()
        
        # Convert to relative path for storage
        try:
            relative_path = str(file_path.relative_to(self.registry.research_root.parent))
        except ValueError:
            # If file is not relative to research root parent, use string representation
            relative_path = str(file_path.relative_to(file_path.resolve().root)) if file_path.is_absolute() else str(file_path)
        
        # Create artifact reference
        artifact_ref = ArtifactReference(
            id=artifact_id,
            experiment_id=experiment_id,
            artifact_type=artifact_type,
            path=relative_path,
            checksum=checksum,
            size_bytes=size_bytes,
            created_at=datetime.now(timezone.utc)
        )
        
        # Save artifact reference
        save_artifact_reference(artifact_ref, self.registry.research_root)
        
        logger.info(
            f"Registered artifact {artifact_id}: {artifact_type} "
            f"({size_bytes} bytes, checksum={checksum[:8]}...)"
        )
        
        return artifact_ref
    
    def verify_artifact(self, artifact_ref: ArtifactReference) -> bool:
        """Verify artifact integrity.
        
        Detects file tampering by comparing current checksum with stored value.
        
        Args:
            artifact_ref: ArtifactReference to verify
            
        Returns:
            True if artifact is valid and unmodified, False otherwise
        """
        # Convert relative path to absolute
        file_path = Path(artifact_ref.path)
        if not file_path.is_absolute():
            # Resolve relative path from research root parent
            file_path = self.registry.research_root.parent / artifact_ref.path
        
        # Check if file exists
        if not file_path.exists():
            logger.warning(f"Artifact file missing: {file_path}")
            return False
        
        # Recompute checksum
        current_checksum = self._compute_sha256(file_path)
        
        # Compare with stored checksum
        is_valid = current_checksum == artifact_ref.checksum
        
        if not is_valid:
            logger.warning(
                f"Artifact integrity check failed for {artifact_ref.id}: "
                f"stored={artifact_ref.checksum[:8]}..., "
                f"current={current_checksum[:8]}..."
            )
        
        return is_valid
    
    def list_artifacts(self, experiment_id: str) -> List[ArtifactReference]:
        """List all artifacts for an experiment.
        
        Args:
            experiment_id: Experiment ID to filter by
            
        Returns:
            List of ArtifactReference objects for the experiment
        """
        artifacts = []
        artifact_dir = self.registry.research_root / "artifacts"
        
        if not artifact_dir.exists():
            return []
        
        for artifact_file in artifact_dir.glob("ART-*.yaml"):
            try:
                artifact_ref = load_artifact_reference(
                    artifact_file.stem,
                    self.registry.research_root
                )
                if artifact_ref.experiment_id == experiment_id:
                    artifacts.append(artifact_ref)
            except Exception as e:
                logger.warning(f"Failed to load artifact {artifact_file}: {e}")
                continue
        
        return artifacts
    
    def get_artifact(self, artifact_id: str) -> ArtifactReference:
        """Get artifact by ID.
        
        Args:
            artifact_id: Artifact ID (ART-NNN format)
            
        Returns:
            ArtifactReference with full metadata
            
        Raises:
            FileNotFoundError: If artifact doesn't exist
        """
        return load_artifact_reference(artifact_id, self.registry.research_root)
    
    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """Compute SHA256 checksum of a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hex-encoded SHA256 checksum (64 characters)
        """
        sha256_hash = hashlib.sha256()
        
        # Read file in chunks to handle large files efficiently
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()
    
    def _next_artifact_id(self) -> str:
        """Generate next artifact ID (ART-NNN format).
        
        Returns:
            Next sequential artifact ID
        """
        artifact_dir = self.registry.research_root / "artifacts"
        artifact_dir.mkdir(exist_ok=True)
        
        # Find max existing ID
        max_num = 0
        for artifact_file in artifact_dir.glob("ART-*.yaml"):
            try:
                num = int(artifact_file.stem.split("-")[1])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                continue
        
        return f"ART-{max_num + 1:03d}"
