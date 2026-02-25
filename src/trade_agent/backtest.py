from __future__ import annotations

from dataclasses import dataclass

from .types import BrokerLike, Candle, OrderSide, RiskLike, Signal, StrategyLike, Trade


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
        strategy: StrategyLike,    # ← Protocol, not SMACrossStrategy
        broker: BrokerLike,        # ← Protocol, not PaperBroker
        risk: RiskLike,            # ← Protocol, not FixedFractionRisk
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
            # Update last equity point after forced close
            equity_curve[-1] = self.broker.equity(last_price)

        final_equity = self.broker.equity(last_price)
        total_return_pct = (final_equity / self.broker.initial_cash - 1) * 100
        total_fees = sum(t.fee for t in self.broker.trades)
        wins, losses = _classify_trades(self.broker.trades)
        max_drawdown_pct = _compute_max_drawdown(equity_curve)

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


def _classify_trades(trades: list[Trade]) -> tuple[int, int]:
    """Classify closed long round-trips as win/loss.

    Cost basis per unit = (buy notional) / buy_qty  (fees excluded from
    buy_notional so we can compare a clean cost-per-unit against net sell
    price after sell fees).

    PnL per unit = net_sell_price_per_unit - avg_buy_cost_per_unit
                 = (sell_price - sell_fee/sell_qty) - (buy_notional / buy_qty)
    """
    wins = 0
    losses = 0

    buy_notional = 0.0   # pure notional (no fees) of accumulated buys
    buy_qty = 0.0

    for t in trades:
        if t.side == OrderSide.BUY:
            buy_notional += t.notional   # ← do NOT add fee here
            buy_qty += t.qty
            continue

        if t.side == OrderSide.SELL and buy_qty > 0:
            avg_entry = buy_notional / buy_qty
            # Subtract sell-side fee from effective sale price
            net_sell_price = t.price - (t.fee / t.qty)
            pnl_per_unit = net_sell_price - avg_entry
            if pnl_per_unit > 0:
                wins += 1
            else:
                losses += 1
            buy_notional = 0.0
            buy_qty = 0.0

    return wins, losses


def _compute_max_drawdown(equity_curve: list[float]) -> float:
    """Return the maximum peak-to-trough drawdown as a positive percentage."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd
