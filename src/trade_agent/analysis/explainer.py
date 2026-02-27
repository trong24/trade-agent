"""Explainability layer: generate human-readable evidence from facts.

Provides 5-10 lines explaining WHY a certain plan was generated.
LLM uses this to avoid hallucinating: every recommendation has evidence.
"""

from __future__ import annotations


def explain_plan(facts: dict, plan: dict) -> list[str]:
    """Generate evidence lines explaining the trade plan.

    Args:
        facts: market_facts payload (interval='ALL')
        plan:  output from build_plan()

    Returns:
        list of human-readable evidence strings.
    """
    lines: list[str] = []

    # 1. Regime
    regime = plan.get("regime", "?")
    lines.append(f"REGIME: Market is in {regime} mode")

    # 2. Trend per TF
    trends = facts.get("trends", {})
    for tf in ("1w", "1d", "4h", "1h"):
        t = trends.get(tf, {})
        if t:
            d = t.get("dir", "?")
            s = t.get("strength", 0)
            atr = t.get("atr_pct", 0)
            lines.append(f"  {tf}: {d} (strength={s:.2f}, ATR%={atr:.1f}%)")

    # 3. Bias chain
    chain = plan.get("bias_chain", {})
    if chain:
        parts = []
        for tf in ("15m", "1h", "4h"):
            b = chain.get(tf, {})
            if b:
                parts.append(f"{tf}→{b.get('bias', '?')}({b.get('from_tf', '?')})")
        if parts:
            lines.append(f"BIAS CHAIN: {' | '.join(parts)}")

    # 4. Primary bias reasoning
    bias = plan.get("primary_bias", "neutral")
    if bias == "long":
        lines.append(
            f"DIRECTION: Long bias — higher TF trending up, looking for pullback to support"
        )
    elif bias == "short":
        lines.append(
            f"DIRECTION: Short bias — higher TF trending down, looking for rally to resistance"
        )
    else:
        lines.append("DIRECTION: Neutral — no clear directional edge")

    # 5. Top S/R levels
    levels = facts.get("key_levels", [])
    sup = [lv for lv in levels if lv["kind"] == "support"][:2]
    res = [lv for lv in levels if lv["kind"] == "resistance"][:2]
    if sup:
        sup_str = ", ".join(
            f"{lv['price']:,.0f} (score={lv.get('score', 0):.1f}, src={lv.get('source_tf', '?')})"
            for lv in sup
        )
        lines.append(f"SUPPORT: {sup_str}")
    if res:
        res_str = ", ".join(
            f"{lv['price']:,.0f} (score={lv.get('score', 0):.1f}, src={lv.get('source_tf', '?')})"
            for lv in res
        )
        lines.append(f"RESISTANCE: {res_str}")

    # 6. Entry reasoning
    entries = plan.get("entry_rules", [])
    for e in entries:
        lines.append(f"ENTRY: {e.get('trigger', '?')} — {e.get('condition', '')}")

    # 7. Stop reasoning
    stop = plan.get("stop", {})
    lines.append(
        f"STOP: {stop.get('price', '?')} ({stop.get('method', '?')}, "
        f"distance={stop.get('distance', '?')})"
    )

    # 8. Target reasoning
    targets = plan.get("targets", [])
    for t in targets:
        lines.append(
            f"TARGET TP{t['tp']}: {t['price']:,.0f} "
            f"(R:R={t.get('rr', '?'):.1f}, from {t.get('source', '?')})"
        )

    # 9. No-trade conditions
    no_trade = plan.get("no_trade", [])
    for nt in no_trade:
        lines.append(f"⚠️ {nt}")

    return lines
