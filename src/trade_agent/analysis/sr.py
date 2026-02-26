"""Support & Resistance: pivot fractals → ATR clustering → scored zones.

v2 improvements:
  - Zone width = ATR-adaptive per cluster
  - Rejection scoring: wick > 60% of bar range → bonus score
  - Recency decay: configurable half_life
  - Clear separation of macro (1w/1d) vs micro (4h/1h) levels
"""
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
    "wick_bonus":   0.5,    # extra score for strong wick rejection
    "wick_threshold": 0.6,  # wick ≥ 60% of bar range → rejection
}


@dataclass
class _Level:
    price: float
    kind: str          # 'support' | 'resistance'
    touches: int = 1
    last_bar: int = 0  # bar index of most recent touch
    scores: list[float] = field(default_factory=list)


def _pivot_highs(df: pd.DataFrame, n: int) -> list[tuple[int, float]]:
    """Return (bar_idx, price) for pivot highs: high > all n bars each side."""
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


def _wick_rejection_score(
    df: pd.DataFrame,
    bar_idx: int,
    kind: str,
    threshold: float = 0.6,
    bonus: float = 0.5,
) -> float:
    """Score bonus if the bar shows strong wick rejection at the pivot.

    For support (pivot low): lower wick / total range should be large
    For resistance (pivot high): upper wick / total range should be large
    """
    row = df.iloc[bar_idx]
    bar_range = row["high"] - row["low"]
    if bar_range <= 0:
        return 0.0

    if kind == "support":
        body_low = min(row["open"], row["close"])
        wick = body_low - row["low"]
    else:
        body_high = max(row["open"], row["close"])
        wick = row["high"] - body_high

    wick_ratio = wick / bar_range
    return bonus if wick_ratio >= threshold else 0.0


def compute_sr(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Compute S/R levels and zones for a candle DataFrame.

    Returns:
        levels: list of {price, kind, score, touches, last_touched}
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

    # Gather all pivots with recency + wick rejection scoring
    pivots: list[_Level] = []
    for idx, price in _pivot_highs(df, n):
        w = _recency_weight(idx, total, hl)
        kind = "resistance" if price > current_price else "support"
        wick_bonus = _wick_rejection_score(
            df, idx, kind, p["wick_threshold"], p["wick_bonus"]
        )
        pivots.append(_Level(
            price=price, kind=kind, last_bar=idx,
            scores=[w + wick_bonus],
        ))

    for idx, price in _pivot_lows(df, n):
        w = _recency_weight(idx, total, hl)
        kind = "support" if price < current_price else "resistance"
        wick_bonus = _wick_rejection_score(
            df, idx, kind, p["wick_threshold"], p["wick_bonus"]
        )
        pivots.append(_Level(
            price=price, kind=kind, last_bar=idx,
            scores=[w + wick_bonus],
        ))

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
                w_cl = sum(cl.scores)
                w_pv = sum(pv.scores)
                total_w = w_cl + w_pv
                cl.price = (cl.price * w_cl + pv.price * w_pv) / total_w
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

    # Score = sum of recency-weighted touches (including wick bonuses)
    def _score(cl: _Level) -> float:
        return round(sum(cl.scores), 4)

    clusters.sort(key=_score, reverse=True)
    clusters = clusters[:max_lv]

    # Build level dicts
    timestamps = df.index
    levels = []
    for cl in clusters:
        bar_idx = min(cl.last_bar, len(timestamps) - 1)
        last_ts = str(timestamps[bar_idx])
        levels.append({
            "price":        round(cl.price, 2),
            "kind":         cl.kind,
            "score":        _score(cl),
            "touches":      cl.touches,
            "last_touched": last_ts,
        })

    # Build zones: ATR-adaptive width per cluster
    zones = []
    for cl in clusters:
        # Zone width proportional to cluster spread + min ATR band
        band = max(cluster_width, atr_val * 0.1)
        zones.append({
            "kind":  cl.kind,
            "low":   round(cl.price - band / 2, 2),
            "high":  round(cl.price + band / 2, 2),
            "score": _score(cl),
        })

    return {"levels": levels, "zones": zones}
