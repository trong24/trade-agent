---
name: trade-agent
description: Research engine for BTCUSDT futures — sync klines, compute market facts, generate trade plans, run backtests. Trigger when user asks: "Phân tích BTC", "phân tích btc", "market analysis BTCUSDT", "trade plan BTC", or wants bias/long-short decision.
---

# SKILL: trade-agent

## Purpose
Comprehensive research engine for BTCUSDT futures. Provides CLI commands to:
- Sync klines from Binance into DuckDB
- Compute market facts (trend + S/R) with versioning
- Generate structured trade plans with evidence
- Run backtests (vectorized and plan-based)
- Grid-search + walk-forward stability analysis

## Included Files
- `skills/trade-agent/wrapper.sh` — Runner wrapping all trade-agent CLIs
- `skills/trade-agent/SKILL.md` — This file

## When To Use
- User asks for market analysis / trade plan for BTCUSDT
- Need to backtest or evaluate a strategy
- Need current market facts (trend, S/R levels, bias chain)
- Need to compare parameter configs or facts versions

## Available Commands

### 1. Sync klines (data layer)
```bash
./skills/trade-agent/wrapper.sh sync-klines --start 2024-01-01
```

### 2. Analyze market (compute facts)
```bash
./skills/trade-agent/wrapper.sh analyze-market --intervals 15m,1h,4h,1d,1w
./skills/trade-agent/wrapper.sh analyze-market --intervals 1h,4h,1d,1w --version v2
```

### 3. Get latest facts (LLM payload)
```bash
./skills/trade-agent/wrapper.sh get-latest-facts
./skills/trade-agent/wrapper.sh get-latest-facts --json
```

### 4. Generate trade plan
```bash
./skills/trade-agent/wrapper.sh plan-trade --explain
./skills/trade-agent/wrapper.sh plan-trade --json --explain
```

### 5. Backtest
```bash
# Vectorized (sr_trend_v1)
./skills/trade-agent/wrapper.sh backtest-facts --start 2025-01-01 --interval 1h --fee-bps 2.0

# Plan-based (plan_v1) with trade log
./skills/trade-agent/wrapper.sh backtest-facts --start 2025-01-01 --strategy plan_v1 --show-trades
```

### 6. Grid search experiments
```bash
./skills/trade-agent/wrapper.sh run-experiments --start 2025-01-01 --interval 1h
```

### 7. Walk-forward stability
```bash
./skills/trade-agent/wrapper.sh walk-forward --start 2024-06-01 --interval 1h
```

## Output Contract
- All commands output Rich tables to stdout
- `--json` flag outputs raw JSON (pipe-friendly for LLM reasoning)
- Data stored in `data/trade.duckdb`
- Facts versioned via `--version` flag

## Guardrails
- **Research / paper only** — no live order execution
- All timestamps UTC-based
- DuckDB is local-only, no remote connections
- Wrapper auto-bootstraps `.venv` if missing

## Example Workflow (daily)
```bash
# 1. Sync latest data
./skills/trade-agent/wrapper.sh sync-klines --start 2024-01-01

# 2. Compute facts
./skills/trade-agent/wrapper.sh analyze-market --intervals 15m,1h,4h,1d,1w

# 3. Get LLM payload
./skills/trade-agent/wrapper.sh get-latest-facts --json

# 4. Generate trade plan
./skills/trade-agent/wrapper.sh plan-trade --explain

# 5. Backtest current strategy
./skills/trade-agent/wrapper.sh backtest-facts --start 2025-01-01 --fee-bps 2.0
```
