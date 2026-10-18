"""Synthetic test values only; not market observations or performance claims."""
import tempfile
import unittest
from pathlib import Path
from tools.analyze_prices import analyze

HEADER = "received_at_utc,source_time_utc,pair,price_usd\n"
A = "2026-01-01T00:00:01Z,2026-01-01T00:00:00Z,TEST-USD,100\n"
B = "2026-01-01T00:05:01Z,2026-01-01T00:05:00Z,TEST-USD,110\n"


class AnalysisTests(unittest.TestCase):
    def report(self, content, expected_interval=60):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text(content, encoding="utf-8")
            result = analyze(path, expected_interval=expected_interval)
            self.assertEqual(path.read_text(encoding="utf-8"), content)
            return result

    def test_regular_legacy(self):
        report = self.report(HEADER + A + B, expected_interval=300)
        self.assertEqual(report["valid_rows"], 2)
        self.assertEqual(report["pairs"][0]["median_interval_seconds"], 300)
        self.assertEqual(report["pairs"][0]["sampled_price_change_percent"], "10.000000")
        self.assertEqual(report["pairs"][0]["gaps_over_threshold_seconds"], [])
        self.assertAlmostEqual(report["gap_threshold_seconds"], 330)

    def test_sixty_and_thirty(self):
        minute = B.replace("00:05:", "00:01:")
        report = self.report(HEADER + A + minute)
        self.assertEqual(report["expected_interval_seconds"], 60)
        self.assertAlmostEqual(report["gap_threshold_seconds"], 66)
        self.assertEqual(report["pairs"][0]["gaps_over_threshold_seconds"], [])
        report = self.report(HEADER + A + minute, expected_interval=30)
        self.assertEqual(len(report["pairs"][0]["gaps_over_threshold_seconds"]), 1)

    def test_invalid_interval(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.report(HEADER, expected_interval=value)

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
        pair = self.report(HEADER + late + A)["pairs"][0]
        self.assertEqual(pair["backwards_steps_in_file"], 1)
        self.assertEqual(pair["gaps_over_threshold_seconds"][0]["seconds"], 900)

    def test_pairs_are_separate(self):
        report = self.report(HEADER + A + A.replace("TEST-USD", "OTHER-USD"))
        self.assertEqual(len(report["pairs"]), 2)
        self.assertTrue(all(p["duplicate_received_extra_rows"] == 0 for p in report["pairs"]))


if __name__ == "__main__":
    unittest.main()
