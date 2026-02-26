"""Tests for DuckDB upsert, trend computation, and S/R stability."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trade_agent.db import connect, init_db, read_candles, upsert_candles
from trade_agent.analysis.indicators import ema, atr, true_range
from trade_agent.analysis.trend import compute_trend
from trade_agent.analysis.sr import compute_sr


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_candle_df(
    n: int = 200,
    start: datetime | None = None,
    interval_h: int = 1,
    base_price: float = 30_000.0,
    trend: str = "up",
) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame with UTC DatetimeIndex."""
    start = start or datetime(2024, 1, 1, tzinfo=timezone.utc)
    idx = [start + timedelta(hours=interval_h * i) for i in range(n)]
    prices = []
    p = base_price
    for i in range(n):
        if trend == "up":
            p *= 1.001
        elif trend == "down":
            p *= 0.999
        else:
            p += (10 if i % 2 == 0 else -10)
        prices.append(p)

    df = pd.DataFrame(index=pd.DatetimeIndex(idx, tz="UTC"))
    df["open"]   = [p * 0.999 for p in prices]
    df["high"]   = [p * 1.002 for p in prices]
    df["low"]    = [p * 0.997 for p in prices]
    df["close"]  = prices
    df["volume"] = 100.0
    return df


def _df_to_upsert_records(df: pd.DataFrame, symbol: str = "BTCUSDT", interval: str = "1h") -> pd.DataFrame:
    """Convert synthetic df to the candles schema format."""
    out = df.reset_index().rename(columns={"index": "open_time"})
    out["symbol"]          = symbol
    out["interval"]        = interval
    out["close_time"]      = out["open_time"] + timedelta(hours=1)
    out["trades"]          = 50
    out["taker_buy_base"]  = 50.0
    out["taker_buy_quote"] = 50.0 * out["close"]
    return out


# ── DB Tests ──────────────────────────────────────────────────────────────────

def test_upsert_no_duplicates(tmp_path):
    """Inserting the same candles twice should yield exactly N rows."""
    db_path = tmp_path / "test.duckdb"
    con = connect(str(db_path))
    init_db(con)

    df = _make_candle_df(n=10)
    records = _df_to_upsert_records(df)

    upsert_candles(con, records)
    upsert_candles(con, records)  # duplicate

    result = con.execute(
        "SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT' AND interval='1h'"
    ).fetchone()[0]
    assert result == 10, f"Expected 10 rows, got {result}"
    con.close()


def test_upsert_incremental(tmp_path):
    """Appending non-overlapping candles should grow the table correctly."""
    db_path = tmp_path / "test.duckdb"
    con = connect(str(db_path))
    init_db(con)

    df1 = _make_candle_df(n=10)
    df2 = _make_candle_df(n=10, start=df1.index[-1] + timedelta(hours=1))

    upsert_candles(con, _df_to_upsert_records(df1))
    upsert_candles(con, _df_to_upsert_records(df2))

    count = con.execute(
        "SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT'"
    ).fetchone()[0]
    assert count == 20
    con.close()


def test_read_candles_utc_index(tmp_path):
    """read_candles should return a UTC DatetimeIndex."""
    db_path = tmp_path / "test.duckdb"
    con = connect(str(db_path))
    init_db(con)

    df = _make_candle_df(n=20)
    upsert_candles(con, _df_to_upsert_records(df))

    result = read_candles(con, "BTCUSDT", "1h")
    assert not result.empty
    assert result.index.tz is not None  # timezone-aware
    assert str(result.index.tz) in ("UTC", "utc", "+00:00")
    con.close()


# ── Trend Tests ───────────────────────────────────────────────────────────────

def test_trend_outputs_no_nan():
    """compute_trend should return all numeric values, no NaN."""
    df = _make_candle_df(n=200, trend="up")
    result = compute_trend(df)
    for key, val in result.items():
        if isinstance(val, float):
            assert not pd.isna(val), f"NaN detected in trend['{key}']"


def test_trend_up_direction():
    df = _make_candle_df(n=200, trend="up")
    result = compute_trend(df)
    assert result["trend_dir"] in ("up", "sideway"), \
        f"Expected up or sideway for uptrend data, got {result['trend_dir']}"


def test_trend_down_direction():
    df = _make_candle_df(n=200, trend="down")
    result = compute_trend(df)
    assert result["trend_dir"] in ("down", "sideway"), \
        f"Expected down or sideway for downtrend data, got {result['trend_dir']}"


def test_trend_strength_bounded():
    df = _make_candle_df(n=200, trend="up")
    result = compute_trend(df)
    assert 0.0 <= result["trend_strength"] <= 1.0


# ── S/R Tests ─────────────────────────────────────────────────────────────────

def test_sr_cluster_stable():
    """compute_sr on the same data twice should produce identical cluster count."""
    df = _make_candle_df(n=200, trend="sideway")
    r1 = compute_sr(df)
    r2 = compute_sr(df)
    assert len(r1["levels"]) == len(r2["levels"]), "S/R is not deterministic"


def test_sr_returns_expected_structure():
    df = _make_candle_df(n=200, trend="sideway")
    result = compute_sr(df)
    assert "levels" in result
    assert "zones" in result
    for lv in result["levels"]:
        assert "price" in lv
        assert lv["kind"] in ("support", "resistance")
        assert lv["touches"] >= 1
        assert lv["score"] >= 0


def test_sr_empty_on_too_few_bars():
    df = _make_candle_df(n=5)   # not enough bars for fractals
    result = compute_sr(df)
    assert result["levels"] == []
    assert result["zones"] == []
