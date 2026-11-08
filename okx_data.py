"""Download and validate closed OKX spot candles."""

import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://openapi.okx.com/api/v5/market/history-candles"
SYMBOLS = ("BTC", "ETH", "SOL")
PERIODS = {"4H": (7, 21, 60, 120, 224, 365), "1Dutc": (60, 365)}
BAR_MS = {"4H": 14_400_000, "1Dutc": 86_400_000}
FIVE_MIN_MS = 300_000
HISTORY_LENGTH = 600
RETRYABLE_CODES = {"50004", "50011", "50013", "50026", "50040"}


@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume_base: float
    volume_quote: float


def request_page(symbol: str, bar: str, after: int | None) -> list:
    params = {"instId": f"{symbol}-USDT", "bar": bar, "limit": "300"}
    if after is not None:
        params["after"] = str(after)
    request = Request(
        f"{API_URL}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "Matsu2-OKX-dashboard/1.0"},
    )
    for attempt in range(4):
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                raise RuntimeError(f"OKX {symbol} {bar} HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            if attempt == 3:
                raise RuntimeError(f"OKX {symbol} {bar} request failed: {exc}") from exc
        else:
            if not isinstance(payload, dict):
                raise RuntimeError(f"OKX {symbol} {bar} returned invalid JSON structure")
            code = payload.get("code")
            if code == "0" and isinstance(payload.get("data"), list):
                return payload["data"]
            if code not in RETRYABLE_CODES or attempt == 3:
                raise RuntimeError(f"OKX {symbol} {bar} error {code}: {payload.get('msg')}")
        time.sleep(2**attempt)
    raise AssertionError("Request retry loop exhausted")


def fetch_candles(symbol: str, bar: str, now: datetime) -> list[Candle]:
    duration = FIVE_MIN_MS if bar == "5m" else BAR_MS[bar]
    length = 60 if bar == "5m" else HISTORY_LENGTH
    cutoff = int(now.timestamp() * 1000)
    expected_latest = ((cutoff - 120_000) // duration - 1) * duration
    confirmed = {}
    after = None
    for _ in range(10):
        page = request_page(symbol, bar, after)
        if not page:
            break
        timestamps = []
        for row in page:
            if not isinstance(row, list) or len(row) != 9 or row[8] not in ("0", "1"):
                raise ValueError(f"OKX {symbol} {bar}: invalid candle format")
            ts = int(row[0])
            timestamps.append(ts)
            if ts <= 0 or ts % duration:
                raise ValueError(f"OKX {symbol} {bar}: invalid opening timestamp {ts}")
            if row[8] == "0" or ts + duration > cutoff:
                continue
            opening, high, low, close, volume_base, volume_quote = map(
                float, (*row[1:6], row[7])
            )
            values = (opening, high, low, close, volume_base, volume_quote)
            if (not all(map(math.isfinite, values))
                    or not 0 < low <= min(opening, close) <= max(opening, close) <= high
                    or min(volume_base, volume_quote) < 0):
                raise ValueError(f"OKX {symbol} {bar}: invalid OHLCV at {ts}")
            candle = Candle(ts, *values)
            if confirmed.setdefault(ts, candle) != candle:
                raise ValueError(f"OKX {symbol} {bar}: conflicting candles at {ts}")
        if len(confirmed) >= length:
            break
        oldest = min(timestamps)
        if after is not None and oldest >= after:
            raise ValueError(f"OKX {symbol} {bar}: pagination did not advance")
        after = oldest
        time.sleep(0.15)

    candles = sorted(confirmed.values(), key=lambda candle: candle.ts)[-length:]
    if len(candles) != length:
        raise ValueError(f"OKX {symbol} {bar}: only {len(candles)} closed candles")
    if candles[-1].ts < expected_latest:
        raise ValueError(f"OKX {symbol} {bar}: latest closed candle is stale")
    if any(b.ts - a.ts != duration for a, b in zip(candles, candles[1:])):
        raise ValueError(f"OKX {symbol} {bar}: gaps in closed candle history")
    return candles


def moving_averages(candles: list[Candle], bar: str) -> dict[int, list[float]]:
    closes = [candle.close for candle in candles]
    return {
        period: [
            fmean(closes[i - period + 1:i + 1]) if i + 1 >= period else math.nan
            for i in range(len(closes))
        ]
        for period in PERIODS[bar]
    }


def utc_time(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1000, timezone.utc)


def write_csv(path: Path, candles: list[Candle], averages: dict[int, list[float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            "open_time_utc", "open", "high", "low", "close", "volume_base",
            "volume_quote_usdt", *(f"sma_{period}" for period in averages),
        ])
        for i, candle in enumerate(candles):
            writer.writerow([
                utc_time(candle.ts).isoformat(), candle.open, candle.high, candle.low,
                candle.close, candle.volume_base, candle.volume_quote,
                *(series[i] if i + 1 >= period else "" for period, series in averages.items()),
            ])
