import re
import threading
import time

import wording
from config import GROK_MODEL, JUDGE_REASONING_EFFORT, grok
from research import HORIZONS, rewrite_text
from timing import step

COMMON_INSTRUCTIONS = """
You are a neutral judge of a stock debate. You have the full DATA and RESEARCH
below, plus the debate. Judge the EVIDENCE, not the debating. Check every claim
against the DATA and RESEARCH yourself. A point counts only if the evidence
supports it, no matter how well or badly it was argued. If strong evidence
exists for a side that the debater failed to use, include it anyway. Do not
reward a side for catching the other's mistakes. Posts from X are opinions,
never proof of a fact.

The reader is considering {HORIZON}. Weigh the evidence for that time frame.
For a short-term trade, near-term events, trader mood and chart levels matter
most. For a long-term hold, business quality, cash and valuation matter most.

Focus on what actually moves the price, and when. Each research fact says how
and when it could move the price. Use that.

HOW TO WRITE:
- The reader has never bought a stock. Write at about a 9th-grade level: short
  sentences (under 18 words), common words, one idea per sentence.
- Use the real name of every financial term. The first time a term appears,
  explain it in a few plain words in brackets, for example "RSI (a 0 to 100 gauge
  of how fast the price has moved)" or "free cash flow (cash left after spending
  on equipment)". Never invent a nickname for a standard term.
- Never work out how many dollars someone would win or lose. The report adds
  those from code. Price levels and percent moves are fine.
"""

DECISION_INSTRUCTIONS = COMMON_INSTRUCTIONS + """
Write a brief for a regular investor who wants enough evidence to make their
own decision. Use exactly these three headings, written exactly as shown with
nothing added, each on its own line, with a blank line between sections:

IN PLAIN ENGLISH
WHERE THE EVIDENCE LEANS
THE DECISION

Under IN PLAIN ENGLISH, write one full paragraph of 7 to 10 short sentences, with
no dashes or lists, for someone who has never bought a stock. Cover, in this order:
what is going on with this company and its stock right now; what someone buying
today is betting will happen; how they make money (what has to happen to the
price, and why it might); how they lose money (what could go wrong); then write
the exact token [MONEY] as its own sentence, where the report will add a dollar
example; then the one price to watch, using a level from PRICE CHART DATA, and
what it would mean if the price crossed it; then the next date that matters
(see NEXT DATE THAT MATTERS) and what happens that day. Explain every term in
plain words or avoid it. No jargon left unexplained.

Under WHERE THE EVIDENCE LEANS, write exactly one line. It must start with
"Leans bullish:" or "Leans bearish:" or "Mixed:" and then give the single
biggest reason for this time frame. Base it only on the weight of the evidence,
never on who debated better. Choose "Mixed:" unless the evidence clearly favors
one side.

Under THE DECISION, write exactly these seven lines, each starting with a dash
and the label shown, each one or two clear sentences:
- The bet: what someone buying at today's price for this time frame is betting will happen.
- You're wrong if: the specific things that would break that bet.
- What the price already assumes: how much good news is already in the price, using the valuation numbers and the analyst price target versus today's price. Remind the reader that analyst targets are opinions.
- Fits you if: describe the person this suits, in terms of how long they can hold, how much of a drop they can sit through, and how closely they can watch it.
- Doesn't fit you if: describe the person this does not suit, in the same terms.
- Case gets stronger if: one or two concrete, checkable signals, using real numbers, price levels or dates from the data.
- Case gets weaker if: one or two concrete, checkable signals, using real numbers, price levels or dates from the data.
Do not tell the reader to buy or sell. Lay out the decision so they can make it.

Hard limit: 500 words total. Plain text, no markdown symbols.
"""

# The rest of the brief is written by two judges at the same time, since a long answer is what
# makes a judge slow. Together they cover the same seven sections, with the same rules.
DETAILS_INTRO = """
Write part of the brief for a regular investor who wants enough evidence
to make their own decision. Use exactly these headings, written exactly
as shown with nothing added, each on its own line, with a blank line between
sections:
"""

POINTS_INSTRUCTIONS = COMMON_INSTRUCTIONS + DETAILS_INTRO + """
BEST BULL POINTS
BEST BEAR POINTS
WHERE THEY DISAGREE

Under BEST BULL POINTS give the 3 best-supported points for owning the stock.
Under BEST BEAR POINTS give the 3 best-supported points against.
Write EACH of those six points as exactly four lines in this form:
- The point in one plain sentence.
Evidence: the specific numbers or facts behind it, and where they come from (company filing, earnings call, named news source).
Why it matters: what this means for someone deciding whether to own the stock for this time frame.
Other side: the strongest counter-argument from the evidence, or "No strong counter in the evidence."
Each of those lines is one or two clear sentences.

Under WHERE THEY DISAGREE, give 1 or 2 questions the evidence cannot settle,
each on its own line starting with a dash, and say what would settle each one.

Hard limit: 600 words total. Plain text, no markdown symbols.
"""

CHECKS_INSTRUCTIONS = COMMON_INSTRUCTIONS + DETAILS_INTRO + """
WHAT THE CHART SHOWS
DATA WARNINGS
CLAIMS THAT FAILED
CHECK THIS YOURSELF

Under WHAT THE CHART SHOWS, write for a beginner, using ONLY the numbers in the
PRICE CHART DATA section. Never invent a price level. Give exactly 5 points,
each on its own line starting with a dash:
1. The trend, and what it means in plain words.
2. The nearest support level, with a few words explaining what support is.
3. The nearest resistance level, with a few words explaining what resistance is.
4. Start with "Bull read:" and say what chart traders would see as a good sign from here.
5. Start with "Bear read:" and say what chart traders would see as a warning sign from here.
If PRICE CHART DATA says no chart data is available, write a dash and "No chart data."
Chart levels describe past behavior. Never present them as predictions.

Under DATA WARNINGS, list numbers where sources conflicted and which to trust,
each on its own line starting with a dash, or write a dash and the word None.
Under CLAIMS THAT FAILED, list up to 3 claims from either side that the
evidence contradicts or does not support, and say which side made each.
Under CHECK THIS YOURSELF, give 2 or 3 things to check.

Hard limit: 400 words total. Plain text, no markdown symbols.
"""


def _debate_text(data, d):
    return (
        data
        + "\n\nBULL CASE:\n" + d["bull"]
        + "\n\nBEAR CASE:\n" + d["bear"]
        + "\n\nBULL REPLY:\n" + d["bull_reply"]
        + "\n\nBEAR REPLY:\n" + d["bear_reply"]
    )


def _stream(instructions_template, horizon, debate_text, label):
    instructions = instructions_template.replace("{HORIZON}", HORIZONS[horizon])

    # Stream the answer so the connection stays alive and progress is visible.
    stream = grok.with_options(timeout=240, max_retries=0).responses.create(
        model=GROK_MODEL,
        input=[{"role": "user", "content": instructions + "\n\n" + debate_text}],
        reasoning={"effort": JUDGE_REASONING_EFFORT},
        stream=True,
    )
    print(f"    {label} judge is thinking and writing...")
    parts = []
    for event in stream:
        if getattr(event, "type", "") == "response.output_text.delta":
            parts.append(event.delta)

    text = "".join(parts).strip()
    if not text:
        raise RuntimeError(f"The {label} judge returned no text. Run the cell again.")
    return text


def plain_section(brief):
    """The IN PLAIN ENGLISH paragraph from a decision brief, or ""."""
    m = re.search(r"IN PLAIN ENGLISH:?\s*\n(.*?)(?=\n\s*WHERE THE EVIDENCE LEANS|\Z)", brief, re.S | re.I)
    return " ".join(m.group(1).split()) if m else ""


def plain_problems(brief):
    """What the IN PLAIN ENGLISH paragraph is missing."""
    text = plain_section(brief)
    if not text:
        return ["The IN PLAIN ENGLISH section is missing. Add it first, as one full paragraph."]
    out = []
    if len(re.findall(r"[.!?](\s|$)", text)) < 6:
        out.append("IN PLAIN ENGLISH must be a full paragraph of 7 to 10 sentences, not a short summary.")
    if "[MONEY]" not in text:
        out.append("IN PLAIN ENGLISH must contain the exact token [MONEY] as its own sentence, right after how you lose money.")
    if not re.search(r"\$\d", text.replace("[MONEY]", "")):
        out.append("IN PLAIN ENGLISH must name the one price to watch, as a dollar price level.")
    if not re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}|\d{4}-\d{2}-\d{2}", text):
        out.append("IN PLAIN ENGLISH must give the next date that matters, as a calendar date.")
    return out


def judge_decision(data, d, horizon):
    return _stream(DECISION_INSTRUCTIONS, horizon, _debate_text(data, d), "decision")


def judge_points(data, d, horizon):
    return _stream(POINTS_INSTRUCTIONS, horizon, _debate_text(data, d), "points")


def judge_checks(data, d, horizon):
    return _stream(CHECKS_INSTRUCTIONS, horizon, _debate_text(data, d), "checks")


def judge(data, d, horizon):
    # Runs the decision judge and the two details judges at the same time, in separate
    # threads, so the wait is the slowest of them rather than all added up.
    out = {}

    errors = []

    def run(name, fn):
        start = time.perf_counter()
        try:
            # The wording check runs here too, so each judge's rewrite also happens in parallel.
            with step(f"{name} judge"):
                text = fn(data, d, horizon)
            out[name], _ = wording.fix(text, rewrite_text, f"{name} judge",
                                       extra_check=plain_problems if name == "decision" else None)
        except Exception as e:
            errors.append(e)
        out[f"{name}_seconds"] = time.perf_counter() - start

    threads = [
        threading.Thread(target=run, args=("decision", judge_decision)),
        threading.Thread(target=run, args=("points", judge_points)),
        threading.Thread(target=run, args=("checks", judge_checks)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # A thread's exception does not reach the caller on its own, so re-raise it here.
    if errors:
        raise errors[0]

    details = out["points"] + "\n\n" + out["checks"]
    return out["decision"], details, out["decision_seconds"], max(out["points_seconds"], out["checks_seconds"])
