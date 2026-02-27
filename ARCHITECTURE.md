# Trade-Agent Architecture

This document describes the overall logic and data flow of the `trade-agent` system.

## System Architecture Diagram

```mermaid
graph TD
    subgraph "1. DATA INGEST"
        Binance["Binance API"] -- "Klines/Candlesticks" --> Sync["sync-klines script"]
        Sync -- "Save to Storage" --> DuckDB[("DuckDB (trade.duckdb)")]
    end

    subgraph "2. MARKET ANALYSIS"
        DuckDB --> Analyze["analyze-market script"]
        
        subgraph "Analysis Modules"
            Analyze --> Trend["trend.py (EMA/SMA/Strength)"]
            Analyze --> SR["sr.py (S/R, Hayden RSI, Structure)"]
            Trend --> Payload["payload.py (Merge MTF)"]
            SR --> Payload
        end
        
        Payload -- "Persist Market Facts (v1, v2...)" --> DuckDB
    end

    subgraph "3. DECISION MAKING (Planning & LLM)"
        DuckDB --> Latest["get-latest-facts"]
        Latest -- "JSON Payload" --> Plan["plan-trade script"]
        Plan -- "Context + Rules" --> LLM["LLM (DeepSeek/Claude)"]
        LLM -- "Reasoning + Signals" --> Strategy["Trade Plan (Plan v1)"]
    end

    subgraph "4. VALIDATION & OPTIMIZATION"
        Strategy --> Backtest["backtest-facts"]
        Backtest -- "Performance Metrics" --> Reports["Reports (Experiments)"]
        Reports --> Optimization["run-experiments / walk-forward"]
        Optimization -- "Refine Params" --> Trend
    end

    %% Styles
    style DuckDB fill:#f9f,stroke:#333,stroke-width:2px
    style LLM fill:#69f,stroke:#333,stroke-width:2px
    style Strategy fill:#0f0,stroke:#333,stroke-width:2px
```

## Component Breakdown

### 1. Data Ingest
- **`sync-klines`**: Downloads OHLCV data from Binance and stores it in DuckDB.

### 2. Market Analysis
- **`analyze-market`**: The main driver for market analysis.
- **`trend.py`**: Computes trend direction and strength using technical indicators.
- **`sr.py`**: Identifies Support and Resistance levels using specialized logic (Hayden RSI, Structural Swings, and Role Swaps).
- **`payload.py`**: Aggregates multi-timeframe analysis into a structured JSON format.

### 3. Decision Making
- **`plan-trade`**: Combines market facts with trading playbooks to generate a detailed trade plan via LLM.
- **LLM**: Analyzes the provided data to suggest entries, stop losses, and take profits.

### 4. Validation
- **`backtest-facts`**: Runs historical simulations to verify the effectiveness of the generated plans.
- **`run-experiments` & `walk-forward`**: Used for grid searching and testing the stability of strategy parameters across different market regimes.
