"""CLI: sync BTCUSDT klines from Binance USD-M Futures into DuckDB.

Usage:
    sync-klines --start 2021-01-01
    sync-klines --intervals 1h,4h --start 2024-01-01 --end 2025-01-01
    python -m trade_agent.scripts.sync_klines --start 2023-01-01
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

import pandas as pd
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from trade_agent.data.binance_client import SUPPORTED_INTERVALS, BinanceClient, _ts_ms
from trade_agent.db import connect, init_db, read_candles, upsert_candles

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(console=console, show_path=False)],
)
log = logging.getLogger(__name__)

_DEFAULT_SYMBOL = "BTCUSDT"
_DEFAULT_START = "2021-01-01"


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _batch_to_df(symbol: str, interval: str, batch: list[dict]) -> pd.DataFrame:
    """Convert a list of raw kline dicts to a typed DataFrame with symbol/interval columns."""
    df = pd.DataFrame(batch)
    df["symbol"] = symbol.upper()
    df["interval"] = interval
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "taker_buy_base", "taker_buy_quote"):
        if col in df.columns:
            df[col] = df[col].astype("float64")
    if "trades" in df.columns:
        df["trades"] = df["trades"].astype("Int64")
    return df


def _get_last_open_time(con, symbol: str, interval: str) -> datetime | None:
    """Return latest open_time stored in DB, or None."""
    row = con.execute(
        """
        SELECT MAX(open_time) FROM candles
        WHERE symbol = ? AND interval = ?
    """,
        [symbol.upper(), interval],
    ).fetchone()
    val = row[0] if row else None
    if val is None:
        return None
    ts = pd.Timestamp(val)
    return ts.to_pydatetime().replace(tzinfo=timezone.utc)


def _sync_one(
    client: BinanceClient,
    con,
    symbol: str,
    interval: str,
    default_start: datetime,
    end_dt: datetime,
    force: bool,
) -> tuple[int, int]:
    """Sync one symbol+interval. Returns (new_candles, batches)."""
    if not force:
        last = _get_last_open_time(con, symbol, interval)
        start_dt = last if last else default_start
    else:
        start_dt = default_start

    start_ms = _ts_ms(start_dt)
    end_ms = _ts_ms(end_dt)
    if start_ms >= end_ms:
        return 0, 0

    total_new = 0
    batches = 0
    for batch in client.get_klines_paginated(symbol, interval, start_ms, end_ms):
        df = _batch_to_df(symbol, interval, batch)
        written = upsert_candles(con, df)
        total_new += written
        batches += 1

    return total_new, batches


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Sync BTCUSDT klines from Binance USD-M Futures → DuckDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Supported intervals: {', '.join(SUPPORTED_INTERVALS)}",
    )
    p.add_argument(
        "--db", default="data/trade.duckdb", help="DuckDB file path (default: data/trade.duckdb)"
    )
    p.add_argument("--symbol", default=_DEFAULT_SYMBOL)
    p.add_argument(
        "--intervals",
        default=",".join(SUPPORTED_INTERVALS),
        help="Comma-separated intervals (default: all)",
    )
    p.add_argument("--start", required=True, help="Start date ISO e.g. 2021-01-01")
    p.add_argument("--end", default=None, help="End date ISO (default: now UTC)")
    p.add_argument(
        "--force", action="store_true", help="Re-sync from --start ignoring stored last_open_time"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main() -> None:
    args = build_parser().parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    requested = [iv.strip() for iv in args.intervals.split(",") if iv.strip()]
    invalid = [iv for iv in requested if iv not in SUPPORTED_INTERVALS]
    if invalid:
        console.print(f"[red]Unknown interval(s): {invalid}[/]")
        raise SystemExit(1)

    con = connect(args.db)
    init_db(con)

    client = BinanceClient()
    end_dt = _parse_dt(args.end) if args.end else datetime.now(timezone.utc)
    start_dt = _parse_dt(args.start)

    console.print(
        f"[bold cyan]Syncing {args.symbol}[/]  "
        f"intervals: {requested}  "
        f"start: {start_dt.date()}  end: {end_dt.date()}  "
        f"db: {args.db}"
    )

    summary_rows: list[tuple] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description:<6}[/]"),
        BarColumn(bar_width=25),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("", total=len(requested))
        for interval in requested:
            progress.update(task, description=interval)
            new, batches = _sync_one(
                client, con, args.symbol, interval, start_dt, end_dt, args.force
            )
            # coverage
            df_cov = read_candles(con, args.symbol, interval)
            total = len(df_cov)
            first = df_cov.index.min().isoformat() if total > 0 else "-"
            last = df_cov.index.max().isoformat() if total > 0 else "-"
            summary_rows.append((interval, str(new), str(batches), str(total), first, last))
            progress.advance(task)

    con.close()

    tbl = Table(title=f"Sync Summary — {args.symbol}", show_header=True)
    for col in ("Interval", "New", "Batches", "Total stored", "First", "Last"):
        tbl.add_column(col, style="cyan" if col == "Interval" else "white")
    for row in summary_rows:
        tbl.add_row(*row)
    console.print(tbl)


if __name__ == "__main__":
    main()
