"""Binance BTCUSDT spot aggregated-trade CVD proxy.

IMPORTANT: aggTrades are exchange aggregates, not identified whale orders.
This is NOT ForeDex's proprietary 1m-10m whale CVD.
"""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = "https://api.binance.com/api/v3/aggTrades"
SYMBOL = "BTCUSDT"
MIN_NOTIONAL = 1_000_000
MAX_NOTIONAL = 10_000_000
PAGE_SIZE = 1000


def fetch_recent(limit=PAGE_SIZE):
    url = API + "?" + urlencode({"symbol": SYMBOL, "limit": limit})
    req = Request(url, headers={"User-Agent": "Matsu2-CVD-research/0.1"})
    with urlopen(req, timeout=25) as response:
        data = json.load(response)
    if not isinstance(data, list) or not data:
        raise RuntimeError("Binance returned no aggregated trades")
    return data


def classify(trades):
    """Return filtered proxy records; buyer maker=True means aggressive sell."""
    records = []
    previous_id = None
    for trade in trades:
        trade_id = int(trade["a"])
        if previous_id is not None and trade_id <= previous_id:
            raise ValueError("Aggregated trade IDs are not strictly increasing")
        previous_id = trade_id
        price, quantity = float(trade["p"]), float(trade["q"])
        notional = price * quantity
        if price <= 0 or quantity <= 0:
            raise ValueError("Nonpositive trade price/quantity")
        if MIN_NOTIONAL <= notional <= MAX_NOTIONAL:
            delta = -notional if trade["m"] else notional
            records.append((trade_id, int(trade["T"]), price, quantity, notional, delta))
    return records


def main():
    out = Path(os.environ.get("CVD_OUTPUT_DIR", "output"))
    out.mkdir(parents=True, exist_ok=True)
    trades = fetch_recent()
    records = classify(trades)
    cumulative = 0.0
    with (out / "binance_btc_large_aggtrade_cvd.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["aggregate_trade_id", "time_utc", "price_usdt", "quantity_btc",
                         "notional_usdt", "signed_notional_usdt", "sample_cvd_usdt"])
        for trade_id, ts, price, quantity, notional, delta in records:
            cumulative += delta
            writer.writerow([trade_id, datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(),
                             price, quantity, notional, delta, cumulative])
    metadata = {
        "source": "Binance public BTCUSDT spot aggTrades",
        "endpoint": API,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "sample_first_utc": datetime.fromtimestamp(int(trades[0]["T"])/1000, timezone.utc).isoformat(),
        "sample_last_utc": datetime.fromtimestamp(int(trades[-1]["T"])/1000, timezone.utc).isoformat(),
        "sample_aggregates": len(trades),
        "qualifying_aggregates": len(records),
        "sample_cvd_usdt": cumulative,
        "filter_aggregate_notional_usdt": [MIN_NOTIONAL, MAX_NOTIONAL],
        "warning": "Short recent sample only. Aggregated trades do not identify whale orders. "
                   "Not ForeDex CVD; do not interpret sample CVD as historical trend or live risk filter.",
    }
    (out / "binance_cvd_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
