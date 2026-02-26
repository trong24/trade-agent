"""CLI: output latest market facts JSON for LLM reasoning.

Usage:
    get-latest-facts                          # pretty table
    get-latest-facts --json                   # raw JSON to stdout
    get-latest-facts --json | jq .key_levels  # pipe to jq/LLM
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from trade_agent.db import connect, init_db, read_latest_facts

console = Console()
logging.basicConfig(
    level=logging.WARNING,
    handlers=[RichHandler(console=console, show_path=False)],
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Get latest market facts JSON for LLM reasoning",
    )
    p.add_argument("--db",      default="data/trade.duckdb")
    p.add_argument("--symbol",  default="BTCUSDT")
    p.add_argument("--version", default="v1")
    p.add_argument("--json",    action="store_true", dest="json_mode",
                   help="Output raw JSON to stdout (for piping)")
    return p


def main() -> None:
    args = build_parser().parse_args()

    con = connect(args.db)
    init_db(con)

    facts = read_latest_facts(con, args.symbol, "ALL", version=args.version)
    con.close()

    if facts is None:
        msg = (
            f"No facts found for {args.symbol} (version={args.version}). "
            "Run: analyze-market first."
        )
        if args.json_mode:
            json.dump({"error": msg}, sys.stdout)
        else:
            console.print(f"[red]{msg}[/]")
        sys.exit(1)

    # ── JSON mode: raw output to stdout ────────────────────────────────────
    if args.json_mode:
        json.dump(facts, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return

    # ── Pretty table mode ──────────────────────────────────────────────────
    console.print(f"\n[bold cyan]{args.symbol}[/] — {facts.get('as_of', '?')}")
    console.print(f"Regime: [bold]{facts.get('regime', '?')}[/]\n")

    # Trends
    trends = facts.get("trends", {})
    if trends:
        tbl = Table(title="Trends", show_header=True)
        tbl.add_column("TF", style="cyan", width=5)
        tbl.add_column("Dir", width=8)
        tbl.add_column("Strength", width=10)
        tbl.add_column("ATR%", width=8)
        for tf in ("1w", "1M", "1d", "4h", "1h", "15m"):
            t = trends.get(tf)
            if t:
                d = t.get("dir", "?")
                color = "green" if d == "up" else "red" if d == "down" else "yellow"
                tbl.add_row(
                    tf,
                    f"[{color}]{d}[/]",
                    f"{t.get('strength', 0):.3f}",
                    f"{t.get('atr_pct', 0):.2f}%",
                )
        console.print(tbl)

    # Bias chain
    chain = facts.get("bias_chain", {})
    if chain:
        tbl = Table(title="Bias Chain", show_header=True)
        tbl.add_column("Entry TF", style="cyan", width=10)
        tbl.add_column("Bias", width=8)
        tbl.add_column("From", width=6)
        tbl.add_column("Macro", width=8)
        tbl.add_column("Confidence", width=10)
        for tf in ("15m", "1h", "4h"):
            b = chain.get(tf)
            if b:
                bias = b.get("bias", "?")
                color = "green" if bias == "long" else "red" if bias == "short" else "yellow"
                tbl.add_row(
                    tf,
                    f"[{color}]{bias}[/]",
                    b.get("from_tf", "?"),
                    b.get("macro", "?"),
                    b.get("confidence", "?"),
                )
        console.print(tbl)

    # Key levels
    levels = facts.get("key_levels", [])
    if levels:
        tbl = Table(title="Key S/R Levels", show_header=True)
        tbl.add_column("Price", style="bold", width=12)
        tbl.add_column("Kind", width=12)
        tbl.add_column("Score", width=8)
        tbl.add_column("Source", width=6)
        tbl.add_column("Touches", width=8)
        for lv in levels[:5]:
            kind = lv.get("kind", "?")
            color = "green" if kind == "support" else "red"
            tbl.add_row(
                f"{lv['price']:,.2f}",
                f"[{color}]{kind}[/]",
                f"{lv.get('score', 0):.2f}",
                lv.get("source_tf", "?"),
                str(lv.get("touches", 0)),
            )
        console.print(tbl)

    # Invalidation
    inv = facts.get("invalidation", {})
    bull = inv.get("bull_above")
    bear = inv.get("bear_below")
    console.print(
        f"\nInvalidation: "
        f"[red]bear below {bear:,.2f}[/]" if bear else "",
        end="",
    )
    if bull:
        console.print(f"  |  [green]bull above {bull:,.2f}[/]")
    else:
        console.print()


if __name__ == "__main__":
    main()
