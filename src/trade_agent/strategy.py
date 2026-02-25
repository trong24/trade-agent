from __future__ import annotations

from statistics import fmean

from .types import Candle, Signal


class SMACrossStrategy:
    """Simple SMA crossover.

    - BUY when short SMA crosses above long SMA
    - SELL when short SMA crosses below long SMA
    - HOLD otherwise
    """

    def __init__(self, short_window: int = 20, long_window: int = 50) -> None:
        if short_window <= 1 or long_window <= 1:
            raise ValueError("Windows must be > 1")
        if short_window >= long_window:
            raise ValueError("short_window must be < long_window")
        self.short_window = short_window
        self.long_window = long_window

    def generate(self, candles: list[Candle]) -> Signal:
        min_needed = self.long_window + 1
        if len(candles) < min_needed:
            return Signal.HOLD

        closes = [c.close for c in candles]

        short_prev = fmean(closes[-self.short_window - 1 : -1])
        short_curr = fmean(closes[-self.short_window :])
        long_prev = fmean(closes[-self.long_window - 1 : -1])
        long_curr = fmean(closes[-self.long_window :])

        crossed_up = short_prev <= long_prev and short_curr > long_curr
        crossed_down = short_prev >= long_prev and short_curr < long_curr

        if crossed_up:
            return Signal.BUY
        if crossed_down:
            return Signal.SELL
        return Signal.HOLD
