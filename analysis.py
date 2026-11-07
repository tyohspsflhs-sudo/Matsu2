"""Derive conditional pullback levels from confirmed candles and SMAs."""

from dataclasses import dataclass
from statistics import fmean, median

from okx_data import Candle


@dataclass(frozen=True)
class Setup:
    regime: str
    relative_volume: float | None
    support: tuple[float, float] | None
    resistance: float | None
    distance: float
    entry1: float | None = None
    entry2: float | None = None
    average: float | None = None
    stop: float | None = None
    short_target: float | None = None
    trend_target: float | None = None
    confluence: bool = False
    market_support: tuple[float, float] | None = None


def regime_of(candles: list[Candle], sma: dict[int, list[float]]) -> str:
    fast, mid, slow = (sma[period][-1] for period in (7, 21, 60))
    spread = (max(fast, mid, slow) - min(fast, mid, slow)) / candles[-1].close
    if spread < 0.005:
        return "COMPRESSED"
    if fast > mid > slow and slow > sma[60][-7]:
        return "BULLISH"
    if fast < mid < slow and slow < sma[60][-7]:
        return "BEARISH"
    return "MIXED"


def pivot_zones(candles: list[Candle], atr: float, lows: bool) -> list[tuple[float, float]]:
    extremes = [c.low if lows else c.high for c in candles]
    pick_extreme = min if lows else max
    zones = []
    for i in range(2, len(candles) - 2):
        neighbors = extremes[i - 2:i + 3]
        wick = extremes[i]
        if wick != pick_extreme(neighbors) or neighbors.count(wick) != 1:
            continue
        candle = candles[i]
        body = pick_extreme(candle.open, candle.close)
        zones.append((wick, body))

    clusters = []
    for wick, body in sorted(zones):
        if not clusters or wick - clusters[-1][0][0] > atr:
            clusters.append([])
        clusters[-1].append((wick, body))
    return [
        tuple(sorted((pick_extreme(wick for wick, _ in cluster),
                      median(body for _, body in cluster))))
        for cluster in clusters if len(cluster) >= 2
    ]


def analyze(candles: list[Candle], sma: dict[int, list[float]],
            daily_sma: dict[int, list[float]], market_bullish: bool) -> Setup:
    close = candles[-1].close
    anchor = sma[60][-1]
    regime = regime_of(candles, sma)
    baseline_volume = fmean(c.volume_quote for c in candles[-21:-1])
    relative_volume = candles[-1].volume_quote / baseline_volume if baseline_volume else None
    atr = fmean(
        max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
        for prev, c in zip(candles[-15:-1], candles[-14:])
    )
    supports = pivot_zones(candles[-180:], atr, lows=True)
    resistances = pivot_zones(candles[-180:], atr, lows=False)
    support = max((zone for zone in supports if zone[1] < anchor), default=None)
    market_support = max((zone for zone in supports if zone[1] < close), default=None)
    resistance = min((low for low, _ in resistances if low > close), default=None)
    distance = (close - anchor) / close
    facts = (regime, relative_volume, support, resistance, distance)
    if not (market_bullish and regime == "BULLISH" and close > anchor
            and close > sma[120][-1] and support):
        return Setup(*facts, market_support=market_support)

    stop, entry2 = support
    if stop >= entry2:
        return Setup(*facts, market_support=market_support)
    # The 40:60 allocation is by quote budget, so divide total cost by total quantity.
    average = 1 / (0.4 / anchor + 0.6 / entry2)
    short = min((level for level in (sma[7][-1], sma[21][-1], sma[120][-1], resistance)
                 if level is not None and level > close), default=None)
    floor = max(close, short or close)
    long_levels = [sma[224][-1], sma[365][-1], daily_sma[60][-1], daily_sma[365][-1]]
    above = [level for level in long_levels if level > floor]
    overlap = [
        level for level in above
        if any(low > floor and low - atr <= level <= high + atr for low, high in resistances)
    ]
    trend = min(overlap or above, default=None)
    return Setup(*facts, entry1=anchor, entry2=entry2, average=average, stop=stop,
                 short_target=short, trend_target=trend, confluence=bool(overlap),
                 market_support=market_support)
