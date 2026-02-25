"""Unit tests for KlinesStore: append, dedup, read_range, metadata."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

from trade_agent.data.klines_store import KlinesStore, _to_df


def _make_record(open_time_ms: int, price: float = 30000.0) -> dict:
    return {
        "open_time":    open_time_ms,
        "open":         price,
        "high":         price * 1.001,
        "low":          price * 0.999,
        "close":        price * 1.0005,
        "volume":       10.0,
        "close_time":   open_time_ms + 59_999,
        "quote_volume": price * 10.0,
        "trades":       50,
    }


def _make_records(count: int, start_ms: int = 1_700_000_000_000) -> list[dict]:
    return [_make_record(start_ms + i * 60_000) for i in range(count)]


# ── _to_df ────────────────────────────────────────────────────────────────────

def test_to_df_types():
    records = _make_records(3)
    df = _to_df(records)
    assert df["open_time"].dtype.tz is not None  # timezone-aware
    assert df["open"].dtype == "float64"
    assert df["trades"].dtype == "int64"
    assert len(df) == 3


# ── append + dedup ────────────────────────────────────────────────────────────

def test_append_new(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(10)
    written = store.append("BTCUSDT", "1m", records)
    assert written == 10


def test_append_dedup(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(10)
    store.append("BTCUSDT", "1m", records)
    # Append same records again — should result in 0 new rows added
    written = store.append("BTCUSDT", "1m", records)
    assert written == 0


def test_append_partial_overlap(tmp_path):
    store = KlinesStore(tmp_path)
    first  = _make_records(10, start_ms=1_700_000_000_000)       # rows 0-9
    second = _make_records(10, start_ms=1_700_000_000_000 + 5 * 60_000)  # rows 5-14 (overlap 5-9)
    store.append("BTCUSDT", "1m", first)
    written = store.append("BTCUSDT", "1m", second)
    assert written == 5  # only rows 10-14 are new


# ── read_range ────────────────────────────────────────────────────────────────

def test_read_range_returns_all(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(100)
    store.append("BTCUSDT", "1m", records)

    df = store.read_range("BTCUSDT", "1m", "2023-11-14", "2023-11-16")
    assert len(df) == 100
    assert list(df.columns).count("open_time") == 1


def test_read_range_empty_when_no_data(tmp_path):
    store = KlinesStore(tmp_path)
    df = store.read_range("BTCUSDT", "1m", "2023-01-01")
    assert df.empty


def test_read_range_sorted_ascending(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(20)
    store.append("BTCUSDT", "1m", records)
    df = store.read_range("BTCUSDT", "1m", "2023-11-14", "2023-11-16")
    assert df["open_time"].is_monotonic_increasing


# ── metadata ──────────────────────────────────────────────────────────────────

def test_get_last_open_time(tmp_path):
    store = KlinesStore(tmp_path)
    assert store.get_last_open_time("BTCUSDT", "1m") is None

    records = _make_records(5, start_ms=1_700_000_000_000)
    store.append("BTCUSDT", "1m", records)

    last = store.get_last_open_time("BTCUSDT", "1m")
    expected_ms = 1_700_000_000_000 + 4 * 60_000
    assert last is not None
    assert int(last.timestamp() * 1000) == expected_ms


def test_coverage(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(60, start_ms=1_700_000_000_000)
    store.append("BTCUSDT", "1m", records)

    cov = store.coverage("BTCUSDT", "1m")
    assert cov["candles"] == 60
    assert cov["symbol"] == "BTCUSDT"
    assert cov["first"] is not None
