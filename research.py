import threading
import time
from datetime import timedelta

from openai import APIConnectionError, APITimeoutError

from config import GROK_MODEL, RESEARCH_CACHE, grok, save_research_cache

# The research call is stopped if it runs longer than this, in seconds.
RESEARCH_TIME_LIMIT = 360

HORIZONS = {
    "short": "a short-term trade lasting days to a few weeks",
    "long": "a long-term hold lasting a year or more",
}

FOCUS = {
    "short": """
Focus on what could move the stock over the next few days and weeks:
- news from the last 14 days
- upcoming dated events: the next earnings date, product launches, court dates,
  and economic reports that matter for this sector
- analyst upgrades, downgrades and price target changes in the last 30 days
- how the sector and its closest competitors have moved recently
- unusual trading volume, and any short squeeze or momentum story
Cover the latest earnings report in at most 2 bullets.
""",
    "long": """
Focus on what drives the business over years:
- what the business does and its main segments
- the latest earnings report, guidance, and what management said
- competitive position and market share
- cash, debt and spending plans
- major deals, customers and new projects
- legal, political and regulatory issues
- the main long-term risks
Also give the next earnings date if it is known.
""",
}


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
                    "Plain text, no markdown symbols."
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


def _stream_research(prompt):
    # Each attempt gets its own time limit.
    # Stream the answer so a slow web search does not drop the connection.
    # A timer closes the stream if the whole call runs past the time limit.
    start = time.perf_counter()
    stream = grok.with_options(timeout=RESEARCH_TIME_LIMIT, max_retries=0).responses.create(
        model=GROK_MODEL,
        input=[{"role": "user", "content": prompt}],
        tools=[{"type": "web_search"}],
        stream=True,
    )
    timed_out = threading.Event()

    def stop():
        timed_out.set()
        stream.close()

    timer = threading.Timer(max(0, RESEARCH_TIME_LIMIT - (time.perf_counter() - start)), stop)
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
            f"Research took longer than {RESEARCH_TIME_LIMIT // 60} minutes and was stopped. Run it again."
        )

    return "".join(parts).strip()


def research(ticker, name, today, horizon):
    key = (ticker, str(today), horizon)
    cache_key = "|".join(key)
    if cache_key in RESEARCH_CACHE:
        print("    (using research saved from earlier today, no Grok search cost)")
        return RESEARCH_CACHE[cache_key]

    prompt = (
        f"Today is {today}. Research the company {name} ({ticker}) using web search.\n"
        f"The reader is considering {HORIZONS[horizon]}.\n"
        + FOCUS[horizon]
        + """
Report facts only, with no opinion on whether to buy or sell.
Prefer the company's own filings and earnings releases for numbers.
Include trailing free cash flow as the company reports it.
For any large legal, tax or regulatory risk, also include the amount already
paid, deposited or reserved, the company's stated view of the outcome,
and the case status.
Hard limit: 14 bullets total, at most 2 sentences each. Start each bullet with a dash.
Every bullet needs its date and one source URL. Include good news and bad news.
Skip anything you cannot source. Plain text, no markdown symbols.
"""
    )
    # If Grok drops the connection mid-answer, try once more. A timeout is not retried.
    try:
        findings = _stream_research(prompt)
    except APIConnectionError as e:
        if isinstance(e, APITimeoutError):
            raise
        print("    (Grok dropped the connection during research, retrying once...)")
        findings = _stream_research(prompt)
    if not findings:
        raise RuntimeError("The research call returned no text. Run it again.")

    if horizon == "short":
        findings += (
            "\n\nWHAT TRADERS ARE SAYING ON X (opinions, not facts):\n"
            + x_sentiment(ticker, name, today)
        )

    RESEARCH_CACHE[cache_key] = findings
    save_research_cache(RESEARCH_CACHE)
    return findings
