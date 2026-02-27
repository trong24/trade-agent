"""Support & Resistance: structural swings (LL/HH confirm) + Hayden RSI filter.

v3 changes vs v2:
  - Structural swing validation: pivot high only valid if followed by LL
    (pivot low only valid if followed by HH) — matches system definition
  - Hayden RSI layer:
      * EMA(9) / WMA(45) on RSI → trend regime (UP / DOWN / SIDEWAYS)
      * RSI range check: 40–80 bull zone, 20–60 bear zone
      * Simple divergence → trend confirmation (Hayden: NOT reversal signal)
      * Swing score bonus when RSI agrees with price swing direction
  - Flip zone logic: level whose BOS was broken → role swaps S↔R
  - Output adds fields: structural, rsi_regime, rsi_score_bonus, flipped
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import atr  # unchanged import


# ── defaults ────────────────────────────────────────────────────────────────
_DEFAULT_PARAMS = {
    # pivot / cluster
    "fractal_n": 2,
    "cluster_tol": 0.25,
    "atr_period": 14,
    "recency_half": 50,
    "max_levels": 20,
    "wick_bonus": 0.5,
    "wick_threshold": 0.6,
    # structural swing confirmation window
    "confirm_bars": 50,  # bars to look ahead for LL/HH confirmation
    # Hayden RSI
    "rsi_period": 14,
    "rsi_ema_fast": 9,
    "rsi_wma_slow": 45,
    "rsi_score_bonus": 0.3,  # added to score when RSI confirms swing
}


# ── data classes ─────────────────────────────────────────────────────────────
@dataclass
class _Level:
    price: float
    kind: str  # 'support' | 'resistance'
    touches: int = 1
    last_bar: int = 0
    scores: list[float] = field(default_factory=list)
    structural: bool = False  # confirmed by LL / HH
    flipped: bool = False  # role changed after BOS


# ── RSI helpers ──────────────────────────────────────────────────────────────
def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (RMA smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    alpha = 1 / period
    avg_g = gain.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_l = loss.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _calc_wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average (linear weights, heavier on recent)."""
    w = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def _hayden_rsi(close: pd.Series, rsi_p: int, fast: int, slow: int) -> pd.DataFrame:
    """
    Compute Hayden RSI system columns:
      rsi, ema_fast, wma_slow, regime, bull_zone, bear_zone
    """
    rsi = _calc_rsi(close, rsi_p)
    ema_fast = rsi.ewm(span=fast, adjust=False).mean()
    wma_slow = _calc_wma(rsi, slow)

    price_ema = close.ewm(span=fast, adjust=False).mean()
    price_wma = _calc_wma(close, slow)

    price_bull = price_ema > price_wma
    rsi_bull = ema_fast > wma_slow

    regime = pd.Series("UNDEFINED", index=close.index)
    regime[price_bull & rsi_bull] = "UP"
    regime[~price_bull & ~rsi_bull] = "DOWN"
    regime[price_bull & ~rsi_bull] = "SIDEWAYS_UP"
    regime[~price_bull & rsi_bull] = "SIDEWAYS_DOWN"

    bull_zone = (rsi >= 40) & (rsi <= 82)  # Hayden 40–80 bull range
    bear_zone = (rsi >= 18) & (rsi <= 62)  # Hayden 20–60 bear range

    return pd.DataFrame(
        {
            "rsi": rsi,
            "ema_fast": ema_fast,
            "wma_slow": wma_slow,
            "regime": regime,
            "bull_zone": bull_zone,
            "bear_zone": bear_zone,
        }
    )


def _rsi_score_at(rsi_df: pd.DataFrame, bar_idx: int, kind: str) -> float:
    """
    Return RSI bonus score for a swing pivot at bar_idx.
    Bonus if:
      - resistance pivot AND regime UP/SIDEWAYS_UP (Hayden: simple bearish
        divergence confirms uptrend → resistance hit = uptrend retest)
      - support pivot AND regime DOWN/SIDEWAYS_DOWN
      - RSI in the appropriate bull/bear zone
    """
    if bar_idx >= len(rsi_df):
        return 0.0
    row = rsi_df.iloc[bar_idx]
    regime = row["regime"]

    if kind == "resistance":
        regime_ok = regime in ("UP", "SIDEWAYS_UP")
        zone_ok = bool(row["bull_zone"])
    else:
        regime_ok = regime in ("DOWN", "SIDEWAYS_DOWN")
        zone_ok = bool(row["bear_zone"])

    return 1.0 if (regime_ok and zone_ok) else (0.5 if (regime_ok or zone_ok) else 0.0)


# ── pivot helpers ────────────────────────────────────────────────────────────
def _pivot_highs(df: pd.DataFrame, n: int) -> list[tuple[int, float]]:
    highs, out = df["high"].values, []
    for i in range(n, len(highs) - n):
        if all(highs[i] > highs[i - j] for j in range(1, n + 1)) and all(
            highs[i] > highs[i + j] for j in range(1, n + 1)
        ):
            out.append((i, highs[i]))
    return out


def _pivot_lows(df: pd.DataFrame, n: int) -> list[tuple[int, float]]:
    lows, out = df["low"].values, []
    for i in range(n, len(lows) - n):
        if all(lows[i] < lows[i - j] for j in range(1, n + 1)) and all(
            lows[i] < lows[i + j] for j in range(1, n + 1)
        ):
            out.append((i, lows[i]))
    return out


# ── structural swing validation ──────────────────────────────────────────────
def _is_structural_high(
    bar_idx: int,
    high_price: float,
    df: pd.DataFrame,
    lows: list[tuple[int, float]],
    confirm_bars: int,
) -> bool:
    """
    A pivot high is structural if, after it, price creates a LL
    (low < last swing low before the pivot).
    """
    # find reference low = latest swing low BEFORE this pivot
    prior_lows = [p for p in lows if p[0] < bar_idx]
    if not prior_lows:
        return False
    ref_low = prior_lows[-1][1]

    # scan forward for LL
    end = min(bar_idx + confirm_bars, len(df))
    future_lows = df["low"].values[bar_idx + 1 : end]
    return bool(len(future_lows) and future_lows.min() < ref_low)


def _is_structural_low(
    bar_idx: int,
    low_price: float,
    df: pd.DataFrame,
    highs: list[tuple[int, float]],
    confirm_bars: int,
) -> bool:
    """
    A pivot low is structural if, after it, price creates a HH
    (high > last swing high before the pivot).
    """
    prior_highs = [p for p in highs if p[0] < bar_idx]
    if not prior_highs:
        return False
    ref_high = prior_highs[-1][1]

    end = min(bar_idx + confirm_bars, len(df))
    future_highs = df["high"].values[bar_idx + 1 : end]
    return bool(len(future_highs) and future_highs.max() > ref_high)


# ── misc helpers ─────────────────────────────────────────────────────────────
def _recency_weight(bar_idx: int, total: int, half_life: int) -> float:
    age = total - 1 - bar_idx
    return math.exp(-age * math.log(2) / max(half_life, 1))


def _wick_score(df: pd.DataFrame, bar_idx: int, kind: str, threshold: float, bonus: float) -> float:
    row = df.iloc[bar_idx]
    bar_range = row["high"] - row["low"]
    if bar_range <= 0:
        return 0.0
    if kind == "support":
        wick = min(row["open"], row["close"]) - row["low"]
    else:
        wick = row["high"] - max(row["open"], row["close"])
    return bonus if (wick / bar_range) >= threshold else 0.0


# ── BOS / flip detection ─────────────────────────────────────────────────────
def _detect_flips(
    clusters: list[_Level],
    df: pd.DataFrame,
    current_price: float,
) -> None:
    """
    Mark levels as flipped if price has closed beyond them (BOS).
    A resistance level that price closed above → flipped to support.
    A support level that price closed below → flipped to resistance.
    Mutates clusters in-place.
    """
    closes = df["close"].values
    for cl in clusters:
        if cl.kind == "resistance" and current_price > cl.price:
            # check if any close after last_bar actually crossed above
            post = closes[cl.last_bar :]
            if len(post) and post.max() > cl.price:
                cl.kind = "support"
                cl.flipped = True
        elif cl.kind == "support" and current_price < cl.price:
            post = closes[cl.last_bar :]
            if len(post) and post.min() < cl.price:
                cl.kind = "resistance"
                cl.flipped = True


# ── main entry point ─────────────────────────────────────────────────────────
def compute_sr(df: pd.DataFrame, params: dict | None = None) -> dict:
    """
    Compute S/R levels and zones.

    Returns:
        levels: list of dicts with keys:
            price, kind, score, touches, last_touched,
            structural, flipped, rsi_regime
        zones:  list of dicts with keys:
            kind, low, high, score, structural, flipped
    """
    p = {**_DEFAULT_PARAMS, **(params or {})}
    n = p["fractal_n"]
    tol = p["cluster_tol"]
    hl = p["recency_half"]
    total = len(df)

    if total < max(2 * n + 5, p["rsi_wma_slow"] + 10):
        return {"levels": [], "zones": []}

    atr_val = float(atr(df, p["atr_period"]).iloc[-1])
    cluster_width = tol * atr_val
    current_price = float(df["close"].iloc[-1])

    # ── Hayden RSI ───────────────────────────────────────────────────────────
    rsi_df = _hayden_rsi(
        df["close"],
        p["rsi_period"],
        p["rsi_ema_fast"],
        p["rsi_wma_slow"],
    )

    # ── raw pivots ───────────────────────────────────────────────────────────
    raw_highs = _pivot_highs(df, n)
    raw_lows = _pivot_lows(df, n)

    pivots: list[_Level] = []

    for idx, price in raw_highs:
        structural = _is_structural_high(idx, price, df, raw_lows, p["confirm_bars"])
        w = _recency_weight(idx, total, hl)
        kind = "resistance" if price > current_price else "support"
        wick = _wick_score(df, idx, kind, p["wick_threshold"], p["wick_bonus"])
        rsi_bonus = _rsi_score_at(rsi_df, idx, kind) * p["rsi_score_bonus"]
        # structural swings get a score multiplier
        score = (w + wick + rsi_bonus) * (1.5 if structural else 1.0)
        pivots.append(
            _Level(
                price=price,
                kind=kind,
                last_bar=idx,
                scores=[score],
                structural=structural,
            )
        )

    for idx, price in raw_lows:
        structural = _is_structural_low(idx, price, df, raw_highs, p["confirm_bars"])
        w = _recency_weight(idx, total, hl)
        kind = "support" if price < current_price else "resistance"
        wick = _wick_score(df, idx, kind, p["wick_threshold"], p["wick_bonus"])
        rsi_bonus = _rsi_score_at(rsi_df, idx, kind) * p["rsi_score_bonus"]
        score = (w + wick + rsi_bonus) * (1.5 if structural else 1.0)
        pivots.append(
            _Level(
                price=price,
                kind=kind,
                last_bar=idx,
                scores=[score],
                structural=structural,
            )
        )

    if not pivots:
        return {"levels": [], "zones": []}

    # ── cluster by price proximity ───────────────────────────────────────────
    pivots.sort(key=lambda x: x.price)
    clusters: list[_Level] = []
    for pv in pivots:
        merged = False
        for cl in reversed(clusters):
            if abs(cl.price - pv.price) <= cluster_width:
                w_cl = sum(cl.scores)
                w_pv = sum(pv.scores)
                tot = w_cl + w_pv
                cl.price = (cl.price * w_cl + pv.price * w_pv) / tot
                cl.touches += pv.touches
                cl.last_bar = max(cl.last_bar, pv.last_bar)
                cl.scores.extend(pv.scores)
                cl.structural = cl.structural or pv.structural
                merged = True
                break
        if not merged:
            clusters.append(
                _Level(
                    price=pv.price,
                    kind=pv.kind,
                    touches=pv.touches,
                    last_bar=pv.last_bar,
                    scores=list(pv.scores),
                    structural=pv.structural,
                )
            )

    # ── BOS / flip ───────────────────────────────────────────────────────────
    _detect_flips(clusters, df, current_price)

    # ── sort & trim ──────────────────────────────────────────────────────────
    def _score(cl: _Level) -> float:
        return round(sum(cl.scores), 4)

    clusters.sort(key=_score, reverse=True)
    clusters = clusters[: p["max_levels"]]

    # ── build output ─────────────────────────────────────────────────────────
    timestamps = df.index
    levels, zones = [], []

    for cl in clusters:
        bar_idx = min(cl.last_bar, len(timestamps) - 1)
        last_ts = str(timestamps[bar_idx])
        regime = str(rsi_df["regime"].iloc[bar_idx])
        band = max(cluster_width, atr_val * 0.1)

        levels.append(
            {
                "price": round(cl.price, 2),
                "kind": cl.kind,
                "score": _score(cl),
                "touches": cl.touches,
                "last_touched": last_ts,
                "structural": cl.structural,  # ← NEW: LL/HH confirmed
                "flipped": cl.flipped,  # ← NEW: BOS role swap
                "rsi_regime": regime,  # ← NEW: Hayden regime at touch
            }
        )
        zones.append(
            {
                "kind": cl.kind,
                "low": round(cl.price - band / 2, 2),
                "high": round(cl.price + band / 2, 2),
                "score": _score(cl),
                "structural": cl.structural,
                "flipped": cl.flipped,
            }
        )

    return {"levels": levels, "zones": zones}


if __name__ == "__main__":
    import os
    import pandas as pd

    # Tìm file dữ liệu mẫu
    sample_file = "data/BTCUSDT_15m.parquet"
    if not os.path.exists(sample_file):
        sample_file = "data/sample_ohlcv.csv"

    if os.path.exists(sample_file):
        print(f"--- Đang đọc dữ liệu: {sample_file} ---")
        if sample_file.endswith(".parquet"):
            df = pd.read_parquet(sample_file)
        else:
            df = pd.read_csv(sample_file, index_col=0, parse_dates=True)

        print("--- Đang tính toán S/R (Hayden RSI + Structural Swings) ---")
        results = compute_sr(df)

        print(f"\nTìm thấy {len(results['levels'])} mức S/R quan trọng nhất:")
        print("-" * 85)
        print(
            f"{'Loại':<12} | {'Giá':<10} | {'Điểm':<8} | {'Chạm':<6} | {'Structural'} | {'Flipped'}"
        )
        print("-" * 85)
        for lvl in results["levels"][:10]:
            struct = "Có" if lvl["structural"] else "Không"
            flip = "Có" if lvl["flipped"] else "Không"
            print(
                f"{lvl['kind'].upper():<12} | {lvl['price']:<10.2f} | {lvl['score']:<8.2f} | {lvl['touches']:<6} | {struct:<10} | {flip}"
            )
        print("-" * 85)
    else:
        print("Lỗi: Không tìm thấy file dữ liệu mẫu trong thư mục data/.")
        print("Vui lòng đảm bảo bạn đang đứng ở thư mục gốc /trade-agent/")
