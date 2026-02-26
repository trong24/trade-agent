"""Trend detection: EMA crossover + slope + ATR band."""
from __future__ import annotations

import pandas as pd

from .indicators import atr, ema


_DEFAULT_PARAMS = {
    "ema_fast":   20,
    "ema_slow":   50,
    "atr_period": 14,
    "sideway_atr_mult": 0.5,   # range < mult*ATR → sideway
    "slope_bars": 5,            # bars over which to measure ema_slow slope
}


def compute_trend(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Compute trend facts for a candle DataFrame.

    Args:
        df:     DataFrame (UTC index) with open/high/low/close/volume.
        params: Override default params.

    Returns dict:
        trend_dir:       'up' | 'down' | 'sideway'
        trend_strength:  0..1  (based on EMA separation / ATR)
        ema_fast:        float (last value)
        ema_slow:        float (last value)
        ema_slow_slope:  float (% change of slow EMA over slope_bars)
        atr_pct:         float (ATR / close price %)
        dist_to_slow:    float ((close - ema_slow) / atr; signed)
        is_sideway:      bool
    """
    p = {**_DEFAULT_PARAMS, **(params or {})}

    close = df["close"]
    fast = ema(close, p["ema_fast"])
    slow = ema(close, p["ema_slow"])
    atr_s = atr(df, p["atr_period"])

    # Use last valid values
    c = float(close.iloc[-1])
    f = float(fast.iloc[-1])
    s = float(slow.iloc[-1])
    a = float(atr_s.iloc[-1])

    # Slope of slow EMA over last N bars (as % per bar)
    n = p["slope_bars"]
    s_prev = float(slow.iloc[-(n + 1)]) if len(slow) > n else s
    slope_pct = ((s - s_prev) / s_prev * 100) if s_prev != 0 else 0.0

    dist = (c - s) / a if a > 0 else 0.0
    atr_pct = (a / c * 100) if c != 0 else 0.0

    # Sideway: EMA fast ≈ EMA slow (within mult*ATR)
    ema_gap = abs(f - s)
    is_sideway = ema_gap < p["sideway_atr_mult"] * a

    if is_sideway:
        trend_dir = "sideway"
        strength = 0.0
    elif f > s:
        trend_dir = "up"
        strength = min(1.0, ema_gap / (2 * a)) if a > 0 else 0.5
    else:
        trend_dir = "down"
        strength = min(1.0, ema_gap / (2 * a)) if a > 0 else 0.5

    return {
        "trend_dir":      trend_dir,
        "trend_strength": round(strength, 4),
        "ema_fast":       round(f, 4),
        "ema_slow":       round(s, 4),
        "ema_slow_slope": round(slope_pct, 6),
        "atr_pct":        round(atr_pct, 4),
        "dist_to_slow":   round(dist, 4),
        "is_sideway":     is_sideway,
    }
