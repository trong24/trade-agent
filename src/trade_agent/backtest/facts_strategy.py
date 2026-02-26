"""Vectorized backtest strategy that consumes precomputed market_facts from DuckDB.

Signal rules (sr_trend_v1):
  - Load latest facts snapshot (ALL interval) at/before end_time
  - For each bar:
      HTF trend (1w > 1d > 4h) determines bias:
        up   → look for long near support zones
        down → look for short near resistance zones
        sideway → HOLD
      Entry: price within zone_mult * zone_width of a zone boundary
      Exit signal flips when trend or zone proximity reverses
  - Positions shifted 1 bar (no lookahead)
  - Returns: pd.Series of position (-1, 0, 1)
"""
from __future__ import annotations

import pandas as pd


def _get_htf_trend(facts: dict) -> str:
    """Derive higher-TF bias from htf_trend in facts payload.

    Priority: 1w > 1d > 4h. Returns 'up', 'down', or 'sideway'.
    """
    htf = facts.get("htf_trend", {})
    for tf in ("1w", "1d", "4h"):
        entry = htf.get(tf, {})
        if entry and not entry.get("sideway", True):
            return entry.get("dir", "sideway")
    return "sideway"


def _get_zones(facts: dict, kind: str) -> list[dict]:
    """Extract zones of given kind from ALL-interval payload."""
    key_levels = facts.get("key_levels", [])
    return [lv for lv in key_levels if lv.get("kind") == kind]


def _price_near_zone(price: float, zones: list[dict], zone_mult: float = 1.5) -> bool:
    """True if price is within zone_mult × zone_width of any zone."""
    for z in zones:
        low = z.get("low", z.get("price", 0))
        high = z.get("high", z.get("price", 0))
        width = max(high - low, 1.0)
        if (low - zone_mult * width) <= price <= (high + zone_mult * width):
            return True
    return False


def generate_signals(
    df: pd.DataFrame,
    facts: dict,
    params: dict | None = None,
) -> pd.Series:
    """Generate vectorized signals (-1, 0, 1) per bar using precomputed facts.

    Args:
        df:     UTC-indexed OHLCV DataFrame from DuckDB.
        facts:  dict from market_facts (interval='ALL') — precomputed payload.
        params: override {zone_mult: float}

    Returns:
        pd.Series of int signals aligned to df.index.
    """
    p = {"zone_mult": 1.5, **(params or {})}

    htf_bias = _get_htf_trend(facts)
    support_zones    = _get_zones(facts, "support")
    resistance_zones = _get_zones(facts, "resistance")

    signals = pd.Series(0, index=df.index, dtype=int)

    if htf_bias == "up":
        # Long only: enter near support, exit near resistance
        near_support = df["close"].apply(
            lambda c: _price_near_zone(c, support_zones, p["zone_mult"])
        )
        near_resist = df["close"].apply(
            lambda c: _price_near_zone(c, resistance_zones, p["zone_mult"])
        )
        signals[near_support & ~near_resist] = 1

    elif htf_bias == "down":
        # Short only: enter near resistance, exit near support
        near_resist = df["close"].apply(
            lambda c: _price_near_zone(c, resistance_zones, p["zone_mult"])
        )
        near_support = df["close"].apply(
            lambda c: _price_near_zone(c, support_zones, p["zone_mult"])
        )
        signals[near_resist & ~near_support] = -1

    # Shift 1 bar to avoid lookahead bias
    return signals.shift(1).fillna(0).astype(int)


def run_vectorized_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    fee_bps: float = 2.0,
) -> dict:
    """Run a vectorized backtest from a signal series.

    Cost model: fee applied on each position change (entry/exit).
    Returns metrics dict.
    """
    fee_rate = fee_bps / 10_000

    returns = df["close"].pct_change().fillna(0)
    pos = signals  # already shifted

    strategy_returns = pos * returns

    # Fee on position changes
    pos_change = pos.diff().abs().fillna(0)
    fees = pos_change * fee_rate
    net_returns = strategy_returns - fees

    cumulative = (1 + net_returns).cumprod()
    total_return = float(cumulative.iloc[-1] - 1) * 100

    # Max drawdown
    roll_max = cumulative.cummax()
    drawdown = (cumulative - roll_max) / roll_max
    max_dd = float(drawdown.min()) * 100

    # Sharpe (annualized, daily bars assumed — scale by sqrt(bars per year))
    bars_per_year = 365 * 24  # rough for 1h
    ann_factor = bars_per_year ** 0.5
    sharpe = 0.0
    if net_returns.std() > 0:
        sharpe = float(net_returns.mean() / net_returns.std() * ann_factor)

    # Trade count: number of entries (pos going 0→nonzero)
    entries = ((pos != 0) & (pos.shift(1).fillna(0) == 0)).sum()

    return {
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe":           round(sharpe, 4),
        "trades":           int(entries),
        "htf_bias":         _get_htf_trend({"htf_trend": {}}),
        "bars":             len(df),
    }
