from __future__ import annotations

from .types import BrokerLike, Signal  # ← no longer imports PaperBroker


class FixedFractionRisk:
    """Risk model:

    - BUY: allocate max_fraction of current equity if not already in position
    - SELL: close full position
    """

    def __init__(self, max_fraction: float = 0.2, min_notional: float = 20.0) -> None:
        if not (0 < max_fraction <= 1):
            raise ValueError("max_fraction must be in (0, 1]")
        if min_notional < 0:
            raise ValueError("min_notional must be >= 0")
        self.max_fraction = max_fraction
        self.min_notional = min_notional

    def size(self, signal: Signal, broker: BrokerLike, price: float) -> float:
        if signal == Signal.BUY:
            if broker.position_qty > 0:
                return 0.0
            equity = broker.equity(price)
            notional = equity * self.max_fraction
            if notional < self.min_notional:
                return 0.0
            # keep small buffer for fee
            notional_after_fee = notional / (1 + broker.fee_rate)
            return max(0.0, notional_after_fee / price)

        if signal == Signal.SELL:
            return broker.position_qty

        return 0.0
