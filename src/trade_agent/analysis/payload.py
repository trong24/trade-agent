"""Build multi-timeframe analysis payload with bias chain, key levels, invalidation.

v2: macro/micro level merge, bias chain, regime classification.
"""
from __future__ import annotations

from datetime import datetime

from .bias import compute_bias_chain


def _classify_regime(per_tf_facts: dict) -> str:
    """Classify overall market regime from higher TFs."""
    for tf in ("1d", "4h"):
        trend = per_tf_facts.get(tf, {}).get("trend", {})
        if not trend:
            continue
        if trend.get("is_sideway"):
            continue
        d = trend.get("trend_dir", "sideway")
        if d == "up":
            return "uptrend"
        if d == "down":
            return "downtrend"
    return "ranging"


def _merge_levels(per_tf_facts: dict, max_levels: int = 5) -> list[dict]:
    """Merge S/R levels across timeframes with macro/micro weighting.

    Macro TFs (1w, 1d): score × 2.0
    Micro TFs (4h, 1h, 15m): score × 1.0
    Dedup within 1% proximity → keep highest scored.
    """
    MACRO_WEIGHT = {"1w": 2.5, "1M": 2.5, "1d": 2.0}
    all_levels: list[dict] = []

    for tf in ("1w", "1M", "1d", "4h", "1h", "15m"):
        tf_data = per_tf_facts.get(tf, {})
        sr = tf_data.get("sr", {})
        weight = MACRO_WEIGHT.get(tf, 1.0)
        for lv in sr.get("levels", []):
            all_levels.append({
                "price":    lv["price"],
                "kind":     lv["kind"],
                "score":    round(lv.get("score", 0) * weight, 4),
                "touches":  lv.get("touches", 0),
                "source_tf": tf,
            })

    # Sort by weighted score desc
    all_levels.sort(key=lambda x: x["score"], reverse=True)

    # Dedup within 1% proximity
    merged: list[dict] = []
    for lv in all_levels:
        too_close = any(
            abs(lv["price"] - m["price"]) / max(m["price"], 1) < 0.01
            for m in merged
        )
        if not too_close:
            merged.append(lv)
        if len(merged) >= max_levels:
            break

    return merged


def build_payload(
    symbol: str,
    as_of: datetime,
    per_tf_facts: dict[str, dict],
) -> dict:
    """Combine per-TF trend+SR facts into a single LLM-ready payload.

    Returns JSON-serializable dict with:
        symbol, as_of, regime, bias_chain, trends, key_levels, invalidation
    """
    # Current price approximation
    current_price: float | None = None
    for tf in ("15m", "1h", "4h", "1d", "1w"):
        trend = per_tf_facts.get(tf, {}).get("trend", {})
        if "ema_fast" in trend:
            current_price = trend["ema_fast"]
            break

    # Regime
    regime = _classify_regime(per_tf_facts)

    # Bias chain
    bias_chain = compute_bias_chain(per_tf_facts)

    # Trend summary per TF
    trends: dict = {}
    for tf in ("1w", "1M", "1d", "4h", "1h", "15m"):
        trend = per_tf_facts.get(tf, {}).get("trend", {})
        if trend:
            trends[tf] = {
                "dir":      trend.get("trend_dir"),
                "strength": trend.get("trend_strength"),
                "atr_pct":  trend.get("atr_pct"),
                "sideway":  trend.get("is_sideway"),
            }

    # Key levels (macro/micro merged)
    key_levels = _merge_levels(per_tf_facts, max_levels=5)

    # Invalidation
    supports_below = sorted(
        [lv for lv in key_levels if lv["kind"] == "support"
         and current_price and lv["price"] < current_price],
        key=lambda x: x["price"], reverse=True,
    )
    resistances_above = sorted(
        [lv for lv in key_levels if lv["kind"] == "resistance"
         and current_price and lv["price"] > current_price],
        key=lambda x: x["price"],
    )

    invalidation = {
        "bull_above": resistances_above[0]["price"] if resistances_above else None,
        "bear_below": supports_below[0]["price"] if supports_below else None,
    }

    return {
        "symbol":       symbol,
        "as_of":        as_of.isoformat(),
        "regime":       regime,
        "bias_chain":   bias_chain,
        "trends":       trends,
        "key_levels":   key_levels,
        "invalidation": invalidation,
        "timeframes":   per_tf_facts,
    }
