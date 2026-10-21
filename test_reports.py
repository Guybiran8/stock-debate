"""Run with: python -m unittest test_reports -v
Tests the report wording, money templates, hard facts and research parsing with made-up data,
so it needs no network and spends nothing."""
import os
import unittest
from datetime import date, datetime, timedelta, timezone

import pandas as pd

for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "POLYGON_API_KEY"):
    os.environ.setdefault(key, "test-placeholder")

import hard_facts  # noqa: E402
import judge  # noqa: E402
import report  # noqa: E402
import research  # noqa: E402
import scenarios  # noqa: E402
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
        s = scenarios.main_sentence(c)
        self.assertIn("If you buy 10 shares at", s)
        self.assertIn("next resistance", s)
        self.assertIn("nearest support", s)
        up = (c["resistances"][0][0] - c["price"]) * 10
        self.assertIn(f"+${up:,.2f}", s)

    def test_at_highs_and_no_support_are_labeled(self):
        s = scenarios.main_sentence(fake_chart(at_highs=True, supports=False))
        self.assertIn("a guess of two normal days", s)
        self.assertIn("15% drop", s)

    def test_support_closer_than_one_day_counts_as_one_day(self):
        c = fake_chart()
        c["supports"] = [(c["price"] - 0.5, "support level, touched 2 times")]
        s = scenarios.main_sentence(c)
        self.assertIn(f"-${c['atr'] * 10:,.2f} if it falls to ${c['price'] - c['atr']:,.2f}", s)
        self.assertIn("one normal day's move down", s)

    def test_20_day_average_and_swing_low_are_not_called_support(self):
        c = fake_chart()
        c["supports"] = [(c["price"] * 0.9, "20-day average")]
        s = scenarios.main_sentence(c)
        self.assertIn("(the 20-day average, the average closing price over the last 20 trading days)", s)
        self.assertNotIn("buyers stepped in", s)
        c["supports"] = [(c["price"] - 0.5, "most recent swing low (a low point where the price turned back up), Oct 02")]
        s = scenarios.main_sentence(c)
        self.assertIn("one normal day's move down; the most recent swing low, a low point where the price turned "
                      f"back up, is at ${c['price'] - 0.5:,.2f}, closer than that", s)

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
        return [f for f in out if f]

    def test_every_fact_says_how_and_when(self):
        facts = self.facts()
        self.assertEqual(len(facts), 6)
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
        self.assertEqual(report._source_html(f), "Yahoo Finance · pulled Oct 16, 2026")
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


DECISION = """IN PLAIN ENGLISH
The company makes vaccines. Its stock rose fast this month. Buyers are betting a cancer drug test goes well.
You make money if the test is good and the price keeps rising. You lose money if the test fails.
[MONEY] The price to watch is $200. If it falls below that, buyers may be giving up.
The next date that matters is Oct 24, when the test results come out.

WHERE THE EVIDENCE LEANS
Mixed: the test could go either way.

THE DECISION
- The bet: the test goes well.
"""


class Report(unittest.TestCase):
    def test_plain_english_checks(self):
        self.assertEqual(judge.plain_problems(DECISION), [])
        bad = DECISION.replace("[MONEY] ", "").replace("Oct 24", "soon")
        self.assertEqual(len(judge.plain_problems(bad)), 2)
        self.assertIn("missing", judge.plain_problems("WHERE THE EVIDENCE LEANS\nMixed: x")[0])

    def test_money_sentence_replaces_the_token(self):
        html = report.brief_to_html(DECISION, "", "If you buy 10 shares at $210.", ["line one"], "")
        self.assertNotIn("[MONEY]", html)
        self.assertIn("<b>If you buy 10 shares at $210.</b>", html)
        self.assertLess(html.index("IN PLAIN ENGLISH"), html.index("WHAT THE MONEY LOOKS LIKE"))
        self.assertLess(html.index("WHAT THE MONEY LOOKS LIKE"), html.index("Mixed:"))

    def test_facts_html_groups_by_topic_with_how_and_when(self):
        facts, _ = research.parse_facts(RAW, research.PLANS["catalyst"]["topics"])
        out = report.facts_html(facts, research.PLANS["catalyst"]["topics"])
        self.assertLess(out.index("Trial data"), out.index("Cash lasts"))
        self.assertIn("How it could move the price:", out)
        self.assertIn("href='https://example.com/a'", out)


if __name__ == "__main__":
    unittest.main()
