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
* **E6 Entry fill price.** ORB entries were priced at the stop-market trigger
  (``OR_high + $0.01``) no matter which bar the signal fired on, while the
  signal itself is evaluated only after a bar completes. Live does not place a
  resting stop -- ``TradeExecutor`` submits a *market* order -- so the backtest
  was claiming a fill at a level the live system never rests an order at. The
  mismatch is not academic: with the stale-wick filter active it lets an entry
  be deferred by up to three hours and still fill at the original breakout
  price, skipping the adverse excursion in between. On QQQ 2022 that is worth
  +46% of net P&L.

Following ADR-015 (default legacy, explicit realistic opt-in), none of these
change unless a caller opts in. Existing research remains bit-comparable.

What is *not* optional is measurement. Ambiguous bars, minimum cash, and peak
leverage are always counted, so a legacy-mode run still reports how much of its
edge depends on the optimistic assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from vibe.backtester.core.commission import CommissionModel
from vibe.backtester.core.exit_slippage import (
    ExitSlippageModel,
    FixedTickExitSlippage,
)

__all__ = [
    "EXECUTION_MODEL_VERSION",
    "IntrabarExitResolution",
    "GapFillPolicy",
    "EntryFillPolicy",
    "ExecutionRealismConfig",
    "BuyingPowerError",
    "clamp_to_bar",
]

# Bumped whenever fill, cost, or intrabar-ordering *semantics* change in code.
# Distinct from the per-run settings below: two runs can share this version and
# still differ, so callers must include ``ExecutionRealismConfig.identity()`` in
# the run fingerprint as well.
#
# Version 3 adds the E4 cost model. Every prior result was computed with no
# commission on either side of a round trip.
#
# Version 4 adds exit-side slippage. Versions 1-3 filled every exit at exactly
# the trigger price, which is the larger half of E4: on the QQQ ORB baseline
# commission costs 2.3% of net P&L, while two ticks of stop slippage costs
# 10.5%.
#
# Version 5 adds E6, the entry fill price. Versions 1-4 filled every ORB entry
# at the stop-market trigger price regardless of when the signal fired.
EXECUTION_MODEL_VERSION = 5


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


class EntryFillPolicy(str, Enum):
    """What price an ORB entry fills at.

    The two options model two different orders, and the choice must match what
    the live system actually submits. ``ORBStrategy`` is shared between the
    backtester and the live bot, so a mismatch here is silent.
    """

    AT_STOP_TRIGGER = "at_stop_trigger"
    """Fill at ``OR_high + $0.01`` (long) or ``OR_low - $0.01`` (short), the
    price a resting stop-market order would have triggered at. Legacy
    behaviour, and self-consistent *only* if the signal also fires the instant
    price crosses the level. It is optimistic whenever entry is deferred,
    because the fill price does not move with the delay."""

    AT_SIGNAL_BAR_CLOSE = "at_signal_bar_close"
    """Fill around the close of the bar that produced the signal, plus
    slippage. This is what live does: ``TradeExecutor`` submits a market order
    after a completed bar, so the obtainable price is wherever the market is
    then -- not where the breakout level sits."""


class BuyingPowerError(RuntimeError):
    """Raised when a position would exceed available buying power."""


@dataclass(frozen=True)
class ExecutionRealismConfig:
    """Declared execution assumptions for one run."""

    intrabar_exit_resolution: IntrabarExitResolution = (
        IntrabarExitResolution.OPTIMISTIC
    )
    gap_fill_policy: GapFillPolicy = GapFillPolicy.AT_LEVEL
    entry_fill_policy: EntryFillPolicy = EntryFillPolicy.AT_STOP_TRIGGER
    enforce_buying_power: bool = False
    max_gross_leverage: float = 1.0
    commission_model: CommissionModel = field(
        default_factory=CommissionModel.zero
    )
    exit_slippage: ExitSlippageModel = field(
        default_factory=FixedTickExitSlippage.zero
    )

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
    def realistic(
        cls,
        *,
        max_gross_leverage: float = 1.0,
        commission_model: CommissionModel | None = None,
        exit_slippage: ExitSlippageModel | None = None,
    ) -> "ExecutionRealismConfig":
        """Conservative, cash-bounded execution for results meant to be believed.

        Args:
            max_gross_leverage: Declared leverage ceiling.
            commission_model: Cost schedule. Defaults to IBKR Pro tiered, the
                schedule this project trades under. Pass
                ``CommissionModel.zero()`` to isolate the effect of E1-E3
                without costs.
            exit_slippage: How much worse than the trigger price exits fill.
                Defaults to two ticks on stops and one at the close. Pass
                ``FixedTickExitSlippage.zero()`` to isolate commission, or a
                wider setting to stress the assumption -- it is the single
                largest lever in the cost model.
        """
        return cls(
            intrabar_exit_resolution=IntrabarExitResolution.CONSERVATIVE,
            gap_fill_policy=GapFillPolicy.AT_OPEN,
            entry_fill_policy=EntryFillPolicy.AT_SIGNAL_BAR_CLOSE,
            enforce_buying_power=True,
            max_gross_leverage=max_gross_leverage,
            commission_model=(
                commission_model
                if commission_model is not None
                else CommissionModel.ibkr_pro_tiered()
            ),
            exit_slippage=(
                exit_slippage
                if exit_slippage is not None
                else FixedTickExitSlippage.liquid_equity()
            ),
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
            "entry_fill_policy": self.entry_fill_policy.value,
            "enforce_buying_power": self.enforce_buying_power,
            "max_gross_leverage": self.max_gross_leverage,
            **self.commission_model.identity(),
            **self.exit_slippage.identity(),
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
