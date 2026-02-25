from __future__ import annotations

from ..types import OrderSide, Trade


def classify_trades(trades: list[Trade]) -> tuple[int, int]:
    """Classify closed long round-trips as win/loss.

    Cost basis per unit = (buy notional) / buy_qty  (fees excluded from
    buy_notional so we can compare a clean cost-per-unit against net sell
    price after sell fees).

    PnL per unit = net_sell_price_per_unit - avg_buy_cost_per_unit
                 = (sell_price - sell_fee/sell_qty) - (buy_notional / buy_qty)
    """
    wins = 0
    losses = 0

    buy_notional = 0.0  # pure notional (no fees) of accumulated buys
    buy_qty = 0.0

    for t in trades:
        if t.side == OrderSide.BUY:
            buy_notional += t.notional  # do NOT add fee here
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


def compute_max_drawdown(equity_curve: list[float]) -> float:
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
