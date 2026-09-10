"""Explicit execution semantics for the backtester.

A design review found that the simulator silently flatters results in ways that
are invisible in the output:

* **E1 Intrabar exit ordering.** ``PortfolioManager.check_exits`` evaluates
  take-profit before stop-loss using ``bar.high``/``bar.low``. Any bar that
  touched *both* levels is therefore always booked as a win. On 5-minute bars
  with a tight stop this is not a rare edge case. The docstring claimed exits
  triggered on ``bar.close``, which was never true.
* **E2 Gap-through fills.** Exits filled at exactly the stop or target price,
  so an overnight or intrabar gap straight through the level cost nothing.
* **E3 Undeclared leverage.** Position sizing has no buying-power check and
  cash has no floor, so a strategy could take positions it could never fund.

Following ADR-015 (default legacy, explicit realistic opt-in), none of these
change unless a caller opts in. Existing research remains bit-comparable.

What is *not* optional is measurement. Ambiguous bars, minimum cash, and peak
leverage are always counted, so a legacy-mode run still reports how much of its
edge depends on the optimistic assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "EXECUTION_MODEL_VERSION",
    "IntrabarExitResolution",
    "GapFillPolicy",
    "ExecutionRealismConfig",
    "BuyingPowerError",
    "clamp_to_bar",
]

# Bumped whenever fill, cost, or intrabar-ordering *semantics* change in code.
# Distinct from the per-run settings below: two runs can share this version and
# still differ, so callers must include ``ExecutionRealismConfig.identity()`` in
# the run fingerprint as well.
EXECUTION_MODEL_VERSION = 2


class IntrabarExitResolution(str, Enum):
    """How to resolve a bar that touched both the stop and the target.

    Intrabar order is unknowable from OHLC alone. The only honest options are to
    pick a convention and declare it, or to refuse to guess.
    """

    OPTIMISTIC = "optimistic"
    """Take-profit wins. Legacy behaviour, retained as the default so existing
    results stay comparable. Overstates performance."""

    CONSERVATIVE = "conservative"
    """Stop-loss wins. Understates performance. The gap between this and
    ``OPTIMISTIC`` is the size of the assumption."""

    WORST_CASE_UNRESOLVED = "worst_case_unresolved"
    """Resolve as the stop *and* flag the trade as ambiguous, so downstream
    analysis can exclude or stress these trades explicitly."""


class GapFillPolicy(str, Enum):
    """Where an exit fills when price gaps past the trigger level."""

    AT_LEVEL = "at_level"
    """Fill exactly at the stop/target price even if the bar opened beyond it.
    Legacy behaviour. Makes gap risk free, which it is not."""

    AT_OPEN = "at_open"
    """Fill at the bar open when the bar opened past the level, which is the
    first realistically obtainable price."""


class BuyingPowerError(RuntimeError):
    """Raised when a position would exceed available buying power."""


@dataclass(frozen=True)
class ExecutionRealismConfig:
    """Declared execution assumptions for one run."""

    intrabar_exit_resolution: IntrabarExitResolution = (
        IntrabarExitResolution.OPTIMISTIC
    )
    gap_fill_policy: GapFillPolicy = GapFillPolicy.AT_LEVEL
    enforce_buying_power: bool = False
    max_gross_leverage: float = 1.0

    def __post_init__(self) -> None:
        if self.max_gross_leverage <= 0:
            raise ValueError(
                f"max_gross_leverage must be positive, got "
                f"{self.max_gross_leverage}"
            )

    @classmethod
    def legacy(cls) -> "ExecutionRealismConfig":
        """Exactly the historical behaviour. Produces identical numbers."""
        return cls()

    @classmethod
    def realistic(cls, *, max_gross_leverage: float = 1.0) -> "ExecutionRealismConfig":
        """Conservative, cash-bounded execution for results meant to be believed."""
        return cls(
            intrabar_exit_resolution=IntrabarExitResolution.CONSERVATIVE,
            gap_fill_policy=GapFillPolicy.AT_OPEN,
            enforce_buying_power=True,
            max_gross_leverage=max_gross_leverage,
        )

    @property
    def is_legacy(self) -> bool:
        return self == ExecutionRealismConfig.legacy()

    def identity(self) -> dict[str, object]:
        """Settings that must participate in the run fingerprint.

        Two runs with the same code but different settings here are not
        comparable, so the settings travel with the identity rather than
        living in a config file the reader never sees.
        """
        return {
            "execution_model_version": EXECUTION_MODEL_VERSION,
            "intrabar_exit_resolution": self.intrabar_exit_resolution.value,
            "gap_fill_policy": self.gap_fill_policy.value,
            "enforce_buying_power": self.enforce_buying_power,
            "max_gross_leverage": self.max_gross_leverage,
        }


def clamp_to_bar(price: float, low: float, high: float) -> float:
    """Constrain a fill price to the bar's traded range.

    A fill outside ``[low, high]`` is a price that did not occur. This is the
    last line of defence: whatever the policy computes, the simulator may not
    invent liquidity at a price nobody traded.
    """
    if high < low:
        raise ValueError(f"Bar high {high} is below low {low}")
    return min(max(price, low), high)
