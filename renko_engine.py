"""
Renko Engine - Builds real-time Renko bricks from price feed
XAUUSD Gold Alert System
"""

import numpy as np
from collections import deque


class RenkoBrick:
    def __init__(self, brick_type, open_price, close_price, high, low, timestamp):
        self.type = brick_type        # "UP" or "DOWN"
        self.open = open_price
        self.close = close_price
        self.high = high
        self.low = low
        self.timestamp = timestamp

    def __repr__(self):
        return f"Brick({self.type} | O:{self.open} C:{self.close})"


class RenkoEngine:
    def __init__(self, brick_size: float):
        self.brick_size = brick_size
        self.bricks: list[RenkoBrick] = []
        self.last_close = None
        self.current_timestamp = None

    def process_price(self, price: float, timestamp) -> list[RenkoBrick]:
        """
        Feed a new price tick. Returns list of NEW bricks formed (0, 1, or more).
        Traditional Renko: bricks form when price moves brick_size in one direction.
        """
        self.current_timestamp = timestamp
        new_bricks = []

        if self.last_close is None:
            self.last_close = price
            return new_bricks

        # Check for UP bricks
        while price >= self.last_close + self.brick_size:
            brick = RenkoBrick(
                brick_type="UP",
                open_price=self.last_close,
                close_price=self.last_close + self.brick_size,
                high=self.last_close + self.brick_size,
                low=self.last_close,
                timestamp=timestamp
            )
            self.bricks.append(brick)
            new_bricks.append(brick)
            self.last_close += self.brick_size

        # Check for DOWN bricks
        while price <= self.last_close - self.brick_size:
            brick = RenkoBrick(
                brick_type="DOWN",
                open_price=self.last_close,
                close_price=self.last_close - self.brick_size,
                high=self.last_close,
                low=self.last_close - self.brick_size,
                timestamp=timestamp
            )
            self.bricks.append(brick)
            new_bricks.append(brick)
            self.last_close -= self.brick_size

        return new_bricks

    def get_closes(self) -> list[float]:
        return [b.close for b in self.bricks]

    def get_highs(self) -> list[float]:
        return [b.high for b in self.bricks]

    def get_lows(self) -> list[float]:
        return [b.low for b in self.bricks]

    def last_brick(self) -> RenkoBrick | None:
        return self.bricks[-1] if self.bricks else None

    def last_n_bricks(self, n: int) -> list[RenkoBrick]:
        return self.bricks[-n:] if len(self.bricks) >= n else self.bricks


def calc_sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average"""
    if len(values) < period:
        return [None] * len(values)
    result = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def calc_stochastic(closes, highs, lows, k_length=12, k_smooth=3, d_smooth=3):
    """
    Stochastic Oscillator (12, 3, 3)
    Returns %K and %D lists
    """
    n = len(closes)
    if n < k_length:
        return [None] * n, [None] * n

    raw_k = []
    for i in range(n):
        if i < k_length - 1:
            raw_k.append(None)
        else:
            h = max(highs[i - k_length + 1:i + 1])
            l = min(lows[i - k_length + 1:i + 1])
            if h == l:
                raw_k.append(50.0)
            else:
                raw_k.append(100 * (closes[i] - l) / (h - l))

    # %K = smoothed raw_k
    valid_raw = [v for v in raw_k if v is not None]
    k_smoothed = []
    for i, v in enumerate(raw_k):
        if v is None:
            k_smoothed.append(None)
        else:
            idx = i - raw_k.index(next(x for x in raw_k if x is not None))
            window = valid_raw[max(0, idx - k_smooth + 1):idx + 1]
            k_smoothed.append(sum(window) / len(window))

    # %D = smoothed %K
    valid_k = [(i, v) for i, v in enumerate(k_smoothed) if v is not None]
    d_smoothed = [None] * n
    for pos, (i, _) in enumerate(valid_k):
        window = [valid_k[max(0, pos - d_smooth + 1):pos + 1][j][1]
                  for j in range(len(valid_k[max(0, pos - d_smooth + 1):pos + 1]))]
        d_smoothed[i] = sum(window) / len(window)

    return k_smoothed, d_smoothed


def calc_vwap(closes, highs, lows, volumes, session_start_idx: int, brick_size: float = 3.0):
    """
    Session VWAP with Standard Deviation Bands (multiplier=1).
    Uses price-range based std dev since we don't have real volume.
    Minimum band width = 2x brick_size to avoid false breakouts.
    Resets at session_start_idx.
    """
    n = len(closes)
    vwap_vals  = [None] * n
    upper_band = [None] * n
    lower_band = [None] * n

    if session_start_idx >= n:
        return vwap_vals, upper_band, lower_band

    # Use equal weighting (volume = 1) since we have no real volume
    # Calculate running mean and std dev of typical price
    tp_values = []

    for i in range(session_start_idx, n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        tp_values.append(tp)

        # Running VWAP = simple mean of typical prices (equal weight)
        vwap = sum(tp_values) / len(tp_values)

        # Running std dev of typical prices
        if len(tp_values) > 1:
            variance = sum((t - vwap) ** 2 for t in tp_values) / len(tp_values)
            std_dev  = variance ** 0.5
        else:
            std_dev = 0

        # Minimum band = 2 brick sizes to prevent false breakouts on tight std dev
        min_band = brick_size * 2
        std_dev  = max(std_dev, min_band)

        vwap_vals[i]  = vwap
        upper_band[i] = vwap + std_dev
        lower_band[i] = vwap - std_dev

    return vwap_vals, upper_band, lower_band


def calc_darvas_box(highs_daily: list[float], lows_daily: list[float], box_length=5):
    """
    Darvas Box: Top = highest high of last box_length bars (daily)
                Bottom = lowest low when box condition met
    Returns top_box, bottom_box (scalar values — current box levels)
    """
    if len(highs_daily) < box_length:
        return None, None

    k1 = max(highs_daily[-box_length:])
    k2 = max(highs_daily[-(box_length - 1):])
    k3 = max(highs_daily[-(box_length - 2):])

    # Box condition: k3 < k2 (consolidating)
    box_active = k3 < k2

    if box_active:
        top_box = k1
        bottom_box = min(lows_daily[-box_length:])
        return top_box, bottom_box

    return None, None
