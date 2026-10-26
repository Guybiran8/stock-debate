"""Run with: python -m unittest test_price_check -v
Uses made-up prices and a fake Tiingo, so it needs no network and spends nothing."""
import contextlib
import io
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import price_check as pc

ROOT = Path(__file__).parent
KEY = "tiingo-test-key-0123456789abcdef"
FRI = date(2026, 10, 23)
AFTER = datetime(2026, 10, 23, 18, 30, tzinfo=pc.NY)
TICKERS = [f"T{i:02d}" for i in range(66)]


def yahoo_all(close=100.0, day=FRI):
    return lambda tickers, last_day: {t: (day, close) for t in tickers}


def tiingo_all(close=100.0, day=FRI):
    return lambda t, key, start: {day - timedelta(days=1): close * 0.99, day: close}


def quiet(fn, *args, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args, **kwargs)
    return result, out.getvalue()


class PickTest(unittest.TestCase):
    def test_half_the_list_and_everyone_within_two_runs(self):
        state = {}
        first = pc.pick_tickers(TICKERS, state, 0, FRI)
        self.assertEqual(len(first), 33)
        for t in first:
            state.setdefault("checked", {})[t] = "2026-10-22"
        second = pc.pick_tickers(TICKERS, state, 0, FRI)
        self.assertEqual(len(second), 33)
        self.assertEqual(set(first) | set(second), set(TICKERS))

    def test_never_more_than_45_a_run(self):
        self.assertEqual(len(pc.pick_tickers([f"X{i}" for i in range(120)], {}, 0, FRI)), 45)

    def test_requests_in_the_last_hour_use_up_the_budget(self):
        now = 10_000
        state = {"requests": [now - 100] * 40 + [now - 4000] * 40}   # 40 in the last hour, 40 older
        self.assertEqual(len(pc.pick_tickers(TICKERS, state, now, FRI)), 5)

    def test_new_symbols_stop_at_the_monthly_limit(self):
        used = [f"OLD{i}" for i in range(pc.MAX_SYMBOLS_MONTH)]
        state = {"month": {"month": "2026-10", "symbols": used + ["T00"]}}
        self.assertEqual(pc.pick_tickers(TICKERS, state, 0, FRI), ["T00"])
        # A new month starts with no symbols used
        self.assertEqual(len(pc.pick_tickers(TICKERS, state, 0, date(2026, 11, 2))), 33)


class CompareTest(unittest.TestCase):
    def test_within_one_percent_on_the_same_day_is_fine(self):
        self.assertIsNone(pc.compare("AAA", (FRI, 100.9), {FRI: 100.0}))

    def test_gap_over_one_percent_lists_both_prices_and_the_gap(self):
        line = pc.compare("AAA", (FRI, 102.0), {FRI: 100.0})
        self.assertEqual(line, "AAA: Yahoo $102.00 (Oct 23) vs Tiingo $100.00 (Oct 23), gap +2.0%")

    def test_date_mismatch_is_a_problem_even_with_the_same_price(self):
        line = pc.compare("AAA", (FRI, 100.0), {FRI - timedelta(days=1): 100.0})
        self.assertIn("dates differ", line)
        self.assertIn("Tiingo $100.00 (Oct 22)", line)

    def test_no_tiingo_prices(self):
        self.assertIn("Tiingo has no prices", pc.compare("AAA", (FRI, 100.0), {}))


class TimingTest(unittest.TestCase):
    def test_before_six_uses_the_previous_session(self):
        self.assertEqual(pc.last_settled_day(datetime(2026, 10, 23, 17, 20, tzinfo=pc.NY)), FRI - timedelta(days=1))
        self.assertEqual(pc.last_settled_day(AFTER), FRI)

    def test_waits_after_the_scan_until_six(self):
        slept = []
        self.assertTrue(quiet(pc.wait_until_settled, datetime(2026, 10, 23, 17, 20, tzinfo=pc.NY), sleep=slept.append)[0])
        self.assertEqual(slept, [40 * 60])

    def test_no_wait_on_weekends_or_after_six(self):
        for when in (datetime(2026, 10, 25, 17, 20, tzinfo=pc.NY), AFTER, datetime(2026, 10, 23, 9, 0, tzinfo=pc.NY)):
            self.assertFalse(pc.wait_until_settled(when, sleep=self.fail))


class RunTest(unittest.TestCase):
    def run_check(self, state=None, **kw):
        state = {} if state is None else state
        kw.setdefault("yahoo", yahoo_all())
        kw.setdefault("tiingo", tiingo_all())
        kw.setdefault("sleep", lambda s: None)
        (problems, printed) = quiet(pc.run, TICKERS, AFTER, KEY, state, clock=lambda: 1000.0, **kw)
        return problems, printed, state

    def test_all_match_sends_nothing(self):
        alerts = []
        problems, printed, state = self.run_check(alert=alerts.append)
        self.assertEqual((problems, alerts), ([], []))
        self.assertIn("33 of 33 checked against Tiingo, 0 differ", printed)
        self.assertEqual(len(state["checked"]), 33)
        self.assertEqual(len(state["requests"]), 33)

    def test_gap_sends_one_telegram_with_the_details(self):
        alerts = []

        def tiingo(t, key, start):
            return {FRI: 90.0 if t == "T05" else 100.0}
        problems, _, _ = self.run_check(tiingo=tiingo, alert=alerts.append)
        self.assertEqual(len(problems), 1)
        self.assertEqual(len(alerts), 1)
        self.assertIn("1 of 33 stocks differ", alerts[0])
        self.assertIn("T05: Yahoo $100.00 (Oct 23) vs Tiingo $90.00 (Oct 23), gap +11.1%", alerts[0])

    def test_tiingo_errors_are_logged_and_skipped(self):
        def tiingo(t, key, start):
            if t == "T03":
                raise ConnectionError(f"https://api.tiingo.com/?token={KEY}")
            if t == "T04":
                raise RuntimeError("HTTP 404")
            return {FRI: 100.0}
        problems, printed, state = self.run_check(tiingo=tiingo, alert=self.fail)
        self.assertEqual(problems, [])
        self.assertIn("T03 (ConnectionError)", printed)
        self.assertIn("T04 (HTTP 404)", printed)
        self.assertNotIn("T03", state["checked"])   # retried next run
        self.assertNotIn(KEY, printed)

    def test_rate_limit_stops_the_run(self):
        calls = []

        def tiingo(t, key, start):
            calls.append(t)
            raise RuntimeError("HTTP 429")
        _, printed, _ = self.run_check(tiingo=tiingo)
        self.assertEqual(len(calls), 1)
        self.assertIn("rate limit", printed)

    def test_yahoo_failure_checks_nothing(self):
        def yahoo(tickers, last_day):
            raise ValueError("boom")
        problems, printed, _ = self.run_check(yahoo=yahoo, tiingo=self.fail)
        self.assertEqual(problems, [])
        self.assertIn("Yahoo download error (ValueError)", printed)

    def test_main_never_fails(self):
        with mock.patch.object(pc, "tiingo_key", side_effect=OSError("no .env")):
            self.assertEqual(quiet(pc.main, ["price_check.py"])[0], 0)
        with mock.patch.object(pc, "tiingo_key", return_value=None):
            result, printed = quiet(pc.main, ["price_check.py"])
        self.assertEqual(result, 0)
        self.assertIn("no TIINGO_API_KEY", printed)


class TiingoRequestTest(unittest.TestCase):
    def test_key_goes_in_a_header_not_the_url(self):
        seen = {}

        def get(url, params, headers, timeout):
            seen.update(url=url, params=params, headers=headers)
            return mock.Mock(status_code=200, json=lambda: [{"date": "2026-10-23T00:00:00.000Z", "close": 12.5}])
        self.assertEqual(pc.tiingo_closes("BRK.B", KEY, date(2026, 10, 16), get=get), {FRI: 12.5})
        self.assertEqual(seen["url"], "https://api.tiingo.com/tiingo/daily/BRK-B/prices")
        self.assertEqual(seen["headers"]["Authorization"], f"Token {KEY}")
        self.assertNotIn(KEY, seen["url"] + str(seen["params"]))

    def test_http_error_raises_with_the_status_only(self):
        get = lambda *a, **k: mock.Mock(status_code=401)  # noqa: E731
        with self.assertRaisesRegex(RuntimeError, r"^HTTP 401$"):
            pc.tiingo_closes("AAA", KEY, FRI, get=get)


class PrivateTest(unittest.TestCase):
    def test_tiingo_data_never_reaches_the_site_or_reports(self):
        # Only price_check.py talks to Tiingo, and it never touches the portfolio or the reports
        for f in ROOT.glob("*.py"):
            if f.name in ("price_check.py", "test_price_check.py"):
                continue
            text = f.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"(?i)tiingo|price_check", f.name)
        src = (ROOT / "price_check.py").read_text(encoding="utf-8")
        for word in ("PORTFOLIO_DIR", "portfolio_page", "REPORTS_DIR", "write_text(build", "publish"):
            self.assertNotIn(word, src)
        self.assertFalse(str(pc.STATE_FILE).startswith(str(ROOT)))

    def test_daily_script_runs_it_last_without_changing_the_exit_status(self):
        sh = (ROOT / "stock-debate-daily.sh").read_text(encoding="utf-8")
        check, price, end = sh.index("scan_check.py \"$status\""), sh.index("python price_check.py"), sh.rindex("exit $status")
        self.assertLess(check, price)
        self.assertLess(price, end)
        self.assertRegex(sh, r"timeout \d+m \.venv/bin/python price_check\.py \|\|")


if __name__ == "__main__":
    unittest.main()
