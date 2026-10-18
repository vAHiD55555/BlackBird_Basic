"""Offline mocked collector tests; all ticker values are synthetic."""
import csv
import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from collector import monitor as m

STAMP = "2026-01-01T00:00:00Z"


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "prices.csv"
        self.log = logging.getLogger("test-monitor")
        self.log.addHandler(logging.NullHandler())

    def rows(self):
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as source:
            return list(csv.DictReader(source))

    def test_six_successes(self):
        with patch.object(m, "fetch_price", return_value=(100.0, STAMP)) as fetch:
            self.assertEqual(m.run(self.path, 60, True, self.log), 0)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], list(m.PAIRS))
        self.assertEqual([row["pair"] for row in self.rows()], list(m.PAIRS))

    def test_failure_keeps_other_successes(self):
        def fetch(pair):
            if pair == "ETH-USD":
                raise ValueError("Synthetic invalid response")
            return 100.0, STAMP
        with patch.object(m, "fetch_price", side_effect=fetch):
            self.assertEqual(m.run(self.path, 60, True, self.log), 1)
        self.assertEqual(len(self.rows()), 5)
        self.assertNotIn("ETH-USD", [row["pair"] for row in self.rows()])

    def test_access_denied_stops_whole_collector(self):
        for code in (401, 403, 451):
            with self.subTest(code=code), patch.object(m, "fetch_price", side_effect=HTTPError("test", code, "blocked", {}, None)) as fetch:
                with self.assertRaises(SystemExit) as error:
                    m.run(self.path, 60, True, self.log)
                self.assertEqual(error.exception.code, 1)
                self.assertEqual(fetch.call_count, 1)

    def test_partial_success_before_denial_preserved(self):
        with patch.object(m, "fetch_price", side_effect=[(100.0, STAMP), HTTPError("test", 403, "blocked", {}, None)]):
            with self.assertRaises(SystemExit):
                m.run(self.path, 60, True, self.log)
        self.assertEqual(len(self.rows()), 1)

    def test_404_disables_only_unavailable_pair(self):
        active = list(m.PAIRS)
        with patch.object(m, "fetch_price", side_effect=[HTTPError("test", 404, "missing", {}, None)] + [(100.0, STAMP)] * 5):
            failed, _ = m.collect_cycle(self.path, active, self.log)
        self.assertTrue(failed)
        self.assertNotIn("BTC-USD", active)
        self.assertEqual(len(self.rows()), 5)

    def test_all_404_stops(self):
        with patch.object(m, "fetch_price", side_effect=HTTPError("test", 404, "missing", {}, None)):
            with self.assertRaises(SystemExit):
                m.collect_cycle(self.path, list(m.PAIRS), self.log)

    def test_429_breaks_cycle_and_respects_retry_after(self):
        with patch.object(m, "fetch_price", side_effect=HTTPError("test", 429, "limited", {"Retry-After": "120"}, None)) as fetch:
            self.assertEqual(m.collect_cycle(self.path, list(m.PAIRS), self.log), (True, 120))
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(m.retry_after_seconds({}), 60)

    def test_once_429_waits_before_remaining_requests(self):
        with patch.object(m, "fetch_price", side_effect=[HTTPError("test", 429, "limited", {}, None)] + [(100.0, STAMP)] * 5), patch.object(m.time, "sleep") as sleep:
            self.assertEqual(m.run(self.path, 60, True, self.log), 1)
        sleep.assert_called_once_with(60)
        self.assertEqual(len(self.rows()), 5)

    def test_invalid_cli_interval_never_calls_network(self):
        for value in ("0", "-1", "nan", "inf", "bad"):
            with self.subTest(value=value), patch.object(m, "urlopen") as network, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as error:
                    m.main(["--interval", value, "--once"])
                self.assertEqual(error.exception.code, 2)
                network.assert_not_called()

    def test_elapsed_processing_deducted(self):
        with patch.object(m, "collect_cycle", return_value=(False, 0)), patch.object(m.time, "monotonic", side_effect=[100, 112]), patch.object(m.time, "sleep", side_effect=KeyboardInterrupt) as sleep:
            self.assertEqual(m.run(self.path, 60, False, self.log), 0)
        sleep.assert_called_once_with(48)

    def test_malformed_ticker_rejected(self):
        for text in ('{"price":"NaN","time":"2026-01-01T00:00:00Z"}', '{"price":true,"time":"2026-01-01T00:00:00Z"}', '{"price":"100","time":"2026-01-01"}'):
            with patch.object(m, "urlopen") as network:
                network.return_value.__enter__.return_value = io.StringIO(text)
                with self.assertRaises(ValueError):
                    m.fetch_price("BTC-USD")

    def test_standalone_matches_collector(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "collector/monitor.py").read_text(encoding="utf-8")
        standalone = (root / "deploy/botkeep/main.py").read_text(encoding="utf-8")
        self.assertEqual(source.replace("ROOT = Path(__file__).resolve().parents[1]", "ROOT = Path(__file__).resolve().parent"), standalone)


if __name__ == "__main__":
    unittest.main()
