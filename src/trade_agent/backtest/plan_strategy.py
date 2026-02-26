"""Plan-based backtest strategy: simulates actual plan rules bar-by-bar.

v2 fix: Re-computes facts from initial candle data (no lookahead bias).
If external facts are provided, validates entry zones are within
reasonable range of the actual price data.

Rules:
  - Entry: price enters a support/resistance zone while bias aligns
  - Stop:  ATR-based stop loss
  - Take profit: next zone boundary
  - Time stop: exit after N bars if flat
  - Invalidation: exit if invalidation level broken
  - Trade log: records each decision with rule trigger
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trade_agent.analysis.payload import build_payload
from trade_agent.analysis.plan_builder import build_plan
from trade_agent.analysis.sr import compute_sr
from trade_agent.analysis.trend import compute_trend


@dataclass
class TradeRecord:
    """A single completed trade with rule triggers."""
    entry_time:   str
    exit_time:    str
    side:         str
    entry_price:  float
    exit_price:   float
    pnl_pct:      float
    exit_reason:  str
    bars_held:    int


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV DataFrame to a lower frequency (e.g. '4h', '1D')."""
    resampled = df.resample(rule).agg({
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return resampled


def _compute_facts_from_candles(
    df: pd.DataFrame,
    symbol: str = "BTCUSDT",
    lookback: int = 500,
    analyze_tfs: list[str] | None = None,
) -> dict:
    """Compute facts inline from candle data (no lookahead).

    Resamples the base TF candles to higher TFs (4h, 1D) for multi-TF
    bias chain analysis.
    """
    analysis_df = df.iloc[:lookback] if len(df) > lookback else df
    as_of = analysis_df.index[-1].to_pydatetime()

    per_tf: dict = {}

    # Base TF (assumed 1h)
    trend_1h = compute_trend(analysis_df)
    per_tf["1h"] = {
        "trend": trend_1h,
        "sr":    compute_sr(analysis_df),
    }

    # Resample to 4h
    df_4h = _resample_ohlcv(analysis_df, "4h")
    if len(df_4h) >= 30:
        per_tf["4h"] = {
            "trend": compute_trend(df_4h),
            "sr":    compute_sr(df_4h),
        }

    # Resample to 1D
    df_1d = _resample_ohlcv(analysis_df, "1D")
    if len(df_1d) >= 14:
        per_tf["1d"] = {
            "trend": compute_trend(df_1d),
            "sr":    compute_sr(df_1d),
        }

    payload = build_payload(symbol, as_of, per_tf)

    # Fallback: if bias_chain is all neutral but 1h has a clear trend,
    # override bias_chain to use 1h trend directly (better than no trades)
    chain = payload.get("bias_chain", {})
    all_neutral = all(
        v.get("bias") == "neutral" for v in chain.values()
    )
    if all_neutral and not trend_1h.get("is_sideway"):
        d = trend_1h.get("trend_dir", "sideway")
        if d == "up":
            override = "long"
        elif d == "down":
            override = "short"
        else:
            override = "neutral"
        if override != "neutral":
            for tf in chain:
                chain[tf]["bias"] = override
                chain[tf]["from_tf"] = "1h_fallback"
                chain[tf]["confidence"] = "low"
            payload["bias_chain"] = chain

    return payload



def _validate_facts_for_data(facts: dict, df: pd.DataFrame) -> dict | None:
    """Check if facts key_levels are within reasonable range of actual data.

    Returns facts if valid, None if too far off.
    """
    price_range = df["close"].values
    data_min = float(price_range.min()) * 0.8
    data_max = float(price_range.max()) * 1.2

    levels = facts.get("key_levels", [])
    valid_levels = [
        lv for lv in levels
        if data_min <= lv.get("price", 0) <= data_max
    ]

    if not valid_levels:
        return None  # all levels out of range → recompute

    facts = dict(facts)
    facts["key_levels"] = valid_levels

    # Also fix invalidation
    current = float(df["close"].iloc[0])
    supports = [lv for lv in valid_levels if lv["kind"] == "support" and lv["price"] < current]
    resists = [lv for lv in valid_levels if lv["kind"] == "resistance" and lv["price"] > current]
    facts["invalidation"] = {
        "bear_below": max((lv["price"] for lv in supports), default=None),
        "bull_above": min((lv["price"] for lv in resists), default=None),
    }
    return facts


def run_plan_backtest(
    df: pd.DataFrame,
    facts: dict | None = None,
    risk_params: dict | None = None,
    fee_bps: float = 2.0,
    lookback: int = 500,
) -> dict:
    """Run a bar-by-bar backtest using plan rules.

    If facts are provided but out-of-range for the data, re-computes
    inline from the first `lookback` bars (no lookahead).

    Args:
        df:          UTC-indexed OHLCV DataFrame.
        facts:       market_facts payload, or None to compute inline.
        risk_params: override plan builder defaults.
        fee_bps:     fee per side in basis points.
        lookback:    bars to use for inline analysis.

    Returns:
        dict with metrics + trade_log.
    """
    # Validate or recompute facts
    if facts is not None:
        facts = _validate_facts_for_data(facts, df)

    if facts is None:
        facts = _compute_facts_from_candles(df, lookback=lookback)

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

    # Extract entry zone
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

    # Skip the lookback period used for analysis
    start_bar = min(lookback, len(closes) - 10)

    trades: list[TradeRecord] = []
    position = 0
    entry_price = 0.0
    entry_bar = 0
    stop_price = 0.0
    tp_price = 0.0
    equity = 1.0

    for i in range(start_bar, len(closes)):
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

            if bias == "long" and lo <= zone_price + zone_width:
                position = 1
                entry_price = c
                entry_bar = i
                stop_price = zone_price - rp["atr_stop_mult"] * zone_width * 2
                tp_price = tp_prices[0] if tp_prices else c * 1.03

            elif bias == "short" and h >= zone_price - zone_width:
                position = -1
                entry_price = c
                entry_bar = i
                stop_price = zone_price + rp["atr_stop_mult"] * zone_width * 2
                tp_price = tp_prices[0] if tp_prices else c * 0.97

        else:
            # ── Exit logic ─────────────────────────────────────────────────
            bars_held = i - entry_bar
            exit_reason = None
            exit_price = c

            if position == 1:
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
    loss_sum = sum(t.pnl_pct for t in losses)
    profit_factor = abs(
        sum(t.pnl_pct for t in wins) / loss_sum
    ) if loss_sum != 0 else 0

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
