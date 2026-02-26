"""Vectorized technical indicators — pure pandas, no external TA library needed."""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int, adjust: bool = False) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=adjust).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range: max of (H-L, |H-prev_C|, |L-prev_C|).

    Args:
        df: DataFrame with columns high, low, close.
    """
    prev_close = df["close"].shift(1)
    hl = df["high"] - df["low"]
    hpc = (df["high"] - prev_close).abs()
    lpc = (df["low"] - prev_close).abs()
    return pd.concat([hl, hpc, lpc], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (EMA-smoothed as Wilder's)."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (0-100)."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))
