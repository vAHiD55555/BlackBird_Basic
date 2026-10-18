"""Sample BTC-USD prices. No keys, wallets, orders or AI model calls."""
import argparse
import csv
import json
import logging
import math
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
URL = "https://api.exchange.coinbase.com/products/BTC-USD/ticker"
FIELDS = ["received_at_utc", "source_time_utc", "pair", "price_usd"]


def fetch_price():
    request = Request(URL, headers={"Accept": "application/json", "User-Agent": "BlackBirdBasic/0.1"})
    with urlopen(request, timeout=20) as response:
        data = json.load(response)
    price = float(data["price"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid price")
    stamp = str(data["time"])
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Source timestamp must include timezone")
    return price, stamp


def save_price(path, price, source_time):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        with path.open(newline="", encoding="utf-8") as existing:
            if next(csv.reader(existing), []) != FIELDS:
                raise ValueError("Existing CSV header differs; use a new output path")
    if path.exists() and path.stat().st_size >= 5 * 1024 * 1024:
        # Preserve old data instead of overwriting the previous archive.
        archive = path.with_name(path.stem + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".csv")
        path.rename(archive)
    header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        if header:
            writer.writerow(FIELDS)
        writer.writerow([datetime.now(timezone.utc).isoformat(), source_time, "BTC-USD", price])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="One request, then exit")
    parser.add_argument("--output", type=Path, default=ROOT / "prices.csv")
    args = parser.parse_args()
    log = logging.getLogger("blackbird")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    log.addHandler(stream)
    handler = RotatingFileHandler(ROOT / "monitor.log", maxBytes=1000000, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)
    log.info("Read-only monitor started. No trading enabled.")
    failures = 0
    while True:
        try:
            price, stamp = fetch_price()
            save_price(args.output, price, stamp)
            log.info("BTC-USD: %.2f | source time: %s", price, stamp)
            failures = 0
            if args.once:
                return
            time.sleep(300)
        except HTTPError as error:
            log.error("HTTP error: %s", error.code)
            if args.once or error.code in (401, 403, 451):
                raise SystemExit(1)
            failures += 1
        except KeyboardInterrupt:
            log.info("Stopped.")
            return
        except (ValueError, OSError, KeyError, TypeError) as error:
            log.error("%s: %s", type(error).__name__, error)
            if args.once:
                raise SystemExit(1)
            failures += 1
        if failures:
            try:
                time.sleep(min(1800, 60 * 2 ** min(failures, 5)))
            except KeyboardInterrupt:
                return


if __name__ == "__main__":
    main()
