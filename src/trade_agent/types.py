from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol


class Signal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Trade:
    ts: datetime
    side: OrderSide
    qty: float
    price: float
    fee: float

    @property
    def notional(self) -> float:
        return self.qty * self.price


# ── Structural Protocols (avoid concrete coupling between modules) ──────────


class StrategyLike(Protocol):
    """Anything that can emit a Signal given a list of candles."""

    def generate(self, candles: list[Candle]) -> Signal: ...


class BrokerLike(Protocol):
    """Minimal broker interface required by risk and backtest modules."""

    fee_rate: float
    position_qty: float
    initial_cash: float
    trades: list[Trade]

    def equity(self, mark_price: float) -> float: ...

    def execute_market_order(
        self,
        side: OrderSide,
        qty: float,
        price: float,
        ts: datetime,
    ) -> Trade | None: ...


class RiskLike(Protocol):
    """Anything that can size a position given a signal and broker state."""

    def size(self, signal: Signal, broker: BrokerLike, price: float) -> float: ...
