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
        f"Sector (its broad line of business): {info.get('sector')}",
        f"Industry: {info.get('industry')}",
        f"Price: {money(price)}",
        f"52-week range (lowest and highest price in the last year): {money(info.get('fiftyTwoWeekLow'))} to {money(info.get('fiftyTwoWeekHigh'))}",
        f"Market cap (the total value of all its shares): {money(cap)}",
        f"Revenue (all money customers paid, last 12 months): {money(info.get('totalRevenue'))}",
        f"Revenue growth (latest quarter vs a year ago): {pct(info.get('revenueGrowth'))}",
        f"Gross margin (share of sales left after the direct cost of the product): {pct(info.get('grossMargins'))}",
        f"Profit margin (share of sales kept as profit after all costs): {pct(info.get('profitMargins'))}",
        f"Free cash flow (cash left after running the business and buying equipment, last 4 quarters): {money(fcf)}",
        f"FCF yield (free cash flow divided by market cap; over 5% is high): {pct(fcf_yield)}",
        f"Cash: {money(cash)}",
        f"Debt: {money(debt)}",
        f"Net cash (cash minus debt): {money(net_cash)}",
        f"P/E (trailing) (price divided by the last 12 months of profit per share; lower is cheaper): {num(info.get('trailingPE'))}",
        f"P/E (forward) (price divided by the profit per share expected over the next 12 months): {num(info.get('forwardPE'))}",
        f"Price to sales (market cap divided by a year of sales): {num(info.get('priceToSalesTrailing12Months'))}",
        f"Average price target of analysts (people at banks who study the company; a target is where they think the price will be in a year, an opinion): {money(target)} ({pct(target_gap)} vs today's price)",
        f"Analyst target range: {money(info.get('targetLowPrice'))} to {money(info.get('targetHighPrice'))}",
        f"Number of analysts: {info.get('numberOfAnalystOpinions') or 'n/a'}",
        f"Analyst consensus (the average rating) label: {info.get('recommendationKey') or 'n/a'}",
        f"Short interest (shares borrowed and sold by traders betting on a fall): {pct(info.get('shortPercentOfFloat'))} of float (the shares free to trade)",
        f"Beta (how much it swings compared with the whole market; above 1 is more): {num(info.get('beta'))}",
    ]
    return "\n".join(lines), name, today
