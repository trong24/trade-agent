"""Multi-TF bias chain: derive entry bias from higher timeframe trend.

Rules:
  15m entry → 1h bias
  1h  entry → 4h bias
  4h  entry → 1d bias
  Macro context: 1w/1M can override if strong

Usage:
  chain = compute_bias_chain(per_tf_facts)
  # chain["1h"] → {"bias": "short", "from_tf": "4h", "macro": "down"}
"""

from __future__ import annotations


# Entry TF → which TF provides directional bias
BIAS_MAP: dict[str, str] = {
    "15m": "1h",
    "1h": "4h",
    "4h": "1d",
}

# Timeframes that provide macro context (override if strong)
MACRO_TFS: list[str] = ["1w", "1M"]


def _trend_to_bias(trend_facts: dict) -> str:
    """Convert trend_dir → bias string: 'long', 'short', or 'neutral'."""
    d = trend_facts.get("trend_dir", "sideway")
    if d == "up":
        return "long"
    if d == "down":
        return "short"
    return "neutral"


def _macro_bias(per_tf_facts: dict) -> str:
    """Derive macro context from 1w/1M: 'long', 'short', or 'neutral'."""
    for tf in MACRO_TFS:
        tf_data = per_tf_facts.get(tf, {})
        trend = tf_data.get("trend", {})
        if not trend:
            continue
        # Only override if not sideway AND strength > 0.2
        if not trend.get("is_sideway", True) and trend.get("trend_strength", 0) > 0.2:
            return _trend_to_bias(trend)
    return "neutral"


def compute_bias_chain(per_tf_facts: dict) -> dict:
    """Compute bias chain for each entry timeframe.

    Args:
        per_tf_facts: {interval: {"trend": {...}, "sr": {...}}}

    Returns:
        {entry_tf: {"bias": str, "from_tf": str, "macro": str, "confidence": str}}
    """
    macro = _macro_bias(per_tf_facts)
    chain: dict[str, dict] = {}

    for entry_tf, bias_tf in BIAS_MAP.items():
        bias_data = per_tf_facts.get(bias_tf, {})
        bias_trend = bias_data.get("trend", {})

        if not bias_trend:
            bias = "neutral"
        else:
            bias = _trend_to_bias(bias_trend)

        # Macro override: if bias is neutral but macro is strong, adopt macro
        effective_bias = bias
        if bias == "neutral" and macro != "neutral":
            effective_bias = macro

        # Confidence: aligned = high, conflicting = low
        if bias == macro or macro == "neutral":
            confidence = "high"
        elif bias == "neutral":
            confidence = "medium"
        else:
            confidence = "low"  # bias and macro conflict

        chain[entry_tf] = {
            "bias": effective_bias,
            "from_tf": bias_tf,
            "macro": macro,
            "confidence": confidence,
        }

    return chain
