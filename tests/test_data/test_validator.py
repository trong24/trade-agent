"""Unit tests for validator: gap detection, schema check, quality_score."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from trade_agent.data.klines_store import _to_df
from trade_agent.data.validator import validate, ValidationReport


def _make_record(open_time_ms: int, price: float = 30000.0) -> dict:
    return {
        "open_time":    open_time_ms,
        "open":         price,
        "high":         price * 1.001,
        "low":          price * 0.999,
        "close":        price,
        "volume":       1.0,
        "close_time":   open_time_ms + 59_999,
        "quote_volume": price,
        "trades":       10,
    }


def _make_df(count: int, start_ms: int = 1_700_000_000_000, interval_ms: int = 60_000) -> pd.DataFrame:
    records = [_make_record(start_ms + i * interval_ms) for i in range(count)]
    return _to_df(records)


# ── perfect data ──────────────────────────────────────────────────────────────

def test_no_gaps_perfect_data():
    df = _make_df(100)
    report = validate(df, "BTCUSDT", "1m")
    assert report.quality_score == pytest.approx(1.0, abs=0.01)
    assert report.missing_gaps == []
    assert report.duplicate_count == 0


# ── gaps ──────────────────────────────────────────────────────────────────────

def test_detect_gap():
    """Insert a 30-candle gap in the middle."""
    start_ms = 1_700_000_000_000
    interval = 60_000
    records_a = [_make_record(start_ms + i * interval) for i in range(50)]
    # Jump 30 candles
    records_b = [_make_record(start_ms + (50 + 30 + i) * interval) for i in range(50)]
    df = _to_df(records_a + records_b)
    report = validate(df, "BTCUSDT", "1m", gap_threshold=5)
    assert len(report.missing_gaps) >= 1
    assert report.missing_gaps[0].missing_candles == 30
    assert report.quality_score < 1.0


def test_small_gap_below_threshold_not_reported():
    """Gaps <= gap_threshold should not appear in missing_gaps."""
    start_ms = 1_700_000_000_000
    interval = 60_000
    records_a = [_make_record(start_ms + i * interval) for i in range(10)]
    records_b = [_make_record(start_ms + (10 + 3 + i) * interval) for i in range(10)]  # 3 missing
    df = _to_df(records_a + records_b)
    report = validate(df, "BTCUSDT", "1m", gap_threshold=5)
    assert report.missing_gaps == []  # gap=3 < threshold=5


# ── duplicates ────────────────────────────────────────────────────────────────

def test_detect_duplicates():
    df = _make_df(10)
    df_with_dup = pd.concat([df, df.iloc[:3]], ignore_index=True)
    report = validate(df_with_dup, "BTCUSDT", "1m")
    assert report.duplicate_count == 3


# ── empty / unknown interval ──────────────────────────────────────────────────

def test_empty_df():
    df = pd.DataFrame()
    report = validate(df, "BTCUSDT", "1m")
    assert report.quality_score == 0.0
    assert report.total_candles == 0


def test_unknown_interval_reports_schema_error():
    df = _make_df(10)
    report = validate(df, "BTCUSDT", "99x")
    assert any("Unknown interval" in e for e in report.schema_errors)


# ── quality_score bounds ──────────────────────────────────────────────────────

def test_quality_score_bounded():
    df = _make_df(100)
    report = validate(df, "BTCUSDT", "1m")
    assert 0.0 <= report.quality_score <= 1.0


def test_is_ok():
    df = _make_df(100)
    report = validate(df, "BTCUSDT", "1m")
    assert report.is_ok(min_score=0.95)
    assert not report.is_ok(min_score=1.01)  # impossible threshold
