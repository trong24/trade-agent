from __future__ import annotations

from dataclasses import dataclass

from ..types import BrokerLike, Candle, OrderSide, RiskLike, Signal, StrategyLike
from .metrics import classify_trades, compute_max_drawdown


@dataclass
class BacktestResult:
    initial_cash: float
    final_equity: float
    total_return_pct: float
    num_trades: int
    wins: int
    losses: int
    total_fees: float
    max_drawdown_pct: float

    @property
    def win_rate(self) -> float:
        """Win rate as a percentage (0–100). Returns 0 if no closed trades."""
        total = self.wins + self.losses
        return (self.wins / total * 100) if total > 0 else 0.0


class BacktestEngine:
    def __init__(
        self,
        candles: list[Candle],
        strategy: StrategyLike,
        broker: BrokerLike,
        risk: RiskLike,
    ) -> None:
        if len(candles) < 2:
            raise ValueError("Need at least 2 candles")
        self.candles = candles
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self._has_run = False

    def run(self) -> BacktestResult:
        # Guard: broker state is mutated; running twice produces garbage results.
        if self._has_run:
            raise RuntimeError(
                "BacktestEngine.run() has already been called. "
                "Create a fresh BacktestEngine (and a fresh PaperBroker) to re-run."
            )
        self._has_run = True

        history: list[Candle] = []
        equity_curve: list[float] = []

        for candle in self.candles:
            history.append(candle)
            signal = self.strategy.generate(history)
            qty = self.risk.size(signal, self.broker, candle.close)

            if signal == Signal.BUY and qty > 0:
                self.broker.execute_market_order(
                    side=OrderSide.BUY,
                    qty=qty,
                    price=candle.close,
                    ts=candle.ts,
                )
            elif signal == Signal.SELL and qty > 0:
                self.broker.execute_market_order(
                    side=OrderSide.SELL,
                    qty=qty,
                    price=candle.close,
                    ts=candle.ts,
                )

            # Mark-to-market equity after each candle (for drawdown)
            equity_curve.append(self.broker.equity(candle.close))

        # Force close position at final candle for a realized result
        last_price = self.candles[-1].close
        if self.broker.position_qty > 0:
            self.broker.execute_market_order(
                side=OrderSide.SELL,
                qty=self.broker.position_qty,
                price=last_price,
                ts=self.candles[-1].ts,
            )
            equity_curve[-1] = self.broker.equity(last_price)

        final_equity = self.broker.equity(last_price)
        total_return_pct = (final_equity / self.broker.initial_cash - 1) * 100
        total_fees = sum(t.fee for t in self.broker.trades)
        wins, losses = classify_trades(self.broker.trades)
        max_drawdown_pct = compute_max_drawdown(equity_curve)

        return BacktestResult(
            initial_cash=self.broker.initial_cash,
            final_equity=final_equity,
            total_return_pct=total_return_pct,
            num_trades=len(self.broker.trades),
            wins=wins,
            losses=losses,
            total_fees=total_fees,
            max_drawdown_pct=max_drawdown_pct,
        )
