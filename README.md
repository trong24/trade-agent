# Trade Agent (MVP)

Trade agent tối giản để **backtest + paper execution** theo kiến trúc module:

- `data`: đọc OHLCV từ CSV
- `strategy`: tín hiệu (SMA cross)
- `risk`: sizing theo % equity
- `broker`: paper broker có phí
- `backtest`: engine chạy candle-by-candle

## 1) Cài đặt

```bash
cd trade-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 2) Tạo dữ liệu mẫu

```bash
python scripts/generate_sample_data.py
```

File output: `data/sample_ohlcv.csv`

## 3) Chạy backtest

```bash
trade-agent --csv data/sample_ohlcv.csv --short 20 --long 50 --risk 0.2 --initial-cash 10000
```

## CSV format bắt buộc

Header:

```csv
timestamp,open,high,low,close,volume
```

- `timestamp`: ISO (`2025-01-01T00:00:00Z`) hoặc epoch seconds/milliseconds
- Các cột còn lại là số thực

## Gợi ý bước tiếp theo

- Thêm strategy khác (RSI, breakout, mean reversion)
- Thêm metrics (max drawdown, Sharpe, expectancy)
- Kết nối exchange testnet qua `ccxt`
- Thêm live loop + watchdog + alert (Telegram/Pushover)

> ⚠️ Mã này để nghiên cứu/kỹ thuật. Không phải lời khuyên đầu tư.
