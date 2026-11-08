"""Build symmetric one-way setups and replay them on confirmed 4H candles."""

from dataclasses import dataclass
from statistics import fmean, median

from okx_data import Candle

STRATEGY_VERSION = "bitjjang-one-way-v1.0.0"
COMPRESSION = 0.005
EXPANSION_CHANGE = 0.001
SLOPE_BARS = 6
MAX_GAP = 0.015
STRUCTURE_BARS = 180
ATR_BARS = 14
FIRST_WEIGHT, SECOND_WEIGHT = 0.40, 0.60
ARRAY_LABELS = {1: "정배열", -1: "역배열", 0: "혼조"}
SIGNALS = {1: "LONG", -1: "SHORT", 0: "WAIT"}


@dataclass(frozen=True)
class Trend:
    direction: int
    regime: str
    spread: float
    spread_change: float
    slope: float


@dataclass(frozen=True)
class Plan:
    direction: int
    anchor: int
    entry1: float
    entry2: float
    zone: tuple[float, float]
    average: float
    structural_stop: float
    short_target: float | None
    short_source: str
    trend_target: float | None
    trend_source: str


@dataclass(frozen=True)
class Setup:
    trend: Trend
    checks: dict[str, tuple[bool, str]]
    relative_volume: float | None
    distance: float | None
    plan: Plan | None

    @property
    def ready(self) -> bool:
        return all(passed for passed, _ in self.checks.values())


@dataclass
class Position:
    plan: Plan
    filled_second: bool
    first_ts: int
    second_ts: int | None = None
    sma60_armed: bool = False


@dataclass(frozen=True)
class Decision:
    setup: Setup
    status: str
    plan: Plan | None
    position: Position | None
    stop: float | None
    stop_source: str
    last_event: str
    event_ts: int | None
    event_price: float | None
    exit_style: str

    @property
    def entry_signal(self) -> str:
        if self.position or not self.setup.ready:
            return "WAIT"
        return SIGNALS[self.setup.trend.direction]


def side_name(direction: int) -> str:
    return {1: "롱", -1: "숏", 0: "중립"}[direction]


def trend_at(candles: list[Candle], sma: dict[int, list[float]], i: int) -> Trend:
    fast, mid, slow = (sma[period][i] for period in (7, 21, 60))
    direction = 1 if fast > mid > slow else -1 if fast < mid < slow else 0

    def spread(index):
        values = [sma[period][index] for period in (7, 21, 60)]
        return (max(values) - min(values)) / candles[index].close

    width = spread(i)
    change = width - spread(i - SLOPE_BARS)
    slope = slow / sma[60][i - SLOPE_BARS] - 1
    regime = ("응축" if width < COMPRESSION else
              "발산" if change >= EXPANSION_CHANGE else
              "수렴" if change <= -EXPANSION_CHANGE else "유지")
    return Trend(direction, regime, width, change, slope)


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
        zones.append((wick, pick_extreme(candle.open, candle.close)))

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


def targets(direction: int, close: float, values: dict[int, float],
            zones: list[tuple[float, float]], atr: float):
    # Signed prices let the same comparisons work for either side.
    start = direction * close
    barriers = sorted(sorted(direction * edge for edge in zone) for zone in zones)
    structure = [(inner, "반복 스윙") for inner, _ in barriers if inner > start]
    nearby = [(direction * values[p], f"SMA{p}") for p in (7, 21, 120)]
    short, short_source = min((level for level in nearby + structure if level[0] > start),
                              default=(None, ""))
    floor = short if short is not None else start
    long_smas = [(direction * values[p], f"SMA{p}") for p in (224, 365)
                 if direction * values[p] > floor]
    overlaps = [(level, f"{source} + 반복 스윙") for level, source in long_smas
                if any(inner > start and inner - atr <= level <= outer + atr
                       for inner, outer in barriers)]
    distant_structure = [level for level in structure if level[0] > floor]
    trend, trend_source = min(overlaps or long_smas or distant_structure, default=(None, ""))
    return (None if short is None else direction * short, short_source,
            None if trend is None else direction * trend, trend_source)


def analyze_at(candles: list[Candle], sma: dict[int, list[float]], i: int,
               market: Trend) -> Setup:
    candle = candles[i]
    close = candle.close
    trend = trend_at(candles, sma, i)
    direction = trend.direction
    values = {period: series[i] for period, series in sma.items()}
    baseline = fmean(c.volume_quote for c in candles[i - 20:i])
    relative_volume = candle.volume_quote / baseline if baseline else None
    checks = {
        "배열": (bool(direction), f"7/21/60 {ARRAY_LABELS[direction]}"),
        "BTC": (bool(direction and direction == market.direction and
                     market.regime != "응축" and direction * market.slope > 0),
                f"BTC {side_name(market.direction)} · {market.regime} · 60선 {market.slope:+.2%}"),
        "장세": (trend.regime != "응축", f"{trend.regime} {trend.spread:.2%} (기준 {COMPRESSION:.2%})"),
        "기울기": (direction * trend.slope > 0, f"60선 6봉 {trend.slope:+.2%}"),
        "방어선": (all(direction * (close - values[p]) > 0 for p in (60, 120)),
                  f"60 {values[60]:,.2f} / 120 {values[120]:,.2f}"),
    }
    anchors = sorted((abs(close / values[p] - 1), p) for p in (21, 60)
                     if direction * (close - values[p]) > 0)
    distance, anchor = anchors[0] if anchors else (None, None)
    plan = None
    if anchors:
        history = candles[i - STRUCTURE_BARS + 1:i + 1]
        atr = fmean(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
                    for prev, c in zip(history[-ATR_BARS - 1:-1], history[-ATR_BARS:]))
        entry_zones = pivot_zones(history, atr, lows=direction > 0)
        exit_zones = pivot_zones(history, atr, lows=direction < 0)
        for gap, period in anchors:
            entry1 = values[period]
            candidates = []
            for zone in entry_zones:
                outer, inner = sorted(direction * edge for edge in zone)
                if direction * values[120] < inner < direction * entry1 and outer < inner:
                    candidates.append((inner, outer, zone))
            if not candidates:
                continue
            inner, outer, zone = max(candidates)
            entry2 = direction * inner
            average = 1 / (FIRST_WEIGHT / entry1 + SECOND_WEIGHT / entry2)
            plan = Plan(direction, period, entry1, entry2, zone, average, direction * outer,
                        *targets(direction, close, values, exit_zones, atr))
            distance, anchor = gap, period
            break

    checks["이격"] = (distance is not None and distance <= MAX_GAP,
                     f"SMA{anchor} {distance:.2%} (한도 {MAX_GAP:.2%})" if anchor else "유효 SMA 없음")
    checks["2차 구조"] = (plan is not None,
                         f"{plan.zone[0]:,.2f}~{plan.zone[1]:,.2f}" if plan else "120선 안쪽 반복 스윙 없음")
    checks["목표"] = (bool(plan and (plan.short_target or plan.trend_target)),
                     "순방향 목표 확인" if plan and (plan.short_target or plan.trend_target)
                     else "종가 너머 목표 없음")
    return Setup(trend, checks, relative_volume, distance, plan)


def close_stop(plan: Plan, sma60: float, sma120: float, sma60_armed: bool = False):
    direction = plan.direction
    levels = [(direction * plan.structural_stop, "구조"), (direction * sma120, "SMA120")]
    if direction * (sma60 - plan.entry2) < 0 or sma60_armed:
        levels.append((direction * sma60, "SMA60"))
    signed, source = max(levels)
    return direction * signed, source


def advance(position: Position | None, plan: Plan, candle: Candle,
            sma60: float, sma120: float, exit_style: str, allow_add: bool = True):
    """Return the simulated position and any event. Evaluate the close stop first."""
    direction = plan.direction
    stop, _ = close_stop(plan, sma60, sma120, bool(position and position.sma60_armed))
    if direction * (candle.close - stop) < 0:
        return None, "종가 무효화" if position else "", stop
    adverse = candle.low if direction == 1 else candle.high
    if position is None:
        if direction * (adverse - plan.entry1) > 0:
            return None, "", None
        position = Position(plan, False, candle.ts)
        event, price = "1차 모의 진입", plan.entry1
    else:
        event, price = "", None

    if allow_add and not position.filled_second and direction * (adverse - plan.entry2) <= 0:
        position.filled_second = True
        position.second_ts = candle.ts
        event, price = "2차 모의 진입", plan.entry2

    target = (plan.trend_target or plan.short_target) if exit_style == "trend" else (
        plan.short_target or plan.trend_target
    )
    if not event and target is not None and direction * (candle.close - target) >= 0:
        return None, "모의 익절", candle.close
    if position.filled_second and direction * (candle.close - sma60) > 0:
        position.sma60_armed = True
    return position, event, price


def replay(candles: list[Candle], sma: dict[int, list[float]], market: list[Trend],
           exit_style: str = "short") -> Decision:
    position = None
    pending = None
    last_event, event_ts, event_price = "", None, None
    for i in range(364, len(candles)):
        candle = candles[i]
        plan = position.plan if position else pending
        if plan:
            allow_add = not position or (setup.trend.direction == plan.direction and setup.checks["BTC"][0])
            position, event, price = advance(position, plan, candle, sma[60][i], sma[120][i],
                                             exit_style, allow_add)
            if event:
                last_event, event_ts, event_price = event, candle.ts, price
        setup = analyze_at(candles, sma, i, market[i - 364])
        pending = setup.plan if setup.ready and not position else None

    plan = position.plan if position else setup.plan
    stop, source = (close_stop(plan, sma[60][-1], sma[120][-1], bool(position and position.sma60_armed))
                    if plan else (None, ""))
    if position:
        status = "2차 모의 보유" if position.filled_second else "1차 모의 보유"
    elif last_event == "모의 익절":
        status = "재진입 후보" if setup.ready else "재진입 대기"
    elif setup.ready:
        status = "신규 진입 후보"
    else:
        status = "관망"
    return Decision(setup, status, plan, position, stop, source, last_event, event_ts, event_price, exit_style)
