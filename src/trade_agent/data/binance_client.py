"""Binance USD-M Futures REST client.

Handles klines fetching with pagination, retry, and rate-limit awareness.
No API key required (public endpoints only).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterator

import requests

log = logging.getLogger(__name__)

_BASE = "https://fapi.binance.com"
_KLINES_ENDPOINT = "/fapi/v1/klines"
_MAX_LIMIT = 1500  # Binance max per request
_RETRY_DELAYS = [1, 3, 10]  # seconds between retries


def _ts_ms(dt: datetime) -> int:
    """Convert UTC datetime → milliseconds epoch."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000) if dt.tzinfo is None else int(dt.timestamp() * 1000)


def _parse_kline(raw: list) -> dict:
    """Convert Binance raw kline list to typed dict."""
    return {
        "open_time":    raw[0],        # int ms
        "open":         float(raw[1]),
        "high":         float(raw[2]),
        "low":          float(raw[3]),
        "close":        float(raw[4]),
        "volume":       float(raw[5]),
        "close_time":   raw[6],        # int ms
        "quote_volume": float(raw[7]),
        "trades":       int(raw[8]),
    }


class BinanceClient:
    """Thin wrapper around Binance USD-M Futures public klines API."""

    def __init__(self, base_url: str = _BASE, timeout: int = 30) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "trade-agent/0.2"})

    def get_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int | None = None,
        limit: int = _MAX_LIMIT,
    ) -> list[dict]:
        """Fetch up to `limit` klines starting from `start_ms`.

        Returns list of typed dicts. Raises on non-retriable HTTP errors.
        """
        params: dict = {
            "symbol":    symbol.upper(),
            "interval":  interval,
            "startTime": start_ms,
            "limit":     min(limit, _MAX_LIMIT),
        }
        if end_ms is not None:
            params["endTime"] = end_ms

        last_exc: Exception | None = None
        for attempt, delay in enumerate([0] + _RETRY_DELAYS, start=1):
            if delay:
                log.debug("Retry %d/%d — sleeping %ds", attempt, len(_RETRY_DELAYS) + 1, delay)
                time.sleep(delay)
            try:
                resp = self._session.get(
                    f"{self._base}{_KLINES_ENDPOINT}",
                    params=params,
                    timeout=self._timeout,
                )
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", delay or 10))
                    log.warning("Rate-limited — sleeping %ds", retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return [_parse_kline(k) for k in resp.json()]
            except requests.RequestException as exc:
                log.warning("Request failed (attempt %d): %s", attempt, exc)
                last_exc = exc

        raise RuntimeError(f"All retries exhausted for {symbol} {interval}") from last_exc

    def get_klines_paginated(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int = _MAX_LIMIT,
    ) -> Iterator[list[dict]]:
        """Yield batches of klines covering [start_ms, end_ms].

        Each yielded batch is sorted ascending by open_time.
        Stops when the last returned open_time reaches end_ms or no new data.
        """
        cursor = start_ms
        while cursor < end_ms:
            batch = self.get_klines(symbol, interval, cursor, end_ms, limit)
            if not batch:
                break
            yield batch
            last_open = batch[-1]["open_time"]
            if last_open <= cursor:
                break  # no progress guard
            # advance past the last candle
            cursor = last_open + 1
