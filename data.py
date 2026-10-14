import re
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

HOW_TO_READ = """HOW TO READ THESE NUMBERS:
- Forward P/E LOWER than trailing P/E means analysts expect earnings to RISE.
- Forward P/E HIGHER than trailing P/E means analysts expect earnings to FALL, or last year had a one-time gain.
- Revenue growth is a growth rate. It does not tell you whether growth is speeding up or slowing down.
- FCF yield is free cash flow divided by market cap. Under 1% is very low. Over 5% is high.
- Net cash is cash minus debt. Negative means the company owes more than it holds.
- Analyst price targets are opinions about where the price may be in about 12 months. They are often wrong and tend to lean optimistic.
- Short interest is the share of the stock that traders have bet against. Above 10% of float is high.
- Beta above 1 means the stock usually moves more than the overall market. Below 1 means less."""


def money(x):
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    x = abs(x)
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if x >= size:
            return f"{sign}${x / size:.2f}{unit}"
    return f"{sign}${x:,.2f}"


def pct(x):
    return "n/a" if x is None else f"{x * 100:.1f}%"


def num(x):
    return "n/a" if x is None else f"{x:.1f}"


def clean_name(name):
    # Yahoo writes some names as "Coca-Cola Company (The)". Turn that into "The Coca-Cola Company".
    if not name:
        return name
    m = re.match(r"^(.*?),?\s*\(The\)$", name.strip())
    return f"The {m.group(1)}" if m else name


def trailing_fcf(stock):
    # Operating cash flow minus capital spending over the last 4 reported quarters.
    # Yahoo's own freeCashflow field is levered free cash flow, which does not match company reports.
    try:
        cf = stock.quarterly_cashflow
        ocf = cf.loc["Operating Cash Flow"].sort_index(ascending=False).iloc[:4]
        capex = cf.loc["Capital Expenditure"].sort_index(ascending=False).iloc[:4]
    except Exception:
        return None
    if len(ocf) < 4 or len(capex) < 4 or ocf.isna().any() or capex.isna().any():
        return None
    # Yahoo stores capital spending as a negative number.
    return float(ocf.sum() - capex.abs().sum())


def get_facts(ticker):
    stock = yf.Ticker(ticker)
    info = stock.info
    if not info or info.get("marketCap") is None:
        raise ValueError(f"No data found for {ticker}. Check the ticker and try again.")

    cap = info.get("marketCap")
    fcf = trailing_fcf(stock)
    cash = info.get("totalCash")
    debt = info.get("totalDebt")
    price = info.get("currentPrice")
    target = info.get("targetMeanPrice")
    fcf_yield = None if fcf is None or not cap else fcf / cap
    net_cash = None if cash is None or debt is None else cash - debt
    target_gap = None if not price or not target else target / price - 1
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    name = clean_name(info.get("shortName"))

    lines = [
        f"Data pulled on {today} from Yahoo Finance",
        f"Company: {name} ({ticker})",
        f"Sector: {info.get('sector')}",
        f"Industry: {info.get('industry')}",
        f"Price: {money(price)}",
        f"52-week range: {money(info.get('fiftyTwoWeekLow'))} to {money(info.get('fiftyTwoWeekHigh'))}",
        f"Market cap: {money(cap)}",
        f"Revenue (last 12 months): {money(info.get('totalRevenue'))}",
        f"Revenue growth (latest quarter vs a year ago): {pct(info.get('revenueGrowth'))}",
        f"Gross margin: {pct(info.get('grossMargins'))}",
        f"Profit margin: {pct(info.get('profitMargins'))}",
        f"Free cash flow (operating cash flow minus capital spending, last 4 quarters): {money(fcf)}",
        f"FCF yield: {pct(fcf_yield)}",
        f"Cash: {money(cash)}",
        f"Debt: {money(debt)}",
        f"Net cash: {money(net_cash)}",
        f"P/E (trailing): {num(info.get('trailingPE'))}",
        f"P/E (forward): {num(info.get('forwardPE'))}",
        f"Price to sales: {num(info.get('priceToSalesTrailing12Months'))}",
        f"Analyst average price target: {money(target)} ({pct(target_gap)} vs today's price)",
        f"Analyst target range: {money(info.get('targetLowPrice'))} to {money(info.get('targetHighPrice'))}",
        f"Number of analysts: {info.get('numberOfAnalystOpinions') or 'n/a'}",
        f"Analyst consensus label: {info.get('recommendationKey') or 'n/a'}",
        f"Short interest: {pct(info.get('shortPercentOfFloat'))} of float",
        f"Beta: {num(info.get('beta'))}",
    ]
    return "\n".join(lines), name, today
