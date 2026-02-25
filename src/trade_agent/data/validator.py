"""Data quality validation: gap detection, schema check, coverage report."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

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

_REQUIRED_COLS = {"open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades"}


@dataclass
class GapInfo:
    gap_start: datetime
    gap_end: datetime
    missing_candles: int


@dataclass
class ValidationReport:
    symbol: str
    interval: str
    start: datetime
    end: datetime
    total_candles: int
    expected_candles: int
    duplicate_count: int
    missing_gaps: list[GapInfo] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    quality_score: float = 1.0

    def is_ok(self, min_score: float = 0.95) -> bool:
        return self.quality_score >= min_score and not self.schema_errors

    def summary(self) -> str:
        lines = [
            f"Symbol   : {self.symbol}  Interval: {self.interval}",
            f"Period   : {self.start.date()} → {self.end.date()}",
            f"Candles  : {self.total_candles:,} / {self.expected_candles:,} expected",
            f"Gaps     : {len(self.missing_gaps)} ({sum(g.missing_candles for g in self.missing_gaps):,} missing candles)",
            f"Dupes    : {self.duplicate_count}",
            f"Score    : {self.quality_score:.3f}  {'✅ OK' if self.is_ok() else '⚠️ ISSUES FOUND'}",
        ]
        if self.schema_errors:
            lines.append("Schema errors: " + "; ".join(self.schema_errors))
        for g in self.missing_gaps[:5]:
            lines.append(f"  Gap: {g.gap_start} → {g.gap_end} ({g.missing_candles} candles)")
        if len(self.missing_gaps) > 5:
            lines.append(f"  … and {len(self.missing_gaps) - 5} more gaps")
        return "\n".join(lines)


def validate(
    df: pd.DataFrame,
    symbol: str,
    interval: str,
    gap_threshold: int = 5,
) -> ValidationReport:
    """Validate a DataFrame of klines.

    Args:
        df: DataFrame from KlinesStore.read_range()
        symbol: e.g. 'BTCUSDT'
        interval: e.g. '1m'
        gap_threshold: report gap only if missing candles > this value
    """
    if df.empty:
        return ValidationReport(
            symbol=symbol, interval=interval,
            start=datetime.now(timezone.utc), end=datetime.now(timezone.utc),
            total_candles=0, expected_candles=0, duplicate_count=0,
            quality_score=0.0,
        )

    schema_errors: list[str] = []
    missing_cols = _REQUIRED_COLS - set(df.columns)
    if missing_cols:
        schema_errors.append(f"Missing columns: {sorted(missing_cols)}")

    interval_ms = _INTERVAL_MS.get(interval)
    if interval_ms is None:
        schema_errors.append(f"Unknown interval: {interval}")

    start_ts = df["open_time"].min()
    end_ts   = df["open_time"].max()
    total    = len(df)

    # Duplicates
    dup_count = int(df.duplicated(subset=["open_time"]).sum())

    # Expected candles
    expected = 0
    gaps: list[GapInfo] = []

    if interval_ms:
        span_ms  = (end_ts - start_ts).total_seconds() * 1000
        expected = int(span_ms / interval_ms) + 1

        # Gap detection: find consecutive pairs with jump > 1 interval
        sorted_times = df["open_time"].drop_duplicates().sort_values().reset_index(drop=True)
        diffs_ms = sorted_times.diff().dt.total_seconds().mul(1000).dropna()

        for idx, diff in diffs_ms.items():
            missing = int(diff / interval_ms) - 1
            if missing > gap_threshold:
                gap_start = sorted_times.iloc[idx - 1].to_pydatetime()
                gap_end   = sorted_times.iloc[idx].to_pydatetime()
                gaps.append(GapInfo(gap_start, gap_end, missing))

    # Quality score: fraction of expected candles present (ignoring duplicates)
    unique_candles = total - dup_count
    quality = min(1.0, unique_candles / expected) if expected > 0 else 0.0

    return ValidationReport(
        symbol=symbol,
        interval=interval,
        start=start_ts.to_pydatetime(),
        end=end_ts.to_pydatetime(),
        total_candles=total,
        expected_candles=expected,
        duplicate_count=dup_count,
        missing_gaps=gaps,
        schema_errors=schema_errors,
        quality_score=quality,
    )
