"""E4, exit side: slippage on the way out.

The simulator fills exits at exactly the trigger price. ``_close_at_level``
uses the stop or target level verbatim and the end-of-day path uses
``bar.close``, so every exit assumes the market paused to accommodate us.

The correction is *not* to apply the entry slippage symmetrically, because the
three exit reasons are different order types live:

======  =========================  ==================================
Reason  Live order                 Can it fill worse than the level?
======  =========================  ==================================
STOP    native ``StopOrder``       Yes, always. It becomes a market
                                   order the moment it triggers.
TP      ``LimitOrder``             No. A limit fills at its price or
                                   better; its risk is *non-fill*.
EOD     ``MarketOrder``            Yes.
======  =========================  ==================================

(Order types confirmed in ``brokers/interactive_brokers.py::_to_ib_order``.)

Charging a take-profit for slippage would therefore be wrong rather than
merely conservative, and it would bias every future comparison of "is a target
worth using?" against the target. Because the current ORB ruleset sets
``take_profit.multiplier: 0`` that error would lie dormant and surface only
once someone enabled targets, so the distinction is encoded structurally here:
an exit that provides liquidity *cannot* be assigned slippage, and the model
raises if you try.

Magnitudes. Stops slip harder than entries. A stop triggers precisely when
price is moving against the position, and it takes liquidity on the wrong side
with no discretion to wait for a better price -- the adverse selection is the
point of the order type, not bad luck. The defaults below make stops twice the
end-of-day cost for that reason.

Calibration. We cannot yet calibrate from our own fills: the only execution
records on hand (``data/local/ib_executions.db``) are two synthetic test rows.
Every number here is therefore a declared assumption, not a measurement, which
is why it travels in the run fingerprint and why ``breakeven_ticks`` exists --
a result should be reported alongside the slippage that would erase it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from vibe.common.models.bar import Bar

__all__ = [
    "TICK_SIZE",
    "ExitCost",
    "ExitSlippageModel",
    "FixedTickExitSlippage",
]

TICK_SIZE = 0.01
"""US equity minimum price increment."""


@dataclass(frozen=True)
class ExitCost:
    """What one exit reason costs, and why it is allowed to cost anything.

    ``takes_liquidity`` is the load-bearing field. It records the order type
    rather than the price effect, so a reader can tell whether a zero is a
    tuned parameter or a structural fact. Zeros that mean different things
    should not look the same.
    """

    ticks: int = 0
    takes_liquidity: bool = True

    def __post_init__(self) -> None:
        if self.ticks < 0:
            raise ValueError(f"ticks must be non-negative, got {self.ticks}")
        if not self.takes_liquidity and self.ticks:
            raise ValueError(
                "A liquidity-providing exit cannot slip adversely: a resting "
                "limit order fills at its price or better. Model its risk as "
                "non-fill, not as a worse fill."
            )


@runtime_checkable
class ExitSlippageModel(Protocol):
    """How much worse than the trigger price an exit actually fills.

    A protocol rather than a concrete class because the fixed-tick default is
    the *simplest* defensible model, not the most accurate one. A volume-scaled
    implementation -- slippage rising with participation, as
    ``execution/slippage.py::SqrtVolumeSlippage`` already does for entries --
    drops in without touching the portfolio.
    """

    def adjust(
        self,
        price: float,
        *,
        reason: str,
        side: str,
        quantity: float,
        bar: Bar,
    ) -> float:
        """Return the fill price after slippage, always at or worse than ``price``.

        ``side`` is the side of the *closing* order: "sell" to exit a long.
        """
        ...

    def identity(self) -> dict[str, object]:
        """Settings that must participate in the run fingerprint."""
        ...

    @property
    def is_zero(self) -> bool:
        """True when this model can never move a price."""
        ...


def _default_rules() -> dict[str, ExitCost]:
    """Per-reason defaults for a liquid US equity ETF.

    Two ticks ($0.02/share) on a stop is deliberately modest: QQQ is among the
    most liquid instruments traded and typically quotes a one-cent spread, so
    a stop crossing it should cost about a spread plus a little. Wider names
    warrant a wider setting, which is why this is configuration.
    """
    return {
        "STOP": ExitCost(ticks=2, takes_liquidity=True),
        "EOD": ExitCost(ticks=1, takes_liquidity=True),
        "TP": ExitCost(ticks=0, takes_liquidity=False),
    }


@dataclass(frozen=True)
class FixedTickExitSlippage:
    """A flat tick cost per exit reason.

    Deliberately independent of order size. A size-dependent model is more
    realistic and is the obvious next step, but pretending to that precision
    without fill data to calibrate it would look rigorous while being a guess
    with more parameters.
    """

    rules: Mapping[str, ExitCost] = field(default_factory=_default_rules)
    tick_size: float = TICK_SIZE
    default: ExitCost = field(default_factory=ExitCost)
    """Applied to an exit reason not named in ``rules``.

    Defaults to zero ticks. An unknown reason must not silently inherit a stop's
    cost, and it must not fail the run either: the cost of a new exit path is a
    decision for whoever adds it.
    """

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be positive, got {self.tick_size}")

    @classmethod
    def zero(cls) -> "FixedTickExitSlippage":
        """No exit slippage. Legacy behaviour, bit-identical to prior results."""
        return cls(rules={}, default=ExitCost(ticks=0))

    @classmethod
    def liquid_equity(cls, *, stop_ticks: int = 2, eod_ticks: int = 1) -> "FixedTickExitSlippage":
        """Defaults suited to a liquid US equity or ETF.

        Args:
            stop_ticks: Cost of a stop crossing the spread under duress.
            eod_ticks: Cost of a market-on-close style exit.
        """
        return cls(
            rules={
                "STOP": ExitCost(ticks=stop_ticks, takes_liquidity=True),
                "EOD": ExitCost(ticks=eod_ticks, takes_liquidity=True),
                "TP": ExitCost(ticks=0, takes_liquidity=False),
            }
        )

    def cost_for(self, reason: str) -> ExitCost:
        return self.rules.get(reason, self.default)

    @property
    def is_zero(self) -> bool:
        return all(not rule.ticks for rule in self.rules.values()) and not self.default.ticks

    def adjust(
        self,
        price: float,
        *,
        reason: str,
        side: str,
        quantity: float,
        bar: Bar,
    ) -> float:
        """Move ``price`` against the closing order by the reason's tick cost.

        The result is clamped to the bar's traded range by the caller, not
        here: this model owns *how much* the fill is worse, and the portfolio
        owns the invariant that a fill must be a price that actually traded.
        """
        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side}")

        amount = self.cost_for(reason).ticks * self.tick_size
        if not amount:
            return price

        # Closing a long means selling, which fills lower; closing a short
        # means buying, which fills higher. Adverse in both directions.
        return price - amount if side == "sell" else price + amount

    def identity(self) -> dict[str, object]:
        return {
            "exit_slippage_model": type(self).__name__,
            "exit_slippage_tick_size": self.tick_size,
            "exit_slippage_ticks": {
                reason: rule.ticks
                for reason, rule in sorted(self.rules.items())
            },
            "exit_slippage_default_ticks": self.default.ticks,
        }

    def __hash__(self) -> int:
        return hash(
            (
                tuple(sorted((r, c.ticks, c.takes_liquidity) for r, c in self.rules.items())),
                self.tick_size,
                self.default.ticks,
                self.default.takes_liquidity,
            )
        )
