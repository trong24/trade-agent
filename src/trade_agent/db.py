"""DuckDB database layer: connection, schema init, upsert, and read operations.

Source of truth for all klines, market facts, and backtest runs.
File: data/trade.duckdb (default)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

log = logging.getLogger(__name__)

_DEFAULT_DB = "data/trade.duckdb"

# ── DDL ───────────────────────────────────────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS candles (
    symbol           TEXT        NOT NULL,
    interval         TEXT        NOT NULL,
    open_time        TIMESTAMPTZ NOT NULL,
    close_time       TIMESTAMPTZ NOT NULL,
    open             DOUBLE      NOT NULL,
    high             DOUBLE      NOT NULL,
    low              DOUBLE      NOT NULL,
    close            DOUBLE      NOT NULL,
    volume           DOUBLE      NOT NULL,
    trades           BIGINT,
    taker_buy_base   DOUBLE,
    taker_buy_quote  DOUBLE,
    PRIMARY KEY (symbol, interval, open_time)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    symbol       TEXT        NOT NULL,
    funding_time TIMESTAMPTZ NOT NULL,
    funding_rate DOUBLE      NOT NULL,
    PRIMARY KEY (symbol, funding_time)
);

CREATE TABLE IF NOT EXISTS market_facts (
    symbol     TEXT        NOT NULL,
    as_of      TIMESTAMPTZ NOT NULL,
    interval   TEXT        NOT NULL,
    facts_json TEXT        NOT NULL,
    version    TEXT        NOT NULL,
    PRIMARY KEY (symbol, as_of, interval, version)
);

CREATE INDEX IF NOT EXISTS idx_market_facts_lookup
    ON market_facts (symbol, interval, as_of);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id        TEXT        PRIMARY KEY,
    symbol        TEXT        NOT NULL,
    interval      TEXT        NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ NOT NULL,
    strategy_id   TEXT        NOT NULL,
    params_json   TEXT        NOT NULL,
    facts_version TEXT        NOT NULL,
    metrics_json  TEXT,
    created_at    TIMESTAMPTZ NOT NULL
);
"""


def connect(db_path: str | Path = _DEFAULT_DB) -> duckdb.DuckDBPyConnection:
    """Open (or create) a DuckDB database file and return a connection."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    return con


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    """Create all tables and indexes if they don't exist."""
    con.execute(_DDL)
    log.debug("DB schema initialised")


def upsert_candles(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Upsert a DataFrame of candles into the candles table.

    Uses INSERT OR REPLACE semantics (DELETE + INSERT for DuckDB).
    Returns the count of rows upserted.
    """
    if df.empty:
        return 0

    rows = _prepare_candles_df(df)

    # DuckDB: register DataFrame as a temporary view, then INSERT
    con.register("_candles_staging", rows)
    try:
        con.execute("""
            INSERT OR REPLACE INTO candles
                (symbol, interval, open_time, close_time,
                 open, high, low, close, volume,
                 trades, taker_buy_base, taker_buy_quote)
            SELECT
                symbol, interval, open_time, close_time,
                open, high, low, close, volume,
                trades, taker_buy_base, taker_buy_quote
            FROM _candles_staging
        """)
    finally:
        con.unregister("_candles_staging")

    return len(rows)


def read_candles(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    interval: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
) -> pd.DataFrame:
    """Load candles from DB into a UTC-indexed DataFrame.

    Returns DataFrame with DatetimeIndex (UTC) and float64 OHLCV columns.
    """
    conditions = ["symbol = ? AND interval = ?"]
    params: list = [symbol.upper(), interval]

    if start is not None:
        start_ts = _to_utc(start)
        conditions.append("open_time >= ?")
        params.append(start_ts)
    if end is not None:
        end_ts = _to_utc(end)
        conditions.append("open_time <= ?")
        params.append(end_ts)

    where = " AND ".join(conditions)
    query = f"""
        SELECT open_time, open, high, low, close, volume,
               trades, taker_buy_base, taker_buy_quote, close_time
        FROM candles
        WHERE {where}
        ORDER BY open_time ASC
    """
    df = con.execute(query, params).df()

    if df.empty:
        return df

    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.set_index("open_time")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("float64")
    return df


def upsert_market_facts(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    as_of: datetime,
    interval: str,
    facts: dict,
    version: str = "v1",
) -> None:
    """Upsert a market_facts row."""
    con.execute("""
        INSERT OR REPLACE INTO market_facts (symbol, as_of, interval, facts_json, version)
        VALUES (?, ?, ?, ?, ?)
    """, [symbol.upper(), as_of, interval, json.dumps(facts, default=str), version])


def read_latest_facts(
    con: duckdb.DuckDBPyConnection,
    symbol: str,
    interval: str,
    as_of_max: datetime | None = None,
    version: str = "v1",
) -> dict | None:
    """Return the latest facts_json dict for given symbol/interval/version at or before as_of_max."""
    params: list = [symbol.upper(), interval, version]
    time_filter = ""
    if as_of_max is not None:
        time_filter = "AND as_of <= ?"
        params.append(as_of_max)

    row = con.execute(f"""
        SELECT facts_json FROM market_facts
        WHERE symbol = ? AND interval = ? AND version = ?
        {time_filter}
        ORDER BY as_of DESC
        LIMIT 1
    """, params).fetchone()

    return json.loads(row[0]) if row else None


def insert_backtest_run(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    strategy_id: str,
    params: dict,
    facts_version: str,
    metrics: dict,
) -> None:
    """Store a backtest run record."""
    now = datetime.now(timezone.utc)
    con.execute("""
        INSERT OR REPLACE INTO backtest_runs
            (run_id, symbol, interval, start_time, end_time,
             strategy_id, params_json, facts_version, metrics_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        run_id, symbol.upper(), interval,
        start_time, end_time, strategy_id,
        json.dumps(params), facts_version,
        json.dumps(metrics), now,
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value)
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _prepare_candles_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw klines DataFrame to match the candles schema."""
    out = pd.DataFrame()
    out["symbol"]          = df["symbol"]
    out["interval"]        = df["interval"]
    out["open_time"]       = pd.to_datetime(df["open_time"], utc=True)
    out["close_time"]      = pd.to_datetime(df["close_time"], utc=True)
    out["open"]            = df["open"].astype("float64")
    out["high"]            = df["high"].astype("float64")
    out["low"]             = df["low"].astype("float64")
    out["close"]           = df["close"].astype("float64")
    out["volume"]          = df["volume"].astype("float64")
    out["trades"]          = df.get("trades", pd.Series(dtype="Int64")).astype("Int64")
    out["taker_buy_base"]  = df.get("taker_buy_base",  pd.Series(dtype="float64"))
    out["taker_buy_quote"] = df.get("taker_buy_quote", pd.Series(dtype="float64"))
    return out
