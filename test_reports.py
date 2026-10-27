"""Run with: python -m unittest test_reports -v
Tests the report wording, money templates, hard facts and research parsing with made-up data,
so it needs no network and spends nothing."""
import math
import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

import pandas as pd

for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "POLYGON_API_KEY"):
    os.environ.setdefault(key, "test-placeholder")

import hard_facts  # noqa: E402
import judge  # noqa: E402
import report  # noqa: E402
import research  # noqa: E402
import scenarios  # noqa: E402
import setup_status  # noqa: E402
import wording  # noqa: E402

TODAY = date(2026, 10, 16)


def fake_daily(n=300, start=100.0, step=0.1):
    out, d, price = [], datetime(2025, 8, 1, tzinfo=timezone.utc), start
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        price += step if len(out) % 7 else -3 * step
        out.append({"t": d.timestamp(), "o": price, "h": price * 1.01, "l": price * 0.99, "c": price, "v": 1e6})
    return out


def fake_chart(at_highs=False, supports=True):
    daily = fake_daily()
    price = daily[-1]["c"]
    return {"daily": daily, "price": price, "atr": 2.0, "at_highs": at_highs, "high52": price * 1.01,
            "low52": 90.0, "resistances": [(price * 1.1, "touched 3 times")],
            "supports": [(price * 0.95, "support level, touched 2 times")] if supports else []}


class Wording(unittest.TestCase):
    def test_unexplained_terms_are_found(self):
        self.assertEqual(wording.unexplained("It sits above support at $120 and RSI is 72."), ["support", "RSI"])

    def test_explanations_in_any_common_form_pass(self):
        text = ("It sits above support (a price where buyers stepped in before). The RSI, a 0 to 100 gauge of "
                "speed, is 72. A price where sellers stepped in, called resistance, is $140. It spent more than "
                "it made (its cash burn). The P/E ratio (price divided by profit per share) is 30.")
        self.assertEqual(wording.unexplained(text), [])

    def test_support_as_a_verb_is_not_a_term(self):
        self.assertEqual(wording.unexplained("The evidence does not support the bet. Sales support a rise."), [])

    def test_only_the_first_use_needs_explaining(self):
        self.assertEqual(wording.unexplained("Beta (how much it swings vs the market) is 2. A high beta hurts."), [])

    def test_a_colon_and_a_number_is_not_an_explanation(self):
        self.assertEqual(wording.unexplained("Beta: 1.2"), ["beta"])

    def test_dollar_win_loss_sentences_are_caught(self):
        self.assertTrue(wording.money_sentences("On 10 shares you would gain about $150."))
        self.assertTrue(wording.money_sentences("That is +$120 on the position."))
        self.assertEqual(wording.money_sentences("The price could fall to $120, down 5%. Watch $140."), [])
        self.assertEqual(wording.money_sentences("Second-quarter profit included a $53.4 billion gain, so you see why."), [])

    def test_grade_tells_easy_from_hard(self):
        easy = "The stock went up. People like the new phone. Sales grew fast. The price may keep rising. " * 3
        hard = ("Notwithstanding considerable macroeconomic uncertainty, institutional participants demonstrated "
                "substantial conviction regarding prospective profitability improvements. ") * 3
        self.assertLess(wording.grade(easy), wording.MAX_GRADE)
        self.assertGreater(wording.grade(hard), wording.MAX_GRADE)

    def test_fix_rewrites_once_and_keeps_a_passing_rewrite(self):
        calls = []

        def rewrite(prompt):
            calls.append(prompt)
            return "RSI (a 0 to 100 gauge of how fast the price moved) is 72."

        text, left = wording.fix("RSI is 72.", rewrite, "test")
        self.assertEqual(len(calls), 1)
        self.assertIn("RSI", calls[0])
        self.assertTrue(text.startswith("RSI (a 0"))
        self.assertEqual(left, [])

    def test_rewrite_drops_terms_left_after_the_sentence(self):
        # Both from the PENG report of Oct 20, 2026.
        bad = ("- The report can still weigh on the stock for about three trading days after Oct 20. "
               "priced in [already reflected in the stock price]\n"
               "- The Fed rate decision on Oct 28, and the inflation report on Oct 29. "
               "interest rates [the cost of borrowing money set by the Fed]\n"
               "- Results come Nov 3. Super Micro (SMCI)")
        text, _ = wording.fix("RSI is 72.", lambda p: bad, "test")
        self.assertIn("three trading days after Oct 20.\n", text)
        self.assertIn("inflation report on Oct 29.\n", text)
        self.assertNotIn("[already reflected", text)
        self.assertNotIn("[the cost of borrowing", text)
        self.assertIn("Super Micro (SMCI)", text)   # not a glossary term, so it stays

    def test_fix_does_not_call_rewrite_for_good_text(self):
        text, left = wording.fix("The stock went up today.", lambda p: self.fail("no rewrite expected"), "test")
        self.assertEqual(left, [])

    def test_tooltips_mark_first_use_and_escape(self):
        out = wording.tip_html("RSI <b> is high. RSI again.")
        self.assertEqual(out.count("class='tip'"), 1)
        self.assertIn("&lt;b&gt;", out)
        self.assertIn("data-tip='RSI: A 0 to 100", out)


class Scenarios(unittest.TestCase):
    def test_main_sentence_uses_resistance_and_support(self):
        c = fake_chart()
        s = scenarios.main_sentence(c, labels=True)
        self.assertIn("If you buy 10 shares at", s)
        self.assertIn("next resistance", s)
        self.assertIn("nearest support", s)
        up = (c["resistances"][0][0] - c["price"]) * 10
        self.assertIn(f"+${up:,.2f}", s)

    def test_at_highs_and_no_support_are_labeled(self):
        s = scenarios.main_sentence(fake_chart(at_highs=True, supports=False), labels=True)
        self.assertIn("a guess of two normal days", s)
        self.assertIn("15% drop", s)

    def test_support_closer_than_one_day_counts_as_one_day(self):
        c = fake_chart()
        c["supports"] = [(c["price"] - 0.5, "support level, touched 2 times")]
        s = scenarios.main_sentence(c, labels=True)
        self.assertIn(f"-${c['atr'] * 10:,.2f} if it falls to ${c['price'] - c['atr']:,.2f}", s)
        self.assertIn("one normal day's move down", s)

    def test_20_day_average_and_swing_low_are_not_called_support(self):
        c = fake_chart()
        c["supports"] = [(c["price"] * 0.9, "20-day average")]
        s = scenarios.main_sentence(c, labels=True)
        self.assertIn("(the 20-day average, the average closing price over the last 20 trading days)", s)
        self.assertNotIn("buyers stepped in", s)
        c["supports"] = [(c["price"] - 0.5, "most recent swing low (a low point where the price turned back up), Oct 02")]
        s = scenarios.main_sentence(c, labels=True)
        self.assertIn("one normal day's move down; the most recent swing low, a low point where the price turned "
                      f"back up, is at ${c['price'] - 0.5:,.2f}, closer than that", s)

    def test_chart_lane_sentence_does_not_repeat_the_tiles(self):
        # The scenario tiles above it already say what each price is (AMZN, Oct 25, 2026).
        c = fake_chart()
        up, down = c["resistances"][0][0], c["supports"][0][0]
        s = scenarios.main_sentence(c)
        self.assertEqual(s, f"If you buy 10 shares at ${c['price']:,.2f} (about ${c['price'] * 10:,.0f} in total): "
                            f"about +${(up - c['price']) * 10:,.2f} at ${up:,.2f} and about "
                            f"-${(c['price'] - down) * 10:,.2f} at ${down:,.2f}.")
        self.assertNotIn("resistance", s)
        self.assertIn("next resistance", scenarios.money_lines(c, "short")[0])

    def test_long_lines_add_target_and_low(self):
        lines = scenarios.money_lines(fake_chart(), "long", target=150.0, earnings=(0.05, 4, TODAY))
        text = " ".join(lines)
        self.assertIn("If it reached $150.00, the average price target", text)
        self.assertIn("lowest price of the past year, $90.00", text)
        self.assertIn("moved 5.0% on average", text)

    def test_money_text_explains_its_terms(self):
        for horizon in ("short", "long"):
            text = " ".join(scenarios.money_lines(fake_chart(), horizon, target=150.0, earnings=(0.05, 4, TODAY)))
            self.assertEqual(wording.unexplained(text), [], horizon)


class FakeStock:
    def __init__(self):
        self.earnings_dates = pd.DataFrame(
            {"EPS Estimate": [1.0, 1.0], "Reported EPS": [None, 1.2]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-11-05 16:00", tz="America/New_York"),
                                    pd.Timestamp("2026-08-05 16:00", tz="America/New_York")]))
        self.calendar = {"Earnings Average": -1.3, "Revenue Average": 9e8}
        self.upgrades_downgrades = pd.DataFrame(
            {"Firm": ["Big Bank"], "ToGrade": ["Sell"], "FromGrade": ["Neutral"], "Action": ["down"],
             "priceTargetAction": ["Raises"], "currentPriceTarget": [80.0], "priorPriceTarget": [60.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2026-10-10")]))
        self.insider_transactions = pd.DataFrame(
            {"Shares": [1000, 500], "Value": [150000.0, None], "Text": ["Sale at price 150.00 per share.", ""],
             "Insider": ["DOE JANE", "ROE RICH"], "Position": ["Chief Executive Officer", "Director"],
             "Start Date": ["2026-09-15", "2026-09-20"]})
        q = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
        self.quarterly_cashflow = pd.DataFrame(
            [[-500e6, -600e6, -400e6, -450e6, -300e6], [-30e6] * 5],
            index=["Operating Cash Flow", "Capital Expenditure"], columns=q)
        self.quarterly_balance_sheet = pd.DataFrame([[400e6, 398e6, 395e6, 392e6, 380e6]],
                                                    index=["Ordinary Shares Number"], columns=q)


INFO = {"shortPercentOfFloat": 0.12, "sharesShort": 3e7, "sharesShortPriorMonth": 4e7, "shortRatio": 2.0,
        "dateShortInterest": 1790726400, "totalCash": 5e9, "targetMeanPrice": 150.0}


class HardFacts(unittest.TestCase):
    def facts(self):
        s, daily = FakeStock(), fake_daily()
        out = [hard_facts.analyst_fact(s, TODAY, 130.0), hard_facts.insider_fact(s, TODAY),
               hard_facts.short_fact(INFO, TODAY + timedelta(days=8))] + hard_facts.cash_facts(s, INFO)
        out.append(hard_facts.earnings_fact(s, {}, daily, TODAY)[0])
        out.append(hard_facts.valuation_fact(hard_facts.pe_rows(*PE_DATA), TODAY, TODAY + timedelta(days=8)))
        return [f for f in out if f]

    def test_every_fact_says_how_and_when(self):
        facts = self.facts()
        self.assertEqual(len(facts), 7)
        for f in facts:
            self.assertTrue(f["effect"] and f["when"], f["topic"])

    def test_templates_explain_their_terms(self):
        text = hard_facts.facts_text(self.facts())
        self.assertEqual(wording.unexplained(research._fact_prose(text)), [])

    def test_targets_below_price_are_called_out(self):
        f = hard_facts.analyst_fact(FakeStock(), TODAY, 130.0)
        self.assertIn("downgraded it (lowered its rating) from Neutral to Sell", f["fact"])
        self.assertIn("every new target is below today's price", f["effect"])

    def _analyst(self, rows, price):
        s = FakeStock()
        s.upgrades_downgrades = pd.DataFrame(
            rows, columns=["Firm", "ToGrade", "FromGrade", "Action", "priceTargetAction", "currentPriceTarget",
                           "priorPriceTarget"],
            index=pd.DatetimeIndex([pd.Timestamp("2026-10-10") - timedelta(days=i) for i in range(len(rows))]))
        return hard_facts.analyst_fact(s, TODAY, price)

    def test_analyst_notes_are_counted_once(self):
        # Like MRNA: a downgrade that also raised its target, plus two target raises, all below the price.
        f = self._analyst([["Citi", "Sell", "Neutral", "down", "Raises", 80.0, 60.0],
                           ["Goldman", "Neutral", "Neutral", "main", "Raises", 137.0, 120.0],
                           ["Morgan", "Equal-Weight", "Equal-Weight", "main", "Raises", 95.0, 89.0]], 242.0)
        self.assertIn("published 3 note(s): 0 upgrade(s), 1 downgrade(s), 0 started covering it and "
                      "2 kept their rating", f["fact"])
        self.assertIn("3 raised and 0 cut", f["fact"])
        self.assertIn("lean negative", f["effect"])
        # Target raises alone, with every target below the price, are not positive.
        f = self._analyst([["Goldman", "Neutral", "Neutral", "main", "Raises", 137.0, 120.0]], 242.0)
        self.assertIn("are mixed", f["effect"])

    def test_reaffirmed_ratings_are_not_changes(self):
        # Like NVDA: two notes that kept the rating and the target.
        f = self._analyst([["Cantor", "Overweight", "Overweight", "main", "Maintains", 350.0, 350.0],
                           ["Rosenblatt", "Buy", "Buy", "reit", "Maintains", 390.0, 390.0]], 216.0)
        self.assertIn("2 kept their rating", f["fact"])
        self.assertIn("None of them changed a rating or a price target.", f["fact"])
        self.assertIn("only repeat earlier views", f["effect"])
        self.assertNotIn("mixed", f["effect"])

    def test_code_facts_show_source_once_with_pull_date(self):
        f = hard_facts.earnings_fact(FakeStock(), {}, fake_daily(), TODAY)[0]
        f["pulled"] = TODAY
        src = report._source_html(f)
        self.assertTrue(src.startswith("Yahoo Finance <span class='kind estimate-analysts'"))
        self.assertIn("class='kind calc'", src)
        self.assertTrue(src.endswith(" · pulled Oct 16, 2026"))
        self.assertEqual(src.count("Yahoo Finance"), 1)
        research_fact = {"source": "https://example.com/a", "date": "2026-10-01", "check": "re-checked against its source: confirmed"}
        self.assertIn("2026-10-01 · re-checked", report._source_html(research_fact))

    def test_insider_sales_only_count_open_market_sales(self):
        f = hard_facts.insider_fact(FakeStock(), TODAY)
        self.assertIn("sold 1,000 shares", f["fact"])
        self.assertIn("Doe Jane (Chief Executive Officer)", f["fact"])

    def test_cash_runway_and_dilution(self):
        cash, shares = hard_facts.cash_facts(FakeStock(), INFO)
        self.assertIn("cash runway", cash["fact"])
        self.assertIn("+5.3%", shares["fact"])

    def test_high_short_interest_mentions_squeeze(self):
        self.assertIn("short squeeze", hard_facts.short_fact(INFO, None)["effect"])

    def test_valuation_puts_every_pe_on_one_basis(self):
        rows = hard_facts.pe_rows(*PE_DATA)
        self.assertEqual(rows[0], ("KO", "The Coca-Cola Company", 28.0, 24.5))     # 98 / 3.5 and 98 / 4
        self.assertEqual(rows[1][2:], (17.0, 16.0))                                   # PEP: price / EPS, both kinds
        self.assertEqual(rows[2][2], hard_facts.LOSS)                                # no P/E on a loss
        self.assertEqual(rows[3][3], hard_facts.BAD_FORECAST)                        # forecast 10x last year's profit
        self.assertNotIn("ZZZ", [r[0] for r in rows])                                # no price, left out
        f = hard_facts.valuation_fact(rows, TODAY)
        self.assertEqual(f["source"], "Yahoo Finance")
        self.assertEqual(f["topic"], "Valuation")
        # Forward is compared; the median (of 16.0 and 20.0) skips peers without a usable forward P/E.
        self.assertIn("On forward P/E it is 36% above the peer median (the middle value) of 18.0.", f["fact"])
        self.assertIn("(PEP) 17.0 and 16.0", f["fact"])
        self.assertIn("premium", f["effect"])

    def test_valuation_falls_back_to_trailing_and_needs_peers(self):
        prices, infos = {"AAA": 55.0, "BBB": 40.0, "CCC": 30.0}, {
            "AAA": {"trailingEps": 5.0}, "BBB": {"trailingEps": 4.0, "forwardEps": 5.0}, "CCC": {"trailingEps": 2.0}}
        f = hard_facts.valuation_fact(hard_facts.pe_rows(["AAA", "BBB", "CCC"], prices, infos), TODAY)
        self.assertIn("On trailing P/E it is 12% below the peer median (the middle value) of 12.5.", f["fact"])
        self.assertIn("about in line", f["effect"])
        self.assertIn("pay less", hard_facts.valuation_fact(
            hard_facts.pe_rows(["AAA", "CCC"], {"AAA": 20.0, "CCC": 30.0}, infos), TODAY)["effect"])
        self.assertIsNone(hard_facts.valuation_fact(hard_facts.pe_rows(["AAA"], prices, infos), TODAY))
        losing = {**infos, "AAA": {"trailingEps": -1.0}}
        self.assertIsNone(hard_facts.valuation_fact(hard_facts.pe_rows(["AAA", "BBB"], prices, losing), TODAY))

    def test_peers_come_from_the_override_or_yahoo_industry(self):
        self.assertEqual(hard_facts.peers("PENG", {}), hard_facts.PEERS["PENG"])
        top = pd.DataFrame({"name": list("abcdef")}, index=["KO", "PEP", "MNST", "KDP", "COKE", "CELH"])
        with mock.patch.object(hard_facts.yf, "Industry", return_value=mock.Mock(top_companies=top)) as industry:
            self.assertEqual(hard_facts.peers("KO", {"industryKey": "beverages-non-alcoholic"}),
                             ["PEP", "MNST", "KDP", "COKE"])
        industry.assert_called_once_with("beverages-non-alcoholic")
        self.assertEqual(hard_facts.peers("XYZ", {}), [])                            # no industry, no peers


PE_DATA = (["KO", "PEP", "LOSE", "SPLT", "ZZZ"],
           {"KO": 98.0, "PEP": 136.0, "LOSE": 10.0, "SPLT": 50.0},
           {"KO": {"longName": "Coca-Cola Company (The)", "trailingEps": 3.5, "forwardEps": 4.0},
            "PEP": {"shortName": "PepsiCo", "trailingEps": 8.0, "forwardEps": 8.5},
            "LOSE": {"trailingEps": -0.5, "forwardEps": 0.5},
            "SPLT": {"trailingEps": 2.0, "forwardEps": 20.0}})


RAW = """Here is what I found.
TOPIC: The catalyst
FACT: Trial data comes out Oct 24.
DATE: 2026-10-01
SOURCE: https://example.com/a.
PRICE EFFECT: A good result could lift the price a lot.
WHEN: Oct 24, 2026.
IMPORTANCE: 5

TOPIC: News
FACT: The company moved offices.
DATE: 2026-10-10
SOURCE: https://example.com/b
PRICE EFFECT: n/a
WHEN: n/a
IMPORTANCE: 1

TOPIC: Cash
FACT: Cash lasts into 2028,
  the company says.
DATE: 2026-08-01
SOURCE: https://example.com/c
PRICE EFFECT: Less risk of a share sale.
WHEN: Over the next year.
IMPORTANCE: 3
"""


class Research(unittest.TestCase):
    def test_prompt_ranks_sources_and_leaves_valuation_to_code(self):
        rules = research.FORMAT
        self.assertNotIn("Prefer the company's own filings", rules)
        for text in ("Tier 1:", "sec.gov", "Tier 2:", "Reuters", "Bloomberg", "Aggregators:",
                     "name the site in the FACT", "Never cite a copy of a filing"):
            self.assertIn(text, rules)
        self.assertIn("come only from the agency that publishes them", rules)
        self.assertIn("Never from economic calendar websites.", rules)
        self.assertLess(rules.index("Tier 1:"), rules.index("Tier 2:"))
        self.assertLess(rules.index("Tier 2:"), rules.index("Aggregators:"))
        self.assertIn("price multiples like P/E, forward P/E, PEG and price-to-sales", research.CODE_COVERS)
        self.assertIn("Do not look up any price multiple", " ".join(research.PLANS["long"]["focus"].split()))

    def test_facts_without_how_or_when_are_dropped(self):
        facts, dropped = research.parse_facts(RAW, research.PLANS["catalyst"]["topics"])
        self.assertEqual(dropped, 1)
        self.assertEqual([f["importance"] for f in facts], [5, 3])
        self.assertEqual(facts[0]["source"], "https://example.com/a")
        self.assertEqual(facts[1]["fact"], "Cash lasts into 2028, the company says.")
        self.assertEqual(facts[1]["topic"], "Cash and dilution")
        other, _ = research.parse_facts(RAW.replace("TOPIC: Cash", "TOPIC: Company overview"), research.PLANS["catalyst"]["topics"])
        self.assertEqual(other[1]["topic"], "Other")

    def test_recheck_corrects_drops_and_adds_priced_in(self):
        facts, _ = research.parse_facts(RAW, research.PLANS["catalyst"]["topics"])
        answers = {"Trial data": """CHECK: 1
RESULT: corrected
FACT: Trial data comes out Oct 25.
SOURCE: https://example.com/a2
ALREADY IN THE PRICE: The stock rose 20% since the date was announced.""",
                   "Cash lasts": """CHECK: 1
RESULT: not found
FACT: -
SOURCE: -
ALREADY IN THE PRICE: Nothing found."""}
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return next(a for k, a in answers.items() if k in prompt)

        out = research.recheck(facts, "XYZ", "XYZ Inc", TODAY, ask=ask)
        self.assertEqual(len(prompts), 2)  # one call per fact
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["fact"], "Trial data comes out Oct 25.")
        self.assertEqual(out[0]["source"], "https://example.com/a2")
        self.assertIn("rose 20%", out[0]["priced_in"])
        self.assertIn("corrected", out[0]["check"])


DECISION = """THE BIG PICTURE
Chart: No setup. It sits near its high of $101.00 [R], but daily moves are getting bigger.
Company: Sales grow, but the price already pays for that.
Link: The Oct 24 report will likely decide which way this goes.

COMPANY LEAN
Mixed: growth is good but already in the price.

CHART EXPLAINED
A setup is a quiet pause under a ceiling price. This stock did the opposite.

COMPANY DRIVERS
- Sales are growing.
Detail: Sales rose 7% [R] to $13.4 billion [R].
- Cash is strong.
Detail: Free cash flow (cash left after bills and equipment) was $14.3B [R].

MAIN RISKS
- A tax case.
Detail: It paid a $6.0B [R] deposit.

WHAT THE PRICE ASSUMES
The average price target (where analysts think the price will be in a year) is $95.12 [EA], 2.6% [C] above today. Targets are opinions.

WHAT CHANGES THE PICTURE
- Oct 24, 2026: results come out. A beat could lift it.
- Late 2026 to mid 2027: the ruling in the tax case above.
"""


def series(closes, ranges, vols):
    out, d = [], date(2025, 1, 1)
    for c, r, v in zip(closes, ranges, vols):
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"d": d, "o": c, "h": c * (1 + r / 2), "l": c * (1 - r / 2), "c": c, "v": v})
    return out


def _next_day(daily, close, volume):
    d = daily[-1]["d"] + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    daily.append({"d": d, "o": close, "h": close * 1.005, "l": close * 0.995, "c": close, "v": volume})
    return daily


def rise(n=220, a=50.0, b=96.0):
    # A steady climb with a dip every 10 days, so there are pivots.
    return [a + (b - a) * i / n - (2.0 if i % 10 in (4, 5, 6) else 0) for i in range(n)]


def base(lows, top=100.0, period=10):
    # Waves under top whose lows are the given prices.
    return [top - (top - low) * (1 + math.cos(2 * math.pi * (k - period / 2) / period)) / 2
            for low in lows for k in range(period)]


def early_setup():
    t, b = rise(), base([94, 95.5, 97, 98]) + [99.0]
    ranges = [0.02] * len(t) + [0.02 - 0.012 * i / len(b) for i in range(len(b))]
    return series(t + b, ranges, [1e6] * len(t) + [6e5] * len(b))


def breakout():
    return _next_day(early_setup(), 103.0, 2e6)


def failed_setup():
    d = breakout()
    for c in (102.0, 99.0, 97.0):
        _next_day(d, c, 1e6)
    return d


def widening():
    # A fast run with growing daily moves and heavier volume, like KO in October 2026.
    t, run = rise(), [90 + i * 0.9 for i in range(12)]
    n = len(t) + 20
    return series(t + base([92, 93]) + run, [0.01] * n + [0.04] * len(run), [1e6] * n + [1.6e6] * len(run))


def downtrend():
    return series([100 - i * 0.2 for i in range(260)], [0.02] * 260, [1e6] * 260)


class SetupStatus(unittest.TestCase):
    def test_each_status_from_the_chart(self):
        self.assertEqual(setup_status.evaluate(early_setup())["status"], "Early setup")
        self.assertEqual(setup_status.evaluate(breakout())["status"], "Confirmed breakout")
        self.assertEqual(setup_status.evaluate(failed_setup())["status"], "Setup failed")
        self.assertEqual(setup_status.evaluate(widening())["status"], "No setup")
        self.assertEqual(setup_status.evaluate(downtrend())["status"], "No setup")

    def test_breakout_needs_heavy_volume(self):
        quiet = _next_day(early_setup(), 103.0, 7e5)   # 1.2 times the base's volume
        self.assertNotEqual(setup_status.evaluate(quiet)["status"], "Confirmed breakout")

    def test_near_a_high_is_not_a_setup_without_tightening(self):
        s = setup_status.evaluate(widening())
        passed = {name: ok for name, _, _, ok in s["rows"]}
        self.assertTrue(passed["Uptrend"] and passed["Near resistance"] and passed["Fail level nearby"])
        self.assertFalse(passed["Range tightening"] or passed["Higher lows"] or passed["Volume drying up"])
        self.assertIn("but none of the 3 tightening checks", s["reason"])
        self.assertNotIn("0 of the 3", s["reason"])
        s["tightening"] = 1
        self.assertIn("but only 1 of the 3 tightening checks", setup_status.reason(s))

    def test_uses_the_watchlist_limits(self):
        import watchlist
        self.assertIs(setup_status.MAX_CONTRACTION, watchlist.MAX_CONTRACTION)
        self.assertEqual(setup_status.EVENT_DAYS, watchlist.EARNINGS_SOON_DAYS)
        rows = setup_status.evaluate(early_setup())["rows"]
        self.assertIn("0.80 or less", rows[3][1])

    def test_failed_setup_names_the_level_it_broke(self):
        s = setup_status.evaluate(failed_setup())
        self.assertLess(s["price"], s["failed"]["fail"][0])
        self.assertIn("has since closed", s["reason"])

    def test_failed_setup_says_which_old_level_it_broke(self):
        s = setup_status.evaluate(failed_setup())
        s["failed"] = dict(s["failed"], was="Confirmed breakout", fail=(257.29, "resistance"))
        self.assertIn("closed back below its breakout level of $257.29", setup_status.reason(s))
        s["failed"]["was"] = "Early setup"
        self.assertIn("below the fail level that setup had then, $257.29", setup_status.reason(s))
        self.assertNotIn("that setup's fail level", setup_status.reason(s))

    def test_short_history_is_no_setup(self):
        s = setup_status.evaluate(early_setup()[-100:])
        self.assertEqual(s["status"], "No setup")
        self.assertIn("Only 100 trading days", s["reason"])

    def test_changes_give_exact_levels_and_no_labels(self):
        for daily in (early_setup(), breakout(), failed_setup(), widening(), downtrend()):
            s = setup_status.evaluate(daily)
            lines = setup_status.changes(s, (date(2026, 10, 27), "earnings report"))
            text = " ".join(lines) + " " + s["reason"]
            self.assertRegex(text, r"\$\d")
            self.assertNotRegex(text.lower(), r"\b(buy|sell|watch)\b")
            self.assertIn("Oct 27, 2026", lines[-1])
        self.assertIn("confirmed breakout on a close above",
                      " ".join(setup_status.changes(setup_status.evaluate(widening()))))

    def test_setup_text_for_the_ai_says_code_decides(self):
        text = setup_status.text(setup_status.evaluate(widening()))
        self.assertTrue(text.startswith("SETUP STATUS (decided by code, do not change it): No setup."))
        self.assertIn("Range tightening", text)


class Evidence(unittest.TestCase):
    EVENT = (date(2026, 10, 27), "earnings report")
    TODAY = date(2026, 10, 21)

    def test_agree_and_disagree(self):
        s = setup_status.evaluate(early_setup())
        self.assertEqual(setup_status.evidence(s, "Supportive")[0], "Chart and company agree")
        self.assertEqual(setup_status.evidence(s, "Against")[0], "Chart and company disagree")
        self.assertEqual(setup_status.evidence(s, "Mixed")[0], "Too early to tell")
        down = setup_status.evaluate(downtrend())
        self.assertEqual(setup_status.evidence(down, "Against")[0], "Chart and company agree")
        self.assertEqual(setup_status.evidence(down, "Supportive")[0], "Chart and company disagree")

    def test_no_setup_in_an_uptrend_is_too_early(self):
        s = setup_status.evaluate(widening())
        self.assertEqual(setup_status.evidence(s, "Supportive")[0], "Too early to tell")

    def test_near_event_with_a_big_move_is_too_early(self):
        s = setup_status.evaluate(early_setup())
        gap = setup_status.nearest_level(s)
        label, why = setup_status.evidence(s, "Supportive", self.EVENT, gap + 0.01, self.TODAY)
        self.assertEqual(label, "Too early to tell")
        self.assertIn("usually moves it", why)
        hard_facts_name = setup_status.evidence(s, "Supportive", (self.EVENT[0], "the next earnings report"),
                                                gap + 0.01, self.TODAY)[1]
        self.assertIn("because the next earnings report", hard_facts_name)
        self.assertNotIn("the the", " ".join(setup_status.changes(s, (self.EVENT[0], "the next earnings report"))).lower())

    def test_small_move_or_far_event_does_not_override(self):
        s = setup_status.evaluate(early_setup())
        gap = setup_status.nearest_level(s)
        self.assertEqual(setup_status.evidence(s, "Supportive", self.EVENT, gap / 2, self.TODAY)[0],
                         "Chart and company agree")
        far = (date(2026, 11, 20), "earnings report")
        self.assertEqual(setup_status.evidence(s, "Supportive", far, gap + 0.05, self.TODAY)[0],
                         "Chart and company agree")
        self.assertEqual(setup_status.evidence(s, "Supportive", self.EVENT, None, self.TODAY)[0],
                         "Chart and company agree")

    def test_table_lists_every_lean(self):
        table = setup_status.evidence_table(setup_status.evaluate(early_setup()))
        self.assertEqual(table.count("If COMPANY LEAN is"), 3)
        near = setup_status.evaluate(early_setup())
        why = setup_status.evidence_table(near, self.EVENT, setup_status.nearest_level(near) + 0.01, self.TODAY)
        self.assertEqual(why.count("Too early because the earnings report"), 3)


class DecisionChecks(unittest.TestCase):
    def test_good_brief_passes(self):
        self.assertEqual(judge.decision_problems(DECISION, "No setup"), [])
        self.assertEqual(judge.company_lean(DECISION), "Mixed")

    def test_chart_line_must_use_the_code_status(self):
        self.assertIn('"Early setup"', judge.decision_problems(DECISION, "Early setup")[0])

    def test_numbers_need_tags(self):
        found = judge.decision_problems(DECISION.replace("$6.0B [R]", "$6.0B"), "No setup")
        self.assertEqual(len(found), 1)
        self.assertIn("$6.0B", found[0])
        self.assertEqual(judge.untagged_numbers("It rose 9% to 10% [EC] this year."), [])

    def test_an_issue_explained_twice_is_caught(self):
        twice = DECISION.replace("the ruling in the tax case above.", "a ruling on the $6.0B [R] deposit.")
        found = judge.decision_problems(twice, "No setup")
        self.assertIn("Explain each issue once", found[0])
        self.assertIn("$6.0b", found[0])

    def test_missing_heading_and_bad_lean(self):
        self.assertIn("MAIN RISKS", judge.decision_problems(DECISION.replace("MAIN RISKS", "RISKS"), "No setup")[0])
        bad = DECISION.replace("Mixed: growth", "Leans bullish: growth")
        self.assertTrue(any("COMPANY LEAN" in p for p in judge.decision_problems(bad, "No setup")))
        self.assertEqual(judge.company_lean(bad), "Mixed")

    def test_prompt_holds_setup_and_table(self):
        self.assertIn("{SETUP}", judge.DECISION_INSTRUCTIONS)
        self.assertIn("{TABLE}", judge.DECISION_INSTRUCTIONS)
        self.assertNotIn("WHAT THE CHART SHOWS", judge.CHECKS_INSTRUCTIONS)


class NumberTags(unittest.TestCase):
    def test_tags_become_dots_with_the_label_on_hover(self):
        out = wording.tip_html("Sales rose 7% [R]. Target $95 [EA]. Plan 5% [EC]. Gap 2% [C].")
        self.assertNotIn("[R]", out)
        for cls in ("reported", "estimate-analysts", "estimate-company", "calc"):
            self.assertIn(f"class='kind {cls}'", out)
        self.assertIn("data-tip='Estimate (company): ", out)
        self.assertIn("aria-label='Reported fact: ", out)

    def test_a_rewrite_that_drops_tags_is_not_kept(self):
        text = "Its EPS rose 7% [R] to $2 [R]."
        kept, left = wording.fix(text, lambda prompt: "Its EPS (profit per share) rose 7% to $2.", "test")
        self.assertEqual(kept, text)
        self.assertTrue(left)

    def test_the_same_percent_in_two_sections_is_not_a_repeat(self):
        both = DECISION.replace("Sales rose 7% [R]", "Sales rose 9% [R]").replace("2.6% [C]", "9% [C]")
        self.assertEqual(judge.decision_problems(both, "No setup"), [])

    def test_tags_are_ignored_by_the_wording_checks(self):
        self.assertEqual(wording.unexplained("EPS [EA] was $1."), ["EPS"])
        self.assertEqual(wording.problems("Sales rose 7% [R]."), [])


def layout_page(daily=None, evidence=("Too early to tell", "Chart: no setup. Company: mixed.")):
    chart = fake_chart()
    chart.update({"support_text": "$95.00 (support level, touched 2 times)", "resistance_text": "$110.00 (touched 3 times)",
                  "volume_vs_20d": 0.95})
    s = setup_status.evaluate(daily or widening())
    d = {"bull": "b", "bear": "c", "bull_reply": "d", "bear_reply": "e", "bull_name": "Claude", "bear_name": "ChatGPT"}
    return report.build_page(
        "ABC", "Abc Inc", TODAY, "Long-term hold", "facts", "chart text", chart, {"facts": [], "x": ""}, d,
        DECISION, "BEST BULL POINTS\n- Sales grow.\nEvidence: up 10%.\nBEST BEAR POINTS\n- Debt is high.\n",
        "If you buy 10 shares at $210.", ["line one"], [], [], "research", s, evidence,
        setup_status.changes(s, (date(2026, 10, 24), "earnings report")))


class Layout(unittest.TestCase):
    def test_sections_in_order(self):
        page = layout_page()
        order = ["THE BIG PICTURE", "CONCLUSION", "CHART LANE", "COMPANY LANE", "WHAT CHANGES THE PICTURE", "FULL DETAILS"]
        spots = [page.index(f"</span>{title}") for title in order]
        self.assertEqual(spots, sorted(spots))
        self.assertLess(page.index("FULL DETAILS"), page.index("BEST BULL POINTS"))
        self.assertLess(page.index("FULL DETAILS"), page.index("WHAT THE MONEY LOOKS LIKE"))

    def test_code_status_and_evidence_are_shown(self):
        page = layout_page()
        self.assertIn("<div class='v status-none'>No setup</div>", page)
        self.assertIn("<dt>Fail level now</dt><dd>a close below $", page)
        self.assertNotIn("Chart idea fails", page)
        self.assertIn("<div class='v ev-early'>Too early to tell</div>", page)
        self.assertIn("<dt>TOGETHER</dt><dd><b>Too early to tell.</b></dd>", page)
        self.assertIn("What would change this", page)
        self.assertIn("Range tightening", page)
        self.assertIn("scenarios, not targets", page)

    def test_no_raw_tags_and_dots_are_shown(self):
        page = layout_page()
        self.assertNotRegex(page, r"\[(R|EC|EA|C)\]")
        self.assertIn("class='kind reported'", page)
        self.assertIn("class='kind calc'", page)
        self.assertIn("Dots after numbers:", page)

    def test_no_rating_labels_in_the_visible_page(self):
        # From the big picture on: the header names the time frame ("Long-term hold"), not a rating.
        visible = layout_page().split("THE BIG PICTURE")[1].split("FULL DETAILS")[0]
        text = re.sub(r"<[^>]+>|data-tip='[^']*'", " ", visible).lower().replace("long-term hold", "")
        # "If you buy 10 shares" is the code's dollar example, not a rating.
        self.assertNotRegex(text, r"\b(buy|sell|hold|watch)\b(?! \d+ shares)")

    def test_together_says_the_label_once(self):
        # KO, MRNA and PLTR, Oct 25, 2026: the Link line repeated the label TOGETHER already shows.
        def together(link):
            points = [["Chart: No setup.", []], ["Company: Mixed news.", []], ["Link: " + link, []]]
            return report.big_picture_html(points, ("Too early to tell", ""), set())
        page = together("Too early to tell. Chart: setup failed. Company: mixed.")
        self.assertEqual(page.count("Too early to tell"), 1)
        self.assertNotIn("class='link'", page)
        page = together("Too early to tell. Chart: setup failed. Company: mixed. Too early because the next "
                        "earnings report on Nov 02 usually moves it 12.8% in a day.")
        self.assertEqual(page.count("Too early"), 1)
        self.assertIn("<div class='link'>The next ", page)
        page = together("Too early to tell, with no setup and a mixed company picture.")
        self.assertEqual(page.count("Too early to tell"), 1)
        self.assertIn("<div class='link'>With no setup", page)
        self.assertIn("The Oct 24 report", together("The Oct 24 report will likely decide which way this goes."))

    def test_link_that_repeats_the_label_is_sent_back(self):
        bad = DECISION.replace("Link: The Oct 24", "Link: Too early to tell. Chart: no setup. Company: mixed. The Oct 24")
        self.assertTrue(any("Link:" in p for p in judge.decision_problems(bad, "No setup")))

    def test_a_term_inside_a_url_does_not_cut_the_link(self):
        # AMZN, Oct 25, 2026: "revenue" in a CNBC link got a tooltip, so the link stopped at "-results-".
        url = "https://www.cnbctv18.com/market/amazon-q2-results-revenue-aws-growth-12345.htm"
        fact = {"topic": "Business", "fact": f"Sales rose 20% ({url}) and revenue grew.", "effect": "Lifts it.",
                "when": "Now.", "source": url}
        out = report.facts_html([fact], [])
        self.assertIn(f"href='{url}'", out)
        self.assertNotIn("</a><span class='tip'", out)
        self.assertEqual(out.count("data-tip='revenue:"), 1)   # the word after the link still gets its tooltip

    def test_bracket_explanations_go_where_a_tooltip_explains(self):
        out = wording.tip_html("The breakout [a move above a high price] failed. Analyst [a person who studies "
                               "companies] targets [the price they expect] lean high. Rate mood [how people feel] fell.")
        self.assertNotIn("[a move", out)
        self.assertNotIn("[a person", out)
        self.assertNotIn("[the price", out)
        self.assertIn(">Analyst targets</span>", out)
        self.assertIn("Rate mood [how people feel]", out)   # not a glossary term, so no tooltip and it stays
        self.assertEqual(wording.drop_explained("an analyst's [person who studies companies] opinion"),
                         "an analyst's opinion")
        self.assertIn("class='kind reported'", wording.tip_html("Sales rose 7% [R]."))

    def test_terms_are_explained_once_on_the_visible_page(self):
        visible = layout_page().split("FULL DETAILS")[0]
        self.assertEqual(visible.count("data-tip='support:"), 1)
        self.assertEqual(visible.count("data-tip='free cash flow:"), 1)

    def test_bad_format_falls_back_to_the_raw_text(self):
        page = report.visible_html("ABC", "Long-term hold", None, None, ("Too early to tell", ""), [], "just text", "")
        self.assertIn("<pre class='raw'>just text</pre>", page)
        self.assertIn("No chart data.", page)

    def test_page_uses_report_css(self):
        page = report.page_html("ABC", "Abc Inc", layout_page())
        self.assertIn("href='report.css'", page)
        self.assertIn(">Guy Biran</a>", page)
        self.assertIn("<summary class='bull'>BEST BULL POINTS</summary>", page)


if __name__ == "__main__":
    unittest.main()
