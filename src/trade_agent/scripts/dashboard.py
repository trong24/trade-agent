"""CLI: Launch the Trade-Agent Analytics Dashboard.

Runs a FastAPI server to serve the webview and provide backtest data.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from datetime import datetime, timezone

try:
    from fastapi import FastAPI, Query
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import HTMLResponse
    import uvicorn
except ImportError:
    print("\n[!] Error: Dashboard requires 'fastapi' and 'uvicorn'.")
    print("    Install them with: pip install fastapi uvicorn\n")
    raise SystemExit(1)

from trade_agent.db import connect, init_db, read_candles, read_latest_facts
from trade_agent.backtest.facts_strategy import (
    generate_signals,
    run_vectorized_backtest,
    _calc_rsi,
    _calc_wma,
)

# Map legacy strategy names to the unified mode parameter
_MODE_MAP = {
    "sr_trend_v1": "sr_trend",
    "rsi_inertia_v1": "rsi_inertia",
    "combined": "combined",
    "sr_trend": "sr_trend",
    "rsi_inertia": "rsi_inertia",
}

log = logging.getLogger(__name__)

app = FastAPI(title="Trade-Agent API")

# Path to static files
WEB_DIR = Path(__file__).parent.parent / "web"


@app.get("/api/backtest")
async def api_backtest(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    strategy: str = "sr_trend_v1",
    start: str = "2025-01-01",
    end: str | None = None,
    fee_bps: float = 2.0,
):
    """Run backtest and return data for the chart."""
    con = connect("data/trade.duckdb")
    init_db(con)

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = (
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        if end
        else datetime.now(timezone.utc)
    )

    df = read_candles(con, symbol, interval, start=start_dt, end=end_dt)
    if df.empty:
        return {"error": "No data found for the given range."}

    # Run strategy (vectorized)
    facts = read_latest_facts(con, symbol, "ALL", version="v1")
    mode = _MODE_MAP.get(strategy, "combined")
    signals = generate_signals(df, facts=facts, interval=interval, mode=mode)
    result = run_vectorized_backtest(df, signals, fee_bps=fee_bps)
    metrics = result["metrics"]
    trade_log = result.get("trade_log", [])

    # Prepare candle data for frontend
    # JSON doesn't handle pandas index well, so we reset_index and convert to list of dicts
    df_plot = df.reset_index()
    candles = []
    for _, row in df_plot.iterrows():
        candles.append(
            {
                "time": row["open_time"].isoformat(),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )

    # Compute Hayden RSI indicators
    rsi = _calc_rsi(df["close"], period=14)
    ema9 = rsi.ewm(span=9, adjust=False).mean()
    wma45 = _calc_wma(rsi, 45)

    rsi_data = []
    for i, (_, row) in enumerate(df_plot.iterrows()):
        r = rsi.iloc[i]
        e = ema9.iloc[i]
        w = wma45.iloc[i]
        # Skip NaN values from initial warmup period
        if any(map(lambda v: v != v, [r, e, w])):  # NaN check
            continue
        rsi_data.append({
            "time": row["open_time"].isoformat(),
            "rsi": round(float(r), 2),
            "ema9": round(float(e), 2),
            "wma45": round(float(w), 2),
        })

    con.close()

    return {
        "symbol": symbol,
        "interval": interval,
        "metrics": metrics,
        "trade_log": trade_log,
        "candles": candles,
        "rsi_data": rsi_data,
    }


# Mount static files
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
else:

    @app.get("/", response_class=HTMLResponse)
    async def missing_web():
        return "<h1>Error: Web directory not found</h1><p>Expected at: " + str(WEB_DIR) + "</p>"


def main():
    parser = argparse.ArgumentParser(description="Trade-Agent Dashboard Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"\n🚀 Launching Trade-Agent Dashboard at http://{args.host}:{args.port}")
    print(f"   View your strategy backtests with TradingView charts!\n")

    uvicorn.run(
        "trade_agent.scripts.dashboard:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
