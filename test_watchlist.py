"""Run with: python -m unittest test_watchlist -v
Uses made-up prices, so it needs no network and spends nothing."""
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

    def test_publish_adds_cards_without_touching_other_text(self):
        page = ('<!DOCTYPE html>\n<html>\n<body>\n  <main class="wrap">\n    <h1>Example reports</h1>\n'
                '    <ul class="cards">\n      <li>\n        <a class="card" href="reports/AMZN-long-2026-10-12.html">\n'
                '          <div class="ticker">AMZN</div>\n        </a>\n      </li>\n    </ul>\n  </main>\n</body>\n</html>\n')
        with tempfile.TemporaryDirectory() as d:
            port, src = Path(d) / "portfolio", Path(d) / "src"
            port.mkdir(); src.mkdir()
            (port / "reports.html").write_text(page, encoding="utf-8")
            wl, rep = src / f"watchlist-{TODAY}.html", src / f"NVDA-short-{TODAY}.html"
            wl.write_text("scan", encoding="utf-8"); rep.write_text("report", encoding="utf-8")

            w.publish_to_portfolio(wl, [rep], TODAY, portfolio=port)
            w.publish_to_portfolio(wl, [rep], TODAY + timedelta(days=1), portfolio=port)  # next day, same report
            s = (port / "reports.html").read_text(encoding="utf-8")
            self.assertEqual(s.count('href="reports/watchlist.html"'), 1)
            self.assertEqual(s.count(f'href="reports/NVDA-short-{TODAY}.html"'), 1)
            self.assertLess(s.index("reports/watchlist.html"), s.index("AMZN"))   # watchlist card is first
            self.assertIn("October 16, 2026", s)                                  # its date moved forward
            self.assertEqual((port / "reports" / "watchlist.html").read_text(encoding="utf-8"), "scan")
            # Removing the added cards gives back the original page exactly.
            stripped = re.sub(r'      <li>\n        <a class="card" href="reports/(watchlist|NVDA)[^"]*">.*?</li>\n', "", s, flags=re.S)
            self.assertEqual(stripped, page)

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
