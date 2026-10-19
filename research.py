"""Grok web research, with a different plan for each kind of stock:
- technical: a short-term trade in a stock that moves on its chart and news
- catalyst: a short-term trade around a dated event (drug trial result, earnings, a big recent move)
- long: a long-term hold

Every fact must say how it could move the price and when. Facts that do not are dropped by code.
After research, the IMPORTANT_CHECKS most important facts go back to Grok to be checked against
their sources, including what is already paid, reserved or priced in. Facts the source does not
support are dropped. Then the wording check (wording.py) runs on what is left.
Hard facts (earnings date, analyst changes, insider trades, short interest) come from
hard_facts.py, so Grok is told not to look for them again.
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from openai import APIConnectionError, APITimeoutError

import wording
from config import (GROK_MODEL, RESEARCH_CACHE, RESEARCH_REASONING_EFFORT, REWRITE_MODEL, chatgpt, grok,
                    save_research_cache)
from timing import step

# The research call is stopped if it runs longer than this, in seconds.
RESEARCH_TIME_LIMIT = 360
# A fact check that runs longer is stopped, and the fact stays in marked as not re-checked.
RECHECK_TIME_LIMIT = 90
IMPORTANT_CHECKS = 3
REWRITE_BATCH = 6   # facts per wording rewrite; batches run at the same time

HORIZONS = {
    "short": "a short-term trade lasting days to a few weeks",
    "long": "a long-term hold lasting a year or more",
}

PLANS = {
    "technical": {
        "what": "a short-term trade in a stock that mostly moves on its chart and its news",
        "topics": ["News", "Sector", "Analyst changes", "Upcoming events"],
        "focus": """
- News: everything from the last 5 trading days (since {SINCE}) that could move the price.
- Sector: how its industry and its 2 or 3 closest competitors moved in the last 5 trading days, and why.
- Analyst changes: code already lists the rating and price target changes. Add only the reasons the
  analysts gave for changes in the last 30 days, with the firm and the date.
- Upcoming events: dated events in the next 4 weeks. Product launches, court dates, investor days,
  lockup ends, and economic reports that matter for this industry. The earnings date is already listed.
""",
    },
    "catalyst": {
        "what": "a short-term trade around a dated event that could move the price sharply",
        "topics": ["The catalyst", "What is being tested", "Cash and dilution", "Past reactions", "News"],
        "focus": """
- The catalyst: what the event is and its exact date, confirmed from the company or an official source.
  {CATALYST}
- What is being tested: for a drug trial, the drug, the disease, how many patients, the main result
  the trial measures, and what a good result and a bad result would look like in numbers (for example
  the result of earlier trials or of the drugs already sold for this disease). For an earnings report,
  the results and forecast that matter most and what would count as a beat or a miss. For a big
  recent move, what caused it.
- Cash and dilution: months of cash left as the company itself states it. Any share sale, at-the-market
  program (selling new shares bit by bit) or convertible debt in the last 12 months, with size and price.
- Past reactions: how this stock moved on the day of its own similar news before (earlier trial results,
  approvals, earnings), and how close competitors moved on similar news. Give the one-day move in percent.
- News: anything else from the last 5 trading days (since {SINCE}) that could move the price.
""",
    },
    "long": {
        "what": "a long-term hold lasting a year or more",
        "topics": ["Business drivers", "Competition", "Cash and spending", "Valuation", "Risks"],
        "focus": """
- Business drivers: what the business does, its main segments, which parts are growing or shrinking,
  and what management said in the latest earnings report and forecast.
- Competition: its position against its main rivals, market share, and any rival gaining on it.
- Cash and spending: cash, debt, free cash flow as the company reports it, and big spending plans.
- Valuation: how expensive the stock is compared with its own history and its peers, and what growth
  today's price assumes, using numbers from named sources.
- Risks: legal, political and regulatory issues and the main business risks. For any large legal, tax or
  regulatory risk, also give the amount already paid, deposited or reserved, the company's stated view,
  and the case status.
""",
    },
}

FORMAT = """
For every fact, write exactly this block, with a blank line between blocks:
TOPIC: one of {TOPICS}
FACT: the fact in one or two short sentences.
DATE: the date of the fact, YYYY-MM-DD
SOURCE: one URL
PRICE EFFECT: how this could move the price, and in which direction.
WHEN: when that effect would show up: a date, or a time window like "the next 2 weeks".
IMPORTANCE: 1 to 5, where 5 means most likely to move the price.

Rules:
- Facts only, with no opinion on whether to buy or sell.
- Prefer the company's own filings and press releases for numbers.
- If you cannot say how a fact could move the price AND when, leave the fact out.
- There is no limit on the number of facts. Include every fact that passes, good news and bad news.
- Skip anything you cannot source.
- Write for someone who has never bought a stock: short sentences and common words. The first time you
  use a financial term, explain it in a few plain words in brackets.
- Never work out how many dollars someone would win or lose.
- Plain text, no markdown symbols.
"""

FIELDS = {"TOPIC": "topic", "FACT": "fact", "DATE": "date", "SOURCE": "source",
          "PRICE EFFECT": "effect", "WHEN": "when", "IMPORTANCE": "importance",
          "ALREADY IN THE PRICE": "priced_in", "CHECK": "check_no", "RESULT": "result"}
_FIELD_RE = re.compile(r"^\s*[-•]?\s*(" + "|".join(FIELDS) + r")\s*:\s*(.*)$", re.I)
_EMPTY = re.compile(r"^(n/?a|none|unknown|not (known|clear|applicable)|-)?\.?$", re.I)


def x_sentiment(ticker, name, today):
    # Asks Grok what traders on X are saying. Billed per post fetched, so it is kept small.
    try:
        r = grok.responses.create(
            model=GROK_MODEL,
            input=[{
                "role": "user",
                "content": (
                    f"Search X for posts from the last 7 days about ${ticker} ({name}). "
                    "Use at most 2 searches. Summarize what traders are saying in at most 5 bullets, "
                    "each starting with a dash: the main bullish view, the main bearish view, "
                    "any specific rumor or event being discussed, and whether the mood is mostly "
                    "positive, mostly negative or split. These are opinions. Label rumors as rumors "
                    "and never present a post as a confirmed fact. Include 2 or 3 post links as examples. "
                    "Write for someone who has never bought a stock: short sentences, common words, and "
                    "explain any trading term in brackets the first time. Plain text, no markdown symbols."
                ),
            }],
            tools=[{
                "type": "x_search",
                "from_date": str(today - timedelta(days=7)),
                "to_date": str(today + timedelta(days=1)),
            }],
            extra_body={"max_turns": 2},
        )
        try:
            used = (r.usage.model_dump().get("server_side_tool_usage_details") or {})
            posts = used.get("x_posts_fetched")
            if posts:
                print(f"    (X search read {posts} posts, about ${posts * 0.005:.2f})")
        except Exception:
            pass
        return r.output_text
    except Exception as e:
        return f"- X search was not available for this run ({type(e).__name__})."


def rewrite_text(prompt):
    """A fast OpenAI call with no web search, used for wording rewrites."""
    r = chatgpt.with_options(timeout=120, max_retries=1).responses.create(model=REWRITE_MODEL, input=prompt)
    return r.output_text


def _stream_research(prompt, limit=RESEARCH_TIME_LIMIT):
    # Each attempt gets its own time limit.
    # Stream the answer so a slow web search does not drop the connection.
    # A timer closes the stream if the whole call runs past the time limit.
    start = time.perf_counter()
    stream = grok.with_options(timeout=limit, max_retries=0).responses.create(
        model=GROK_MODEL,
        input=[{"role": "user", "content": prompt}],
        tools=[{"type": "web_search"}],
        reasoning={"effort": RESEARCH_REASONING_EFFORT},
        stream=True,
    )
    timed_out = threading.Event()

    def stop():
        timed_out.set()
        stream.close()

    timer = threading.Timer(max(0, limit - (time.perf_counter() - start)), stop)
    timer.start()
    parts = []
    try:
        for event in stream:
            if getattr(event, "type", "") == "response.output_text.delta":
                parts.append(event.delta)
    except Exception:
        if not timed_out.is_set():
            raise
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise RuntimeError(
            f"Research took longer than {limit} seconds and was stopped."
        )

    return "".join(parts).strip()


def _search(prompt, label):
    # If Grok drops the connection mid-answer, try once more. A timeout is not retried.
    with step(label):
        try:
            return _stream_research(prompt)
        except APIConnectionError as e:
            if isinstance(e, APITimeoutError):
                raise
            print(f"    (Grok dropped the connection during {label}, retrying once...)")
            return _stream_research(prompt)


def parse_blocks(text, start="TOPIC"):
    """Splits labeled text into a list of {field: value} dicts. A new block starts at each start label."""
    blocks, cur, last = [], None, None
    for line in text.splitlines():
        m = _FIELD_RE.match(line)
        if m:
            key = FIELDS[m.group(1).upper()]
            if key == FIELDS[start] or cur is None:
                cur = {}
                blocks.append(cur)
            cur[key], last = _unlink(m.group(2).strip()), key
        elif cur is not None and last and line.strip():
            cur[last] += " " + _unlink(line.strip())
    return blocks


def _unlink(text):
    # Grok sometimes cites in markdown, like [[1]](https://...). Keep just the URL in brackets.
    return re.sub(r"\[\[?[^\]]*\]?\]\((https?://[^)\s]+)\)", r"(\1)", text)


def parse_facts(text, topics):
    """Facts with every required field. Returns (kept, number dropped)."""
    kept, dropped = [], 0
    for b in parse_blocks(text):
        url = re.search(r"https?://\S+", b.get("source", ""))
        if not url or any(_EMPTY.match(b.get(k, "").strip()) for k in ("fact", "effect", "when")):
            dropped += 1
            continue
        b["source"] = url.group(0).rstrip(".,;)")
        if b.get("topic") not in topics:
            given = b.get("topic", "").lower()
            b["topic"] = next((t for t in topics if given and (given in t.lower() or t.lower() in given)), "Other")
        try:
            b["importance"] = int(re.search(r"\d", b.get("importance", "")).group(0))
        except AttributeError:
            b["importance"] = 1
        kept.append(b)
    return kept, dropped


CHECK_PROMPT = """Today is {TODAY}. Below is one of the most important facts from research on {NAME} ({TICKER}).
1. Open its source (search for it if the link does not work) and check that the fact matches the
   source exactly: numbers, dates and names.
2. Find what is already paid, reserved or priced in. For a legal, tax or regulatory cost: how much is
   already paid, deposited or set aside. For an expected event or result: what analysts and investors
   already expect, and how much the stock has already moved on it.

Answer in exactly this form:
CHECK: 1
RESULT: confirmed, corrected, or not found
FACT: the fact as the source supports it (rewritten if corrected)
SOURCE: the URL you used
ALREADY IN THE PRICE: one or two short, plain sentences, or "Nothing found."

Write for someone who has never bought a stock. Plain text, no markdown symbols.

FACT:
{FACT}"""

def _check_one(fact, n, ticker, name, today, ask):
    prompt = (CHECK_PROMPT.replace("{TODAY}", str(today)).replace("{NAME}", str(name)).replace("{TICKER}", ticker)
              .replace("{FACT}", f"{fact['fact']} (DATE: {fact.get('date', '')}, SOURCE: {fact['source']})"))
    try:
        with step(f"re-check {n}: {fact['fact'][:50]}"):
            blocks = parse_blocks(ask(prompt), start="CHECK")
    except Exception as e:
        print(f"    fact check {n} failed ({type(e).__name__}: {str(e)[:80]}), fact kept unchecked")
        fact["check"] = "not re-checked: the check ran out of time"
        return None
    return blocks[0] if blocks else None


def most_important(facts):
    """Indexes of the facts that get re-checked."""
    return sorted(range(len(facts)), key=lambda i: -facts[i]["importance"])[:IMPORTANT_CHECKS]


def reword(facts, topics):
    """Wording check on facts, in batches that run at the same time, one rewrite each.
    A batch's rewrite is kept only if no fact was lost in it. Changes facts in place."""
    def one(n, batch):
        text, _ = wording.fix(facts_text(batch), rewrite_text, f"research facts {n}", view=_fact_prose)
        new, _ = parse_facts(text, topics)
        if len(new) == len(batch):
            for old, fresh in zip(batch, new):
                for k in ("fact", "effect", "when", "priced_in"):
                    if fresh.get(k):
                        old[k] = fresh[k]

    batches = [facts[i:i + REWRITE_BATCH] for i in range(0, len(facts), REWRITE_BATCH)]
    if batches:
        with ThreadPoolExecutor(len(batches)) as pool:
            list(pool.map(lambda nb: one(*nb), enumerate(batches, 1)))


def recheck(facts, ticker, name, today, ask=None):
    """Checks the most important facts against their sources, one call per fact, all at the same
    time. Changes facts in place and returns the list without the facts the source did not support."""
    ask = ask or (lambda p: _stream_research(p, RECHECK_TIME_LIMIT))
    top = most_important(facts)
    if not top:
        return facts
    with ThreadPoolExecutor(len(top)) as pool:
        answers = list(pool.map(lambda nk: _check_one(facts[nk[1]], nk[0], ticker, name, today, ask), enumerate(top, 1)))
    drop = set()
    for i, b in zip(top, answers):
        if not b:
            continue
        result = b.get("result", "").lower()
        if "not found" in result:
            drop.add(i)
            print(f"    fact check: dropped, source does not support it: {facts[i]['fact'][:90]}")
            continue
        if "corrected" in result and b.get("fact"):
            print(f"    fact check: corrected: {facts[i]['fact'][:90]}")
            facts[i]["fact"] = b["fact"]
            url = re.search(r"https?://\S+", b.get("source", ""))
            if url:
                facts[i]["source"] = url.group(0).rstrip(".,;)")
        facts[i]["check"] = "re-checked against its source: " + ("corrected" if "corrected" in result else "confirmed")
        priced = b.get("priced_in", "").strip()
        if priced and not priced.lower().startswith("nothing found"):
            facts[i]["priced_in"] = priced
    return [f for i, f in enumerate(facts) if i not in drop]


def facts_text(facts):
    from hard_facts import facts_text as fmt
    return fmt(facts)


def _fact_prose(text):
    # The parts of a fact block the reader sees as sentences, for the wording check.
    return "\n".join(m.group(2) for m in map(_FIELD_RE.match, text.splitlines())
                     if m and FIELDS[m.group(1).upper()] in ("fact", "effect", "when", "priced_in"))


# Pulled by hard_facts.py at the same time as the research, so Grok skips them.
CODE_COVERS = ("These are pulled from data by code, so do not research them: the next earnings date and "
               "analyst estimates for it, past earnings-day moves, the list of analyst rating and price target "
               "changes, insider trades, short interest, the 5-day sector move, cash on hand, cash burn and "
               "share count change. You may still give context code cannot, like a reason or a company statement.")


def research(ticker, name, today, horizon, plan, catalyst=None, fresh=False):
    """Returns {"facts": [...], "x": text or "", "plan": plan}. fresh=True skips today's saved research."""
    cache_key = "|".join((ticker, str(today), horizon, plan, "v2"))
    if cache_key in RESEARCH_CACHE and not fresh:
        print("    (using research saved from earlier today, no Grok search cost)")
        out = RESEARCH_CACHE[cache_key]
        for f in out["facts"]:
            for k in ("fact", "effect", "when", "priced_in"):
                if f.get(k):
                    f[k] = _unlink(f[k])
        return out

    p = PLANS[plan]
    since = today - timedelta(days=7)
    cat = (f"Our verified list says: {catalyst}. Confirm it or correct it." if catalyst
           else "If there is no dated event, say so in a fact with TOPIC The catalyst.")
    prompt = (
        f"Today is {today}. Research the company {name} ({ticker}) using web search.\n"
        f"The reader is considering {HORIZONS[horizon]}. Treat it as {p['what']}.\n"
        "Find what could actually move the price. Research plan:\n"
        + p["focus"].replace("{SINCE}", str(since)).replace("{CATALYST}", cat)
        + FORMAT.replace("{TOPICS}", ", ".join(p["topics"]))
        + "\n" + CODE_COVERS
    )

    def x_posts():
        with step("X posts search"):
            text = x_sentiment(ticker, name, today)
        return wording.fix(text, rewrite_text, "X posts")[0]

    # The X search does not depend on the web research, so it runs at the same time.
    # One search covers the whole plan. One search per topic was tried and was slower: each still
    # took 100-185s, and the report has to wait for the slowest.
    with ThreadPoolExecutor(2) as pool:
        x_job = pool.submit(x_posts) if horizon == "short" else None
        raw = _search(prompt, "research search")
        if not raw:
            raise RuntimeError("The research call returned no text. Run it again.")
        facts, dropped = parse_facts(raw, p["topics"])
        print(f"    {len(facts)} facts kept, {dropped} dropped for missing how, when or source")
        if not facts:
            raise RuntimeError("Research returned no usable facts. Run it again.")

        # The facts that are not re-checked get their wording check while the re-checks run.
        # The re-checked ones get theirs afterwards, so it covers any corrected text.
        top = set(most_important(facts))
        check_job = pool.submit(recheck, facts, ticker, name, today)
        reword([f for i, f in enumerate(facts) if i not in top], p["topics"])
        checked = [f for i, f in enumerate(facts) if i in top]
        facts = check_job.result()
        reword([f for f in checked if f in facts], p["topics"])
        x = x_job.result() if x_job else ""

    out = {"facts": facts, "x": x, "plan": plan}
    RESEARCH_CACHE[cache_key] = out
    save_research_cache(RESEARCH_CACHE)
    return out
