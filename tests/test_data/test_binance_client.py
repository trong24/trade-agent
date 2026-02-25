"""Unit tests for BinanceClient: pagination, retry logic, parse."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_agent.data.binance_client import BinanceClient, _parse_kline, _ts_ms


def _make_raw_kline(open_time_ms: int = 1_700_000_000_000) -> list:
    """Return a minimal raw kline list as Binance returns it."""
    return [
        open_time_ms,          # 0 open_time
        "30000.5",             # 1 open
        "30100.0",             # 2 high
        "29900.0",             # 3 low
        "30050.0",             # 4 close
        "10.5",                # 5 volume
        open_time_ms + 59999,  # 6 close_time
        "315525.0",            # 7 quote_volume
        "123",                 # 8 trades
        "5.2", "156000.0", "0" # 9-11 (ignored)
    ]


# ── parse ─────────────────────────────────────────────────────────────────────

def test_parse_kline_types():
    raw = _make_raw_kline()
    k = _parse_kline(raw)
    assert isinstance(k["open_time"], int)
    assert isinstance(k["open"], float)
    assert isinstance(k["trades"], int)
    assert k["open"] == 30000.5
    assert k["high"] == 30100.0


def test_ts_ms_round_trip():
    from datetime import datetime, timezone
    dt = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    ms = _ts_ms(dt)
    assert ms == int(dt.timestamp() * 1000)


# ── get_klines ─────────────────────────────────────────────────────────────────

def _mock_response(data: list, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status}")
    return resp


def test_get_klines_success():
    raw = [_make_raw_kline(1_700_000_000_000 + i * 60_000) for i in range(3)]
    client = BinanceClient()

    with patch.object(client._session, "get", return_value=_mock_response(raw)):
        result = client.get_klines("BTCUSDT", "1m", 1_700_000_000_000)

    assert len(result) == 3
    assert result[0]["open"] == 30000.5


def test_get_klines_empty():
    client = BinanceClient()
    with patch.object(client._session, "get", return_value=_mock_response([])):
        result = client.get_klines("BTCUSDT", "1m", 1_700_000_000_000)
    assert result == []


def test_get_klines_pagination_stops_on_no_progress():
    """Pagination guard: if last open_time doesn't advance, stop."""
    ts = 1_700_000_000_000
    batch = [_make_raw_kline(ts)]  # one candle, stays at same ts

    client = BinanceClient()
    with patch.object(client._session, "get", return_value=_mock_response(batch)):
        batches = list(client.get_klines_paginated("BTCUSDT", "1m", ts, ts + 120_000))

    assert len(batches) == 1


def test_get_klines_paginated_advances():
    """Pagination yields multiple batches when open_time advances."""
    ts = 1_700_000_000_000
    batch1 = [_make_raw_kline(ts)]
    batch2 = [_make_raw_kline(ts + 60_000)]
    batch3: list = []  # sentinel: stop

    responses = [_mock_response(batch1), _mock_response(batch2), _mock_response(batch3)]
    client = BinanceClient()

    with patch.object(client._session, "get", side_effect=responses):
        batches = list(client.get_klines_paginated("BTCUSDT", "1m", ts, ts + 200_000))

    assert len(batches) == 2
