from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .types import Candle


def _parse_ts(raw: str) -> datetime:
    raw = raw.strip()
    if raw.isdigit():
        ts = int(raw)
        # heuristic: milliseconds vs seconds
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    # ISO 8601 support, including trailing Z
    if raw.endswith("Z"):
        raw = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_candle_values(row: dict[str, str], line: int) -> None:
    """Raise ValueError with a descriptive message if OHLCV data is invalid."""
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])
    v = float(row["volume"])

    errors: list[str] = []
    if o <= 0:
        errors.append(f"open={o} must be > 0")
    if h <= 0:
        errors.append(f"high={h} must be > 0")
    if l <= 0:
        errors.append(f"low={l} must be > 0")
    if c <= 0:
        errors.append(f"close={c} must be > 0")
    if v < 0:
        errors.append(f"volume={v} must be >= 0")
    if h < l:
        errors.append(f"high={h} < low={l}")
    if h < o or h < c:
        errors.append(f"high={h} must be >= open and close")
    if l > o or l > c:
        errors.append(f"low={l} must be <= open and close")

    if errors:
        raise ValueError(f"Invalid OHLCV at row {line}: {'; '.join(errors)}")


def load_candles_from_csv(path: str | Path) -> list[Candle]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CSV not found: {p}")

    candles: list[Candle] = []
    with p.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        headers = {h.strip().lower() for h in (reader.fieldnames or [])}
        missing = required - headers
        if missing:
            raise ValueError(
                f"CSV missing required columns: {', '.join(sorted(missing))}. "
                f"Expected: timestamp,open,high,low,close,volume"
            )

        for line_num, row in enumerate(reader, start=2):  # start=2: header is line 1
            # Normalize keys: strip whitespace + lowercase so CSV headers like
            # "Open", "TIMESTAMP", " close " all work correctly.
            row = {k.strip().lower(): v.strip() for k, v in row.items()}
            _validate_candle_values(row, line_num)
            candles.append(
                Candle(
                    ts=_parse_ts(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )

    candles.sort(key=lambda c: c.ts)
    return candles
