from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


def generate(path: Path, rows: int = 400, start_price: float = 30_000.0) -> None:
    random.seed(42)
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    price = start_price

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])

        for _ in range(rows):
            drift = random.uniform(-0.008, 0.01)
            vol = random.uniform(0.001, 0.02)

            o = price
            c = max(100.0, o * (1 + drift))
            h = max(o, c) * (1 + vol)
            low = min(o, c) * (1 - vol)
            v = random.uniform(10, 100)

            writer.writerow(
                [
                    ts.isoformat().replace("+00:00", "Z"),
                    f"{o:.2f}",
                    f"{h:.2f}",
                    f"{low:.2f}",
                    f"{c:.2f}",
                    f"{v:.4f}",
                ]
            )

            ts += timedelta(hours=1)
            price = c


if __name__ == "__main__":
    out = Path("data/sample_ohlcv.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    generate(out)
    print(f"Wrote {out}")
