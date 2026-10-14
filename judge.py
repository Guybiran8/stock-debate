import threading
import time

from config import GROK_MODEL, JUDGE_REASONING_EFFORT, grok
from research import HORIZONS

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

HOW TO WRITE:
- Write complete, clear sentences. Being clear matters more than being short.
- Use the real name of every financial term. The first time a term appears,
  explain it in a few words in brackets, for example "RSI (a 0 to 100 gauge of
  how fast the price has moved)" or "free cash flow (cash left after spending
  on equipment)". Never invent a nickname for a standard term.
- One idea per sentence. Do not cram several facts into one sentence with commas.
"""

DECISION_INSTRUCTIONS = COMMON_INSTRUCTIONS + """
Write a brief for a regular investor who wants enough evidence to make their
own decision. Use exactly these two headings, written exactly as shown with
nothing added, each on its own line, with a blank line between sections:

WHERE THE EVIDENCE LEANS
THE DECISION

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

Hard limit: 300 words total. Plain text, no markdown symbols.
"""

DETAILS_INSTRUCTIONS = COMMON_INSTRUCTIONS + """
Write the rest of the brief for a regular investor who wants enough evidence
to make their own decision. Use exactly these seven headings, written exactly
as shown with nothing added, each on its own line, with a blank line between
sections:

BEST BULL POINTS
BEST BEAR POINTS
WHERE THEY DISAGREE
WHAT THE CHART SHOWS
DATA WARNINGS
CLAIMS THAT FAILED
CHECK THIS YOURSELF

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

Hard limit: 900 words total. Plain text, no markdown symbols.
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


def judge_decision(data, d, horizon):
    return _stream(DECISION_INSTRUCTIONS, horizon, _debate_text(data, d), "decision")


def judge_details(data, d, horizon):
    return _stream(DETAILS_INSTRUCTIONS, horizon, _debate_text(data, d), "details")


def judge(data, d, horizon):
    # Runs the decision and details judges at the same time, in separate
    # threads, so the wait is the slower of the two rather than both added up.
    out = {}

    errors = []

    def run(name, fn):
        start = time.perf_counter()
        try:
            out[name] = fn(data, d, horizon)
        except Exception as e:
            errors.append(e)
        out[f"{name}_seconds"] = time.perf_counter() - start

    threads = [
        threading.Thread(target=run, args=("decision", judge_decision)),
        threading.Thread(target=run, args=("details", judge_details)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # A thread's exception does not reach the caller on its own, so re-raise it here.
    if errors:
        raise errors[0]

    return out["decision"], out["details"], out["decision_seconds"], out["details_seconds"]
