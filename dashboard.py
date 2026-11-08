"""Render the BTC, ETH and SOL report from validated OKX candles."""

import argparse
import csv
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from tempfile import TemporaryDirectory

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MaxNLocator

from analysis import ARRAY_LABELS, Decision, replay, side_name, trend_at
from okx_data import BAR_MS, FIVE_MIN_MS, SYMBOLS, fetch_candles, moving_averages, utc_time, write_csv

KST = timezone(timedelta(hours=9), "KST")
BG, PANEL, GRID = "#05060a", "#0b1018", "#20293a"
TEXT, MUTED = "#edf6ff", "#9daec7"
UP, DOWN = "#38ffb1", "#ff5684"
COLORS = {7: "#20ebff", 21: "#ffdb55", 60: "#c189ff",
          120: "#ff9c54", 224: "#79a8ff", 365: "#ff6fcd"}
ASSET_COLORS = {"BTC": "#ff9c54", "ETH": "#c189ff", "SOL": "#38ffb1"}
SIDE_COLORS = {1: UP, -1: DOWN, 0: MUTED}


def price(value: float | None) -> str:
    if value is None:
        return "—"
    precision = 4 if value < 100 else 2
    return f"{value:,.{precision}f}"


def configure_font() -> None:
    fonts = (
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "malgun.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    )
    for font in fonts:
        if font.is_file():
            font_manager.fontManager.addfont(font)
            matplotlib.rcParams["font.family"] = [
                font_manager.FontProperties(fname=font).get_name(), "DejaVu Sans"
            ]
            return
    raise RuntimeError("Korean chart font unavailable. Install fonts-noto-cjk on Linux.")


def style_axis(ax) -> None:
    ax.set_facecolor(PANEL)
    ax.set_axisbelow(True)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MaxNLocator(5))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:,.0f}"))
    ax.tick_params(colors=MUTED, labelsize=8, length=0, pad=5)
    for spine in ax.spines.values():
        spine.set_color(GRID)


def draw_price(ax, candles, averages, count, decision: Decision | None = None):
    visible = candles[-count:]
    times = mdates.date2num([utc_time(candle.ts) for candle in visible])
    step = times[1] - times[0]
    colors = [UP if c.close >= c.open else DOWN for c in visible]
    ax.vlines(times, [c.low for c in visible], [c.high for c in visible],
              colors=colors, linewidth=0.6)
    ax.bar(times, [abs(c.close - c.open) for c in visible],
           bottom=[min(c.close, c.open) for c in visible],
           color=colors, width=0.68 * step, linewidth=0)
    for period, values in averages.items():
        ax.plot(times, values[-count:], color=COLORS[period], lw=1.12, alpha=0.94)
    if decision is not None and decision.plan is not None:
        plan = decision.plan
        ax.axhspan(*plan.zone, color=SIDE_COLORS[plan.direction], alpha=0.12)
        for level, color in ((plan.entry1, COLORS[plan.anchor]), (plan.entry2, COLORS[7]),
                             (plan.average, TEXT), (plan.short_target, UP),
                             (plan.trend_target, UP), (decision.stop, DOWN)):
            if level is not None:
                ax.axhline(level, color=color, lw=.8, ls="--", alpha=.65)
    ax.set_xlim(times[0] - step, times[-1] + step)
    style_axis(ax)
    return times, colors


def date_ticks(ax, interval: int) -> None:
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=interval, tz=KST))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=KST))


def add_setup_card(ax, decision: Decision, micro: dict) -> None:
    ax.set_facecolor(PANEL)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color(GRID)

    def line(y, label, color=MUTED, size=10, weight="normal", x=.045):
        ax.text(x, y, label, transform=ax.transAxes, color=color, fontsize=size,
                fontweight=weight, va="center")

    setup, plan = decision.setup, decision.plan
    direction = plan.direction if plan else setup.trend.direction
    prefix = f"{side_name(direction)} · " if direction else ""
    line(.951, prefix + decision.status, SIDE_COLORS[direction], 14, "bold")
    line(.893, "신규 진입 판단  /  최신 확정 4H", COLORS[7], 10, "bold")
    for i, (name, (passed, reason)) in enumerate(setup.checks.items()):
        y = .846 - i * .037
        line(y, "충족" if passed else "미충족", UP if passed else DOWN, 8.5)
        line(y, f"{name}  {reason}", TEXT, 9, x=.154)

    volume = f"{setup.relative_volume:.2f}배" if setup.relative_volume is not None else "미확인"
    line(.540, f"4H 상대 거래량 {volume}  /  이격 변화 {setup.trend.spread_change * 100:+.2f}%p", size=9)
    line(.502, f"5m {micro['status']}  ·  {micro['detail']}", size=9)
    ax.axhline(.471, xmin=.045, xmax=.955, color=GRID, lw=.7)
    line(.436, "분할 진입 · 익절 · 종가 무효화", COLORS[7], 10, "bold")
    if plan:
        label = "모의 보유 계획" if decision.position else "조건부 계획" if setup.ready else "관찰 가격"
        line(.391, f"{label}  /  1차 SMA{plan.anchor} {price(plan.entry1)}  ·  2차 {price(plan.entry2)}", TEXT, 9)
        line(.349, f"예상 평단 {price(plan.average)}  (USDT 예산 40/60 가정)", TEXT, 9)
        line(.307, f"단기 TP  {price(plan.short_target)}  {plan.short_source}", TEXT, 9)
        line(.265, f"추세 TP  {price(plan.trend_target)}  {plan.trend_source}", TEXT, 9)
        operator = "<" if plan.direction == 1 else ">"
        line(.223, f"SL  4H 종가 {operator} {price(decision.stop)}  ({decision.stop_source})", TEXT, 9)
        line(.181, f"구조 경계 {price(plan.structural_stop)}  /  2차 구간 {price(plan.zone[0])}~{price(plan.zone[1])}", size=9)
    else:
        line(.391, "유효한 두 단계 가격 구조 없음", TEXT, 9)
    ax.axhline(.149, xmin=.045, xmax=.955, color=GRID, lw=.7)
    if decision.event_ts is not None:
        event_time = utc_time(decision.event_ts + BAR_MS["4H"]).astimezone(KST)
        event = f"{decision.last_event}  {price(decision.event_price)}  ·  {event_time:%m/%d %H:%M}"
    else:
        event = "기록된 모의 이벤트 없음"
    line(.107, event, size=9)
    style = "단기 우선" if decision.exit_style == "short" else "추세 우선"
    line(.062, f"확정봉 모의 추적  /  {style}", size=9)


def five_min_evidence(candles: list | None, failure: str | None = None) -> dict:
    if not candles:
        return {"status": "미확인", "detail": "수집 또는 검증 실패" if failure else "미수집",
                "close_time_utc": "", "sma21_gap": None, "relative_volume": None,
                "error": failure or ""}
    sma21 = fmean(c.close for c in candles[-21:])
    gap = candles[-1].close / sma21 - 1
    baseline = fmean(c.volume_quote for c in candles[-21:-1])
    volume = candles[-1].volume_quote / baseline if baseline else None
    timestamp = utc_time(candles[-1].ts + FIVE_MIN_MS)
    volume_text = f"{volume:.2f}배" if volume is not None else "미확인"
    return {"status": "확인", "detail": f"{timestamp.astimezone(KST):%H:%M} SMA21 {gap:+.2%} · 거래량 {volume_text}",
            "close_time_utc": timestamp.isoformat(), "sma21_gap": gap,
            "relative_volume": volume, "error": ""}


def decision_rows(data: dict, averages: dict, decisions: dict, micro: dict) -> list[dict]:
    rows = []
    market = decisions["BTC"].setup.trend
    for symbol, decision in decisions.items():
        setup, plan, position = decision.setup, decision.plan, decision.position
        candle = data[symbol, "4H"][-1]
        row = {
            "instrument": f"{symbol}-USDT", "close_time_utc": close_timestamp(candle.ts),
            "close": candle.close, "direction": side_name(plan.direction if plan else setup.trend.direction),
            "status": decision.status, "array": ARRAY_LABELS[setup.trend.direction],
            "regime": setup.trend.regime, "btc_array": ARRAY_LABELS[market.direction],
            "btc_regime": market.regime, "sma_spread": setup.trend.spread,
            "sma_spread_change_6_bars": setup.trend.spread_change, "sma60_slope_6_bars": setup.trend.slope,
            "entry_gap": setup.distance, "relative_volume": setup.relative_volume,
            "entry_conditions_met": setup.ready,
            "ready_for_new_entry": setup.ready and position is None, "exit_style": decision.exit_style,
            "entry1_filled": bool(position), "entry2_filled": bool(position and position.filled_second),
            "first_fill_close_utc": close_timestamp(position.first_ts) if position else "",
            "second_fill_close_utc": close_timestamp(position.second_ts) if position else "",
            "last_event": decision.last_event, "event_close_time_utc": close_timestamp(decision.event_ts),
            "event_reference_price": decision.event_price, "stop_close_level": decision.stop,
            "stop_source": decision.stop_source,
            "stop_operator": ("<" if plan.direction == 1 else ">") if plan else "",
            "allocation_assumption": "40/60 of USDT budget",
            "strategy_source": "user-provided rules", "video_directly_verified": False,
            "plan_kind": "simulated_position" if position else "conditional" if setup.ready else "watch_only",
        }
        for key in ("anchor", "entry1", "entry2", "average", "structural_stop",
                    "short_target", "short_source", "trend_target", "trend_source"):
            row[key] = getattr(plan, key) if plan else None
        row["zone_low"], row["zone_high"] = plan.zone if plan else (None, None)
        row.update({f"sma_{period}": values[-1] for period, values in averages[symbol, "4H"].items()})
        for key, (passed, reason) in setup.checks.items():
            row[f"{key}_충족"], row[f"{key}_근거"] = passed, reason
        row.update({f"5m_{key}": value for key, value in micro[symbol].items()})
        rows.append(row)
    return rows


def close_timestamp(open_ts: int | None) -> str:
    return utc_time(open_ts + BAR_MS["4H"]).isoformat() if open_ts is not None else ""


def render(path: Path, data: dict, averages: dict, micro: dict, exit_style: str) -> None:
    configure_font()
    btc, btc_sma = data["BTC", "4H"], averages["BTC", "4H"]
    market_history = [trend_at(btc, btc_sma, i) for i in range(364, len(btc))]
    market = market_history[-1]
    decisions = {
        symbol: replay(data[symbol, "4H"], averages[symbol, "4H"], market_history, exit_style)
        for symbol in SYMBOLS
    }
    rows = decision_rows(data, averages, decisions, micro)
    with (path / "entry_decisions.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    candidate = min((s for s in SYMBOLS if decisions[s].setup.ready and not decisions[s].position),
                    key=lambda s: decisions[s].setup.distance, default=None)
    top = f"{candidate} {side_name(decisions[candidate].setup.trend.direction)}" if candidate else "없음"
    close_time = utc_time(btc[-1].ts + BAR_MS["4H"]).astimezone(KST)
    daily_time = utc_time(data["BTC", "1Dutc"][-1].ts + BAR_MS["1Dutc"]).astimezone(KST)
    fig = plt.figure(figsize=(19, 16), facecolor=BG)
    grid = fig.add_gridspec(4, 3, height_ratios=(2.35, .57, 1.25, 4.5),
                            left=.041, right=.958, top=.855, bottom=.055,
                            hspace=.37, wspace=.19)
    fig.text(.041, .963, "MATSU2   /   OKX 현물 데이터  ·  BTC / ETH / SOL", color=COLORS[7], fontsize=11,
             fontweight="bold")
    fig.text(.040, .923, "4H 추세 추종 진입 판단", color=TEXT, fontsize=30, fontweight="bold")
    fig.text(.958, .963, f"확정봉 마감  |  4H {close_time:%Y-%m-%d %H:%M}   ·   일봉 {daily_time:%m-%d %H:%M} KST",
             ha="right", color=MUTED, fontsize=11)
    fig.text(.958, .929, f"BTC {ARRAY_LABELS[market.direction]} · {market.regime}     /     최우선 후보 {top}",
             ha="right", color=SIDE_COLORS[market.direction], fontsize=15, fontweight="bold")
    fast_values = "   ·   ".join(f"SMA {n}  {price(btc_sma[n][-1])}" for n in (7, 21, 60))
    fig.text(.958, .901, fast_values, ha="right", color=MUTED, fontsize=10)
    handles = [Line2D([0], [0], color=color, lw=2, label=f"SMA {period}")
               for period, color in COLORS.items()]
    fig.legend(handles=handles, ncol=6, loc="upper left", bbox_to_anchor=(.035, .907),
               frameon=False, labelcolor=MUTED, fontsize=9)

    for column, symbol in enumerate(SYMBOLS):
        candles = data[symbol, "4H"]
        decision = decisions[symbol]
        trend = decision.setup.trend
        chart = fig.add_subplot(grid[0, column])
        times, colors = draw_price(chart, candles, averages[symbol, "4H"], 120, decision)
        chart.spines["top"].set_color(ASSET_COLORS[symbol])
        chart.set_title(f"{symbol} / USDT   ·   {price(candles[-1].close)}", loc="left",
                        color=ASSET_COLORS[symbol], fontsize=16, pad=10, fontweight="bold")
        chart.set_title(f"{ARRAY_LABELS[trend.direction]} · {trend.regime}", loc="right",
                        color=SIDE_COLORS[trend.direction],
                        fontsize=9, pad=12, fontweight="bold")
        chart.text(.025, .95, "4시간봉 · 120개", transform=chart.transAxes, va="top",
                   color=MUTED, fontsize=8,
                   bbox={"facecolor": PANEL, "edgecolor": "none", "alpha": .85})
        chart.tick_params(labelbottom=False)

        volume = fig.add_subplot(grid[1, column], sharex=chart)
        volume.bar(times, [c.volume_quote / 1_000_000 for c in candles[-120:]],
                   width=.68 * (times[1] - times[0]), color=colors, linewidth=0, alpha=.72)
        style_axis(volume)
        volume.yaxis.set_major_locator(MaxNLocator(3))
        volume.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f"{y:g}M"))
        date_ticks(volume, 4)
        volume.set_title("4시간봉 거래량  /  USDT", loc="left", color=MUTED, fontsize=8)

        daily = fig.add_subplot(grid[2, column])
        draw_price(daily, data[symbol, "1Dutc"], averages[symbol, "1Dutc"], 150)
        date_ticks(daily, 30)
        daily.set_title("UTC 일봉 · 150개  /  SMA 60·365", loc="left",
                        color=MUTED, fontsize=8)
        add_setup_card(fig.add_subplot(grid[3, column]), decision, micro[symbol])

    fig.text(.041, .033, "OKX Global · 확정봉만 사용   /   가격 USDT   /   상대 거래량은 직전 20봉 평균 대비",
             color=MUTED, fontsize=9)
    fig.text(.958, .033, "점선  계획 가격   /   색상 영역  2차 매물대   /   영상 직접 검증 미실시",
             ha="right", color=MUTED, fontsize=9)
    fig.savefig(path / "dashboard.png", dpi=160, facecolor=BG)
    fig.savefig(path / "dashboard.pdf", facecolor=BG,
                metadata={"Title": "Matsu2 OKX Pullback Entry Dashboard", "Author": "Matsu2"})
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output"),
                        help="directory to create for this run (must not already exist)")
    parser.add_argument("--with-5m", action="store_true", help="collect optional confirmed 5m evidence")
    parser.add_argument("--exit-style", choices=("short", "trend"), default="short",
                        help="target to prioritize in the simulated position replay")
    args = parser.parse_args()
    output = args.output
    if output.exists():
        parser.error(f"choose a new output directory: {output}")

    now = datetime.now(timezone.utc)
    data = {}
    averages = {}
    for symbol in SYMBOLS:
        for bar in BAR_MS:
            candles = fetch_candles(symbol, bar, now)
            data[symbol, bar] = candles
            averages[symbol, bar] = moving_averages(candles, bar)
            print(f"OKX {symbol}-USDT {bar}: {len(candles)} closed candles; "
                  f"last open {utc_time(candles[-1].ts).isoformat()}", flush=True)
    for bar in BAR_MS:
        if len({data[symbol, bar][-1].ts for symbol in SYMBOLS}) != 1:
            raise ValueError(f"OKX {bar}: instruments disagree on the latest closed candle")

    micro = {}
    for symbol in SYMBOLS:
        if args.with_5m:
            try:
                candles = fetch_candles(symbol, "5m", now)
            except (RuntimeError, ValueError) as exc:
                micro[symbol] = five_min_evidence(None, str(exc))
                print(f"OKX {symbol}-USDT optional 5m unavailable: {exc}", flush=True)
            else:
                data[symbol, "5m"] = candles
                averages[symbol, "5m"] = {}
                micro[symbol] = five_min_evidence(candles)
        else:
            micro[symbol] = five_min_evidence(None)

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / "report"
        staging.mkdir()
        for (symbol, bar), candles in data.items():
            write_csv(staging / f"{symbol}-USDT_{bar}.csv", candles, averages[symbol, bar])
        render(staging, data, averages, micro, args.exit_style)
        os.rename(staging, output)
    print(f"Saved {len(data) + 1} CSVs, dashboard.png and dashboard.pdf to {output}")


if __name__ == "__main__":
    main()

