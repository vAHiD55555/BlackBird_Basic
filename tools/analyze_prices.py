"""Offline CSV quality report; no network access and no trading."""
import argparse
import csv
import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED = {"received_at_utc", "source_time_utc", "pair", "price_usd"}


def parse_time(value):
    stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timezone missing")
    return stamp.astimezone(timezone.utc)


def analyze(path, expected_interval=60):
    expected_interval = float(expected_interval)
    if not math.isfinite(expected_interval) or expected_interval <= 0:
        raise ValueError("Expected interval must be positive and finite")
    threshold = expected_interval * 1.1
    if not math.isfinite(threshold):
        raise ValueError("Expected interval is too large")
    records, invalid = [], []
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise ValueError("Missing required columns: " + ", ".join(sorted(REQUIRED)))
        for line, row in enumerate(reader, 2):
            try:
                if None in row:
                    raise ValueError("Extra CSV columns")
                received = parse_time(row["received_at_utc"])
                source_time = parse_time(row["source_time_utc"])
                pair = row["pair"].strip()
                price = Decimal(row["price_usd"])
                if not pair or not price.is_finite() or price <= 0:
                    raise ValueError("Invalid price or pair")
                records.append((received, source_time, pair, price))
            except (ValueError, TypeError, AttributeError, KeyError, InvalidOperation):
                invalid.append(line)
    result = {"valid_rows": len(records), "invalid_row_lines": invalid, "expected_interval_seconds": expected_interval, "gap_threshold_seconds": threshold, "pairs": []}
    for pair in sorted({row[2] for row in records}):
        original = [row for row in records if row[2] == pair]
        rows = sorted(original, key=lambda row: row[0])
        gaps = [(rows[i][0] - rows[i-1][0]).total_seconds() for i in range(1, len(rows))]
        lags = [(row[0] - row[1]).total_seconds() for row in rows]
        prices = [row[3] for row in rows]
        received_counts = Counter(row[0] for row in rows)
        source_counts = Counter(row[1] for row in rows)
        result["pairs"].append({
            "pair": pair,
            "rows": len(rows),
            "first_received_utc": rows[0][0].isoformat(),
            "last_received_utc": rows[-1][0].isoformat(),
            "duration_seconds": (rows[-1][0] - rows[0][0]).total_seconds(),
            "duplicate_received_extra_rows": sum(n-1 for n in received_counts.values()),
            "duplicate_source_extra_rows": sum(n-1 for n in source_counts.values()),
            "backwards_steps_in_file": sum(original[i][0] < original[i-1][0] for i in range(1, len(original))),
            "median_interval_seconds": statistics.median(gaps) if gaps else None,
            "max_interval_seconds": max(gaps) if gaps else None,
            "gaps_over_threshold_seconds": [
                {"from": rows[i-1][0].isoformat(), "to": rows[i][0].isoformat(), "seconds": gaps[i-1]}
                for i in range(1, len(rows)) if gaps[i-1] > threshold
            ],
            "negative_source_lag_count": sum(lag < 0 for lag in lags),
            "median_source_lag_seconds": statistics.median(lags),
            "max_source_lag_seconds": max(lags),
            "first_price": str(prices[0]),
            "last_price": str(prices[-1]),
            "min_sampled_price": str(min(prices)),
            "max_sampled_price": str(max(prices)),
            "sampled_price_change_percent": str(round((prices[-1] / prices[0] - 1) * 100, 6)),
        })
    result["notes"] = [
        "Sampled prices are not candles or guaranteed execution prices.",
        "Price change is not strategy profit. No trades or fees simulated.",
        "Source lag includes clock effects and is not pure network latency.",
        "Gap threshold is 1.1 times the supplied expected interval, per pair.",
        "Only pairs present in the file are reported; absent assets are not inferred.",
        "This report describes only the supplied file, not service uptime or market stress.",
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", default="prices.csv")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--expected-interval", type=float, default=60, help="Nominal seconds per pair (default 60; legacy 300)")
    args = parser.parse_args()
    if args.output and Path(args.file).resolve() == Path(args.output).resolve():
        parser.error("Output must not overwrite input")
    try:
        report = analyze(args.file, expected_interval=args.expected_interval)
        text = json.dumps(report, indent=2, ensure_ascii=False)
        if args.output:
            destination = Path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(text + "\n", encoding="utf-8")
        print(text)
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, str(error) + "\n")
    if report["invalid_row_lines"] or not report["valid_rows"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
