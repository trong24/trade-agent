"""Load Candle objects directly from KlinesStore (Parquet).

Provides a clean bridge between the data layer and the backtest engine,
without going through CSV as intermediary.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..data.klines_store import KlinesStore
from ..types import Candle


def load_candles_from_store(
    symbol: str,
    interval: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    data_dir: str | Path = "data",
) -> list[Candle]:
    """Load candles from the local Parquet store for a given symbol/interval.

    Args:
        symbol:   e.g. 'BTCUSDT'
        interval: e.g. '1h', '4h', '1d'
        start:    ISO date string or datetime (inclusive), default: all data
        end:      ISO date string or datetime (inclusive), default: all data
        data_dir: root of the KlinesStore (default: 'data/')

    Returns:
        list[Candle] sorted ascending by timestamp.

    Raises:
        FileNotFoundError: if no Parquet file exists for the given symbol+interval.
        ValueError:        if the resulting candle list is empty after filtering.
    """
    store = KlinesStore(data_dir)
    fpath = store._filepath(symbol, interval)

    if not fpath.exists():
        raise FileNotFoundError(
            f"No data found for {symbol} {interval} in '{data_dir}'. "
            f"Run: sync-klines --symbol {symbol} --intervals {interval}"
        )

    df = store.read_range(symbol, interval, start=start, end=end)

    if df.empty:
        period = f"{start} → {end}" if start or end else "all"
        raise ValueError(
            f"No candles for {symbol} {interval} in period [{period}]. "
            "Check --start/--end or run sync-klines to fetch more data."
        )

    candles: list[Candle] = []
    for row in df.itertuples(index=False):
        candles.append(
            Candle(
                ts=row.open_time.to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
        )
    return candles
