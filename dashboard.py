"""Render the BTC, ETH and SOL report from validated OKX candles."""

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MaxNLocator

from analysis import Setup, analyze, regime_of
from okx_data import BAR_MS, SYMBOLS, fetch_candles, moving_averages, utc_time, write_csv

JST = timezone(timedelta(hours=9), "JST")
BG, PANEL, GRID = "#05060a", "#0b1018", "#20293a"
TEXT, MUTED = "#edf6ff", "#9daec7"
UP, DOWN = "#38ffb1", "#ff5684"
COLORS = {7: "#20ebff", 21: "#ffdb55", 60: "#c189ff",
          120: "#ff9c54", 224: "#79a8ff", 365: "#ff6fcd"}
ASSET_COLORS = {"BTC": "#ff9c54", "ETH": "#c189ff", "SOL": "#38ffb1"}
REGIME_COLORS = {"BULLISH": UP, "BEARISH": DOWN, "COMPRESSED": "#ffdb55", "MIXED": MUTED}
REGIME_LABELS = {"BULLISH": "정배열 추세", "BEARISH": "역배열 조정",
                 "COMPRESSED": "응축 · 박스권", "MIXED": "혼조 · 방향 확인"}


def price(value: float | None) -> str:
    return f"{value:,.2f}" if value is not None else "—"


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


def draw_price(ax, candles, averages, count, setup: Setup | None = None):
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
    if setup is not None and setup.entry1 is not None:
        ax.axhspan(*setup.support, color=UP, alpha=0.10)
    ax.set_xlim(times[0] - step, times[-1] + step)
    style_axis(ax)
    return times, colors


def date_ticks(ax, interval: int) -> None:
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=interval, tz=JST))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=JST))


def add_setup_card(ax, setup: Setup, close: float, sma: dict, daily_sma: dict,
                   market: str) -> None:
    ax.set_facecolor(PANEL)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color(GRID)

    def line(x, y, label, color=MUTED, size=9, weight="normal"):
        ax.text(x, y, label, transform=ax.transAxes, color=color, fontsize=size,
                fontweight=weight, va="center")

    valid = setup.entry1 is not None
    anchor, defense = sma[60][-1], sma[120][-1]
    gap = (close / anchor - 1) * 100
    if valid:
        reason = "BTC·종목 정배열 / 60·120선 위 / 하단 지지 확인"
    elif market != "BULLISH":
        reason = "BTC 정배열 확인 대기"
    elif setup.regime != "BULLISH":
        reason = "종목 7·21·60 정배열 확인 대기"
    elif close <= max(anchor, defense):
        reason = "4H 종가의 60·120선 회복 대기"
    else:
        reason = "SMA 60 아래 반복 저점 확인 대기"
    volume = f"{setup.relative_volume:.2f}×" if setup.relative_volume is not None else "—"
    support = " ~ ".join(map(price, setup.market_support)) if setup.market_support else "—"
    confluence = setup.market_support and setup.market_support[0] <= anchor <= setup.market_support[1]
    support_label = "지지 · 60선 중첩" if confluence else "현가 아래 지지"
    line(.04, .945, "조건부 분할 매수" if valid else "관망 · 롱 조건 미충족",
         UP if valid else MUTED, 13, "bold")
    line(.04, .874, f"{reason}   |   상대 거래량 {volume}", TEXT)
    line(.04, .793, f"4H SMA 60  {price(anchor)} ({gap:+.2f}%)   /   120  {price(defense)}")
    line(.04, .729, f"일봉 SMA 60  {price(daily_sma[60][-1])}   /   365  {price(daily_sma[365][-1])}")
    line(.04, .665, f"{support_label}  {support}   /   상단 저항  {price(setup.resistance)}")

    ax.axhline(.614, xmin=.04, xmax=.96, color=GRID, lw=.7)
    line(.04, .558, "분할 진입 · 익절 · 무효화", COLORS[7], 10, "bold")
    line(.04, .482, f"1차 SMA 60 (40%)  {price(setup.entry1)}   /   2차 매물대 (60%)  {price(setup.entry2)}", TEXT)
    invalidation = f"4H 종가 < {price(setup.stop)}" if valid else "—"
    line(.04, .410, f"예상 평단  {price(setup.average)}   /   무효화  {invalidation}", TEXT)
    marker = "*" if setup.confluence else ""
    line(.04, .338, f"단기 익절  {price(setup.short_target)}   /   추세 익절  {price(setup.trend_target)}{marker}", TEXT)

    ax.axhline(.280, xmin=.04, xmax=.96, color=GRID, lw=.7)
    line(.04, .222, "다음 4H 마감에서 볼 조건", COLORS[7], 10, "bold")
    if valid:
        upside = (f"상방  {price(setup.resistance)} 저항 돌파 확인"
                  if setup.resistance is not None else "상방  7·21·60 정배열 및 120선 지지 유지")
    else:
        upside = "회복  60·120선 위 마감 및 BTC·종목 정배열 확인"
    downside = setup.stop if valid else (setup.market_support[0] if setup.market_support else None)
    downside_action = "진입 계획 무효" if valid else "관망 유지"
    line(.04, .151, upside)
    line(.04, .078, f"하방  {price(downside)} 아래 마감 시 {downside_action}" if downside is not None
         else f"하방  확인된 반복 저점 지지 없음 · {downside_action}")


def render(path: Path, data: dict, averages: dict) -> None:
    configure_font()
    market = regime_of(data["BTC", "4H"], averages["BTC", "4H"])
    setups = {
        symbol: analyze(data[symbol, "4H"], averages[symbol, "4H"],
                        averages[symbol, "1Dutc"], market == "BULLISH")
        for symbol in SYMBOLS
    }
    candidate = min((s for s in SYMBOLS if setups[s].entry1 is not None),
                    key=lambda s: setups[s].distance, default="없음")
    close_time = utc_time(data["BTC", "4H"][-1].ts + BAR_MS["4H"]).astimezone(JST)
    daily_time = utc_time(data["BTC", "1Dutc"][-1].ts + BAR_MS["1Dutc"]).astimezone(JST)
    fig = plt.figure(figsize=(19, 13), facecolor=BG)
    grid = fig.add_gridspec(4, 3, height_ratios=(2.4, .58, 1.35, 2.85),
                            left=.041, right=.958, top=.806, bottom=.066,
                            hspace=.38, wspace=.21)
    fig.text(.041, .960, "MATSU2   /   OKX 현물  ·  BTC / ETH / SOL", color=COLORS[7], fontsize=11,
             fontweight="bold")
    fig.text(.040, .910, "눌림목 진입조건", color=TEXT, fontsize=32, fontweight="bold")
    fig.text(.958, .960, f"확정봉 마감  |  4H {close_time:%Y-%m-%d %H:%M}   ·   일봉 {daily_time:%m-%d %H:%M} KST/JST",
             ha="right", color=MUTED, fontsize=11)
    fig.text(.958, .921, f"BTC {REGIME_LABELS[market]}     /     최우선 롱 후보 {candidate}",
             ha="right", color=REGIME_COLORS[market], fontsize=15, fontweight="bold")
    btc_sma = averages["BTC", "4H"]
    fast_values = "   ·   ".join(f"SMA {n}  {price(btc_sma[n][-1])}" for n in (7, 21, 60))
    fig.text(.958, .887, fast_values, ha="right", color=MUTED, fontsize=10)
    handles = [Line2D([0], [0], color=color, lw=2, label=f"SMA {period}")
               for period, color in COLORS.items()]
    fig.legend(handles=handles, ncol=6, loc="upper left", bbox_to_anchor=(.035, .890),
               frameon=False, labelcolor=MUTED, fontsize=9)

    for column, symbol in enumerate(SYMBOLS):
        candles = data[symbol, "4H"]
        setup = setups[symbol]
        chart = fig.add_subplot(grid[0, column])
        times, colors = draw_price(chart, candles, averages[symbol, "4H"], 120, setup)
        chart.spines["top"].set_color(ASSET_COLORS[symbol])
        chart.set_title(f"{symbol} / USDT   ·   {price(candles[-1].close)}", loc="left",
                        color=ASSET_COLORS[symbol], fontsize=16, pad=10, fontweight="bold")
        chart.set_title(REGIME_LABELS[setup.regime], loc="right", color=REGIME_COLORS[setup.regime],
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
        add_setup_card(fig.add_subplot(grid[3, column]), setup, candles[-1].close,
                       averages[symbol, "4H"], averages[symbol, "1Dutc"], market)

    fig.text(.041, .033, "OKX Global · 확정봉만 사용   /   가격 USDT   /   상대 거래량은 직전 20봉 평균 대비",
             color=MUTED, fontsize=9)
    fig.text(.958, .033, "—  조건 미충족 또는 해당 수준 없음   ·   *  장기 SMA와 4H 매물대 저항 중첩",
             ha="right", color=MUTED, fontsize=9)
    fig.savefig(path / "dashboard.png", dpi=160, facecolor=BG)
    fig.savefig(path / "dashboard.pdf", facecolor=BG,
                metadata={"Title": "Matsu2 OKX Pullback Entry Dashboard", "Author": "Matsu2"})
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output"),
                        help="directory to create for this run (must not already exist)")
    output = parser.parse_args().output
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

    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary) / "report"
        staging.mkdir()
        for (symbol, bar), candles in data.items():
            write_csv(staging / f"{symbol}-USDT_{bar}.csv", candles, averages[symbol, bar])
        render(staging, data, averages)
        os.rename(staging, output)
    print(f"Saved 6 CSVs, dashboard.png and dashboard.pdf to {output}")


if __name__ == "__main__":
    main()
