"""Trial tags: declare which research programme and phase a study belongs to.

A tag is stored inside an experiment's definition so the trial ledger can count
how many variants a programme has examined, per phase and family.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrialTag(BaseModel):
    model_config = ConfigDict(frozen=True)

    programme: str = Field(..., min_length=1)
    phase: Literal["exploration", "confirmation", "platform_validation"]
    family: Literal["baseline", "condition", "enhancement", "validation"]
    label: str = Field(..., min_length=1)
    hypothesis_ids: tuple[str, ...] = ()
    n_trials: int = Field(default=1, ge=1)
