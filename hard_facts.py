"""Hard facts pulled from data by code, so Grok does not have to find them. No AI, no cost.

Each fact says how it could move the price and when, in plain words written from templates:
- next earnings report, what analysts expect from it, and how the stock moved after its last 4
- analyst rating and price target changes in the last ANALYST_DAYS days
- insider buying and selling on the open market in the last INSIDER_DAYS days
- short interest and how it changed from the month before
- the last 5 trading days: the stock vs its sector fund vs the S&P 500
- cash runway and share count growth (catalyst and long-term plans)
Anything Yahoo does not have is left out, never guessed.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from chart import candle_date
from data import money

ANALYST_DAYS = 30
INSIDER_DAYS = 90
NY = ZoneInfo("America/New_York")
YAHOO = "Yahoo Finance data, pulled by code"

SECTOR_FUNDS = {
    "Technology": "XLK", "Healthcare": "XLV", "Financial Services": "XLF", "Consumer Cyclical": "XLY",
    "Communication Services": "XLC", "Industrials": "XLI", "Energy": "XLE", "Utilities": "XLU",
    "Real Estate": "XLRE", "Basic Materials": "XLB", "Consumer Defensive": "XLP",
}
INDUSTRY_FUNDS = {"biotechnology": "XBI", "semiconductor": "SMH"}


def _fact(topic, fact, effect, when, day=None):
    return {"topic": topic, "fact": fact, "date": str(day) if day else "", "source": YAHOO,
            "effect": effect, "when": when, "check": "pulled from data by code"}


def _pct(x):
    return f"{x * 100:+.1f}%"


def next_earnings(info, today):
    """(date, "before the market opens" / "after the market closes" / "", is_estimate) or None."""
    now = datetime.now(timezone.utc).timestamp()
    stamps = [s for s in (info.get("earningsTimestampStart"), info.get("earningsTimestamp")) if s and s > now]
    if not stamps:
        return None
    when = datetime.fromtimestamp(min(stamps), NY)
    part = "before the market opens" if when.hour < 9 else "after the market closes" if when.hour >= 16 else ""
    return when.date(), part, bool(info.get("isEarningsDateEstimate"))


def earnings_moves(stock, daily, n=4):
    """[(report date, one-day move), ...] for the last n reports, newest first. A report after
    the close is judged on the next trading day, one before the open on the same day."""
    try:
        ed = stock.earnings_dates
    except Exception:
        return []
    if ed is None or ed.empty:
        return []
    days = [candle_date(c) for c in daily]
    out = []
    for ts, row in ed.sort_index(ascending=False).iterrows():
        if pd.isna(row.get("Reported EPS")):
            continue
        ts = ts.tz_convert(NY)
        target = ts.date() + timedelta(days=1) if ts.hour >= 16 else ts.date()
        i = next((k for k, d in enumerate(days) if d >= target), None)
        if i is None or i == 0:
            continue
        out.append((ts.date(), daily[i]["c"] / daily[i - 1]["c"] - 1))
        if len(out) == n:
            break
    return out


def earnings_fact(stock, info, daily, today):
    nxt = next_earnings(info, today)
    moves = earnings_moves(stock, daily)
    if not nxt and not moves:
        return None, None, moves
    parts = []
    if nxt:
        day, part, estimate = nxt
        parts.append(f"The next earnings report (the company's results for the last three months) is on "
                     f"{day:%b %d, %Y}" + (f", {part}" if part else "")
                     + (" (Yahoo marks this date as an estimate)." if estimate else " (date confirmed by the company)."))
        try:
            cal = stock.calendar or {}
        except Exception:
            cal = {}
        eps, rev = cal.get("Earnings Average"), cal.get("Revenue Average")
        if eps is not None:
            what = f"a profit of ${eps:.2f}" if eps >= 0 else f"a loss of ${-eps:.2f}"
            parts.append(f"Analysts (people at banks who study the company) expect {what} per share"
                         + (f" on about {money(rev)} of sales." if rev else "."))
    if moves:
        avg = sum(abs(m) for _, m in moves) / len(moves)
        parts.append("On the first trading day after each of its last " + str(len(moves)) + " reports, the stock moved "
                     + ", ".join(f"{_pct(m)} ({d:%b %Y})" for d, m in moves)
                     + f". That is an average move of {avg * 100:.1f}% either way.")
    effect = ("If the results or the company's own forecast beat what analysts expect, the stock often jumps. "
              "If they miss, it often drops. The expected numbers above are already part of today's price, "
              "so only a surprise moves it much.")
    if not nxt:
        when = "At the next report, date not yet known."
    elif nxt[1] == "before the market opens":
        when = f"{nxt[0]:%b %d, %Y}, the same day, since the report comes out before trading starts."
    elif nxt[1] == "after the market closes":
        when = f"The next trading day after {nxt[0]:%b %d, %Y}, since the report comes out after trading ends."
    else:
        when = f"{nxt[0]:%b %d, %Y} or the next trading day, depending on the time of the report."
    return _fact("Upcoming events", " ".join(parts), effect, when, nxt[0] if nxt else None), (nxt[0] if nxt else None), moves


def _grade_sentence(row):
    firm, to, frm, action = row.get("Firm"), row.get("ToGrade"), row.get("FromGrade"), row.get("Action")
    if action == "up":
        s = f"{firm} upgraded it (raised its rating) from {frm} to {to}"
    elif action == "down":
        s = f"{firm} downgraded it (lowered its rating) from {frm} to {to}"
    elif action == "init":
        s = f"{firm} started covering it with a {to} rating"
    else:
        s = f"{firm} kept its {to} rating"
    cur, prior, pta = row.get("currentPriceTarget") or 0, row.get("priorPriceTarget") or 0, row.get("priceTargetAction")
    if cur and prior and pta in ("Raises", "Lowers"):
        s += f" and {'raised' if pta == 'Raises' else 'lowered'} its price target from ${prior:,.0f} to ${cur:,.0f}"
    elif cur:
        s += f" with a price target of ${cur:,.0f}"
    return s


def analyst_fact(stock, today, price):
    try:
        ud = stock.upgrades_downgrades
    except Exception:
        return None
    if ud is None or ud.empty:
        return None
    recent = ud[ud.index >= pd.Timestamp(today - timedelta(days=ANALYST_DAYS))].sort_index(ascending=False)
    if recent.empty:
        return _fact("Analyst changes",
                     f"No analyst (a person at a bank who rates the stock) changed a rating or price target "
                     f"in the last {ANALYST_DAYS} days.",
                     "Nothing new from analysts is pushing the price right now.", "No effect expected.")
    lines = [f"{ts:%b %d}: {_grade_sentence(r)}." for ts, r in recent.iterrows()]
    ups = int((recent["Action"] == "up").sum())
    downs = int((recent["Action"] == "down").sum())
    raises = int((recent["priceTargetAction"] == "Raises").sum())
    lowers = int((recent["priceTargetAction"] == "Lowers").sum())
    targets = [t for t in recent["currentPriceTarget"] if t]
    lean = ups + raises - downs - lowers
    summary = (f"In the last {ANALYST_DAYS} days analysts (people at banks who rate the stock) made {len(recent)} changes: "
               f"{ups} upgrade(s), {downs} downgrade(s), {raises} price target (where an analyst thinks the price "
               f"will be in a year) raise(s) and {lowers} cut(s).")
    below = 0
    if targets and price:
        avg = sum(targets) / len(targets)
        below = sum(t < price for t in targets)
        summary += f" The average of these new targets is ${avg:,.0f}, {(avg / price - 1) * 100:+.0f}% from today's price."
    effect = ("Upgrades and higher targets can lift the price as other investors follow them. "
              "Downgrades and lower targets can push it down. "
              + ("By count these changes lean positive." if lean > 0 else "By count these changes lean negative."
                 if lean < 0 else "By count these changes are mixed."))
    if targets and below == len(targets):
        effect += (" But every new target is below today's price, so these analysts think the stock has already "
                   "risen past what they think it is worth. That can cap rises.")
    elif targets and below == 0:
        effect += " Every new target is above today's price."
    effect += " Targets are opinions and are often wrong."
    when = ("Mostly on the day each change came out, so the dated changes above have likely already moved the price. "
            "A new change can move it again on the day it is published.")
    return _fact("Analyst changes", summary + " " + " ".join(lines), effect, when, recent.index[0].date())


def insider_fact(stock, today):
    try:
        it = stock.insider_transactions
    except Exception:
        return None
    if it is None or it.empty or "Start Date" not in it:
        return None
    it = it[pd.to_datetime(it["Start Date"]) >= pd.Timestamp(today - timedelta(days=INSIDER_DAYS))]
    text = it["Text"].fillna("")
    buys, sells = it[text.str.startswith("Purchase")], it[text.str.startswith("Sale")]

    def total(df):
        return f"{int(df['Shares'].sum()):,} shares worth about {money(float(df['Value'].fillna(0).sum()))}"

    def who(df):
        names = df.groupby("Insider")["Value"].sum().sort_values(ascending=False).index[:3]
        return ", ".join(f"{n.title()} ({df[df['Insider'] == n]['Position'].iloc[0]})" for n in names)

    parts = [f"Over the last {INSIDER_DAYS} days, insiders (top managers and board members, who must report "
             f"their trades) "]
    if buys.empty and sells.empty:
        parts[0] += "did not buy or sell any shares on the open market."
    else:
        parts[0] += (f"bought {total(buys)} on the open market" if not buys.empty else "bought no shares on the open market")
        parts[0] += f" and sold {total(sells)}." if not sells.empty else " and sold none."
        if not sells.empty:
            parts.append(f"The biggest sellers: {who(sells)}.")
        if not buys.empty:
            parts.append(f"The buyers: {who(buys)}.")
    if not buys.empty:
        effect = ("Insiders buying with their own money is often read as a sign they expect the price to rise, "
                  "which can draw in other buyers.")
    elif not sells.empty:
        effect = ("Insider selling usually moves the price very little. Managers often sell on a plan set months "
                  "ahead, for taxes or to spread out their savings. Heavy selling just before big news would be a warning.")
    else:
        effect = "No insider trades, so nothing here is pushing the price."
    return _fact("Insider trades", " ".join(parts), effect, "Slowly, over weeks, if at all.")


def short_fact(info, next_day):
    pct, shares, prior, ratio = (info.get(k) for k in ("shortPercentOfFloat", "sharesShort", "sharesShortPriorMonth", "shortRatio"))
    if pct is None:
        return None
    stamp = info.get("dateShortInterest")
    as_of = datetime.fromtimestamp(stamp, timezone.utc).date() if stamp else None
    fact = (f"Short interest (shares that traders borrowed and sold, betting the price will fall) is {pct * 100:.1f}% "
            f"of the float (the shares free to trade)" + (f", as of {as_of:%b %d}" if as_of else "") + ".")
    if shares and prior:
        fact += f" That is {(shares / prior - 1) * 100:+.0f}% from a month earlier."
    if ratio:
        fact += (f" Days to cover (how many days of normal trading it would take them all to buy back) is about {ratio:.1f}.")
    if pct >= 0.10:
        effect = ("This is high. If good news lifts the price, these traders may rush to buy back shares to limit "
                  "their losses, which can push the price up even faster (a short squeeze). It also shows many "
                  "traders expect bad news.")
    elif pct >= 0.05:
        effect = ("This is moderate. Good news could make some of these traders buy back, adding to a rise, but "
                  "probably not a big squeeze.")
    else:
        effect = "This is low, so traders betting against it are unlikely to push the price much either way."
    if shares and prior and shares < prior * 0.9:
        effect += " Fewer shares are being bet against than a month ago, so some of that buying back already happened."
    when = (f"Most likely around the next big news, {next_day:%b %d, %Y}." if next_day
            else "Most likely around the next big news.")
    return _fact("Short interest", fact, effect, when, as_of)


def sector_fact(ticker, sector, industry):
    fund = next((f for k, f in INDUSTRY_FUNDS.items() if k in (industry or "").lower()), None) or SECTOR_FUNDS.get(sector)
    names = [ticker, "SPY"] + ([fund] if fund else [])
    try:
        closes = yf.download(names, period="1mo", auto_adjust=True, progress=False)["Close"].dropna()
    except Exception:
        return None
    if len(closes) < 6:
        return None
    move = {n: closes[n].iloc[-1] / closes[n].iloc[-6] - 1 for n in names}
    fact = (f"Over the last 5 trading days {ticker} moved {_pct(move[ticker])}"
            + (f", its sector fund (a fund holding many companies in the same business) {fund} moved {_pct(move[fund])}"
               if fund else "")
            + f", and the S&P 500 (500 large U.S. companies, a stand-in for the whole market) moved {_pct(move['SPY'])}.")
    base = move[fund] if fund else move["SPY"]
    gap = move[ticker] - base
    effect = (("It has been doing better than its peers, so buyers are favoring it right now. "
               if gap > 0.02 else "It has been doing worse than its peers, so something specific to it is weighing on it. "
               if gap < -0.02 else "It has moved about in line with its peers, so the group mood is driving it. ")
              + "When the whole group moves, this stock usually moves with it.")
    return _fact("Sector", fact, effect, "Day to day over the coming weeks, as the group's mood shifts.",
                 closes.index[-1].date())


def cash_facts(stock, info):
    """Cash runway and share count growth. Returns a list of facts."""
    out = []
    try:
        cf = stock.quarterly_cashflow
        ocf = cf.loc["Operating Cash Flow"].sort_index(ascending=False).dropna().iloc[:4]
        burn = (cf.loc["Operating Cash Flow"] + cf.loc["Capital Expenditure"]).sort_index(ascending=False).dropna().iloc[:4]
    except Exception:
        ocf = burn = None
    cash = info.get("totalCash")
    if burn is not None and len(burn) == 4 and len(ocf) == 4 and cash:
        year, recent = float(burn.sum()), float(burn.iloc[:2].sum())
        if year < 0 and float(ocf.sum()) > 0:
            # Running the business brings in cash; the gap is spending on equipment and buildings.
            fact = (f"The company holds {money(cash)} in cash and short-term investments. Over the last 4 quarters "
                    f"running the business brought in {money(float(ocf.sum()))}, but it spent "
                    f"{money(float(ocf.sum()) - year)} on things like data centers and equipment. So its free cash "
                    f"flow (cash left after that spending) was {money(year)}.")
            effect = ("It is not short of cash, so it is unlikely to sell new shares. Investors watch whether the heavy "
                      "spending pays off: signs that it does tend to lift the price, and signs that it does not "
                      "tend to pull it down.")
            when = "Mostly at each earnings report, when the company updates its spending plans."
        elif year < 0:
            fact = (f"The company holds {money(cash)} in cash and short-term investments. Over the last 4 quarters it "
                    f"spent {money(-year)} more than it brought in (its cash burn), about {money(-year / 4)} a quarter. "
                    f"At that pace the cash lasts about {cash / (-year / 12):.0f} months (its cash runway).")
            if recent < 0:
                fact += f" At the pace of the last 2 quarters alone, about {cash / (-recent / 6):.0f} months."
            months = cash / (-min(year / 12, recent / 6 if recent < 0 else year / 12))
            effect = ("A company that runs low on cash usually sells new shares, which shrinks each owner's slice "
                      "(dilution) and often drops the price that day. "
                      + ("Even at the faster pace it has over 3 years of cash, so that risk is not close." if months > 36
                         else f"At the faster pace it has about {months:.0f} months of cash, so selling new shares in the "
                              "next year or two is possible." if months > 18
                         else f"At the faster pace it has only about {months:.0f} months of cash, so selling new "
                              "shares is a real risk."))
            when = ("Companies often sell shares right after good news, when the price is high, or about a year "
                    "before the cash would run out.")
        else:
            fact = (f"The company holds {money(cash)} in cash and short-term investments, and over the last 4 quarters "
                    f"it brought in {money(year)} more cash than it spent.")
            effect = "It does not need to raise money to keep going, which removes one common reason for a price drop."
            when = "Ongoing. This changes only if the business starts losing cash."
        out.append(_fact("Cash and dilution", fact, effect, when, burn.index[0].date()))
    try:
        bs = stock.quarterly_balance_sheet
        shares = bs.loc["Ordinary Shares Number"].sort_index(ascending=False).dropna()
    except Exception:
        shares = None
    if shares is not None and len(shares) >= 5:
        change = shares.iloc[0] / shares.iloc[4] - 1
        fact = (f"The number of shares went from {shares.iloc[4] / 1e6:,.0f} million to {shares.iloc[0] / 1e6:,.0f} "
                f"million over the last year ({_pct(change)}).")
        if change > 0.02:
            effect = ("More shares means each one owns a smaller piece of the company (dilution). "
                      "That slowly holds back the price even when the business grows.")
        elif change < -0.01:
            effect = ("Fewer shares means each one owns a bigger piece, usually because the company bought back its "
                      "own shares. That slowly supports the price.")
        else:
            effect = "The share count barely changed, so this is not moving the price."
        out.append(_fact("Cash and dilution", fact, effect, "Slowly, over months and years.", shares.index[0].date()))
    return out


def get_hard_facts(ticker, daily, today, plan, catalyst=None):
    """Returns {"facts": [...], "next_date": (date, what) or None, "text": block for the AI,
    "earnings": (average one-day move, reports counted, next report date) or None, "target": average analyst target}."""
    stock = yf.Ticker(ticker)
    try:
        info = stock.info or {}
    except Exception:
        info = {}
    price = daily[-1]["c"] if daily else info.get("currentPrice")
    facts = []
    earn, earn_day, moves = earnings_fact(stock, info, daily or [], today)
    dates = [(earn_day, "the next earnings report")] if earn_day else []
    if catalyst:
        dates.append(catalyst)
    dates = sorted(d for d in dates if d[0] >= today)
    next_day = dates[0] if dates else None

    for f in (earn, analyst_fact(stock, today, price), insider_fact(stock, today),
              short_fact(info, next_day[0] if next_day else None)):
        if f:
            facts.append(f)
    if plan == "technical":
        s = sector_fact(ticker, info.get("sector"), info.get("industry"))
        if s:
            facts.append(s)
    else:
        facts += cash_facts(stock, info)
    earnings = (sum(abs(m) for _, m in moves) / len(moves), len(moves), earn_day) if moves else None
    return {"facts": facts, "next_date": next_day, "text": facts_text(facts), "earnings": earnings,
            "target": info.get("targetMeanPrice")}


def facts_text(facts):
    return "\n\n".join(
        f"TOPIC: {f['topic']}\nFACT: {f['fact']}\nDATE: {f['date'] or 'n/a'}\nSOURCE: {f['source']}\n"
        f"PRICE EFFECT: {f['effect']}\nWHEN: {f['when']}"
        + (f"\nALREADY IN THE PRICE: {f['priced_in']}" if f.get("priced_in") else "")
        for f in facts)
