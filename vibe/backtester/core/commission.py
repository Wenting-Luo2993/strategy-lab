"""Commission models for the backtester.

E4 in section 6 of the backtest pipeline plan: the simulator constructed every
``FillResult`` with ``commission=0.0``, nothing applied commission anywhere,
and slippage was applied only at entry. Stops, targets, and end-of-day exits
filled at exact levels at no cost, so a round trip was free on one side and a
slippage-monotonicity test could only ever exercise the other.

Costs are modelled per *order*, not per share, because every real schedule has
a per-order minimum and a notional cap that a flat per-share rate cannot
express. A 1-share order does not cost a third of a cent.

The default for realistic runs is IBKR Pro tiered, which is what this project
trades through. It is a parameter rather than a constant: a commission
schedule is an account-level fact that changes with broker, tier, and volume,
and hardcoding one would silently embed a stale assumption in every result.

Note this covers commission only. Exchange, clearing, and regulatory fees
(SEC, FINRA TAF) are real but small, venue-dependent, and asymmetric between
buys and sells; folding a guess at them into the commission rate would make
the number look precise while being less accurate. Use ``other_per_share`` to
add them explicitly when a schedule is known.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CommissionModel"]


@dataclass(frozen=True)
class CommissionModel:
    """A per-order commission schedule.

    Cost for one order is::

        min(max(per_share * qty + other_per_share * qty, minimum_per_order),
            maximum_pct_of_notional * qty * price)

    The ordering is deliberate and matches how brokers actually bill: the
    per-order minimum is applied first, then the notional cap overrides it.
    For a 1-share $10 trade on IBKR Pro tiered, the $0.35 minimum exceeds the
    1% cap of $0.10, and the cap wins. Applying them in the other order would
    overcharge small orders, which is precisely where the minimum bites.
    """

    per_share: float = 0.0
    minimum_per_order: float = 0.0
    maximum_pct_of_notional: float = 1.0
    other_per_share: float = 0.0
    name: str = "zero"

    def __post_init__(self) -> None:
        for field_name in (
            "per_share", "minimum_per_order", "maximum_pct_of_notional",
            "other_per_share",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(
                    f"{field_name} must be non-negative, got {value}"
                )

    @classmethod
    def zero(cls) -> "CommissionModel":
        """No costs. The historical behaviour, retained for legacy runs."""
        return cls()

    @classmethod
    def ibkr_pro_tiered(cls) -> "CommissionModel":
        """IBKR Pro tiered for US stocks and ETFs.

        $0.0035 per share, $0.35 per-order minimum, capped at 1% of trade
        value. Exchange and regulatory fees are billed separately and are not
        included here.
        """
        return cls(
            per_share=0.0035,
            minimum_per_order=0.35,
            maximum_pct_of_notional=0.01,
            name="ibkr_pro_tiered",
        )

    @classmethod
    def ibkr_pro_fixed(cls) -> "CommissionModel":
        """IBKR Pro fixed for US stocks and ETFs.

        $0.005 per share, $1.00 per-order minimum, capped at 1% of trade
        value. Fixed pricing bundles exchange and regulatory fees.
        """
        return cls(
            per_share=0.005,
            minimum_per_order=1.00,
            maximum_pct_of_notional=0.01,
            name="ibkr_pro_fixed",
        )

    @property
    def is_zero(self) -> bool:
        """True when this model can never charge anything."""
        return (
            self.per_share == 0.0
            and self.other_per_share == 0.0
            and self.minimum_per_order == 0.0
        )

    def cost(self, quantity: float, price: float) -> float:
        """Return the commission for filling ``quantity`` shares at ``price``.

        Args:
            quantity: Shares filled. Sign is ignored; a sell costs the same as
                a buy.
            price: Fill price per share.

        Returns:
            Commission in account currency. Zero for an empty fill, so that a
            rejected or zero-quantity order is never billed a minimum.
        """
        shares = abs(quantity)
        if shares == 0 or price <= 0:
            return 0.0

        variable = (self.per_share + self.other_per_share) * shares
        charged = max(variable, self.minimum_per_order)
        cap = self.maximum_pct_of_notional * shares * price
        return float(min(charged, cap))

    def identity(self) -> dict[str, object]:
        """Settings that must participate in the run fingerprint.

        Two runs that differ only in commission schedule are not comparable,
        and the difference is invisible in the headline metrics.
        """
        return {
            "commission_model": self.name,
            "per_share": self.per_share,
            "minimum_per_order": self.minimum_per_order,
            "maximum_pct_of_notional": self.maximum_pct_of_notional,
            "other_per_share": self.other_per_share,
        }
