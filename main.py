import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

import timing

from chart import CHART_GUIDE, get_chart
from data import HOW_TO_READ, get_facts
from debate import run_debate
from hard_facts import get_hard_facts
import setup_status
from judge import company_lean, judge
from report import build_page, page_html
from research import PLANS, facts_text, research
from scenarios import main_sentence, money_lines
from timing import step

REPORT_FILE = Path(__file__).parent / "report.html"
REPORTS_DIR = Path(__file__).parent / "reports"


def timed(label, fn, *args):
    with step(label):
        return fn(*args)


def stock_plan(ticker, horizon, today):
    """Picks the research plan: long, or for a short-term trade the watchlist's own tag
    (catalyst-driven or technical-driven). Needs only Yahoo's profile and the last few closes,
    so the research can start before the chart and hard facts are done.
    Returns (plan, verified catalyst text for Grok, (date, what) for the next-date line, name)."""
    import watchlist as w
    profile = w._profile(ticker)
    c = w.load_catalysts(today=today).get(ticker)
    catalyst_text, catalyst = (f"{c['date']} {c['event']} ({c['source']})", (c["date"], c["event"])) if c else (None, None)
    if horizon == "long":
        return "long", catalyst_text, catalyst, profile["name"]
    closes = list(yf.Ticker(ticker).history(period="10d")["Close"])
    moves = [abs(b / a - 1) for a, b in zip(closes[-6:], closes[-5:])]
    tag, _, _ = w.tag_stock({"max_move_5d": max(moves) if moves else 0}, profile, today)
    return ("catalyst" if tag == "catalyst-driven" else "technical"), catalyst_text, catalyst, profile["name"]


def _chart_and_hard_facts(ticker, today, plan, catalyst):
    # Hard facts need the chart's daily prices, so these two run one after the other in one thread.
    try:
        with step("chart data"):
            chart = get_chart(ticker, today)
    except Exception as e:
        print(f"    Chart skipped ({e})")
        chart = None
    with step("hard facts"):
        hard = get_hard_facts(ticker, chart["daily"] if chart else [], today, plan, catalyst)
    return chart, hard


def analyze(ticker, horizon, open_browser=True, fresh_research=False):
    label = "Short-term trade" if horizon == "short" else "Long-term hold"
    print(f"Working on {ticker} ({label}).\n")
    timing.reset()

    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    with step("pick research plan"):
        plan, catalyst_text, catalyst, name = stock_plan(ticker, horizon, today)
    print(f"1/5 Research plan: {plan}")

    # The numbers, the chart and hard facts, and the Grok research do not depend on each other.
    with ThreadPoolExecutor(3) as pool:
        research_job = pool.submit(research, ticker, name, today, horizon, plan, catalyst_text, fresh_research)
        facts_job = pool.submit(timed, "numbers (Yahoo)", get_facts, ticker)
        chart, hard = pool.submit(_chart_and_hard_facts, ticker, today, plan, catalyst).result()
        facts, name, today = facts_job.result()
        found = research_job.result()
    # The setup status is decided here, by code, before any AI sees the data.
    setup = setup_status.evaluate(chart["daily"]) if chart else None
    chart_text = (chart["text"] + "\n\n" + setup_status.text(setup)) if chart else "No chart data is available for this stock."
    nxt = hard["next_date"]
    # Only an earnings report has a known average move; other events never force "too early".
    event_move = hard["earnings"][0] if hard["earnings"] and nxt and nxt[0] == hard["earnings"][2] else None
    next_line = f"NEXT DATE THAT MATTERS: {nxt[0]:%b %d, %Y}, {nxt[1]}." if nxt else "NEXT DATE THAT MATTERS: none known."
    research_text = facts_text(hard["facts"] + found["facts"]) + (
        "\n\nWHAT TRADERS ARE SAYING ON X (opinions, not facts):\n" + found["x"] if found["x"] else "")
    print(f"2/5 Numbers, chart, {len(hard['facts'])} hard facts and {len(found['facts'])} research facts "
          f"({timing.total():.0f}s)")

    data = (
        f"DATA:\n{facts}\n\n"
        f"PRICE CHART DATA (part of DATA):\n{chart_text}\n\n"
        f"{next_line}\n\n"
        f"{HOW_TO_READ}\n\n{CHART_GUIDE}\n\n"
        f"RESEARCH (each fact says how and when it could move the price):\n{research_text}"
    )
    d = run_debate(data, horizon)
    print(f"3/5 Debate done ({timing.total():.0f}s)")

    status = setup["status"] if setup else setup_status.NONE
    table = setup_status.evidence_table(setup, nxt, event_move, today) if setup else "- Any lean: Too early to tell."
    decision_brief, details_brief, _, _ = judge(data, d, horizon, status,
                                                setup_status.text(setup) if setup else "No chart data.", table)
    print(f"4/5 Judges done ({timing.total():.0f}s)")

    # Every dollar sentence is written by code from templates (scenarios.py).
    if chart:
        money_sentence = main_sentence(chart)
        lines = money_lines(chart, horizon, hard["target"], hard["earnings"])
    else:
        money_sentence, lines = "No chart data, so no dollar example.", []

    lean = company_lean(decision_brief)
    if setup:
        evidence = setup_status.evidence(setup, lean, nxt, event_move, today)
        changes = setup_status.changes(setup, nxt)
    else:
        evidence, changes = (setup_status.TOO_EARLY, f"No chart data. Company: {lean.lower()}."), []
    page = build_page(ticker, name, today, label, facts, chart_text, chart, found, d, decision_brief, details_brief,
                      money_sentence, lines, hard["facts"], PLANS[plan]["topics"], research_text,
                      setup, evidence, changes)

    full_html = page_html(ticker, name, page)
    REPORT_FILE.write_text(full_html, encoding="utf-8")

    # Also keep a copy of every run, so older reports are not lost.
    REPORTS_DIR.mkdir(exist_ok=True)
    saved = REPORTS_DIR / f"{ticker}-{horizon}-{today}.html"
    saved.write_text(full_html, encoding="utf-8")

    print(f"5/5 Saved {saved}")
    timing.print_table()

    if open_browser:
        webbrowser.open(REPORT_FILE.as_uri())
    return saved


if __name__ == "__main__":
    ticker = input("Ticker: ").strip().upper()
    answer = input("Short-term trade or long-term hold? Type short or long: ").strip().lower()
    analyze(ticker, "short" if answer.startswith("s") else "long")
