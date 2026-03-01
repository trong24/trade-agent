---
name: trade-agent
description: Research engine for BTCUSDT futures — sync klines, compute market facts, generate trade plans, run backtests.
---

# SKILL: trade-agent

## Purpose
BTCUSDT futures research engine. Sync klines, compute facts (trend+S/R), generate trade plans, backtest strategies, grid-search parameters, walk-forward analysis.

## When To Use
- Market analysis / trade plans for BTCUSDT
- Backtest strategies or evaluate parameters
- Current market facts (trend, S/R, bias)
- Parameter tuning with grid-search or walk-forward

## Commands
```bash
# Sync klines
./skills/trade-agent/wrapper.sh sync-klines --start 2024-01-01

# Analyze market (compute facts)
./skills/trade-agent/wrapper.sh analyze-market --intervals 15m,1h,4h,1d,1w
./skills/trade-agent/wrapper.sh analyze-market --intervals 1h,4h,1d --version v2

# Get facts (LLM payload)
./skills/trade-agent/wrapper.sh get-latest-facts [--json]

# Generate trade plan
./skills/trade-agent/wrapper.sh plan-trade [--explain] [--json]

# Backtest (vectorized)
./skills/trade-agent/wrapper.sh backtest-facts --start 2025-01-01 --interval 1h [--fee-bps 2.0]

# Backtest (plan-based)
./skills/trade-agent/wrapper.sh backtest-facts --start 2025-01-01 --strategy plan_v1 [--show-trades]

# Grid-search experiments
./skills/trade-agent/wrapper.sh run-experiments --start 2025-01-01 --interval 1h

# Walk-forward stability
./skills/trade-agent/wrapper.sh walk-forward --start 2024-06-01 --interval 1h

# Interactive dashboard
./skills/trade-agent/wrapper.sh dashboard
```

## Output
- Rich tables to stdout (default)
- `--json` for raw JSON (LLM-friendly)
- Data: `data/trade.duckdb`
- Facts versioned via `--version`

## Guardrails
- Research/paper only — no live execution
- UTC timestamps
- Local DuckDB only
- Auto-bootstraps `.venv`
