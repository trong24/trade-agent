"""Unit tests for KlinesStore (flat file storage)."""

from __future__ import annotations

from trade_agent.data.klines_store import KlinesStore, _to_df


def _make_record(open_time_ms: int, price: float = 30000.0) -> dict:
    return {
        "open_time": open_time_ms,
        "open": price,
        "high": price * 1.001,
        "low": price * 0.999,
        "close": price * 1.0005,
        "volume": 10.0,
        "close_time": open_time_ms + 59_999,
        "quote_volume": price * 10.0,
        "trades": 50,
    }


def _make_records(
    count: int, start_ms: int = 1_700_000_000_000, step_ms: int = 900_000
) -> list[dict]:
    """Default step = 15m (900_000 ms)."""
    return [_make_record(start_ms + i * step_ms) for i in range(count)]


# ── _to_df ────────────────────────────────────────────────────────────────────


def test_to_df_types():
    df = _to_df(_make_records(3))
    assert df["open_time"].dtype.tz is not None  # timezone-aware
    assert df["open"].dtype == "float64"
    assert df["trades"].dtype == "int64"
    assert len(df) == 3


# ── flat file path ────────────────────────────────────────────────────────────


def test_filepath_naming(tmp_path):
    store = KlinesStore(tmp_path)
    p = store._filepath("BTCUSDT", "15m")
    assert p.name == "BTCUSDT_15m.parquet"
    assert p.parent == tmp_path


# ── append + dedup ────────────────────────────────────────────────────────────


def test_append_new(tmp_path):
    store = KlinesStore(tmp_path)
    written = store.append("BTCUSDT", "15m", _make_records(10))
    assert written == 10
    assert (tmp_path / "BTCUSDT_15m.parquet").exists()


def test_append_dedup_same_records(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(10)
    store.append("BTCUSDT", "15m", records)
    written = store.append("BTCUSDT", "15m", records)
    assert written == 0


def test_append_partial_overlap(tmp_path):
    store = KlinesStore(tmp_path)
    first = _make_records(10, start_ms=1_700_000_000_000)
    second = _make_records(10, start_ms=1_700_000_000_000 + 5 * 900_000)  # overlap 5
    store.append("BTCUSDT", "15m", first)
    written = store.append("BTCUSDT", "15m", second)
    assert written == 5


def test_intervals_stored_separately(tmp_path):
    store = KlinesStore(tmp_path)
    store.append("BTCUSDT", "15m", _make_records(5))
    store.append("BTCUSDT", "1h", _make_records(5, step_ms=3_600_000))
    assert (tmp_path / "BTCUSDT_15m.parquet").exists()
    assert (tmp_path / "BTCUSDT_1h.parquet").exists()


# ── read_range ────────────────────────────────────────────────────────────────


def test_read_range_all(tmp_path):
    store = KlinesStore(tmp_path)
    store.append("BTCUSDT", "15m", _make_records(20))
    df = store.read_range("BTCUSDT", "15m")
    assert len(df) == 20
    assert df["open_time"].is_monotonic_increasing


def test_read_range_filtered(tmp_path):
    store = KlinesStore(tmp_path)
    store.append("BTCUSDT", "15m", _make_records(100))
    df_all = store.read_range("BTCUSDT", "15m")
    mid = df_all["open_time"].iloc[50]
    df_half = store.read_range("BTCUSDT", "15m", start=mid)
    assert len(df_half) == 50


def test_read_range_empty(tmp_path):
    store = KlinesStore(tmp_path)
    df = store.read_range("BTCUSDT", "15m")
    assert df.empty


# ── metadata ──────────────────────────────────────────────────────────────────


def test_get_last_open_time_none(tmp_path):
    store = KlinesStore(tmp_path)
    assert store.get_last_open_time("BTCUSDT", "15m") is None


def test_get_last_open_time_value(tmp_path):
    store = KlinesStore(tmp_path)
    records = _make_records(5, start_ms=1_700_000_000_000)
    store.append("BTCUSDT", "15m", records)
    last = store.get_last_open_time("BTCUSDT", "15m")
    expected_ms = 1_700_000_000_000 + 4 * 900_000
    assert last is not None
    assert int(last.timestamp() * 1000) == expected_ms


def test_coverage(tmp_path):
    store = KlinesStore(tmp_path)
    store.append("BTCUSDT", "1h", _make_records(24, step_ms=3_600_000))
    cov = store.coverage("BTCUSDT", "1h")
    assert cov["candles"] == 24
    assert cov["first"] is not None
    assert cov["last"] is not None
