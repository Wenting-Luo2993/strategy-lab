"""Pre-registered hypothesis cards and their planned power."""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from vibe.research_pipeline.inference import minimum_detectable_effect

MAX_REGISTERED_ALPHA = 0.05


class HypothesisCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str = Field(..., min_length=1)
    journal_id: Optional[str] = None
    programme: str = Field(..., min_length=1)
    claim: str
    rule: str
    prediction: str
    min_edge_r: float = 0.05
    alpha_share: float = Field(..., gt=0, le=MAX_REGISTERED_ALPHA)
    analysis: str
    expected_n_trades: int = Field(..., gt=0)
    expected_sd_r: float = Field(..., gt=0)
    expected_design_effect: float = Field(default=1.0, gt=0)
    kill_criteria: list[str]
    confirmation_data: str
    status: Literal[
        "example", "draft", "registered", "supported", "contradicted", "underpowered"
    ]

    def planned_mde_r(self, power: float = 0.80) -> float:
        se = self.expected_sd_r * math.sqrt(
            self.expected_design_effect / self.expected_n_trades
        )
        return minimum_detectable_effect(se, alpha=self.alpha_share, power=power)


def load_cards(directory: str | Path) -> list[HypothesisCard]:
    """Load every ``*.yaml`` card and check the registered alpha budget."""
    cards = []
    for path in sorted(Path(directory).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cards.append(HypothesisCard(**data))
    spent: dict[str, float] = defaultdict(float)
    for card in cards:
        if card.status not in ("example", "draft"):
            spent[card.programme] += card.alpha_share
    for programme, total in spent.items():
        if total > MAX_REGISTERED_ALPHA + 1e-12:
            raise ValueError(
                f"registered alpha shares for {programme!r} sum to {total:.4f}, "
                f"above {MAX_REGISTERED_ALPHA}"
            )
    return cards
