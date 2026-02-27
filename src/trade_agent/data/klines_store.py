"""Flat-file Parquet storage for klines, one file per symbol+interval.

Storage layout:
    <root>/BTCUSDT_15m.parquet
    <root>/BTCUSDT_1h.parquet
    <root>/BTCUSDT_4h.parquet
    ...

Design:
- Single file per symbol/interval → fast load with pandas for any date range
- Dedup on open_time (ms int key, stored as UTC timestamp)
- Incremental append: read → merge → dedup → sort → write
- Schema enforced via pyarrow on every write
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)


def _to_utc_ts(value: str | datetime | pd.Timestamp) -> pd.Timestamp:
    """Convert str/datetime/Timestamp to UTC-aware pd.Timestamp safely."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


# ── Supported intervals ───────────────────────────────────────────────────────
SUPPORTED_INTERVALS: list[str] = ["15m", "1h", "4h", "1d", "1w", "1M"]

INTERVAL_MS: dict[str, int] = {
    "15m": 15 * 60 * 1_000,
    "1h": 60 * 60 * 1_000,
    "4h": 4 * 60 * 60 * 1_000,
    "1d": 24 * 60 * 60 * 1_000,
    "1w": 7 * 24 * 60 * 60 * 1_000,
    "1M": 30 * 24 * 60 * 60 * 1_000,  # approximate; used only for gap estimation
}

# ── Pyarrow schema ────────────────────────────────────────────────────────────
_SCHEMA = pa.schema(
    [
        pa.field("open_time", pa.timestamp("ms", tz="UTC")),
        pa.field("open", pa.float64()),
        pa.field("high", pa.float64()),
        pa.field("low", pa.float64()),
        pa.field("close", pa.float64()),
        pa.field("volume", pa.float64()),
        pa.field("close_time", pa.timestamp("ms", tz="UTC")),
        pa.field("quote_volume", pa.float64()),
        pa.field("trades", pa.int64()),
    ]
)


def _to_df(records: list[dict]) -> pd.DataFrame:
    """Convert raw kline dicts to a typed DataFrame."""
    df = pd.DataFrame(records)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[col] = df[col].astype("float64")
    df["trades"] = df["trades"].astype("int64")
    return df


class KlinesStore:
    """Read/write klines to flat Parquet files (one per symbol+interval)."""

    def __init__(self, root: str | Path = "data") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _filepath(self, symbol: str, interval: str) -> Path:
        return self.root / f"{symbol.upper()}_{interval}.parquet"

    # ── Write ─────────────────────────────────────────────────────────────────

    def append(self, symbol: str, interval: str, records: list[dict]) -> int:
        """Append records, dedup by open_time. Returns count of new rows written."""
        if not records:
            return 0

        incoming = _to_df(records)
        fpath = self._filepath(symbol, interval)

        if fpath.exists():
            existing = pd.read_parquet(fpath)
            merged = pd.concat([existing, incoming], ignore_index=True)
        else:
            merged = incoming

        before = len(merged)
        merged = (
            merged.drop_duplicates(subset=["open_time"])
            .sort_values("open_time")
            .reset_index(drop=True)
        )
        new_rows = len(merged) - (before - len(incoming))

        table = pa.Table.from_pandas(merged, schema=_SCHEMA)
        pq.write_table(table, fpath, compression="snappy")
        log.debug("Wrote %d rows → %s (new: %d)", len(merged), fpath.name, max(new_rows, 0))
        return max(new_rows, 0)

    # ── Read ──────────────────────────────────────────────────────────────────

    def read_range(
        self,
        symbol: str,
        interval: str,
        start: str | datetime | None = None,
        end: str | datetime | None = None,
    ) -> pd.DataFrame:
        """Load candles in [start, end]. Both are inclusive and optional."""
        fpath = self._filepath(symbol, interval)
        if not fpath.exists():
            return pd.DataFrame()

        df = pd.read_parquet(fpath)

        if start is not None:
            start_ts = _to_utc_ts(start)
            df = df[df["open_time"] >= start_ts]
        if end is not None:
            end_ts = _to_utc_ts(end)
            df = df[df["open_time"] <= end_ts]

        return df.reset_index(drop=True)

    # ── Metadata ──────────────────────────────────────────────────────────────

    def get_last_open_time(self, symbol: str, interval: str) -> datetime | None:
        """Return latest open_time stored, or None if no data yet."""
        fpath = self._filepath(symbol, interval)
        if not fpath.exists():
            return None
        df = pd.read_parquet(fpath, columns=["open_time"])
        if df.empty:
            return None
        return df["open_time"].max().to_pydatetime()

    def coverage(self, symbol: str, interval: str) -> dict:
        """Summary: first/last candle and total count."""
        fpath = self._filepath(symbol, interval)
        if not fpath.exists():
            return {
                "symbol": symbol,
                "interval": interval,
                "candles": 0,
                "first": None,
                "last": None,
            }
        df = pd.read_parquet(fpath, columns=["open_time"])
        if df.empty:
            return {
                "symbol": symbol,
                "interval": interval,
                "candles": 0,
                "first": None,
                "last": None,
            }
        return {
            "symbol": symbol,
            "interval": interval,
            "candles": len(df),
            "first": df["open_time"].min().isoformat(),
            "last": df["open_time"].max().isoformat(),
        }
