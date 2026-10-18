"""Read-only six-asset price sampler. No keys, wallets, orders or AI calls."""
import argparse
import csv
import json
import logging
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
PAIRS = ("BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD")
BASE_URL = "https://api.exchange.coinbase.com/products/"
FIELDS = ["received_at_utc", "source_time_utc", "pair", "price_usd"]


def positive_interval(value):
    try:
        interval = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Interval must be positive and finite") from error
    if not math.isfinite(interval) or interval <= 0:
        raise argparse.ArgumentTypeError("Interval must be positive and finite")
    return interval


def fetch_price(pair):
    if pair not in PAIRS:
        raise ValueError("Unsupported configured pair")
    request = Request(BASE_URL + pair + "/ticker", headers={"Accept": "application/json", "User-Agent": "BlackBirdBasic/0.2"})
    with urlopen(request, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, dict) or isinstance(data.get("price"), bool):
        raise ValueError("Invalid ticker response")
    price = float(data["price"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid price")
    stamp = data["time"]
    if not isinstance(stamp, str):
        raise ValueError("Invalid source timestamp")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Source timestamp must include timezone")
    return price, stamp


def save_price(path, pair, price, source_time):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        with path.open(newline="", encoding="utf-8") as existing:
            if next(csv.reader(existing), []) != FIELDS:
                raise ValueError("Existing CSV header differs; use a new output path")
    if path.exists() and path.stat().st_size >= 5 * 1024 * 1024:
        archive = path.with_name(path.stem + "." + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".csv")
        path.rename(archive)
    header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        if header:
            writer.writerow(FIELDS)
        writer.writerow([datetime.now(timezone.utc).isoformat(), source_time, pair, price])


def retry_after_seconds(headers):
    value = headers.get("Retry-After", "") if headers else ""
    try:
        delay = float(value)
        if math.isfinite(delay):
            return max(60.0, delay)
    except (ValueError, TypeError):
        pass
    try:
        stamp = parsedate_to_datetime(value)
        return max(60.0, (stamp - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return 60.0


def collect_cycle(path, active, log, once=False):
    """Append successes immediately; mutate active only for unavailable products."""
    failed, cooldown = False, 0.0
    for index, pair in enumerate(tuple(active)):
        try:
            price, stamp = fetch_price(pair)
            save_price(path, pair, price, stamp)
            log.info("%s: %.8f | source time: %s", pair, price, stamp)
        except HTTPError as error:
            failed = True
            log.error("%s HTTP %s", pair, error.code)
            if error.code in (401, 403, 451):
                log.error("Access blocked; stopping whole collector without bypass.")
                raise SystemExit(1)
            if error.code == 404:
                active.remove(pair)
                log.error("%s disabled for this run (HTTP 404); no substitution.", pair)
            if error.code == 429:
                cooldown = retry_after_seconds(error.headers)
                log.warning("Global rate-limit backoff: at least %.1f seconds", cooldown)
                if not once:
                    break
                # In one-shot mode, still visit remaining symbols without a burst.
                if index < len(active) - 1:
                    time.sleep(cooldown)
                cooldown = 0.0
        except (ValueError, OSError, KeyError, TypeError, csv.Error) as error:
            failed = True
            log.error("%s %s: %s", pair, type(error).__name__, error)
    if not active:
        log.error("No active products remain; stopping.")
        raise SystemExit(1)
    return failed, cooldown


def run(path, interval, once, log):
    active = list(PAIRS)
    try:
        while True:
            started = time.monotonic()
            failed, cooldown = collect_cycle(path, active, log, once=once)
            if once:
                return 1 if failed else 0
            elapsed = time.monotonic() - started
            # No overlap or catch-up bursts; failed symbols retry next cycle only.
            time.sleep(max(1.0, interval - elapsed, cooldown))
    except KeyboardInterrupt:
        log.info("Stopped.")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Visit each active symbol once; nonzero on any failure")
    parser.add_argument("--interval", type=positive_interval, default=60.0, help="Nominal cycle seconds (default: 60; e.g. 30)")
    parser.add_argument("--output", type=Path, default=ROOT / "prices.csv")
    args = parser.parse_args(argv)
    log = logging.getLogger("blackbird")
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    log.addHandler(stream)
    handler = RotatingFileHandler(ROOT / "monitor.log", maxBytes=1000000, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    log.addHandler(handler)
    log.info("Read-only monitor: %s | nominal interval %.1fs. No trading.", ", ".join(PAIRS), args.interval)
    raise SystemExit(run(args.output, args.interval, args.once, log))


if __name__ == "__main__":
    main()
