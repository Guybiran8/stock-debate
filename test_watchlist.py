"""Run with: python -m unittest test_watchlist -v
Uses made-up prices, so it needs no network and spends nothing."""
import json
import os
import random
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

# chart.py imports config.py, which needs these to exist. No API call is made.
for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "POLYGON_API_KEY"):
    os.environ.setdefault(key, "test-placeholder")

import portfolio_page as w_page  # noqa: E402
import watchlist as w  # noqa: E402

TODAY = date(2026, 10, 15)


def fake_candles(seed, days=500, drift=0.0005, vol=0.02, volume=5e6, start_price=50.0):
    rng = random.Random(seed)
    out, price, d = [], start_price, TODAY - timedelta(days=days * 7 // 5)
    while len(out) < days:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        o = price
        price = max(1.0, price * (1 + drift + rng.gauss(0, vol)))
        hi, lo = max(o, price) * (1 + abs(rng.gauss(0, vol / 2))), min(o, price) * (1 - abs(rng.gauss(0, vol / 2)))
        out.append({"d": d, "o": o, "h": hi, "l": lo, "c": price, "v": volume * (0.5 + rng.random())})
    return out


def perfect_metrics(**overrides):
    m = {
        "price": 110, "date": TODAY, "partial_day": False, "sma20": 105, "sma50": 100, "sma200": 90,
        "vs20": 0.05, "vs50": 0.1, "vs200": 0.22, "slope20": 0.01, "slope50": 0.02, "slope200": 0.03,
        "rs20": 0.05, "rs60": 0.1, "vol_ratio": 1.0, "contraction": 0.6,
        "support": 105, "support_label": "touched 3 times", "to_support": 0.045,
        "resistance": 125, "resistance_label": "touched 2 times", "to_resistance": 0.136,
        "at_highs": False, "high52": 140, "atr": 3.0,
        "risk": 105, "risk_label": "20-day average", "to_risk": 0.045,
        "room": 3.0, "dollar_volume": 50e6, "max_move_5d": 0.02,
    }
    m.update(overrides)
    return m


class WatchlistTest(unittest.TestCase):
    def test_watchlist_file_has_44_unique_tickers(self):
        tickers = w.load_watchlist()
        self.assertEqual(len(tickers), 44)
        self.assertEqual(len(set(tickers)), 44)

    def test_scoring_runs_on_all_44_tickers(self):
        tickers = w.load_watchlist()
        prices = {w.BENCHMARK: fake_candles("spy", drift=0.0004, vol=0.01, volume=80e6)}
        profiles = {}
        for i, t in enumerate(tickers):
            # Mix of up, down and flat paths, liquid and thin, biotech and not.
            prices[t] = fake_candles(t, drift=(i % 5 - 2) * 0.001, vol=0.01 + (i % 4) * 0.01,
                                     volume=(2e5 if i % 7 == 0 else 5e6))
            profiles[t] = {"industry": "Biotechnology" if i % 3 == 0 else "Semiconductors",
                           "earnings": None, "earnings_estimate": None}
        results, skipped = w.score_all(tickers, prices, profiles, {}, TODAY)

        self.assertEqual(skipped, [])
        self.assertEqual(sorted(r["ticker"] for r in results), sorted(tickers))
        for r in results:
            self.assertIn(r["group"], w.GROUPS)
            self.assertIn(r["tag"], ("technical-driven", "catalyst-driven"))
            self.assertTrue(r["why"], f"{r['ticker']} has no reason")
            if r["tag"] == "catalyst-driven" and not r["catalyst"]:
                self.assertIn(r["group"], ("Reject", "Catalyst watch"))
            if profiles[r["ticker"]]["industry"] == "Biotechnology":
                self.assertEqual(r["tag"], "catalyst-driven")
                self.assertNotEqual(r["group"], "Top candidates")
        page = w.build_page(results, skipped, TODAY)
        for t in tickers:
            self.assertIn(f"<b>{t}</b>", page)

    def test_missing_or_short_data_is_skipped_and_reported(self):
        prices = {w.BENCHMARK: fake_candles("spy"), "AAA": fake_candles("a"), "NEW": fake_candles("n", days=100), "GONE": []}
        results, skipped = w.score_all(["AAA", "NEW", "GONE"], prices, {}, {}, TODAY)
        self.assertEqual([r["ticker"] for r in results], ["AAA"])
        self.assertEqual(dict(skipped), {"NEW": "only 100 trading days of data", "GONE": "no price data"})

    def test_rules(self):
        tech = {"industry": "Semiconductors", "earnings": None, "earnings_estimate": None}
        bio = {"industry": "Biotechnology", "earnings": TODAY + timedelta(days=30), "earnings_estimate": False}
        self.assertEqual(w.classify("X", perfect_metrics(), tech, {}, TODAY)["group"], "Top candidates")
        # Biotech passing every technical rule still cannot be Top without a verified catalyst.
        r = w.classify("B", perfect_metrics(), bio, {}, TODAY)
        self.assertEqual((r["tag"], r["group"]), ("catalyst-driven", "Catalyst watch"))
        cat = {"B": {"date": TODAY + timedelta(days=20), "event": "FDA decision", "source": "https://example.com"}}
        self.assertEqual(w.classify("B", perfect_metrics(), bio, cat, TODAY)["group"], "Top candidates")
        self.assertEqual(w.classify("X", perfect_metrics(dollar_volume=1e6), tech, {}, TODAY)["group"], "Reject")
        self.assertEqual(w.classify("X", perfect_metrics(contraction=1.2), tech, {}, TODAY)["group"], "Watch closely")
        self.assertEqual(w.classify("X", perfect_metrics(price=85, sma20=88), tech, {}, TODAY)["group"], "Reject")
        # A big one-day move makes a stock catalyst-driven even outside biotech.
        self.assertEqual(w.tag_stock(perfect_metrics(max_move_5d=0.2), tech, TODAY)[0], "catalyst-driven")

    def test_at_highs_can_be_top_and_no_support_cannot(self):
        tech = {"industry": "Semiconductors", "earnings": None, "earnings_estimate": None}
        r = w.classify("X", perfect_metrics(at_highs=True, high52=111), tech, {}, TODAY)
        self.assertEqual((r["group"], r["flags"]), ("Top candidates", ["At highs / breakout"]))
        r = w.classify("X", perfect_metrics(risk=None, to_risk=None, room=None), tech, {}, TODAY)
        self.assertEqual(r["group"], "Watch closely")
        self.assertIn("no nearby support (none within 15% below)", r["why"])

    def test_risk_level_picks_nearest_within_15_percent(self):
        sw = (95.0, TODAY)
        self.assertEqual(w.risk_level(100, [(90.0, 3)], sw, 97.0), (97.0, "20-day average"))
        # 20-day average above price is not support; swing low is nearer than the level.
        self.assertEqual(w.risk_level(100, [(90.0, 3)], sw, 101.0)[0], 95.0)
        # Everything more than 15% below price: no nearby support.
        self.assertIsNone(w.risk_level(100, [(80.0, 2)], (84.0, TODAY), 84.9))
        self.assertIsNone(w.risk_level(100, [], None, None))

    def test_steady_uptrend_is_at_highs_and_uses_atr(self):
        spy = fake_candles("spy")
        up = fake_candles("up", drift=0.004, vol=0.005)
        m = w.compute_metrics(up, spy)
        self.assertTrue(m["at_highs"])
        self.assertAlmostEqual(m["resistance"], m["price"] + 2 * m["atr"])
        self.assertIn("ATR", m["resistance_label"])

    def test_risk_distance_is_floored_at_one_atr(self):
        spy = fake_candles("spy")
        for seed in ("a", "b", "c", "d", "e", "f"):
            m = w.compute_metrics(fake_candles(seed, drift=0.002), spy)
            if m["risk"] is not None:
                self.assertGreaterEqual(m["to_risk"], m["atr"] / m["price"] - 1e-12)
                if m["risk_floored"]:
                    self.assertAlmostEqual(m["to_risk"], m["atr"] / m["price"])
                    self.assertLess((m["price"] - m["risk"]) / m["price"], m["to_risk"])

    def _top(self, ticker, room, rs60=0.1):
        return {"ticker": ticker, "group": "Top candidates", "metrics": perfect_metrics(room=room, rs60=rs60)}

    def test_auto_reports_best_first_and_max_two_per_day(self):
        results = [self._top("LOW", 1.6), self._top("HIGH", 4.0), self._top("MID", 2.5),
                   {"ticker": "W", "group": "Watch closely", "metrics": perfect_metrics(room=9.0)}]
        ran = []

        def runner(t):
            ran.append(t)
            return Path(f"{t}-short-{TODAY}.html")

        with tempfile.TemporaryDirectory() as d:
            out = w.auto_reports(results, TODAY, runner, log_dir=d)
            self.assertEqual(ran, ["HIGH", "MID"])
            self.assertEqual([o for _, o, _ in out], ["report run", "report run", "not run: daily limit of 2 reached"])
            # A second run the same day starts nothing new.
            out = w.auto_reports(results, TODAY, runner, log_dir=d)
            self.assertEqual(ran, ["HIGH", "MID"])
            self.assertEqual([o for _, o, _ in out][:2], ["already run today", "already run today"])

    def test_auto_reports_failure_counts_and_zero_top_runs_nothing(self):
        def boom(t):
            raise RuntimeError("network down")

        with tempfile.TemporaryDirectory() as d:
            out = w.auto_reports([self._top("A", 3.0), self._top("B", 2.0), self._top("C", 1.8)], TODAY, boom, log_dir=d)
            self.assertEqual([o for _, o, _ in out][:2], ["report failed: RuntimeError: network down"] * 2)
            self.assertEqual(out[2][1], "not run: daily limit of 2 reached")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(w.auto_reports([], TODAY, boom, log_dir=d), [])
        page = w.build_page([], [], TODAY, w.auto_section([], [], True))
        self.assertIn("0 Top candidates today, so no full reports were run.", page)

    def test_portfolio_page_rebuild(self):
        top = ('<!DOCTYPE html>\n<html>\n<head><link rel="stylesheet" href="style.css"></head>\n<body>\n'
               '  <nav>Guy Biran</nav>\n  <main class="wrap">\n    <header class="intro">\n'
               '      <h1>Example reports</h1>\n    </header>\n')
        bottom = '  </main>\n  <footer>Guy Biran</footer>\n</body>\n</html>\n'
        old_middle = '    <ul class="cards"><li>old card</li></ul>\n'
        with tempfile.TemporaryDirectory() as d:
            port = Path(d)
            (port / "reports").mkdir()
            (port / "reports.html").write_text(top + old_middle + bottom, encoding="utf-8")
            files = {"NVDA-short-2026-10-13.html": "NVIDIA Corporation (NVDA)",
                     "NVDA-short-2026-10-01.html": "NVIDIA Corporation (NVDA)",
                     "NVDA-long-2026-09-20.html": "NVIDIA Corporation (NVDA)",
                     "KO-long-2026-10-13.html": "The Coca-Cola Company (KO)",
                     f"watchlist-{TODAY}.html": "Watchlist", "watchlist.html": "Watchlist"}
            for name, title in files.items():
                (port / "reports" / name).write_text(f"<html><head><title>{title}</title></head></html>", encoding="utf-8")

            asked = []

            def download(tickers):
                asked.extend(tickers)
                return {t: fake_candles(t) for t in tickers}

            built = w_page.build(port, TODAY, prices={"NVDA": fake_candles("nvda")}, download=download)
            page = (port / "reports.html").read_text(encoding="utf-8")

            self.assertEqual(asked, ["KO"])                         # KO is not on the watchlist; NVDA was already loaded
            self.assertEqual(built["charts"], ["KO", "NVDA"])
            self.assertTrue(page.startswith(top) and page.endswith(bottom))
            self.assertNotIn("old card", page)
            self.assertLess(page.index("Daily watchlist"), page.index('id="report-search"'))
            self.assertLess(page.index('id="report-search"'), page.index('class="stock"'))
            self.assertIn("No reports for that stock yet.", page)
            self.assertIn('data-search="nvda nvidia corporation"', page)
            self.assertIn('data-search="ko the coca-cola company"', page)
            self.assertIn("Chart today (updated daily)", page)

            nvda = page[page.index('id="stock-NVDA"'):]
            nvda = nvda[:nvda.index("</section>")]
            latest = nvda[nvda.index("Latest AI report"):nvda.index("Older reports")]
            self.assertIn("NVDA-short-2026-10-13.html", latest)
            self.assertIn("(2 days old)", latest)
            self.assertIn('<span class="stale" hidden>', latest)    # 2 days: not out of date
            older = nvda[nvda.index("<details"):]
            self.assertLess(older.index("2026-10-01"), older.index("2026-09-20"))   # newest first
            self.assertIn("(14 days old)", older)

            data = (port / "reports" / "charts-data.js").read_text(encoding="utf-8")
            self.assertTrue(data.startswith("window.CHART_DATA = "))
            charts = json.loads(data[len("window.CHART_DATA = "):].rstrip().rstrip(";"))
            self.assertEqual(set(charts), {"NVDA", "KO"})
            self.assertEqual(len(charts["NVDA"]["candles"]), w_page.CHART_DAYS)

            # Rebuilding again replaces the generated part instead of stacking it.
            w_page.build(port, TODAY, prices={"NVDA": fake_candles("nvda"), "KO": fake_candles("KO")}, download=download)
            again = (port / "reports.html").read_text(encoding="utf-8")
            self.assertEqual(again.count('id="report-search"'), 1)
            self.assertTrue(again.startswith(top) and again.endswith(bottom))

    def test_short_report_older_than_five_days_is_flagged(self):
        r = {"file": "X-short-2026-10-09.html", "horizon": "short", "date": date(2026, 10, 9), "name": "X"}
        self.assertIn('<span class="stale">may be out of date', w_page._report_link(r, TODAY, latest=True))
        self.assertIn("(6 days old)", w_page._report_link(r, TODAY, latest=True))
        long_r = dict(r, horizon="long", file="X-long-2026-10-01.html", date=date(2026, 10, 1))
        self.assertIn('<span class="stale" hidden>', w_page._report_link(long_r, TODAY, latest=True))

    def test_todays_events(self):
        base = fake_candles("e", days=300, drift=0.0, vol=0.002, volume=1e6)
        closes = [c["c"] for c in base]
        ma20 = w_page.sma_series(closes, 20)
        self.assertEqual(w_page.todays_events(base, ma20), [])
        # A big up day on heavy volume to a new high.
        jump = [dict(c) for c in base]
        top = max(c["h"] for c in jump[-253:-1])
        jump[-1].update(o=jump[-2]["c"], c=top * 1.08, h=top * 1.09, l=jump[-2]["c"], v=5e6)
        events = w_page.todays_events(jump, w_page.sma_series([c["c"] for c in jump], 20))
        self.assertTrue(any(e.startswith("moved +") for e in events))
        self.assertTrue(any(e.startswith("breakout: closed above the prior 52-week high") for e in events))
        self.assertTrue(any(e.startswith("volume ") for e in events))
        # Closing below the 20-day average after being above it.
        drop = [dict(c) for c in base]
        drop[-2].update(c=max(ma20[-2], drop[-2]["c"]) * 1.001)
        drop[-1].update(c=ma20[-1] * 0.99, l=ma20[-1] * 0.98)
        events = w_page.todays_events(drop, w_page.sma_series([c["c"] for c in drop], 20))
        self.assertIn("closed below the 20-day average", events)

    def test_catalyst_file_needs_future_date_and_source(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write("ticker,date,event,source_url\n"
                    "AAA,2026-11-01,FDA decision,https://example.com/a\n"
                    "BBB,2026-11-01,Trial data,\n"
                    "CCC,2026-01-01,Old event,https://example.com/c\n")
        try:
            self.assertEqual(list(w.load_catalysts(f.name, today=TODAY)), ["AAA"])
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
