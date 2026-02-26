"""Trade Plan Builder: facts → actionable plan JSON.

Generates a structured trade plan from market_facts payload:
  - Scenarios (bull/base/bear)
  - Entry rules with zone proximity
  - ATR-based stop sizing
  - Zone-based take profit targets
  - Invalidation levels
  - No-trade conditions
"""
from __future__ import annotations

from datetime import datetime


def _get_current_price(facts: dict) -> float | None:
    """Extract current price approximation from facts payload."""
    for tf in ("15m", "1h", "4h", "1d"):
        trend = facts.get("timeframes", {}).get(tf, {}).get("trend", {})
        if "ema_fast" in trend:
            return trend["ema_fast"]
    return None


def _get_atr_pct(facts: dict, tf: str = "4h") -> float:
    """Get ATR% for a given timeframe."""
    trend = facts.get("timeframes", {}).get(tf, {}).get("trend", {})
    return trend.get("atr_pct", 2.0)


def _sorted_levels(facts: dict, kind: str) -> list[dict]:
    """Get key_levels sorted by price (asc for support, desc for resistance)."""
    levels = [lv for lv in facts.get("key_levels", []) if lv["kind"] == kind]
    reverse = kind == "resistance"
    return sorted(levels, key=lambda x: x["price"], reverse=reverse)


def build_plan(facts: dict, risk_params: dict | None = None) -> dict:
    """Build a trade plan from precomputed facts payload.

    Args:
        facts:       market_facts payload (interval='ALL')
        risk_params: override defaults {atr_stop_mult, min_rr, time_stop_bars, max_atr_pct}

    Returns:
        Plan dict with scenarios, entries, stops, targets, no_trade conditions.
    """
    rp = {
        "atr_stop_mult": 1.5,    # stop = entry ± mult × ATR
        "min_rr":        2.0,    # minimum reward:risk ratio
        "time_stop_bars": 20,    # exit if no movement after N bars
        "max_atr_pct":   8.0,    # no trade if ATR% > threshold (too volatile)
        "min_atr_pct":   0.3,    # no trade if ATR% < threshold (no movement)
        **(risk_params or {}),
    }

    price = _get_current_price(facts)
    if price is None:
        return {"error": "Cannot determine current price from facts"}

    regime = facts.get("regime", "ranging")
    bias_chain = facts.get("bias_chain", {})
    inv = facts.get("invalidation", {})
    atr_pct_4h = _get_atr_pct(facts, "4h")
    atr_pct_1d = _get_atr_pct(facts, "1d")

    supports = _sorted_levels(facts, "support")
    resistances = _sorted_levels(facts, "resistance")

    # ATR in absolute terms (approx)
    atr_abs = price * atr_pct_4h / 100

    # ── Scenarios ──────────────────────────────────────────────────────────
    bull_target = resistances[0]["price"] if resistances else price * 1.05
    bear_target = supports[0]["price"] if supports else price * 0.95

    scenarios = {
        "bull": {
            "condition": f"Break above {inv.get('bull_above', '?')}",
            "target": round(bull_target, 2),
            "probability": "medium" if regime == "uptrend" else "low",
        },
        "base": {
            "condition": f"Range between key S/R zones",
            "target": round(price, 2),
            "probability": "high" if regime == "ranging" else "medium",
        },
        "bear": {
            "condition": f"Break below {inv.get('bear_below', '?')}",
            "target": round(bear_target, 2),
            "probability": "medium" if regime == "downtrend" else "low",
        },
    }

    # ── Entry rules ────────────────────────────────────────────────────────
    # Determine primary bias from 1h entry (most common)
    primary_bias = bias_chain.get("1h", {}).get("bias", "neutral")

    entry_rules: list[dict] = []
    if primary_bias == "long" and supports:
        nearest_sup = supports[0]
        entry_rules.append({
            "type":      "long",
            "trigger":   f"Pullback to support zone {nearest_sup['price']:,.2f}",
            "zone":      nearest_sup,
            "condition": "Trend up on bias TF + wick rejection at zone",
        })
    elif primary_bias == "short" and resistances:
        nearest_res = resistances[0]
        entry_rules.append({
            "type":      "short",
            "trigger":   f"Rally to resistance zone {nearest_res['price']:,.2f}",
            "zone":      nearest_res,
            "condition": "Trend down on bias TF + rejection at zone",
        })
    else:
        entry_rules.append({
            "type":      "wait",
            "trigger":   "No clear bias — wait for trend alignment",
            "zone":      None,
            "condition": "Bias chain neutral or conflicting",
        })

    # ── Stops ──────────────────────────────────────────────────────────────
    stop_distance = round(atr_abs * rp["atr_stop_mult"], 2)
    if primary_bias == "long" and supports:
        stop_price = round(supports[0]["price"] - stop_distance, 2)
    elif primary_bias == "short" and resistances:
        stop_price = round(resistances[0]["price"] + stop_distance, 2)
    else:
        stop_price = round(price - stop_distance, 2)

    # ── Targets ────────────────────────────────────────────────────────────
    targets: list[dict] = []
    if primary_bias == "long":
        for i, lv in enumerate(resistances[:3]):
            rr = abs(lv["price"] - price) / max(abs(price - stop_price), 1)
            targets.append({
                "tp":    i + 1,
                "price": round(lv["price"], 2),
                "rr":    round(rr, 2),
                "source": lv.get("source_tf", "?"),
            })
    elif primary_bias == "short":
        for i, lv in enumerate(reversed(supports[:3])):
            rr = abs(price - lv["price"]) / max(abs(stop_price - price), 1)
            targets.append({
                "tp":    i + 1,
                "price": round(lv["price"], 2),
                "rr":    round(rr, 2),
                "source": lv.get("source_tf", "?"),
            })

    # ── No-trade conditions ────────────────────────────────────────────────
    no_trade: list[str] = []
    if atr_pct_4h > rp["max_atr_pct"]:
        no_trade.append(f"ATR% too high ({atr_pct_4h:.1f}% > {rp['max_atr_pct']}%) — extreme volatility")
    if atr_pct_4h < rp["min_atr_pct"]:
        no_trade.append(f"ATR% too low ({atr_pct_4h:.1f}% < {rp['min_atr_pct']}%) — no movement")
    if regime == "ranging":
        no_trade.append("Market ranging — only take highest-conviction setups")
    if primary_bias == "neutral":
        no_trade.append("Bias chain neutral — no directional edge")

    # ── Plan score (0..100) ────────────────────────────────────────────────
    score = _compute_plan_score(
        bias_chain, primary_bias, regime, entry_rules, targets,
        atr_pct_4h, rp, no_trade,
    )
    no_trade_flag = score < 30

    # ── Assemble plan ──────────────────────────────────────────────────────
    return {
        "symbol":         facts.get("symbol", "BTCUSDT"),
        "as_of":          facts.get("as_of"),
        "current_price":  round(price, 2),
        "regime":         regime,
        "primary_bias":   primary_bias,
        "bias_chain":     bias_chain,
        "scenarios":      scenarios,
        "entry_rules":    entry_rules,
        "stop": {
            "price":    stop_price,
            "distance": stop_distance,
            "method":   f"{rp['atr_stop_mult']}× ATR(4h)",
        },
        "targets":        targets,
        "invalidation":   inv,
        "risk_params": {
            "min_rr":         rp["min_rr"],
            "time_stop_bars": rp["time_stop_bars"],
        },
        "no_trade":       no_trade,
        "plan_score":     score,
        "no_trade_flag":  no_trade_flag,
    }


def _compute_plan_score(
    bias_chain: dict,
    primary_bias: str,
    regime: str,
    entry_rules: list,
    targets: list,
    atr_pct_4h: float,
    rp: dict,
    no_trade: list,
) -> int:
    """Score 0..100 based on evidence alignment.

    Breakdown:
      Bias alignment (0-30): all TFs agree = 30, partial = 15, neutral = 0
      Zone setup    (0-25): entry rule exists with zone = 25
      Targets R:R   (0-20): any TP with R:R >= min_rr = 20
      Volatility    (0-15): ATR% in sweet spot (1-5%) = 15
      No-trade      (-10 each): deductions
    """
    pts = 0

    # Bias alignment: check confidence across chain
    confidences = [v.get("confidence", "low") for v in bias_chain.values()]
    high_count = confidences.count("high")
    if primary_bias != "neutral":
        if high_count >= 2:
            pts += 30
        elif high_count >= 1:
            pts += 20
        else:
            pts += 10

    # Zone setup
    if entry_rules and entry_rules[0].get("zone"):
        pts += 25
    elif entry_rules and entry_rules[0].get("type") != "wait":
        pts += 10

    # Targets R:R
    good_targets = [t for t in targets if t.get("rr", 0) >= rp.get("min_rr", 2.0)]
    if good_targets:
        pts += 20
    elif targets:
        pts += 10

    # Volatility sweet spot
    if 1.0 <= atr_pct_4h <= 5.0:
        pts += 15
    elif 0.5 <= atr_pct_4h <= 8.0:
        pts += 8

    # No-trade deductions
    pts -= len(no_trade) * 10
    return max(0, min(100, pts))

