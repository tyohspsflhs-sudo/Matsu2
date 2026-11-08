import unittest
from unittest.mock import patch

from analysis import (Plan, Setup, Trend, advance, analyze_at, close_stop, pivot_zones,
                      replay, trend_at)
from dashboard import five_min_evidence
from okx_data import Candle


def candle(close, low=None, high=None, ts=0):
    return Candle(ts, close, high if high is not None else close + 1,
                  low if low is not None else close - 1, close, 10, 1_000)


def series(direction=1, last=101):
    candles = [candle(100)] * 364 + [candle(100 + direction * (last - 100))]
    values = {7: 101.2, 21: 100.5, 60: 99.8, 120: 96, 224: 105, 365: 108}
    sma = {p: [100 + direction * (v - 100)] * len(candles) for p, v in values.items()}
    for p, earlier in ((7, 100.3), (21, 100.0), (60, 99.6)):
        sma[p][-7] = 100 + direction * (earlier - 100)
    return candles, sma


def plan_for(direction=1):
    def price(value):
        return 100 + direction * (value - 100)
    return Plan(direction, 21, price(100), price(97), tuple(sorted((price(95), price(97)))),
                price(98.2), price(95), price(104), "SMA7", price(110), "SMA224")


class StrategyTests(unittest.TestCase):
    def test_one_way_and_symmetric_levels(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                candles, sma = series(direction)
                i = len(candles) - 1
                market = trend_at(candles, sma, i)
                zones = {True: [(97, 98)], False: [(102, 103)]}
                with patch("analysis.pivot_zones", side_effect=lambda _, atr, lows: zones[lows]):
                    setup = analyze_at(candles, sma, i, market)
                    opposite = analyze_at(candles, sma, i, Trend(-direction, "발산", .01, .002, -.01 * direction))
                self.assertTrue(setup.ready, setup.checks)
                self.assertEqual(market.regime, "발산")
                self.assertEqual(setup.plan.direction, direction)
                self.assertEqual(setup.plan.anchor, 21)
                self.assertGreater(direction * (setup.plan.entry1 - setup.plan.entry2), 0)
                self.assertGreater(direction * (setup.plan.short_target - candles[-1].close), 0)
                self.assertGreater(direction * (setup.plan.trend_target - setup.plan.short_target), 0)
                self.assertAlmostEqual(setup.plan.average, 1 / (.4 / setup.plan.entry1 + .6 / setup.plan.entry2))
                self.assertFalse(opposite.ready)
                self.assertFalse(opposite.checks["BTC"][0])

    def test_compression_and_chasing_block(self):
        candles, sma = series(last=105)
        i = len(candles) - 1
        market = trend_at(candles, sma, i)
        with patch("analysis.pivot_zones", side_effect=[[(97, 98)], [(106, 107)]]):
            extended = analyze_at(candles, sma, i, market)
        self.assertIsNotNone(extended.plan)
        self.assertFalse(extended.checks["이격"][0])
        self.assertFalse(extended.ready)
        sma[7][-1], sma[21][-1], sma[60][-1] = 100.2, 100.1, 100.0
        compressed = analyze_at(candles, sma, i, market)
        self.assertEqual(compressed.trend.direction, 1)
        self.assertEqual(compressed.trend.regime, "응축")
        self.assertFalse(compressed.checks["장세"][0])

    def test_two_confirmed_swings_form_structure(self):
        lows = [101, 101, 96, 101, 101, 101, 97, 101, 101, 95]
        candles = [candle(102, low=low) for low in lows]
        self.assertEqual(pivot_zones(candles, 1, lows=True), [(96, 102)])
        self.assertEqual(pivot_zones(candles, .5, lows=True), [])
        self.assertEqual(pivot_zones(candles, 1, lows=False), [])

    def test_stop_from_structure_sma120_and_sma60(self):
        self.assertEqual(close_stop(plan_for(), 99, 94), (95, "구조"))
        self.assertEqual(close_stop(plan_for(), 99, 96), (96, "SMA120"))
        self.assertEqual(close_stop(plan_for(), 96.5, 96), (96.5, "SMA60"))
        self.assertEqual(close_stop(plan_for(), 99, 96, True), (99, "SMA60"))
        self.assertEqual(close_stop(plan_for(-1), 101, 104, True), (101, "SMA60"))

    def test_fill_close_priority_and_reclaimed_sma60(self):
        plan = plan_for()
        pos, event, _ = advance(None, plan, candle(99, 94, 105), 98, 96, "short")
        self.assertEqual(event, "2차 모의 진입")
        self.assertTrue(pos.filled_second)
        self.assertTrue(pos.sma60_armed)
        pos, event, _ = advance(pos, plan, candle(98, 94, 103), 98, 96, "short")
        self.assertIsNotNone(pos)  # Wick and equal close do not invalidate.
        pos, event, _ = advance(pos, plan, candle(97.9, 94, 105), 98, 96, "short")
        self.assertIsNone(pos)
        self.assertEqual(event, "종가 무효화")

    def test_short_touch_and_stop(self):
        plan = plan_for(-1)
        pos, event, _ = advance(None, plan, candle(100, 99, 104), 102, 104, "short")
        self.assertEqual(event, "2차 모의 진입")
        self.assertTrue(pos.sma60_armed)
        pos, event, _ = advance(pos, plan, candle(103, 95, 105), 102, 104, "short")
        self.assertIsNone(pos)
        self.assertEqual(event, "종가 무효화")

    def test_second_entry_can_wait_for_direction_alignment(self):
        plan = plan_for()
        pos, _, _ = advance(None, plan, candle(100, 99, 102), 98, 96, "short")
        pos, event, _ = advance(pos, plan, candle(99, 96, 103), 98, 96, "short", allow_add=False)
        self.assertFalse(pos.filled_second)
        self.assertEqual(event, "")

    def test_take_profit_and_fresh_reentry(self):
        plan = plan_for()
        trend = Trend(1, "발산", .01, .002, .01)
        setup = Setup(trend, {"BTC": (True, "")}, 1, .01, plan)
        candles = [candle(101)] * 364 + [
            candle(101, 99, 103, 1), candle(101, 99, 104, 2),
            candle(102, 100.5, 105, 3), candle(104, 99, 104, 4),
        ]
        sma = {60: [98] * len(candles), 120: [96] * len(candles)}
        with patch("analysis.analyze_at", return_value=setup):
            closed = replay(candles, sma, [trend] * 4)
            reentered = replay(candles + [candle(100, 99, 103, 5)],
                               {p: arr + [arr[-1]] for p, arr in sma.items()}, [trend] * 5)
            held = replay(candles, sma, [trend] * 4, "trend")
        self.assertEqual((closed.status, closed.last_event, closed.event_ts),
                         ("재진입 후보", "모의 익절", 4))
        self.assertEqual(closed.entry_signal, "LONG")
        self.assertEqual(reentered.entry_signal, "WAIT")
        self.assertEqual(reentered.position.first_ts, 5)
        self.assertEqual(held.position.first_ts, 2)

    def test_short_take_profit_requires_close(self):
        plan = plan_for(-1)
        pos, _, _ = advance(None, plan, candle(99, 97, 101), 102, 104, "short")
        pos, event, _ = advance(pos, plan, candle(97, 95, 100), 102, 104, "short")
        self.assertIsNotNone(pos)
        self.assertEqual(event, "")
        pos, event, price = advance(pos, plan, candle(95, 94, 100), 102, 104, "short")
        self.assertIsNone(pos)
        self.assertEqual((event, price), ("모의 익절", 95))

    def test_missing_5m_is_never_confirmed(self):
        self.assertEqual(five_min_evidence(None)["detail"], "미수집")
        self.assertEqual(five_min_evidence(None, "test failure")["status"], "미확인")
        evidence = five_min_evidence([candle(100, ts=i * 300_000) for i in range(60)])
        self.assertEqual(evidence["status"], "확인")
        self.assertEqual(evidence["sma21_gap"], 0)
        self.assertEqual(evidence["relative_volume"], 1)


if __name__ == "__main__":
    unittest.main()
