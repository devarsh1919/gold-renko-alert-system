"""
Strategy Engine — All 4 Gold Trading Strategies
XAUUSD Renko Alert System
"""

from renko_engine import RenkoEngine, calc_sma, calc_stochastic, calc_vwap, calc_darvas_box
from dataclasses import dataclass
from typing import Optional


@dataclass
class Signal:
    strategy_num: int
    strategy_name: str
    signal_type: str          # "BUY", "SELL", "EXIT_BUY", "EXIT_SELL"
    price: float
    sl: Optional[float]
    tp: Optional[float]
    message: str


# ─────────────────────────────────────────────
# STRATEGY 1: MA Pullback (8 SMA + 21 SMA)
# ─────────────────────────────────────────────
class Strategy1_MAPullback:
    """
    Rules:
    - Uptrend: 8 SMA > 21 SMA
    - Pullback: max 3 red bricks touching/near 8 SMA
    - Signal: first green brick after pullback
    - Trend broken if price closes beyond 21 SMA
    - SL: lowest low of pullback bricks
    - TP: 1:2 RR
    """
    def __init__(self, max_pullback=3):
        self.max_pullback = max_pullback
        self.pull_count_up = 0
        self.pull_count_down = 0
        self.pullback_low = None
        self.pullback_high = None
        self.last_signal = None

    def check(self, engine: RenkoEngine) -> Optional[Signal]:
        bricks = engine.bricks
        if len(bricks) < 22:
            return None

        closes = engine.get_closes()
        highs  = engine.get_highs()
        lows   = engine.get_lows()

        sma8  = calc_sma(closes, 8)
        sma21 = calc_sma(closes, 21)

        if sma8[-1] is None or sma21[-1] is None:
            return None

        last  = bricks[-1]
        price = last.close
        s8    = sma8[-1]
        s21   = sma21[-1]

        uptrend   = s8 > s21
        downtrend = s8 < s21
        green     = last.type == "UP"
        red       = last.type == "DOWN"

        # Trend broken checks
        trend_broken_up   = uptrend   and price < s21
        trend_broken_down = downtrend and price > s21

        if trend_broken_up or trend_broken_down:
            self.pull_count_up   = 0
            self.pull_count_down = 0
            self.pullback_low    = None
            self.pullback_high   = None
            return None

        # ── UPTREND PULLBACK TRACKING ──
        if uptrend:
            if red:
                self.pull_count_up += 1
                # Track pullback low
                self.pullback_low = last.low if self.pullback_low is None \
                    else min(self.pullback_low, last.low)
            elif green and self.pull_count_up > 0:
                # Reversal brick — check conditions
                touched_sma = self.pullback_low is not None and self.pullback_low <= s8 * 1.002
                valid_pull  = 1 <= self.pull_count_up <= self.max_pullback

                if valid_pull and touched_sma:
                    sl = self.pullback_low
                    entry = price
                    risk  = entry - sl
                    tp    = entry + (2 * risk)

                    self.pull_count_up = 0
                    self.pullback_low  = None

                    return Signal(
                        strategy_num=1,
                        strategy_name="MA Pullback",
                        signal_type="BUY",
                        price=entry,
                        sl=round(sl, 3),
                        tp=round(tp, 3),
                        message=(
                            f"🟢 *GOLD BUY — Strategy 1 (MA Pullback)*\n"
                            f"Entry: ${entry:.3f}\n"
                            f"Pullback Bricks: {self.pull_count_up}\n"
                            f"8 SMA: ${s8:.3f} | 21 SMA: ${s21:.3f}\n"
                            f"SL: ${sl:.3f} | TP: ${tp:.3f} (1:2 RR)"
                        )
                    )
                else:
                    self.pull_count_up = 0
                    self.pullback_low  = None

        # ── DOWNTREND PULLBACK TRACKING ──
        if downtrend:
            if green:
                self.pull_count_down += 1
                self.pullback_high = last.high if self.pullback_high is None \
                    else max(self.pullback_high, last.high)
            elif red and self.pull_count_down > 0:
                touched_sma = self.pullback_high is not None and self.pullback_high >= s8 * 0.998
                valid_pull  = 1 <= self.pull_count_down <= self.max_pullback

                if valid_pull and touched_sma:
                    sl = self.pullback_high
                    entry = price
                    risk  = sl - entry
                    tp    = entry - (2 * risk)

                    self.pull_count_down = 0
                    self.pullback_high   = None

                    return Signal(
                        strategy_num=1,
                        strategy_name="MA Pullback",
                        signal_type="SELL",
                        price=entry,
                        sl=round(sl, 3),
                        tp=round(tp, 3),
                        message=(
                            f"🔴 *GOLD SELL — Strategy 1 (MA Pullback)*\n"
                            f"Entry: ${entry:.3f}\n"
                            f"Pullback Bricks: {self.pull_count_down}\n"
                            f"8 SMA: ${s8:.3f} | 21 SMA: ${s21:.3f}\n"
                            f"SL: ${sl:.3f} | TP: ${tp:.3f} (1:2 RR)"
                        )
                    )
                else:
                    self.pull_count_down = 0
                    self.pullback_high   = None

        return None


# ─────────────────────────────────────────────
# STRATEGY 2: 200 SMA + Stochastic (12,3,3)
# ─────────────────────────────────────────────
class Strategy2_StochVWAP:
    """
    Rules:
    - Price above 200 SMA + both K&D below 20 + green brick = BUY
    - Price below 200 SMA + both K&D above 80 + red brick = SELL
    - Exit BUY: Stoch reverses from 80 zone (was >80, now falling)
    - Exit SELL: Stoch reverses from 20 zone (was <20, now rising)
    """
    def __init__(self):
        self.in_buy  = False
        self.in_sell = False

    def check(self, engine: RenkoEngine) -> Optional[Signal]:
        bricks = engine.bricks
        if len(bricks) < 200:
            return None

        closes = engine.get_closes()
        highs  = engine.get_highs()
        lows   = engine.get_lows()

        sma200        = calc_sma(closes, 200)
        stoch_k, stoch_d = calc_stochastic(closes, highs, lows, 12, 3, 3)

        if any(v is None for v in [sma200[-1], stoch_k[-1], stoch_d[-1],
                                    stoch_k[-2], stoch_d[-2]]):
            return None

        last  = bricks[-1]
        price = last.close
        s200  = sma200[-1]
        k1, d1 = stoch_k[-1], stoch_d[-1]
        k2, d2 = stoch_k[-2], stoch_d[-2]
        green = last.type == "UP"
        red   = last.type == "DOWN"

        # ── EXIT SIGNALS ──
        if self.in_buy:
            # Was overbought, now reversing down
            if k2 > 80 and d2 > 80 and (k1 < k2 or d1 < d2):
                self.in_buy = False
                return Signal(
                    strategy_num=2,
                    strategy_name="200 SMA + Stochastic",
                    signal_type="EXIT_BUY",
                    price=price,
                    sl=None, tp=None,
                    message=(
                        f"⚠️ *EXIT BUY — Strategy 2 (Stochastic)*\n"
                        f"Stochastic reversing from overbought zone (80)\n"
                        f"Price: ${price:.3f}\n"
                        f"Stoch %K: {k1:.1f} | %D: {d1:.1f}\n"
                        f"📌 Close or manage your BUY trade now!"
                    )
                )

        if self.in_sell:
            # Was oversold, now reversing up
            if k2 < 20 and d2 < 20 and (k1 > k2 or d1 > d2):
                self.in_sell = False
                return Signal(
                    strategy_num=2,
                    strategy_name="200 SMA + Stochastic",
                    signal_type="EXIT_SELL",
                    price=price,
                    sl=None, tp=None,
                    message=(
                        f"⚠️ *EXIT SELL — Strategy 2 (Stochastic)*\n"
                        f"Stochastic reversing from oversold zone (20)\n"
                        f"Price: ${price:.3f}\n"
                        f"Stoch %K: {k1:.1f} | %D: {d1:.1f}\n"
                        f"📌 Close or manage your SELL trade now!"
                    )
                )

        # ── ENTRY SIGNALS ──
        # BUY: above 200 SMA + both K&D < 20 + green brick
        if price > s200 and k1 < 20 and d1 < 20 and green and not self.in_buy:
            self.in_buy = True
            sl = last.low
            return Signal(
                strategy_num=2,
                strategy_name="200 SMA + Stochastic",
                signal_type="BUY",
                price=price,
                sl=round(sl, 3),
                tp=None,
                message=(
                    f"🟢 *GOLD BUY — Strategy 2 (200 SMA + Stochastic)*\n"
                    f"Price: ${price:.3f} | Above 200 SMA ✅\n"
                    f"Stoch %K: {k1:.1f} | %D: {d1:.1f} — Oversold ✅\n"
                    f"Green brick closed ✅\n"
                    f"SL Reference: ${sl:.3f}\n"
                    f"Exit: When Stoch reverses from 80 zone"
                )
            )

        # SELL: below 200 SMA + both K&D > 80 + red brick
        if price < s200 and k1 > 80 and d1 > 80 and red and not self.in_sell:
            self.in_sell = True
            sl = last.high
            return Signal(
                strategy_num=2,
                strategy_name="200 SMA + Stochastic",
                signal_type="SELL",
                price=price,
                sl=round(sl, 3),
                tp=None,
                message=(
                    f"🔴 *GOLD SELL — Strategy 2 (200 SMA + Stochastic)*\n"
                    f"Price: ${price:.3f} | Below 200 SMA ✅\n"
                    f"Stoch %K: {k1:.1f} | %D: {d1:.1f} — Overbought ✅\n"
                    f"Red brick closed ✅\n"
                    f"SL Reference: ${sl:.3f}\n"
                    f"Exit: When Stoch reverses from 20 zone"
                )
            )

        return None


# ─────────────────────────────────────────────
# STRATEGY 3: VWAP Band Breakout
# ─────────────────────────────────────────────
class Strategy3_VWAPBreakout:
    """
    Rules:
    - VWAP resets at 00:00 UTC daily
    - Wait for first 3 bricks after session open
    - Scenario 1: price was outside band → came back inside → breaks again = alert
    - Scenario 2: price inside band → first close outside = alert
    - Exit BUY: brick closes below 8 SMA
    - Exit SELL: brick closes above 8 SMA
    """
    def __init__(self):
        self.session_brick_count = 0
        self.session_start_idx   = 0
        self.in_buy  = False
        self.in_sell = False
        self.prev_was_outside_upper = False
        self.prev_was_outside_lower = False

    def new_session(self, brick_idx: int):
        """Call this when a new UTC day begins"""
        self.session_start_idx      = brick_idx
        self.session_brick_count    = 0
        self.prev_was_outside_upper = False
        self.prev_was_outside_lower = False

    def check(self, engine: RenkoEngine, volumes: list[float]) -> Optional[Signal]:
        bricks = engine.bricks
        n = len(bricks)
        if n < 4:
            return None

        closes = engine.get_closes()
        highs  = engine.get_highs()
        lows   = engine.get_lows()

        vwap, upper, lower = calc_vwap(
            closes, highs, lows, volumes, self.session_start_idx
        )
        sma8 = calc_sma(closes, 8)

        if vwap[-1] is None or upper[-1] is None or sma8[-1] is None:
            return None

        # Count bricks since session start
        self.session_brick_count = n - self.session_start_idx

        # Wait for first 3 bricks
        if self.session_brick_count < 4:
            return None

        last  = bricks[-1]
        price = last.close
        prev_price = bricks[-2].close if n >= 2 else price

        u1 = upper[-1]
        l1 = lower[-1]
        u2 = upper[-2] if upper[-2] is not None else u1
        l2 = lower[-2] if lower[-2] is not None else l1
        s8 = sma8[-1]

        green = last.type == "UP"
        red   = last.type == "DOWN"

        # ── EXIT SIGNALS ──
        if self.in_buy and red and price < s8:
            self.in_buy = False
            return Signal(
                strategy_num=3,
                strategy_name="VWAP Breakout",
                signal_type="EXIT_BUY",
                price=price,
                sl=None, tp=None,
                message=(
                    f"⚠️ *EXIT BUY — Strategy 3 (VWAP)*\n"
                    f"Brick closed below 8 SMA (${s8:.3f})\n"
                    f"Price: ${price:.3f}\n"
                    f"📌 Close or manage your BUY trade now!"
                )
            )

        if self.in_sell and green and price > s8:
            self.in_sell = False
            return Signal(
                strategy_num=3,
                strategy_name="VWAP Breakout",
                signal_type="EXIT_SELL",
                price=price,
                sl=None, tp=None,
                message=(
                    f"⚠️ *EXIT SELL — Strategy 3 (VWAP)*\n"
                    f"Brick closed above 8 SMA (${s8:.3f})\n"
                    f"Price: ${price:.3f}\n"
                    f"📌 Close or manage your SELL trade now!"
                )
            )

        prev_inside = l2 <= prev_price <= u2

        # ── SCENARIO 1: Was outside → came back → breaks again ──
        scenario1_buy = (
            self.prev_was_outside_upper and
            prev_inside and
            price > u1 and green
        )
        scenario1_sell = (
            self.prev_was_outside_lower and
            prev_inside and
            price < l1 and red
        )

        # ── SCENARIO 2: Was inside → first clean breakout ──
        scenario2_buy  = prev_inside and price > u1 and green
        scenario2_sell = prev_inside and price < l1 and red

        buy_signal  = (scenario1_buy  or scenario2_buy)  and not self.in_buy
        sell_signal = (scenario1_sell or scenario2_sell) and not self.in_sell

        # Track outside status for next brick
        self.prev_was_outside_upper = price > u1
        self.prev_was_outside_lower = price < l1

        if buy_signal:
            self.in_buy = True
            sc = "Returned inside → broke above again" if scenario1_buy else "Direct breakout above band"
            return Signal(
                strategy_num=3,
                strategy_name="VWAP Breakout",
                signal_type="BUY",
                price=price,
                sl=round(lower[-1], 3),
                tp=None,
                message=(
                    f"🟢 *GOLD BUY — Strategy 3 (VWAP Breakout)*\n"
                    f"Scenario: {sc}\n"
                    f"Brick Close: ${price:.3f}\n"
                    f"VWAP Upper Band: ${u1:.3f}\n"
                    f"8 SMA: ${s8:.3f}\n"
                    f"SL Reference: ${lower[-1]:.3f} (lower band)\n"
                    f"Exit: When brick closes below 8 SMA"
                )
            )

        if sell_signal:
            self.in_sell = True
            sc = "Returned inside → broke below again" if scenario1_sell else "Direct breakout below band"
            return Signal(
                strategy_num=3,
                strategy_name="VWAP Breakout",
                signal_type="SELL",
                price=price,
                sl=round(upper[-1], 3),
                tp=None,
                message=(
                    f"🔴 *GOLD SELL — Strategy 3 (VWAP Breakout)*\n"
                    f"Scenario: {sc}\n"
                    f"Brick Close: ${price:.3f}\n"
                    f"VWAP Lower Band: ${l1:.3f}\n"
                    f"8 SMA: ${s8:.3f}\n"
                    f"SL Reference: ${upper[-1]:.3f} (upper band)\n"
                    f"Exit: When brick closes above 8 SMA"
                )
            )

        return None


# ─────────────────────────────────────────────
# STRATEGY 4: DBOX (Darvas Box) Breakout
# ─────────────────────────────────────────────
class Strategy4_DarvasBox:
    """
    Rules:
    - Darvas Box from Daily OHLC data (box_length=5)
    - BUY: brick closes above Top Box
    - SELL: brick closes below Bottom Box
    - SL: opposite side of box
    - Exit BUY: brick closes below 21 SMA
    - Exit SELL: brick closes above 21 SMA
    """
    def __init__(self, box_length=5):
        self.box_length = box_length
        self.in_buy  = False
        self.in_sell = False
        self.top_box    = None
        self.bottom_box = None

    def update_daily_box(self, daily_highs: list[float], daily_lows: list[float]):
        """Update Darvas Box from latest daily candles"""
        self.top_box, self.bottom_box = calc_darvas_box(
            daily_highs, daily_lows, self.box_length
        )

    def check(self, engine: RenkoEngine) -> Optional[Signal]:
        bricks = engine.bricks
        if len(bricks) < 22:
            return None
        if self.top_box is None or self.bottom_box is None:
            return None

        closes = engine.get_closes()
        sma21  = calc_sma(closes, 21)

        if sma21[-1] is None:
            return None

        last  = bricks[-1]
        price = last.close
        s21   = sma21[-1]
        green = last.type == "UP"
        red   = last.type == "DOWN"

        # ── EXIT SIGNALS ──
        if self.in_buy and red and price < s21:
            self.in_buy = False
            return Signal(
                strategy_num=4,
                strategy_name="Darvas Box",
                signal_type="EXIT_BUY",
                price=price,
                sl=None, tp=None,
                message=(
                    f"⚠️ *EXIT BUY — Strategy 4 (DBOX)*\n"
                    f"Brick closed below 21 SMA (${s21:.3f})\n"
                    f"Price: ${price:.3f}\n"
                    f"📌 Close or manage your BUY trade now!"
                )
            )

        if self.in_sell and green and price > s21:
            self.in_sell = False
            return Signal(
                strategy_num=4,
                strategy_name="Darvas Box",
                signal_type="EXIT_SELL",
                price=price,
                sl=None, tp=None,
                message=(
                    f"⚠️ *EXIT SELL — Strategy 4 (DBOX)*\n"
                    f"Brick closed above 21 SMA (${s21:.3f})\n"
                    f"Price: ${price:.3f}\n"
                    f"📌 Close or manage your SELL trade now!"
                )
            )

        # ── ENTRY SIGNALS ──
        if price > self.top_box and green and not self.in_buy:
            self.in_buy = True
            return Signal(
                strategy_num=4,
                strategy_name="Darvas Box",
                signal_type="BUY",
                price=price,
                sl=round(self.bottom_box, 3),
                tp=None,
                message=(
                    f"🟢 *GOLD BUY — Strategy 4 (Darvas Box)*\n"
                    f"Brick closed above Top Box ✅\n"
                    f"Entry: ${price:.3f}\n"
                    f"Top Box: ${self.top_box:.3f} | Bottom Box: ${self.bottom_box:.3f}\n"
                    f"SL: ${self.bottom_box:.3f} (opposite side)\n"
                    f"Exit: When brick closes below 21 SMA (${s21:.3f})"
                )
            )

        if price < self.bottom_box and red and not self.in_sell:
            self.in_sell = True
            return Signal(
                strategy_num=4,
                strategy_name="Darvas Box",
                signal_type="SELL",
                price=price,
                sl=round(self.top_box, 3),
                tp=None,
                message=(
                    f"🔴 *GOLD SELL — Strategy 4 (Darvas Box)*\n"
                    f"Brick closed below Bottom Box ✅\n"
                    f"Entry: ${price:.3f}\n"
                    f"Top Box: ${self.top_box:.3f} | Bottom Box: ${self.bottom_box:.3f}\n"
                    f"SL: ${self.top_box:.3f} (opposite side)\n"
                    f"Exit: When brick closes above 21 SMA (${s21:.3f})"
                )
            )

        return None
