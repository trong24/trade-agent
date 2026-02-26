"""Plan-based backtest strategy: simulates actual plan rules bar-by-bar.

Unlike facts_strategy (vectorized signals), this walks through each bar
and applies discrete plan rules:
  - Entry: price enters a support/resistance zone while bias aligns
  - Stop:  ATR-based stop loss
  - Take profit: next zone boundary
  - Time stop: exit after N bars if flat
  - Invalidation: exit if invalidation level broken
  - Trade log: records each decision with rule trigger
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from trade_agent.analysis.plan_builder import build_plan


@dataclass
class TradeRecord:
    """A single completed trade with rule triggers."""
    entry_time:   str
    exit_time:    str
    side:         str      # 'long' | 'short'
    entry_price:  float
    exit_price:   float
    pnl_pct:      float
    exit_reason:  str      # 'stop' | 'tp' | 'time_stop' | 'invalidation' | 'end'
    bars_held:    int


def run_plan_backtest(
    df: pd.DataFrame,
    facts: dict,
    risk_params: dict | None = None,
    fee_bps: float = 2.0,
) -> dict:
    """Run a bar-by-bar backtest using plan rules.

    Args:
        df:          UTC-indexed OHLCV DataFrame.
        facts:       market_facts payload (interval='ALL').
        risk_params: override plan builder defaults.
        fee_bps:     fee per side in basis points.

    Returns:
        dict with metrics + trade_log.
    """
    plan = build_plan(facts, risk_params=risk_params)

    if "error" in plan:
        return {"error": plan["error"], "trades": [], "metrics": {}}

    fee_rate = fee_bps / 10_000
    bias = plan.get("primary_bias", "neutral")
    stop_info = plan.get("stop", {})
    targets = plan.get("targets", [])
    inv = plan.get("invalidation", {})
    rp = {
        "atr_stop_mult":  1.5,
        "time_stop_bars": 20,
        **(risk_params or {}),
    }

    # Extract zones for entry detection
    entry_rules = plan.get("entry_rules", [])
    entry_zone = None
    if entry_rules and entry_rules[0].get("zone"):
        entry_zone = entry_rules[0]["zone"]

    tp_prices = [t["price"] for t in targets] if targets else []

    # ── Bar-by-bar simulation ──────────────────────────────────────────────
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    times = df.index

    trades: list[TradeRecord] = []
    position = 0       # 0 = flat, 1 = long, -1 = short
    entry_price = 0.0
    entry_bar = 0
    stop_price = 0.0
    tp_price = 0.0
    equity = 1.0

    for i in range(1, len(closes)):
        c = closes[i]
        h = highs[i]
        lo = lows[i]
        ts = str(times[i])

        if position == 0:
            # ── Entry logic ────────────────────────────────────────────────
            if bias == "neutral" or not entry_zone:
                continue

            zone_price = entry_zone.get("price", 0)
            zone_width = max(zone_price * 0.005, 50.0)

            if bias == "long" and abs(c - zone_price) <= zone_width * 1.5:
                position = 1
                entry_price = c
                entry_bar = i
                stop_price = zone_price - rp["atr_stop_mult"] * zone_width * 2
                tp_price = tp_prices[0] if tp_prices else c * 1.05

            elif bias == "short" and abs(c - zone_price) <= zone_width * 1.5:
                position = -1
                entry_price = c
                entry_bar = i
                stop_price = zone_price + rp["atr_stop_mult"] * zone_width * 2
                tp_price = tp_prices[0] if tp_prices else c * 0.95

        else:
            # ── Exit logic ─────────────────────────────────────────────────
            bars_held = i - entry_bar
            exit_reason = None
            exit_price = c

            if position == 1:
                # Long exits
                if lo <= stop_price:
                    exit_reason = "stop"
                    exit_price = stop_price
                elif h >= tp_price:
                    exit_reason = "tp"
                    exit_price = tp_price
                elif inv.get("bear_below") and lo <= inv["bear_below"]:
                    exit_reason = "invalidation"
                    exit_price = inv["bear_below"]
                elif bars_held >= rp["time_stop_bars"]:
                    exit_reason = "time_stop"

            elif position == -1:
                # Short exits
                if h >= stop_price:
                    exit_reason = "stop"
                    exit_price = stop_price
                elif lo <= tp_price:
                    exit_reason = "tp"
                    exit_price = tp_price
                elif inv.get("bull_above") and h >= inv["bull_above"]:
                    exit_reason = "invalidation"
                    exit_price = inv["bull_above"]
                elif bars_held >= rp["time_stop_bars"]:
                    exit_reason = "time_stop"

            if exit_reason:
                if position == 1:
                    pnl = (exit_price / entry_price - 1) - 2 * fee_rate
                else:
                    pnl = (entry_price / exit_price - 1) - 2 * fee_rate

                equity *= (1 + pnl)

                trades.append(TradeRecord(
                    entry_time=str(times[entry_bar]),
                    exit_time=ts,
                    side="long" if position == 1 else "short",
                    entry_price=round(entry_price, 2),
                    exit_price=round(exit_price, 2),
                    pnl_pct=round(pnl * 100, 4),
                    exit_reason=exit_reason,
                    bars_held=bars_held,
                ))
                position = 0

    # Close any open position at end
    if position != 0:
        c = closes[-1]
        bars_held = len(closes) - 1 - entry_bar
        if position == 1:
            pnl = (c / entry_price - 1) - 2 * fee_rate
        else:
            pnl = (entry_price / c - 1) - 2 * fee_rate
        equity *= (1 + pnl)
        trades.append(TradeRecord(
            entry_time=str(times[entry_bar]),
            exit_time=str(times[-1]),
            side="long" if position == 1 else "short",
            entry_price=round(entry_price, 2),
            exit_price=round(c, 2),
            pnl_pct=round(pnl * 100, 4),
            exit_reason="end",
            bars_held=bars_held,
        ))

    # ── Metrics ────────────────────────────────────────────────────────────
    total_return = (equity - 1) * 100
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    # Max drawdown from trade-level equity curve
    eq_curve = [1.0]
    for t in trades:
        eq_curve.append(eq_curve[-1] * (1 + t.pnl_pct / 100))
    peak = eq_curve[0]
    max_dd = 0.0
    for eq in eq_curve:
        peak = max(peak, eq)
        dd = (eq - peak) / peak
        max_dd = min(max_dd, dd)

    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    profit_factor = abs(
        sum(t.pnl_pct for t in wins) / sum(t.pnl_pct for t in losses)
    ) if losses and sum(t.pnl_pct for t in losses) != 0 else 0

    metrics = {
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "trades":           len(trades),
        "wins":             len(wins),
        "losses":           len(losses),
        "win_rate_pct":     round(len(wins) / max(len(trades), 1) * 100, 1),
        "avg_win_pct":      round(avg_win, 4),
        "avg_loss_pct":     round(avg_loss, 4),
        "profit_factor":    round(profit_factor, 3),
        "bars":             len(df),
        "bias":             bias,
    }

    trade_log = [
        {
            "entry":       t.entry_time,
            "exit":        t.exit_time,
            "side":        t.side,
            "entry_price": t.entry_price,
            "exit_price":  t.exit_price,
            "pnl_pct":     t.pnl_pct,
            "reason":      t.exit_reason,
            "bars":        t.bars_held,
        }
        for t in trades
    ]

    return {
        "metrics":   metrics,
        "trade_log": trade_log,
        "plan":      plan,
    }
