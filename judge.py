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

NUMBER_TAGS = """
NUMBER TAGS: right after every number you write (a dollar amount, a percent, or
a ratio like "1.4 times"), add one tag in square brackets that says where it
comes from:
[R] reported fact: a filing, the company's reported results, or market data
    (price, volume, cash, debt, short interest, insider trades).
[EC] estimate by the company: its own forecast, guidance, long-term target or
    estimate (for example its own estimate of a tax bill).
[EA] estimate by analysts: price targets, expected profit or sales, analyst
    growth forecasts, or a named bank's estimate.
[C] calculation: worked out by code: P/E, distances, averages, support,
    resistance, the average move after earnings, peer comparisons.
Dates and years get no tag. Example: "Sales rose 7% [R] to $13.4 billion [R].
The average price target is $95.12 [EA], 2.6% [C] above today."
"""

DECISION_INSTRUCTIONS = COMMON_INSTRUCTIONS + NUMBER_TAGS + """
THE CHART SIDE IS DECIDED BY CODE. The setup status below comes from the chart
rules in PRICE CHART DATA. Never change it, argue with it or soften it. Only
explain it.
{SETUP}

The report's evidence label is also set by code, from your COMPANY LEAN:
{TABLE}

Write a brief for a regular investor who wants enough evidence to make their
own decision. Never tell the reader to buy, sell, hold or watch the stock, and
never give the stock a rating. Use exactly these headings, written exactly as
shown with nothing added, each on its own line, with a blank line between
sections:

THE BIG PICTURE
COMPANY LEAN
CHART EXPLAINED
COMPANY DRIVERS
MAIN RISKS
WHAT THE PRICE ASSUMES
WHAT CHANGES THE PICTURE

Under THE BIG PICTURE, write exactly three lines, each one short sentence of
under 25 words:
Chart: what the chart says. Use the exact setup status words, for example
"No setup".
Company: what the company and the news say for this time frame.
Link: one sentence that connects the two, matching the evidence label your
COMPANY LEAN leads to (see above). The report prints that label just before
this line, so do not repeat the label or its "Chart: ... Company: ..." words.

Under COMPANY LEAN, write exactly one line that starts with "Supportive:",
"Against:" or "Mixed:" and gives the single biggest reason. Judge only the
company and news evidence here, not the chart. Choose "Mixed:" unless the
evidence clearly points one way for this time frame.

Under CHART EXPLAINED, write 2 or 3 short sentences that explain in plain words
why the checks in PRICE CHART DATA give this setup status. Do not list the
levels again; the report shows them.

Under COMPANY DRIVERS, give only the 2 or 3 drivers that matter most for this
stock and time frame. Write each as two lines:
- The driver in one plain sentence.
Detail: the numbers and facts behind it, in one to three short sentences.

Under MAIN RISKS, give the 1 or 2 biggest risks, in the same two-line form.

Under WHAT THE PRICE ASSUMES, write 2 or 3 short sentences on how much good
news is already in the price, using the valuation numbers and the analyst price
target versus today's price. Say that analyst targets are opinions.

Under WHAT CHANGES THE PICTURE, list the upcoming dates and events, soonest
first, each on its own line in this form:
- Oct 27, 2026: what happens, and what a good or a bad result would mean.
Use a calendar date or a date range such as "Late 2026 to mid 2027".
Include the date under NEXT DATE THAT MATTERS.

EXPLAIN EACH ISSUE ONCE. Each fact, risk and event belongs under exactly one
heading. If another section needs it, refer to it in a few words ("the tax
case above") without repeating its numbers.

Hard limit: 550 words total. Plain text, no markdown symbols.
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
DATA WARNINGS
CLAIMS THAT FAILED
CHECK THIS YOURSELF

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


DECISION_HEADINGS = ["THE BIG PICTURE", "COMPANY LEAN", "CHART EXPLAINED", "COMPANY DRIVERS", "MAIN RISKS",
                     "WHAT THE PRICE ASSUMES", "WHAT CHANGES THE PICTURE"]
# Sections where a repeated number means an issue was explained twice. The big picture and the
# chart explanation are summaries, so they may echo a number from below.
ONCE_SECTIONS = ["COMPANY DRIVERS", "MAIN RISKS", "WHAT THE PRICE ASSUMES", "WHAT CHANGES THE PICTURE"]
_NUMBER = re.compile(r"(?<![\w.$])(\$\d[\d,]*(?:\.\d+)?(?:\s?(?:billion|million|trillion|B|M|T)\b)?"
                     r"|\d[\d,]*(?:\.\d+)?\s?%|\d+(?:\.\d+)?\s?(?:x|times)\b)")
_BREAK = re.compile(r"[.;!?](\s|$)|\n")
_MONTH = r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\b"


def sections(brief, headings=DECISION_HEADINGS):
    """{heading: text under it} for the headings found in a brief."""
    found = [(m.start(), m.end(), m.group(1).upper()) for m in
             re.finditer(r"^\s*(" + "|".join(headings) + r"):?\s*$", brief, re.M | re.I)]
    return {h: brief[end:(found[i + 1][0] if i + 1 < len(found) else len(brief))].strip()
            for i, (_, end, h) in enumerate(found)}


def untagged_numbers(text):
    """Numbers with no [R]/[EC]/[EA]/[C] tag after them in the same sentence."""
    out = []
    for m in _NUMBER.finditer(text):
        tail = text[m.end():m.end() + 45]
        cut = _BREAK.search(tail)
        if not wording._KIND_RE.search(tail[:cut.start()] if cut else tail):
            out.append(m.group(0))
    return out


def repeated_numbers(parts):
    """Dollar amounts that appear in more than one of the ONCE_SECTIONS: [(amount, [sections])].
    Only dollars: two different facts often share a small percent (9% growth, a 9% drop)."""
    where = {}
    for h in ONCE_SECTIONS:
        for n in {m.group(0).replace(" ", "").lower() for m in _NUMBER.finditer(parts.get(h, ""))
                  if m.group(0).startswith("$")}:
            where.setdefault(n, []).append(h)
    return [(n, hs) for n, hs in where.items() if len(hs) > 1]


def _items(text):
    return [line for line in text.splitlines() if line.strip().startswith(("-", "•"))]


def decision_problems(brief, status):
    """What the decision brief gets wrong in format, setup status, number tags or repeats."""
    parts = sections(brief)
    missing = [h for h in DECISION_HEADINGS if h not in parts]
    if missing:
        return [f"These headings are missing: {', '.join(missing)}. Use every heading exactly as written."]
    out = []
    big = {k.lower(): v.strip() for k, v in re.findall(r"^\s*-?\s*(Chart|Company|Link):\s*(.+)$", parts["THE BIG PICTURE"], re.M | re.I)}
    if set(big) != {"chart", "company", "link"}:
        out.append("THE BIG PICTURE must be exactly three lines starting with Chart:, Company: and Link:.")
    elif status.lower() not in big["chart"].lower():
        out.append(f'The Chart: line must use the exact setup status set by code: "{status}".')
    elif any(len(wording.strip_kinds(v).split()) > 30 for v in big.values()):
        out.append("Each line under THE BIG PICTURE must be one short sentence of under 25 words.")
    elif re.match(r"(too early to tell|chart and company (agree|disagree))\b|.*\bchart:", big["link"], re.I):
        out.append("The Link: line must not repeat the evidence label or its \"Chart: ... Company: ...\" words. "
                   "The report already shows them. Say in one sentence how the chart and the company connect.")
    if not re.match(r"\s*-?\s*(Supportive|Against|Mixed):", parts["COMPANY LEAN"]):
        out.append('COMPANY LEAN must be one line starting with "Supportive:", "Against:" or "Mixed:".')
    if not 2 <= len(_items(parts["COMPANY DRIVERS"])) <= 3:
        out.append("COMPANY DRIVERS must have 2 or 3 drivers, each starting with a dash.")
    if not 1 <= len(_items(parts["MAIN RISKS"])) <= 2:
        out.append("MAIN RISKS must have 1 or 2 risks, each starting with a dash.")
    dates = _items(parts["WHAT CHANGES THE PICTURE"])
    if not dates or any(not re.search(_MONTH + r"|\b20\d\d\b", line.split(":")[0]) for line in dates):
        out.append("Each line under WHAT CHANGES THE PICTURE must start with a dash and a date, then a colon.")
    shown = "\n".join(parts[h] for h in DECISION_HEADINGS if h != "COMPANY LEAN")
    bare = untagged_numbers(shown)
    if bare:
        out.append("Add a number tag ([R], [EC], [EA] or [C]) right after each of these numbers: "
                   + ", ".join(dict.fromkeys(bare)) + ".")
    twice = repeated_numbers(parts)
    if twice:
        out.append("Explain each issue once. These numbers appear under more than one heading: "
                   + "; ".join(f"{n} ({' and '.join(hs)})" for n, hs in twice)
                   + ". Keep each under one heading and refer to it in a few words elsewhere.")
    return out


def company_lean(brief):
    """Supportive, Against or Mixed from the COMPANY LEAN line. Mixed if it cannot be read."""
    m = re.match(r"\s*-?\s*(Supportive|Against|Mixed):", sections(brief).get("COMPANY LEAN", ""), re.I)
    return m.group(1).capitalize() if m else "Mixed"


def judge_decision(data, d, horizon, setup_text="", table=""):
    instructions = DECISION_INSTRUCTIONS.replace("{SETUP}", setup_text).replace("{TABLE}", table)
    return _stream(instructions, horizon, _debate_text(data, d), "decision")


def judge_points(data, d, horizon):
    return _stream(POINTS_INSTRUCTIONS, horizon, _debate_text(data, d), "points")


def judge_checks(data, d, horizon):
    return _stream(CHECKS_INSTRUCTIONS, horizon, _debate_text(data, d), "checks")


def judge(data, d, horizon, status="No setup", setup_text="", table=""):
    # Runs the decision judge and the two details judges at the same time, in separate
    # threads, so the wait is the slowest of them rather than all added up.
    # status, setup_text and table come from setup_status.py: the code's setup status, its
    # checks, and which evidence label each company lean leads to.
    out = {}

    errors = []
    decide = lambda data, d, horizon: judge_decision(data, d, horizon, setup_text, table)  # noqa: E731

    def run(name, fn):
        start = time.perf_counter()
        try:
            # The wording check runs here too, so each judge's rewrite also happens in parallel.
            with step(f"{name} judge"):
                text = fn(data, d, horizon)
            out[name], _ = wording.fix(text, rewrite_text, f"{name} judge",
                                       extra_check=(lambda t: decision_problems(t, status)) if name == "decision" else None)
        except Exception as e:
            errors.append(e)
        out[f"{name}_seconds"] = time.perf_counter() - start

    threads = [
        threading.Thread(target=run, args=("decision", decide)),
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
