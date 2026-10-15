import html
import time
import webbrowser
from pathlib import Path

from chart import CHART_GUIDE, get_chart
from data import HOW_TO_READ, get_facts
from debate import run_debate
from judge import judge
from report import build_page
from research import research

REPORT_FILE = Path(__file__).parent / "report.html"
REPORTS_DIR = Path(__file__).parent / "reports"


def analyze(ticker, horizon, open_browser=True):
    label = "Short-term trade" if horizon == "short" else "Long-term hold"
    print(f"Working on {ticker} ({label}). This takes a minute or two.\n")

    t0 = time.perf_counter()
    facts, name, today = get_facts(ticker)
    print(f"1/6 Got the numbers ({time.perf_counter() - t0:.1f}s)")

    t0 = time.perf_counter()
    try:
        chart = get_chart(ticker, today)
        chart_text = chart["text"]
        print(f"2/6 Got the chart ({time.perf_counter() - t0:.1f}s)")
    except Exception as e:
        chart = None
        chart_text = "No chart data is available for this stock."
        print(f"2/6 Chart skipped ({e}) ({time.perf_counter() - t0:.1f}s)")

    t0 = time.perf_counter()
    findings = research(ticker, name, today, horizon)
    print(f"3/6 Research done ({time.perf_counter() - t0:.1f}s)")

    data = (
        f"DATA:\n{facts}\n\n"
        f"PRICE CHART DATA (part of DATA):\n{chart_text}\n\n"
        f"{HOW_TO_READ}\n\n{CHART_GUIDE}\n\n"
        f"RESEARCH:\n{findings}"
    )
    t0 = time.perf_counter()
    d = run_debate(data, horizon)
    print(f"4/6 Debate done ({time.perf_counter() - t0:.1f}s)")

    decision_brief, details_brief, decision_s, details_s = judge(data, d, horizon)
    print(f"5/6 Decision judge done ({decision_s:.1f}s)")
    print(f"6/6 Details judge done ({details_s:.1f}s)\n")

    page = build_page(ticker, name, today, label, facts, chart_text, chart, findings, d, decision_brief, details_brief)

    full_html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(str(name))} ({html.escape(ticker)})</title></head><body>"
        + page
        + "</body></html>"
    )
    REPORT_FILE.write_text(full_html, encoding="utf-8")

    # Also keep a copy of every run, so older reports are not lost.
    REPORTS_DIR.mkdir(exist_ok=True)
    saved = REPORTS_DIR / f"{ticker}-{horizon}-{today}.html"
    saved.write_text(full_html, encoding="utf-8")

    if open_browser:
        webbrowser.open(REPORT_FILE.as_uri())
    return saved


if __name__ == "__main__":
    ticker = input("Ticker: ").strip().upper()
    answer = input("Short-term trade or long-term hold? Type short or long: ").strip().lower()
    analyze(ticker, "short" if answer.startswith("s") else "long")
