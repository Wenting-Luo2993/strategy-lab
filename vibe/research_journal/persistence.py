"""Persistence layer for Research Journal domain models.

Provides YAML-based file I/O for Hypothesis, Experiment, ResearchNote, RejectedIdea,
and ArtifactReference models. Automatically creates research directory structure
and handles serialization/deserialization with validation.
"""

import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from vibe.research_journal.models import (
    ArtifactReference,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    RejectedIdea,
    ResearchNote,
)


class ImmutabilityError(Exception):
    """Raised when attempting to modify a completed/failed experiment."""

    pass


def ensure_research_directories(research_root: Optional[Path] = None) -> Path:
    """Create research directory structure.

    Creates the following directories if they don't exist:
    - research/hypotheses/
    - research/experiments/
    - research/notes/
    - research/rejected/
    - research/artifacts/

    Args:
        research_root: Optional override for research directory root.
                      If None, uses current working directory.

    Returns:
        Path to research root directory

    Raises:
        OSError: If directory creation fails due to permissions
    """
    if research_root is None:
        research_root = Path.cwd() / "research"
    else:
        research_root = Path(research_root)

    # Create main research directory
    research_root.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    subdirs = ["hypotheses", "experiments", "notes", "rejected", "artifacts"]
    for subdir in subdirs:
        (research_root / subdir).mkdir(parents=True, exist_ok=True)

    # Create .gitkeep in artifacts directory
    gitkeep = research_root / "artifacts" / ".gitkeep"
    gitkeep.touch(exist_ok=True)

    return research_root


def save_hypothesis(
    hypothesis: Hypothesis, research_root: Optional[Path] = None
) -> Path:
    """Save hypothesis to YAML file.

    Args:
        hypothesis: Hypothesis instance to save
        research_root: Optional override for research directory root

    Returns:
        Path to saved file

    Raises:
        FileExistsError: If file already exists (prevent overwrites)
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "hypotheses" / f"{hypothesis.id}.yaml"

    if filepath.exists():
        raise FileExistsError(
            f"Hypothesis file already exists: {filepath}\n"
            f"Use a different ID or delete existing file to overwrite."
        )

    # Serialize to dict with ISO datetime format
    data = hypothesis.model_dump(mode="json")

    # Write YAML with nice formatting
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return filepath


def load_hypothesis(
    hypothesis_id: str, research_root: Optional[Path] = None
) -> Hypothesis:
    """Load hypothesis from YAML file.

    Args:
        hypothesis_id: ID of hypothesis to load (e.g., "HYP-001")
        research_root: Optional override for research directory root

    Returns:
        Hypothesis instance

    Raises:
        FileNotFoundError: If hypothesis file doesn't exist
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "hypotheses" / f"{hypothesis_id}.yaml"

    if not filepath.exists():
        raise FileNotFoundError(
            f"Hypothesis not found: {filepath}\n"
            f"Available hypotheses: {list(research_root.glob('hypotheses/*.yaml'))}"
        )

    with open(filepath, "r") as f:
        data = yaml.safe_load(f)

    return Hypothesis(**data)


def save_experiment(
    experiment: Experiment, research_root: Optional[Path] = None
) -> Path:
    """Save experiment to YAML file.

    If experiment is immutable (COMPLETED/FAILED), file is saved with read-only
    permissions (0o444) to prevent accidental modifications.

    Args:
        experiment: Experiment instance to save
        research_root: Optional override for research directory root

    Returns:
        Path to saved file

    Raises:
        FileExistsError: If file already exists (prevent overwrites)
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "experiments" / f"{experiment.id}.yaml"

    if filepath.exists():
        raise FileExistsError(
            f"Experiment file already exists: {filepath}\n"
            f"Use a different ID or delete existing file to overwrite."
        )

    # Serialize to dict with ISO datetime format
    data = experiment.model_dump(mode="json")

    # Write YAML with nice formatting
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Set read-only permissions if completed/failed
    if experiment.is_immutable():
        os.chmod(filepath, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # 0o444

    return filepath


def load_experiment(
    experiment_id: str, research_root: Optional[Path] = None
) -> Experiment:
    """Load experiment from YAML file.

    Args:
        experiment_id: ID of experiment to load (e.g., "EXP-001")
        research_root: Optional override for research directory root

    Returns:
        Experiment instance

    Raises:
        FileNotFoundError: If experiment file doesn't exist
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "experiments" / f"{experiment_id}.yaml"

    if not filepath.exists():
        raise FileNotFoundError(
            f"Experiment not found: {filepath}\n"
            f"Available experiments: {list(research_root.glob('experiments/*.yaml'))}"
        )

    with open(filepath, "r") as f:
        data = yaml.safe_load(f)

    return Experiment(**data)


def update_experiment_status(
    experiment_id: str,
    status: ExperimentStatus,
    research_root: Optional[Path] = None,
) -> None:
    """Update experiment status and save back to disk.

    Cannot update status of completed or failed experiments (immutable states).

    Args:
        experiment_id: ID of experiment to update
        status: New status to set
        research_root: Optional override for research directory root

    Raises:
        ImmutabilityError: If experiment is in completed/failed state
        FileNotFoundError: If experiment file doesn't exist
    """
    experiment = load_experiment(experiment_id, research_root)

    if experiment.is_immutable():
        raise ImmutabilityError(
            f"Cannot update experiment {experiment_id}: status is {experiment.status} (immutable)\n"
            f"Completed and failed experiments cannot be modified."
        )

    experiment.status = status
    research_root_path = ensure_research_directories(research_root)
    filepath = research_root_path / "experiments" / f"{experiment_id}.yaml"

    # Remove existing file and save new version
    filepath.unlink()
    save_experiment(experiment, research_root)


def save_research_note(
    note: ResearchNote, research_root: Optional[Path] = None
) -> Path:
    """Save research note to Markdown file with YAML frontmatter.

    Format:
    ```
    ---
    id: NOTE-001
    related_experiment_id: EXP-001
    tags: [tag1, tag2]
    created_at: 2026-05-24T10:00:00+00:00
    ---

    Note content here...
    ```

    Args:
        note: ResearchNote instance to save
        research_root: Optional override for research directory root

    Returns:
        Path to saved file

    Raises:
        FileExistsError: If file already exists (prevent overwrites)
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "notes" / f"{note.id}.md"

    if filepath.exists():
        raise FileExistsError(
            f"Research note file already exists: {filepath}\n"
            f"Use a different ID or delete existing file to overwrite."
        )

    # Create frontmatter
    frontmatter = {
        "id": note.id,
        "related_experiment_id": note.related_experiment_id,
        "tags": note.tags,
        "created_at": note.created_at.isoformat(),
    }

    # Write Markdown with YAML frontmatter
    with open(filepath, "w") as f:
        f.write("---\n")
        yaml.dump(frontmatter, f, default_flow_style=False, sort_keys=False)
        f.write("---\n\n")
        f.write(note.content)
        f.write("\n")

    return filepath


def save_rejected_idea(
    idea: RejectedIdea, research_root: Optional[Path] = None
) -> Path:
    """Save rejected idea to YAML file.

    Args:
        idea: RejectedIdea instance to save
        research_root: Optional override for research directory root

    Returns:
        Path to saved file

    Raises:
        FileExistsError: If file already exists (prevent overwrites)
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "rejected" / f"{idea.id}.yaml"

    if filepath.exists():
        raise FileExistsError(
            f"Rejected idea file already exists: {filepath}\n"
            f"Use a different ID or delete existing file to overwrite."
        )

    # Serialize to dict with ISO datetime format
    data = idea.model_dump(mode="json")

    # Write YAML with nice formatting
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return filepath


def save_artifact_reference(
    artifact_ref: ArtifactReference, research_root: Optional[Path] = None
) -> Path:
    """Save artifact reference to YAML file.

    Args:
        artifact_ref: ArtifactReference instance to save
        research_root: Optional override for research directory root

    Returns:
        Path to saved file

    Raises:
        FileExistsError: If file already exists (prevent overwrites)
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "artifacts" / f"{artifact_ref.id}.yaml"

    if filepath.exists():
        raise FileExistsError(
            f"Artifact reference file already exists: {filepath}\n"
            f"Use a different ID or delete existing file to overwrite."
        )

    # Serialize to dict with ISO datetime format
    data = artifact_ref.model_dump(mode="json")

    # Write YAML with nice formatting
    with open(filepath, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return filepath


def load_artifact_reference(
    artifact_id: str, research_root: Optional[Path] = None
) -> ArtifactReference:
    """Load artifact reference from YAML file.

    Args:
        artifact_id: ID of artifact to load (e.g., "ART-001")
        research_root: Optional override for research directory root

    Returns:
        ArtifactReference instance

    Raises:
        FileNotFoundError: If artifact file doesn't exist
    """
    research_root = ensure_research_directories(research_root)
    filepath = research_root / "artifacts" / f"{artifact_id}.yaml"

    if not filepath.exists():
        raise FileNotFoundError(
            f"Artifact not found: {filepath}\n"
            f"Available artifacts: {list(research_root.glob('artifacts/*.yaml'))}"
        )

    with open(filepath, "r") as f:
        data = yaml.safe_load(f)

    return ArtifactReference(**data)
