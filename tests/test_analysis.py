"""Synthetic test values only; not market observations or performance claims."""
import tempfile
import unittest
from pathlib import Path
from tools.analyze_prices import analyze

HEADER = "received_at_utc,source_time_utc,pair,price_usd\n"
A = "2026-01-01T00:00:01Z,2026-01-01T00:00:00Z,TEST-USD,100\n"
B = "2026-01-01T00:05:01Z,2026-01-01T00:05:00Z,TEST-USD,110\n"


class AnalysisTests(unittest.TestCase):
    def report(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text(content, encoding="utf-8")
            result = analyze(path)
            self.assertEqual(path.read_text(encoding="utf-8"), content)
            return result

    def test_regular(self):
        report = self.report(HEADER + A + B)
        self.assertEqual(report["valid_rows"], 2)
        self.assertEqual(report["pairs"][0]["median_interval_seconds"], 300)
        self.assertEqual(report["pairs"][0]["sampled_price_change_percent"], "10.000000")

    def test_duplicate(self):
        report = self.report(HEADER + A + A)
        self.assertEqual(report["pairs"][0]["duplicate_received_extra_rows"], 1)

    def test_invalid_and_empty(self):
        report = self.report(HEADER + "bad,bad,TEST-USD,NaN\n")
        self.assertEqual(report["invalid_row_lines"], [2])
        self.assertEqual(report["valid_rows"], 0)
        self.assertEqual(self.report(HEADER)["pairs"], [])

    def test_missing_header(self):
        with self.assertRaises(ValueError):
            self.report("wrong\nvalue\n")

    def test_gap_and_backwards(self):
        late = "2026-01-01T00:15:01Z,2026-01-01T00:15:00Z,TEST-USD,105\n"
        report = self.report(HEADER + late + A)
        pair = report["pairs"][0]
        self.assertEqual(pair["backwards_steps_in_file"], 1)
        self.assertEqual(pair["gaps_over_330_seconds"][0]["seconds"], 900)


if __name__ == "__main__":
    unittest.main()
