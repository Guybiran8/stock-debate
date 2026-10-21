"""Keeps every report readable by someone who has never bought a stock. Pure code, no AI.

- TERMS: financial terms with a plain explanation. The same list drives the check below and
  the hover tooltips in the report.
- problems(text): what is wrong with a piece of AI text. A term used before it is explained,
  a reading level above about 9th grade, or a sentence about dollars won or lost
  (only code writes those, see scenarios.py).
- fix(text, rewrite, label): runs the check and, if anything is wrong, sends the text back for
  one rewrite. The rewrite is checked again and kept even if it still has problems, which are
  printed and returned.
- tip_html(text): escapes text for HTML and adds a hover tooltip to the first use of each term.
"""
import html
import re

from timing import step

MAX_GRADE = 9.5   # "roughly 9th grade"

# (key, regex, plain explanation, check). Longer terms come before shorter ones that they
# contain (free cash flow before cash flow). check=False terms only get a tooltip, because the
# report's own labels use them (Bull, Bear, "Leans bullish").
TERMS = [
    ("forward P/E", r"forward P/?E( ratio)?", "The share price divided by the profit per share analysts expect over the next 12 months. Lower means cheaper.", True),
    ("trailing P/E", r"trailing P/?E( ratio)?", "The share price divided by the profit per share over the last 12 months. Lower means cheaper.", True),
    ("P/E", r"\bP/E( ratio)?\b|price[- ]to[- ]earnings( ratio)?", "Price-to-earnings: the share price divided by the yearly profit per share. It shows how many years of profit you pay for. Lower means cheaper.", True),
    ("price to sales", r"price[- ]to[- ]sales( ratio)?|\bP/S\b", "The company's total value divided by a year of sales. Lower means cheaper.", True),
    ("EPS", r"\bEPS\b|earnings per share", "Earnings per share: the company's profit divided by the number of shares.", True),
    ("FCF yield", r"\bFCF yield\b|free cash flow yield", "Free cash flow divided by the company's total value. Like an interest rate the business earns for owners. Over 5% is high, under 1% is low.", True),
    ("free cash flow", r"free cash flow|\bFCF\b", "Cash the business has left after paying its bills and buying equipment and buildings.", True),
    ("operating cash flow", r"operating cash flow", "Cash that came in from running the business, before spending on equipment and buildings.", True),
    ("capital spending", r"capital spending|capital expenditures?|\bcapex\b", "Money spent on long-lasting things like data centers, factories and equipment.", True),
    ("cash runway", r"cash runway|\brunway\b", "How many months the company can keep going on the cash it has before it needs more.", True),
    ("cash burn", r"cash burn|burn rate|burning cash", "How much more cash goes out than comes in, usually per quarter.", True),
    ("net cash", r"net cash|net debt", "Cash minus debt. Negative means the company owes more than it holds.", True),
    ("market cap", r"market cap(italization)?", "The total value of all the company's shares: share price times number of shares.", True),
    ("gross margin", r"gross margins?", "The share of each sales dollar left after paying the direct cost of making the product.", True),
    ("operating margin", r"operating margins?", "The share of each sales dollar left after paying for running the business, before interest and taxes.", True),
    ("profit margin", r"(profit|net) margins?", "The share of each sales dollar the company keeps as profit after all costs.", True),
    ("guidance", r"\bguidance\b|\boutlook\b(?= for)", "The company's own forecast of its sales or profit for the coming quarter or year.", True),
    ("earnings report", r"earnings (report|release|call|results|date|day)s?|quarterly (results|report)", "The company's results for the last three months: sales, profit and its forecast. Stocks often jump or drop the next day.", True),
    ("earnings", r"\bearnings\b", "The company's profit. 'Earnings season' is when companies report their quarterly results.", True),
    ("revenue", r"\brevenues?\b", "All the money customers paid the company, before any costs. Also called sales.", True),
    ("valuation", r"\bvaluations?\b", "How expensive the stock is compared with what the business earns or sells.", True),
    ("price target", r"price targets?|target prices?|analyst targets?", "Where an analyst thinks the share price will be in about 12 months. An opinion, often wrong, and usually on the hopeful side.", True),
    ("upgrade", r"\bupgrade[sd]?\b", "An analyst raised their rating, for example from Hold to Buy.", True),
    ("downgrade", r"\bdowngrade[sd]?\b", "An analyst lowered their rating, for example from Buy to Hold.", True),
    ("consensus", r"\bconsensus\b", "The average of what the analysts who follow the stock expect.", True),
    ("analyst", r"\banalysts?\b", "A person at a bank or research firm who studies a company and publishes a rating and a price target.", True),
    ("short interest", r"short interest", "The share of the stock that traders have borrowed and sold, betting the price will fall. Above 10% is high.", True),
    ("days to cover", r"days to cover|short ratio", "How many days of normal trading it would take for short sellers to buy back all the shares they sold.", True),
    ("short squeeze", r"short squeeze", "When the price rises and short sellers rush to buy back shares to limit losses, which pushes the price up even faster.", True),
    ("short seller", r"short sell(er|ers|ing)|\bshorts\b|\bshorted\b|\bshorting\b", "A trader who borrows shares and sells them, hoping to buy them back cheaper later.", True),
    ("float", r"\bfloat\b", "The shares that are free to trade, not counting shares held by insiders.", True),
    ("dilution", r"\bdilut(ion|ive|ed|e)\b", "When a company creates and sells new shares. Each existing share then owns a smaller piece, which often pushes the price down.", True),
    ("share offering", r"(share|stock|equity) (offering|sale|issuance)s?|at-the-market|\bATM program\b", "The company sells new shares to raise cash. More shares usually means each share is worth a bit less.", True),
    ("buyback", r"buybacks?|share repurchases?|repurchas(e|ed|ing) shares", "The company uses its cash to buy its own shares, so each remaining share owns a bigger piece.", True),
    ("dividend", r"\bdividends?\b", "Cash the company pays out to shareholders, usually every quarter.", True),
    ("insider", r"\binsiders?\b", "Top managers, board members and big owners of the company. They must report when they buy or sell its stock.", True),
    ("RSI", r"\bRSI\b|relative strength index", "A 0 to 100 gauge of how fast the price has moved lately. Above 70 means it rose very fast, below 30 means it fell very fast.", True),
    ("overbought", r"\boverbought\b", "The price rose so fast that some traders expect a pause or a dip. Not a guarantee.", True),
    ("oversold", r"\boversold\b", "The price fell so fast that some traders expect a bounce. Not a guarantee.", True),
    ("ATR", r"\bATR\b|average true range", "Average true range: how much the price usually moves in one day, in dollars.", True),
    ("50-day average", r"50-day( moving)? average|50-day\b|\b50 ?DMA\b", "The average closing price over the last 50 trading days. Shows the trend over about 2 months.", True),
    ("200-day average", r"200-day( moving)? average|200-day\b|\b200 ?DMA\b", "The average closing price over the last 200 trading days. Shows the trend over about 10 months.", True),
    ("20-day average", r"20-day( moving)? average|20-day\b", "The average closing price over the last 20 trading days. Shows the trend over about a month.", True),
    ("moving average", r"moving averages?", "The average closing price over a set number of recent days. It smooths out daily noise.", True),
    ("support", r"\bsupport( levels?| zones?)?\b(?! (for|from|of|to|the|this|that|a|an|it|its|their|his|her|our|your|any|growth|demand|higher|lower|buying|sales|both|each|either)\b)", "A price where buyers stepped in before and the stock stopped falling. It can break.", True),
    ("resistance", r"\bresistance( levels?| zones?)?\b", "A price where sellers stepped in before and the stock stopped rising. It can break.", True),
    ("swing low", r"swing lows?", "A recent low point where the price turned back up.", True),
    ("52-week high", r"52-week high", "The highest price in the last year.", True),
    ("52-week low", r"52-week low", "The lowest price in the last year.", True),
    ("uptrend", r"\buptrend\b", "The price has been making higher highs and higher lows, and sits above its longer averages.", True),
    ("downtrend", r"\bdowntrend\b", "The price has been making lower highs and lower lows, and sits below its longer averages.", True),
    ("pullback", r"\bpullbacks?\b", "A drop inside a bigger rise. The longer trend is still up.", True),
    ("breakout", r"\bbreakouts?\b", "When the price climbs above a level that stopped it before.", True),
    ("momentum", r"\bmomentum\b", "The tendency of a stock that has been rising to keep rising for a while, or falling to keep falling.", True),
    ("trading volume", r"trading volume|\bvolume\b", "How many shares changed hands. A move on high volume carries more weight.", True),
    ("volatility", r"\bvolatil(e|ity)\b", "How much and how fast the price swings up and down.", True),
    ("beta", r"\bbeta\b", "How much the stock tends to move compared with the whole market. Above 1 means it swings more.", True),
    ("priced in", r"priced in|baked in|already in the price", "Investors already expect it, so it is part of today's price. When it happens, the price may not move much.", True),
    ("catalyst", r"\bcatalysts?\b", "A dated event, like a drug trial result or an earnings report, that could move the price sharply.", True),
    ("Phase 3", r"Phase (3|III)\b", "The last and largest stage of human testing for a new drug, needed before asking for approval.", True),
    ("Phase 2", r"Phase (2|II)\b", "The middle stage of human testing for a new drug: does it work, and at what dose.", True),
    ("Phase 1", r"Phase (1|I)\b", "The first stage of human testing for a new drug, mostly to check that it is safe.", True),
    ("primary endpoint", r"primary endpoints?|\bendpoints?\b", "The main result a drug trial was designed to measure. Missing it usually counts as failure.", True),
    ("placebo", r"\bplacebo\b", "A fake treatment given to some patients so the real drug can be compared against it.", True),
    ("hazard ratio", r"hazard ratio", "Compares how often a bad event happens on the drug versus without it. 0.6 means 40% less often.", True),
    ("readout", r"\breadouts?\b|\btopline\b", "When a company releases the results of a drug trial.", True),
    ("FDA", r"\bFDA\b", "The U.S. agency that decides whether a new drug can be sold.", True),
    ("PDUFA date", r"\bPDUFA\b", "The deadline for the FDA to decide whether to approve a drug.", True),
    ("sector fund", r"sector (fund|ETF)", "A fund that holds many stocks from one industry. Its moves show how the whole industry is doing.", True),
    ("ETF", r"\bETFs?\b", "Exchange-traded fund: one stock you can buy that holds a basket of many stocks.", True),
    ("S&P 500", r"S&P 500|\bSPY\b", "An index of 500 large U.S. companies, used as a stand-in for the whole stock market.", True),
    ("sector", r"\bsectors?\b", "A group of companies in the same line of business, like tech or health care.", True),
    ("interest rates", r"interest rates?|rate cuts?|rate hikes?|the Fed\b|Federal Reserve", "The cost of borrowing money, set mainly by the Federal Reserve (the U.S. central bank). Lower rates usually help stock prices.", True),
    ("bullish", r"\bbull(ish|s)?\b", "Expecting the price to go up.", False),
    ("bearish", r"\bbear(ish|s)?\b", "Expecting the price to go down.", False),
]

_CASE_SENSITIVE = {"EPS", "RSI", "ATR", "FDA", "ETF", "PDUFA date", "P/E", "S&P 500", "float"}
_KEY = {f"t{i}": t[0] for i, t in enumerate(TERMS)}
EXPLAIN = {t[0]: t[2] for t in TERMS}
CHECKED = {t[0] for t in TERMS if t[3]}
_TERM_RE = re.compile("|".join(
    f"(?P<t{i}>{p})" if key in _CASE_SENSITIVE else f"(?P<t{i}>(?i:{p}))"
    for i, (key, p, _, _) in enumerate(TERMS)))

# Counts as an explanation: a bracket or dash right after the term (allowing up to 2 more words,
# as in "P/E ratio ("), "X, which ...", "X means ...", "X is the ...", or "... called X".
_AFTER = re.compile(r"^((\s+[\w'/-]+){0,2}\s*[\(\[]|\s*[—–]\s|:\s+[A-Za-z]|\s+-\s|,\s*(which|meaning|that is|or|a|an|the)\b"
                    r"|\s+(means|meaning|is a|is an|is the|is when|is how|is where|are the|are when|refers to|measures)\b)", re.I)
_BEFORE = re.compile(r"((called|known as|named|termed)\s+(an?\s+|the\s+)?[\"'“]?|\((its |the |a |an |this is )?)$", re.I)

# A rewrite sometimes tacks "priced in [already reflected in the stock price]" onto the end of a
# line, after the sentence is over, instead of explaining the term where it is used.
_DANGLING = re.compile(r"(?<=[.!?])[ \t]+([A-Za-z][\w'/ -]{0,40}?)[ \t]*[\[(][^\]()\n]{3,200}[\])][ \t]*(?=\n|$)")


def drop_dangling_terms(text):
    """Removes a glossary term and its explanation left on its own after the end of a sentence."""
    return _DANGLING.sub(lambda m: "" if _TERM_RE.fullmatch(m.group(1).strip()) else m.group(0), text)

# A sentence that works out dollars won or lost on a position, like "+$120 on 10 shares" or
# "you would lose about $300". Company-sized numbers ($2.4B) and per-share prices do not count.
_BIG = r"(?![\d,.]*\s?(B|M|T|billion|million|trillion)\b)"
_PNL_RE = re.compile(r"[+−-]\s?\$\d[\d,.]*" + _BIG
                     + r"|\b(gain|gains|lose|lost|losing|make|making|made|profit|loss|earn|win)\b[^.$]{0,25}\$\d[\d,.]*" + _BIG,
                     re.I)
_PNL_CONTEXT = re.compile(r"\b(\d+ shares|you|your|position|investment|invested)\b", re.I)


def first_uses(text):
    """[(key, start, end), ...] for the first use of each term, in order."""
    seen, out = set(), []
    for m in _TERM_RE.finditer(text):
        key = _KEY[m.lastgroup]
        if key not in seen:
            seen.add(key)
            out.append((key, m.start(), m.end()))
    return out


def unexplained(text):
    """Checked terms whose first use has no plain explanation right next to it."""
    return [key for key, start, end in first_uses(text)
            if key in CHECKED and not _AFTER.match(text[end:end + 60]) and not _BEFORE.search(text[max(0, start - 30):start])]


def _syllables(word):
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups) - (1 if word.endswith("e") and not word.endswith(("le", "ee")) and len(groups) > 1 else 0)
    return max(1, n)


def grade(text):
    """Flesch-Kincaid grade level. URLs, numbers, tickers and labels are left out."""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\[MONEY\]|\b[A-Z]{2,6}\b|[$%]?\d[\d,.:/%-]*", " ", text)
    sentences = [s for s in re.split(r"[.!?;]+|\n+", text) if re.search(r"[A-Za-z]{2}", s)]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if len(words) < 30 or not sentences:
        return 0.0
    syl = sum(_syllables(w) for w in words)
    return 0.39 * len(words) / len(sentences) + 11.8 * syl / len(words) - 15.59


def money_sentences(text):
    return [s.strip()[:90] for s in re.split(r"(?<=[.!?])\s+|\n", text)
            if _PNL_RE.search(s) and _PNL_CONTEXT.search(s) and not re.search(r"\b(per|a) share\b", s)]


def problems(text, extra=None):
    """List of plain-English problems to send back, empty if the text passes."""
    out = []
    terms = unexplained(text)
    if terms:
        out.append("Unexplained terms: " + ", ".join(terms) + ". The first time each appears, put a short plain "
                   "explanation in brackets right after it, for example \"support (a price where buyers stepped in "
                   "before)\".")
    g = grade(text)
    if g > MAX_GRADE:
        out.append(f"The text reads at about grade {g:.1f}. Rewrite it for a 9th grader: short sentences "
                   "(under 18 words), common words, one idea per sentence.")
    money = money_sentences(text)
    if money:
        out.append("Remove every sentence that works out dollars won or lost on a position (found: "
                   + "; ".join(f'"{m}"' for m in money[:3]) + "). The report adds those itself. "
                   "Price levels and percent moves are fine.")
    return out + (extra or [])


REWRITE_PROMPT = """Rewrite the text below so a 9th grader who has never bought a stock can follow it.
Fix exactly these problems:
{PROBLEMS}

Keep every fact, number, date, price level, URL, heading and label exactly as it is.
Keep the same layout: the same headings, the same lines starting with a dash, the same labels.
Put each explanation right after the word it explains, inside its sentence. Never add a term and
its explanation on their own after a sentence ends.
Keep the token [MONEY] exactly as written and in the same place, if the text has it. Keep every
required part of each section: shorten sentences, but do not cut what they cover.
Do not add new facts. Do not remove facts except a sentence about dollars won or lost.
Return only the rewritten text, with no comments before or after it.

TEXT:
{TEXT}"""


def fix(text, rewrite, label, extra_check=None, view=None):
    """Checks text, and if it fails, sends it back once through rewrite(prompt) -> text.
    view(text) picks the part the reader sees as sentences (default: all of it).
    Returns (text, problems still left after the rewrite)."""
    view = view or (lambda t: t)

    def check(t):
        return problems(view(t), extra_check(t) if extra_check else None)

    found = check(text)
    if not found:
        print(f"    wording check passed: {label} (grade {grade(view(text)):.1f})")
        return text, []
    print(f"    wording check sent back {label}: " + " | ".join(p[:160] for p in found))
    try:
        with step(f"rewrite: {label}"):
            new = rewrite(REWRITE_PROMPT.replace("{PROBLEMS}", "\n".join(f"- {p}" for p in found)).replace("{TEXT}", text))
    except Exception as e:
        print(f"    rewrite of {label} failed ({type(e).__name__}), keeping the first version")
        return text, found
    new = drop_dangling_terms((new or "").strip())
    if not new:
        return text, found
    left = check(new)
    print(f"    {label} after rewrite: grade {grade(view(new)):.1f}, "
          + ("passed" if not left else "still: " + " | ".join(p[:160] for p in left)))
    return new, left


def tip_html(text, seen=None):
    """HTML-escapes text and wraps the first use of each term in a hover tooltip.
    Pass the same seen set across blocks to tooltip a term only once in that run."""
    seen = set() if seen is None else seen
    out, pos = [], 0
    for m in _TERM_RE.finditer(text):
        key = _KEY[m.lastgroup]
        if key in seen:
            continue
        seen.add(key)
        tip = html.escape(f"{key}: {EXPLAIN[key]}", quote=True)
        out.append(html.escape(text[pos:m.start()]))
        out.append(f"<span class='tip' tabindex='0' data-tip='{tip}'>{html.escape(m.group(0))}</span>")
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)
