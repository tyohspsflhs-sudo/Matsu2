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
REGIME_COLORS = {"BULLISH": UP, "BEARISH": DOWN, "COMPRESSED": "#ffdb55", "MIXED": MUTED}


def price(value: float | None) -> str:
    return f"{value:,.2f}" if value is not None else "—"


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


def add_setup_card(ax, setup: Setup) -> None:
    ax.set_facecolor(PANEL)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color(GRID)

    def line(x, y, label, color=MUTED, size=9, weight="normal"):
        ax.text(x, y, label, transform=ax.transAxes, color=color, fontsize=size,
                fontweight=weight, va="center")

    valid = setup.entry1 is not None
    line(.04, .84, "CONDITIONAL LONG" if valid else "WATCH  /  NO VALID LONG",
         UP if valid else MUTED, 10, "bold")
    volume = f"{setup.relative_volume:.2f}×" if setup.relative_volume is not None else "—"
    line(.04, .64, f"4H volume / prior 20-bar avg  {volume}")
    if valid:
        line(.04, .44, f"Entry 1 (40%)  {price(setup.entry1)}    Entry 2 (60%)  {price(setup.entry2)}", TEXT)
        line(.04, .25, f"Avg  {price(setup.average)}    Invalidate on 4H close < {price(setup.stop)}", TEXT)
        marker = "*" if setup.confluence else ""
        line(.04, .09, f"Short TP  {price(setup.short_target)}    Trend TP  {price(setup.trend_target)}{marker}")
    else:
        support = " — ".join(map(price, setup.support)) if setup.support else "—"
        line(.04, .42, f"Support below 4H SMA 60  {support}", TEXT)
        line(.04, .21, f"Pivot resistance above close  {price(setup.resistance)}", TEXT)


def render(path: Path, data: dict, averages: dict) -> None:
    market = regime_of(data["BTC", "4H"], averages["BTC", "4H"])
    setups = {
        symbol: analyze(data[symbol, "4H"], averages[symbol, "4H"],
                        averages[symbol, "1Dutc"], market == "BULLISH")
        for symbol in SYMBOLS
    }
    candidate = min((s for s in SYMBOLS if setups[s].entry1 is not None),
                    key=lambda s: setups[s].distance, default="NONE")
    close_time = utc_time(data["BTC", "4H"][-1].ts + BAR_MS["4H"]).astimezone(JST)
    fig = plt.figure(figsize=(18, 11), facecolor=BG)
    grid = fig.add_gridspec(4, 3, height_ratios=(3.25, .72, 2.10, 1.25),
                            left=.043, right=.954, top=.805, bottom=.084,
                            hspace=.45, wspace=.26)
    fig.text(.043, .957, "MATSU2   /   OKX SPOT MARKET", color=COLORS[7], fontsize=11,
             fontweight="bold")
    fig.text(.042, .908, "Trend Dashboard", color=TEXT, fontsize=31, fontweight="bold")
    fig.text(.954, .954, f"BTC 4H CLOSE   {close_time:%Y-%m-%d  %H:%M JST}",
             ha="right", color=MUTED, fontsize=11)
    fig.text(.954, .914, f"BTC REGIME  {market}     /     TOP PULLBACK  {candidate}",
             ha="right", color=REGIME_COLORS[market], fontsize=11, fontweight="bold")
    handles = [Line2D([0], [0], color=color, lw=2, label=f"SMA {period}")
               for period, color in COLORS.items()]
    fig.legend(handles=handles, ncol=6, loc="upper left", bbox_to_anchor=(.04, .892),
               frameon=False, labelcolor=MUTED, fontsize=9)

    for column, symbol in enumerate(SYMBOLS):
        candles = data[symbol, "4H"]
        setup = setups[symbol]
        chart = fig.add_subplot(grid[0, column])
        times, colors = draw_price(chart, candles, averages[symbol, "4H"], 120, setup)
        chart.set_title(f"{symbol} / USDT   ·   {price(candles[-1].close)}", loc="left",
                        color=TEXT, fontsize=16, pad=10, fontweight="bold")
        chart.set_title(setup.regime, loc="right", color=REGIME_COLORS[setup.regime],
                        fontsize=9, pad=12, fontweight="bold")
        chart.text(.025, .95, "4H  /  120 BARS", transform=chart.transAxes, va="top",
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
        volume.set_title("4H QUOTE VOLUME  /  USDT", loc="left", color=MUTED, fontsize=8)

        daily = fig.add_subplot(grid[2, column])
        draw_price(daily, data[symbol, "1Dutc"], averages[symbol, "1Dutc"], 150)
        date_ticks(daily, 30)
        daily.set_title("1D UTC  /  150 BARS  /  SMA 60 & 365", loc="left",
                        color=MUTED, fontsize=8)
        add_setup_card(fig.add_subplot(grid[3, column]), setup)

    fig.text(.043, .038, "SOURCE  OKX Global  /  Closed candles only  /  Axis dates in JST",
             color=MUTED, fontsize=9)
    fig.text(.954, .038, "*  Long SMA overlaps a 4H pivot resistance zone",
             ha="right", color=MUTED, fontsize=9)
    fig.savefig(path / "dashboard.png", dpi=160, facecolor=BG)
    fig.savefig(path / "dashboard.pdf", facecolor=BG,
                metadata={"Title": "Matsu2 OKX Trend Dashboard", "Author": "Matsu2"})
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
