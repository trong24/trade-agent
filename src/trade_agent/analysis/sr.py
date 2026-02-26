"""Support & Resistance: pivot fractals → ATR clustering → scored zones."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .indicators import atr


_DEFAULT_PARAMS = {
    "fractal_n":    2,      # bars each side for pivot confirmation
    "cluster_tol":  0.25,   # cluster width = tol * ATR
    "atr_period":   14,
    "recency_half": 50,     # half-life in bars for recency decay
    "max_levels":   20,     # max levels to return
}


@dataclass
class _Level:
    price: float
    kind: str          # 'support' | 'resistance'
    touches: int = 1
    last_bar: int = 0  # bar index of most recent touch
    scores: list[float] = field(default_factory=list)


def _pivot_highs(df: pd.DataFrame, n: int) -> list[tuple[int, float]]:
    """Return (bar_idx, price) for piviot highs: high > all n bars each side."""
    result = []
    highs = df["high"].values
    for i in range(n, len(highs) - n):
        if all(highs[i] > highs[i - j] for j in range(1, n + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, n + 1)):
            result.append((i, highs[i]))
    return result


def _pivot_lows(df: pd.DataFrame, n: int) -> list[tuple[int, float]]:
    """Return (bar_idx, price) for pivot lows."""
    result = []
    lows = df["low"].values
    for i in range(n, len(lows) - n):
        if all(lows[i] < lows[i - j] for j in range(1, n + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, n + 1)):
            result.append((i, lows[i]))
    return result


def _recency_weight(bar_idx: int, total_bars: int, half_life: int) -> float:
    """Exponential recency weight: bars closer to end weight more."""
    age = total_bars - 1 - bar_idx
    return math.exp(-age * math.log(2) / max(half_life, 1))


def compute_sr(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Compute S/R levels and zones for a candle DataFrame.

    Returns:
        levels: list of {price, kind, score, touches, last_touched (ISO str)}
        zones:  list of {kind, low, high, score}
    """
    p = {**_DEFAULT_PARAMS, **(params or {})}
    n = p["fractal_n"]
    tol = p["cluster_tol"]
    hl = p["recency_half"]
    max_lv = p["max_levels"]
    total = len(df)

    if total < 2 * n + 5:
        return {"levels": [], "zones": []}

    atr_val = float(atr(df, p["atr_period"]).iloc[-1])
    cluster_width = tol * atr_val
    current_price = float(df["close"].iloc[-1])

    # Gather all pivots
    pivots: list[_Level] = []
    for idx, price in _pivot_highs(df, n):
        w = _recency_weight(idx, total, hl)
        kind = "resistance" if price > current_price else "support"
        pivots.append(_Level(price=price, kind=kind, last_bar=idx, scores=[w]))

    for idx, price in _pivot_lows(df, n):
        w = _recency_weight(idx, total, hl)
        kind = "support" if price < current_price else "resistance"
        pivots.append(_Level(price=price, kind=kind, last_bar=idx, scores=[w]))

    if not pivots:
        return {"levels": [], "zones": []}

    # Cluster by price proximity
    pivots.sort(key=lambda x: x.price)
    clusters: list[_Level] = []
    for pv in pivots:
        merged = False
        for cl in reversed(clusters):
            if abs(cl.price - pv.price) <= cluster_width:
                # merge: weighted average price
                total_w = sum(cl.scores) + sum(pv.scores)
                cl.price = (cl.price * sum(cl.scores) + pv.price * sum(pv.scores)) / total_w
                cl.touches += pv.touches
                cl.last_bar = max(cl.last_bar, pv.last_bar)
                cl.scores.extend(pv.scores)
                merged = True
                break
        if not merged:
            clusters.append(_Level(
                price=pv.price,
                kind=pv.kind,
                touches=pv.touches,
                last_bar=pv.last_bar,
                scores=list(pv.scores),
            ))

    # Score = sum of recency-weighted touches
    def _score(cl: _Level) -> float:
        return round(sum(cl.scores), 4)

    clusters.sort(key=_score, reverse=True)
    clusters = clusters[:max_lv]

    # Build level dicts
    timestamps = df.index
    levels = []
    for cl in clusters:
        bar_idx = min(cl.last_bar, len(timestamps) - 1)
        last_ts = timestamps[bar_idx].isoformat() if hasattr(timestamps, "__getitem__") else ""
        levels.append({
            "price":        round(cl.price, 2),
            "kind":         cl.kind,
            "score":        _score(cl),
            "touches":      cl.touches,
            "last_touched": last_ts,
        })

    # Build zones: pair nearby support+resistance into bands
    zones = []
    for cl in clusters:
        band = cluster_width
        zones.append({
            "kind":  cl.kind,
            "low":   round(cl.price - band / 2, 2),
            "high":  round(cl.price + band / 2, 2),
            "score": _score(cl),
        })

    return {"levels": levels, "zones": zones}
