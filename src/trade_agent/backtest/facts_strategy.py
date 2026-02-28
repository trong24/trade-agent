"""Vectorized backtest strategy consuming precomputed market_facts.

v2: Uses bias_chain from payload instead of hardcoded HTF lookup.
Signal rules (sr_trend_v1):
  - Read bias_chain[interval] for directional bias
  - Long bias + near support zone → +1
  - Short bias + near resistance zone → -1
  - Neutral / sideway → 0
  - Positions shifted 1 bar (no lookahead)
"""

from __future__ import annotations

import pandas as pd


def _get_bias(facts: dict | None, interval: str) -> str:
    """Get bias for an interval from facts payload's bias_chain.

    Falls back to htf_trend priority if bias_chain not present.
    """
    if not facts:
        return "neutral"
    chain = facts.get("bias_chain", {})
    if interval in chain:
        return chain[interval].get("bias", "neutral")

    # Fallback: old htf_trend lookup
    htf = facts.get("htf_trend", {})
    for tf in ("1w", "1d", "4h"):
        entry = htf.get(tf, {})
        if entry and not entry.get("sideway", True):
            d = entry.get("dir", "sideway")
            return "long" if d == "up" else "short" if d == "down" else "neutral"
    return "neutral"


def _get_zones(facts: dict | None, kind: str) -> list[dict]:
    """Extract zones from key_levels or per-TF SR."""
    if not facts:
        return []
    return [lv for lv in facts.get("key_levels", []) if lv.get("kind") == kind]


def _price_near_zone(price: float, zones: list[dict], zone_mult: float = 1.5) -> bool:
    """True if price is within zone_mult × zone_width of any zone."""
    for z in zones:
        level_price = z.get("price", 0)
        # Use score-based proximity: higher score = wider catch area
        width = max(level_price * 0.005, 50.0)  # min 0.5% or $50
        if (level_price - zone_mult * width) <= price <= (level_price + zone_mult * width):
            return True
    return False


def generate_signals(
    df: pd.DataFrame,
    facts: dict | None,
    interval: str = "1h",
    params: dict | None = None,
) -> pd.Series:
    """Generate vectorized signals (-1, 0, 1) per bar.

    Args:
        df:       UTC-indexed OHLCV DataFrame.
        facts:    dict from market_facts (interval='ALL').
        interval: candle interval being backtested (for bias chain lookup).
        params:   override {zone_mult: float}

    Returns:
        pd.Series of int signals aligned to df.index.
    """
    p = {"zone_mult": 1.5, **(params or {})}

    bias = _get_bias(facts, interval)
    support_zones = _get_zones(facts, "support")
    resistance_zones = _get_zones(facts, "resistance")

    signals = pd.Series(0, index=df.index, dtype=int)

    if bias == "long":
        near_support = df["close"].apply(
            lambda c: _price_near_zone(c, support_zones, p["zone_mult"])
        )
        near_resist = df["close"].apply(
            lambda c: _price_near_zone(c, resistance_zones, p["zone_mult"])
        )
        signals[near_support & ~near_resist] = 1

    elif bias == "short":
        near_resist = df["close"].apply(
            lambda c: _price_near_zone(c, resistance_zones, p["zone_mult"])
        )
        near_support = df["close"].apply(
            lambda c: _price_near_zone(c, support_zones, p["zone_mult"])
        )
        signals[near_resist & ~near_support] = -1

    # Shift 1 bar to avoid lookahead
    return signals.shift(1).fillna(0).astype(int)


def run_vectorized_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    fee_bps: float = 2.0,
) -> dict:
    """Run vectorized backtest. Returns metrics dict."""
    fee_rate = fee_bps / 10_000
    returns = df["close"].pct_change().fillna(0)
    pos = signals

    strategy_returns = pos * returns
    pos_change = pos.diff().abs().fillna(0)
    fees = pos_change * fee_rate
    net_returns = strategy_returns - fees

    cumulative = (1 + net_returns).cumprod()
    total_return = float(cumulative.iloc[-1] - 1) * 100

    roll_max = cumulative.cummax()
    drawdown = (cumulative - roll_max) / roll_max
    max_dd = float(drawdown.min()) * 100

    # Sharpe
    sharpe = 0.0
    if net_returns.std() > 0:
        # Annualize based on interval assumed hourly
        ann_factor = (365 * 24) ** 0.5
        sharpe = float(net_returns.mean() / net_returns.std() * ann_factor)

    entries = ((pos != 0) & (pos.shift(1).fillna(0) == 0)).sum()

    return {
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "trades": int(entries),
        "bars": len(df),
    }
