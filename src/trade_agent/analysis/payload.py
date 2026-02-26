"""Build multi-timeframe analysis payload with key levels and invalidation."""
from __future__ import annotations

from datetime import datetime


def build_payload(
    symbol: str,
    as_of: datetime,
    per_tf_facts: dict[str, dict],
) -> dict:
    """Combine per-TF trend+SR facts into a single analysis payload.

    Args:
        symbol:       e.g. 'BTCUSDT'
        as_of:        timestamp of analysis
        per_tf_facts: {interval: {'trend': {...}, 'sr': {...}}}

    Returns JSON-serializable dict with:
        - symbol, as_of
        - timeframes: full per-TF facts
        - key_levels: top S/R from 1w + 1d merged by proximity
        - invalidation: nearest support below & resistance above current price
    """
    # Current price: from the smallest interval with data
    priority = ["15m", "1h", "4h", "1d", "1w", "1M"]
    current_price: float | None = None
    for tf in priority:
        tf_data = per_tf_facts.get(tf, {})
        trend = tf_data.get("trend", {})
        if "ema_fast" in trend:
            # approximate current price from EMA fast (close)
            current_price = trend.get("ema_fast")
            break

    # Key levels: merge 1w + 1d + 4h SR levels
    senior_tfs = ["1w", "1d", "4h"]
    all_levels: list[dict] = []
    for tf in senior_tfs:
        tf_data = per_tf_facts.get(tf, {})
        sr = tf_data.get("sr", {})
        for lv in sr.get("levels", []):
            lv["source_tf"] = tf
            all_levels.append(lv)

    # Sort by score descending, deduplicate by price proximity (1% gap)
    all_levels.sort(key=lambda x: x.get("score", 0), reverse=True)
    key_levels: list[dict] = []
    for lv in all_levels:
        too_close = any(
            abs(lv["price"] - kl["price"]) / max(kl["price"], 1) < 0.01
            for kl in key_levels
        )
        if not too_close:
            key_levels.append(lv)
        if len(key_levels) >= 10:
            break

    # Invalidation levels
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
        "support_below":    supports_below[0]["price"] if supports_below else None,
        "resistance_above": resistances_above[0]["price"] if resistances_above else None,
    }

    # Higher-TF trend summary
    htf_trend = {}
    for tf in ["1w", "1d", "4h"]:
        tf_data = per_tf_facts.get(tf, {})
        trend = tf_data.get("trend", {})
        if trend:
            htf_trend[tf] = {
                "dir":      trend.get("trend_dir"),
                "strength": trend.get("trend_strength"),
                "sideway":  trend.get("is_sideway"),
            }

    return {
        "symbol":      symbol,
        "as_of":       as_of.isoformat(),
        "timeframes":  per_tf_facts,
        "key_levels":  key_levels,
        "invalidation": invalidation,
        "htf_trend":   htf_trend,
    }
