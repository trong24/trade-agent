from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .types import OrderSide, Trade


@dataclass
class PaperBroker:
    initial_cash: float
    fee_bps: float = 6.0
    cash: float = field(init=False)
    position_qty: float = field(default=0.0, init=False)
    trades: list[Trade] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be > 0")
        if self.fee_bps < 0:
            raise ValueError("fee_bps must be >= 0")
        self.cash = self.initial_cash

    @property
    def fee_rate(self) -> float:
        return self.fee_bps / 10_000

    def equity(self, mark_price: float) -> float:
        return self.cash + (self.position_qty * mark_price)

    def execute_market_order(
        self,
        side: OrderSide,
        qty: float,
        price: float,
        ts: datetime,          # ← was untyped
    ) -> Trade | None:
        if qty <= 0:
            return None
        notional = qty * price
        fee = notional * self.fee_rate

        if side == OrderSide.BUY:
            total_cost = notional + fee
            if total_cost > self.cash:
                return None
            self.cash -= total_cost
            self.position_qty += qty
        else:
            if qty > self.position_qty:
                qty = self.position_qty
                notional = qty * price
                fee = notional * self.fee_rate
            if qty <= 0:
                return None
            proceeds = notional - fee
            self.cash += proceeds
            self.position_qty -= qty

        trade = Trade(ts=ts, side=side, qty=qty, price=price, fee=fee)
        self.trades.append(trade)
        return trade
