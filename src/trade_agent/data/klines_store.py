"""Parquet-based klines storage with daily partitioning, dedup, and incremental append.

Partition layout:
  <root>/klines/symbol=<SYMBOL>/interval=<INTERVAL>/date=<YYYY-MM-DD>/data.parquet
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────
_SCHEMA = pa.schema(
    [
        pa.field("open_time",    pa.timestamp("ms", tz="UTC")),
        pa.field("open",         pa.float64()),
        pa.field("high",         pa.float64()),
        pa.field("low",          pa.float64()),
        pa.field("close",        pa.float64()),
        pa.field("volume",       pa.float64()),
        pa.field("close_time",   pa.timestamp("ms", tz="UTC")),
        pa.field("quote_volume", pa.float64()),
        pa.field("trades",       pa.int64()),
    ]
)

_INTERVAL_MS: dict[str, int] = {
    "1m":  60_000,
    "3m":  180_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
}


def _to_df(records: list[dict]) -> pd.DataFrame:
    """Convert raw kline dicts → typed DataFrame."""
    df = pd.DataFrame(records)
    df["open_time"]  = pd.to_datetime(df["open_time"],  unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "quote_volume"):
        df[col] = df[col].astype("float64")
    df["trades"] = df["trades"].astype("int64")
    return df


class KlinesStore:
    """Read/write Binance klines to daily Parquet partitions."""

    def __init__(self, root: str | Path = "data/raw") -> None:
        self.root = Path(root)

    # ── Paths ─────────────────────────────────────────────────────────────────

    def _partition_dir(self, symbol: str, interval: str, day: date) -> Path:
        return self.root / "klines" / f"symbol={symbol.upper()}" / f"interval={interval}" / f"date={day.isoformat()}"

    def _partition_file(self, symbol: str, interval: str, day: date) -> Path:
        return self._partition_dir(symbol, interval, day) / "data.parquet"

    # ── Write ─────────────────────────────────────────────────────────────────

    def append(self, symbol: str, interval: str, records: list[dict]) -> int:
        """Append records to the store. Returns number of new candles written."""
        if not records:
            return 0

        incoming = _to_df(records)
        # group by UTC date so each batch may touch multiple partitions
        incoming["_date"] = incoming["open_time"].dt.date
        total_new = 0

        for day, group in incoming.groupby("_date"):
            group = group.drop(columns=["_date"])
            pfile = self._partition_file(symbol, interval, day)  # type: ignore[arg-type]

            if pfile.exists():
                existing = pd.read_parquet(pfile)
                merged = pd.concat([existing, group], ignore_index=True)
            else:
                merged = group

            before = len(merged)
            merged = merged.drop_duplicates(subset=["open_time"]).sort_values("open_time")
            new_rows = len(merged) - (before - len(group))
            total_new += max(new_rows, 0)

            pfile.parent.mkdir(parents=True, exist_ok=True)
            table = pa.Table.from_pandas(merged.reset_index(drop=True), schema=_SCHEMA)
            pq.write_table(table, pfile, compression="snappy")
            log.debug("Wrote %d rows → %s", len(merged), pfile)

        return total_new

    # ── Read ──────────────────────────────────────────────────────────────────

    def read_range(
        self,
        symbol: str,
        interval: str,
        start: str | datetime,
        end: str | datetime | None = None,
    ) -> pd.DataFrame:
        """Load candles in [start, end] from Parquet partitions.

        start/end can be ISO strings ('2024-01-01') or datetime objects.
        """
        start_dt = pd.Timestamp(start, tz="UTC") if isinstance(start, str) else pd.Timestamp(start, tz="UTC")
        end_dt = pd.Timestamp(end, tz="UTC") if end and isinstance(end, str) else (pd.Timestamp(end, tz="UTC") if end else pd.Timestamp.utcnow())

        base = self.root / "klines" / f"symbol={symbol.upper()}" / f"interval={interval}"
        if not base.exists():
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for date_dir in sorted(base.iterdir()):
            pfile = date_dir / "data.parquet"
            if not pfile.exists():
                continue
            df = pd.read_parquet(pfile)
            df = df[(df["open_time"] >= start_dt) & (df["open_time"] <= end_dt)]
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True).sort_values("open_time").reset_index(drop=True)
        return result

    # ── Metadata ──────────────────────────────────────────────────────────────

    def get_last_open_time(self, symbol: str, interval: str) -> datetime | None:
        """Return the latest open_time already stored, or None if empty."""
        base = self.root / "klines" / f"symbol={symbol.upper()}" / f"interval={interval}"
        if not base.exists():
            return None

        all_dirs = sorted(base.iterdir(), reverse=True)
        for date_dir in all_dirs:
            pfile = date_dir / "data.parquet"
            if pfile.exists():
                df = pd.read_parquet(pfile, columns=["open_time"])
                if not df.empty:
                    ts = df["open_time"].max()
                    return ts.to_pydatetime()
        return None

    def coverage(self, symbol: str, interval: str) -> dict:
        """Return dict with first/last open_time and total candle count."""
        df = self.read_range(symbol, interval, "2000-01-01")
        if df.empty:
            return {"symbol": symbol, "interval": interval, "candles": 0, "first": None, "last": None}
        return {
            "symbol":   symbol,
            "interval": interval,
            "candles":  len(df),
            "first":    df["open_time"].min().isoformat(),
            "last":     df["open_time"].max().isoformat(),
        }
