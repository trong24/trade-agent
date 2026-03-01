"""Unified backtest strategy: SR-Trend + RSI Inertia.

Merges sr_trend_v1 and rsi_inertia_v1 into a single module.

Modes (via generate_signals(..., mode=...)):
  sr_trend     : bias + S/R zone proximity
  rsi_inertia  : bias + RSI state-machine (momentum → correction → entry)
  combined     : RSI inertia timing filtered by S/R zone proximity (default)

Shared runner: run_vectorized_backtest()
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Bias / zone helpers
# ---------------------------------------------------------------------------


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
    """True if price is within zone_mult x zone_width of any zone."""
    for z in zones:
        level_price = z.get("price", 0)
        width = max(level_price * 0.005, 50.0)  # min 0.5% or $50
        if (level_price - zone_mult * width) <= price <= (level_price + zone_mult * width):
            return True
    return False


# ---------------------------------------------------------------------------
# RSI indicator helpers
# ---------------------------------------------------------------------------


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-smoothed RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _calc_wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)

    def _wma(x: np.ndarray) -> float:
        w = weights[-len(x) :]
        return float((x * w).sum() / w.sum())

    return series.rolling(window=period, min_periods=1).apply(_wma, raw=True)


def _detect_divergence(price: pd.Series, rsi: pd.Series, lookback: int = 10) -> pd.Series:
    """Detect price/RSI divergence.

    Returns:
        1 = bullish divergence (price lower-low, RSI higher-low)
       -1 = bearish divergence (price higher-high, RSI lower-high)
        0 = none
    """
    div = pd.Series(0, index=price.index, dtype=int)
    p = price.to_numpy()
    r = rsi.to_numpy()
    for i in range(lookback, len(p)):
        p_win = p[i - lookback : i + 1]
        r_win = r[i - lookback : i + 1]
        if p_win[-1] == p_win.max() and r_win[-1] < r_win.max():
            div.iloc[i] = -1
        elif p_win[-1] == p_win.min() and r_win[-1] > r_win.min():
            div.iloc[i] = 1
    return div


# ---------------------------------------------------------------------------
# RSI Inertia state machines
# ---------------------------------------------------------------------------


def _long_signals(
    rsi: pd.Series,
    rsi_ema: pd.Series,
    rsi_wma: pd.Series,
    div: pd.Series,
    p: dict,
) -> pd.Series:
    """State machine for LONG signals in an uptrend.

    States: idle → momentum → correction → ENTRY(+1) → hold → idle
    """
    n = len(rsi)
    pos_arr = np.zeros(n, dtype=np.int8)
    state = "idle"

    rsi_arr = rsi.to_numpy()
    ema_arr = rsi_ema.to_numpy()
    wma_arr = rsi_wma.to_numpy()
    div_arr = div.to_numpy()

    for i in range(1, n):
        rv = rsi_arr[i]
        ev = ema_arr[i]
        wv = wma_arr[i]
        rv_prev = rsi_arr[i - 1]
        ev_prev = ema_arr[i - 1]
        dv = div_arr[i]

        if state == "idle":
            if rv >= p["rsi_momentum_long"]:
                state = "momentum"

        elif state == "momentum":
            if rv >= p["rsi_momentum_long"]:
                pass
            elif rv < ev and rv < wv:
                state = "correction"
            elif rv < p["rsi_sideway_low"]:
                state = "idle"

        elif state == "correction":
            crosses_ema = rv > ev and rv_prev <= ev_prev
            above_wma = rv > wv
            if crosses_ema and above_wma:
                pos_arr[i] = 1
                state = "hold"
            elif rv < p["rsi_sideway_low"]:
                state = "idle"

        elif state == "hold":
            pos_arr[i] = 1
            if rv < p["rsi_sideway_low"]:
                pos_arr[i] = 0
                state = "idle"
            elif dv == -1 and rv < ev:
                pos_arr[i] = 0
                state = "momentum"
            elif rv >= p["rsi_momentum_long"]:
                state = "momentum"

    return pd.Series(pos_arr, index=rsi.index, dtype=int)


def _short_signals(
    rsi: pd.Series,
    rsi_ema: pd.Series,
    rsi_wma: pd.Series,
    div: pd.Series,
    p: dict,
) -> pd.Series:
    """State machine for SHORT signals in a downtrend (mirror of _long_signals)."""
    n = len(rsi)
    pos_arr = np.zeros(n, dtype=np.int8)
    state = "idle"

    rsi_arr = rsi.to_numpy()
    ema_arr = rsi_ema.to_numpy()
    wma_arr = rsi_wma.to_numpy()
    div_arr = div.to_numpy()

    for i in range(1, n):
        rv = rsi_arr[i]
        ev = ema_arr[i]
        wv = wma_arr[i]
        rv_prev = rsi_arr[i - 1]
        ev_prev = ema_arr[i - 1]
        dv = div_arr[i]

        if state == "idle":
            if rv <= p["rsi_momentum_short"]:
                state = "momentum"

        elif state == "momentum":
            if rv <= p["rsi_momentum_short"]:
                pass
            elif rv > ev and rv > wv:
                state = "correction"
            elif rv > p["rsi_sideway_high"]:
                state = "idle"

        elif state == "correction":
            crosses_ema = rv < ev and rv_prev >= ev_prev
            below_wma = rv < wv
            if crosses_ema and below_wma:
                pos_arr[i] = -1
                state = "hold"
            elif rv > p["rsi_sideway_high"]:
                state = "idle"

        elif state == "hold":
            pos_arr[i] = -1
            if rv > p["rsi_sideway_high"]:
                pos_arr[i] = 0
                state = "idle"
            elif dv == 1 and rv > ev:
                pos_arr[i] = 0
                state = "momentum"
            elif rv <= p["rsi_momentum_short"]:
                state = "momentum"

    return pd.Series(pos_arr, index=rsi.index, dtype=int)


# ---------------------------------------------------------------------------
# SR-trend signal generator
# ---------------------------------------------------------------------------


def _sr_trend_signals(
    df: pd.DataFrame,
    facts: dict | None,
    interval: str,
    p: dict,
) -> pd.Series:
    """Bias + S/R zone proximity signals."""
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

    return signals


# ---------------------------------------------------------------------------
# RSI-inertia signal generator
# ---------------------------------------------------------------------------


def _rsi_inertia_signals(
    df: pd.DataFrame,
    facts: dict | None,
    interval: str,
    p: dict,
) -> pd.Series:
    """Bias + RSI state-machine signals."""
    bias = _get_bias(facts, interval)
    close = df["close"]

    rsi = _calc_rsi(close, p["rsi_period"])
    rsi_ema = _calc_ema(rsi, p["ema_period"])
    rsi_wma = _calc_wma(rsi, p["wma_period"])
    div = _detect_divergence(close, rsi, p["div_lookback"])

    if bias == "long":
        return _long_signals(rsi, rsi_ema, rsi_wma, div, p)
    elif bias == "short":
        return _short_signals(rsi, rsi_ema, rsi_wma, div, p)
    return pd.Series(0, index=df.index, dtype=int)


# ---------------------------------------------------------------------------
# Combined signal generator
# ---------------------------------------------------------------------------


def _combined_signals(
    df: pd.DataFrame,
    facts: dict | None,
    interval: str,
    p: dict,
) -> pd.Series:
    """RSI inertia timing filtered by S/R zone proximity.

    Uses RSI state machine for entry/exit timing.  When S/R zones are
    available, entries are only allowed near a confirming zone (support
    for long, resistance for short).  Exits remain purely RSI-driven.

    If no zones exist, falls back to pure RSI inertia.
    """
    rsi_sig = _rsi_inertia_signals(df, facts, interval, p)

    support_zones = _get_zones(facts, "support")
    resistance_zones = _get_zones(facts, "resistance")

    # No zones available → pure RSI inertia
    if not support_zones and not resistance_zones:
        return rsi_sig

    bias = _get_bias(facts, interval)
    signals = rsi_sig.copy()

    if bias == "long" and support_zones:
        # Only allow NEW long entries near support
        near_support = df["close"].apply(
            lambda c: _price_near_zone(c, support_zones, p["zone_mult"])
        )
        # Detect entry bars: transition from 0 → 1
        entries = (rsi_sig == 1) & (rsi_sig.shift(1).fillna(0) != 1)
        # Block entries that aren't near support
        blocked = entries & ~near_support
        # For each blocked entry, zero out the entire hold run that follows
        for idx in signals.index[blocked]:
            loc = signals.index.get_loc(idx)
            for j in range(loc, len(signals)):
                if signals.iloc[j] == 1:
                    signals.iloc[j] = 0
                elif j > loc:
                    break

    elif bias == "short" and resistance_zones:
        near_resist = df["close"].apply(
            lambda c: _price_near_zone(c, resistance_zones, p["zone_mult"])
        )
        entries = (rsi_sig == -1) & (rsi_sig.shift(1).fillna(0) != -1)
        blocked = entries & ~near_resist
        for idx in signals.index[blocked]:
            loc = signals.index.get_loc(idx)
            for j in range(loc, len(signals)):
                if signals.iloc[j] == -1:
                    signals.iloc[j] = 0
                elif j > loc:
                    break

    return signals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEFAULT_PARAMS: dict = {
    # SR-trend
    "zone_mult": 1.5,
    # RSI inertia
    "rsi_period": 14,
    "ema_period": 9,
    "wma_period": 45,
    "rsi_momentum_long": 80,
    "rsi_momentum_short": 20,
    "rsi_sideway_low": 40,
    "rsi_sideway_high": 60,
    "div_lookback": 10,
}


def generate_signals(
    df: pd.DataFrame,
    facts: dict | None,
    interval: str = "1h",
    params: dict | None = None,
    mode: str = "combined",
) -> pd.Series:
    """Generate position signals (-1, 0, 1) per bar.

    Args:
        df:       UTC-indexed OHLCV DataFrame.
        facts:    dict from market_facts (interval='ALL').
        interval: candle interval being backtested.
        params:   parameter overrides.
        mode:     'sr_trend' | 'rsi_inertia' | 'combined' (default).

    Returns:
        pd.Series of int signals aligned to df.index, shifted 1 bar.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if mode == "sr_trend":
        signals = _sr_trend_signals(df, facts, interval, p)
    elif mode == "rsi_inertia":
        signals = _rsi_inertia_signals(df, facts, interval, p)
    else:
        signals = _combined_signals(df, facts, interval, p)

    # Shift 1 bar to avoid lookahead
    return signals.shift(1).fillna(0).astype(int)


def run_vectorized_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    fee_bps: float = 2.0,
) -> dict:
    """Run vectorized backtest. Returns metrics dict + trade_log."""
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

    sharpe = 0.0
    if net_returns.std() > 0:
        ann_factor = (365 * 24) ** 0.5
        sharpe = float(net_returns.mean() / net_returns.std() * ann_factor)

    # Build trade log
    trade_log: list[dict] = []
    current_trade: dict | None = None
    df_reset = df.reset_index()

    for i in range(1, len(df_reset)):
        prev_pos = pos.iloc[i - 1]
        curr_pos = pos.iloc[i]

        if curr_pos != prev_pos:
            if current_trade is not None:
                exit_price = float(df_reset["close"].iloc[i])
                entry_price = current_trade["entry_price"]
                side_mult = 1 if current_trade["side"] == "long" else -1
                raw_pnl = (exit_price - entry_price) / entry_price * side_mult
                net_pnl = raw_pnl - (fee_rate * 2)
                current_trade["exit"] = df_reset["open_time"].iloc[i].isoformat()
                current_trade["exit_price"] = exit_price
                current_trade["pnl_pct"] = round(net_pnl * 100, 4)
                current_trade["bars"] = i - current_trade.pop("_entry_idx")
                trade_log.append(current_trade)
                current_trade = None

            if curr_pos != 0:
                current_trade = {
                    "entry": df_reset["open_time"].iloc[i].isoformat(),
                    "side": "long" if curr_pos == 1 else "short",
                    "entry_price": float(df_reset["close"].iloc[i]),
                    "reason": "Signal Flip",
                    "_entry_idx": i,
                }

    metrics = {
        "total_return_pct": round(total_return, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "trades": len(trade_log),
        "bars": len(df),
    }

    return {"metrics": metrics, "trade_log": trade_log}
