from concurrent.futures import ThreadPoolExecutor

import wording
from config import CLAUDE_MODEL, OPENAI_MODEL, REWRITE_MODEL, chatgpt, claude
from research import HORIZONS
from timing import step

BULL_MODEL = "claude"   # change to "chatgpt" to swap sides

RULES = """
Use ONLY the DATA block and the RESEARCH block for facts and figures.
Never invent a number or a fact. If something you need is in neither block, say "not in the research".
If a number in DATA conflicts with a number from a company filing in RESEARCH, the filing wins.
Use the filing number and say that the two sources conflict.
Posts from X are opinions and rumors. You may describe the mood, but never use a post as proof of a fact.
Read the HOW TO READ sections before interpreting any number.
Use only the chart levels given in PRICE CHART DATA. Never invent a price level.
Argue about the stock itself. Do not build your case on what is missing from the data.
Focus on what actually moves the price, and say when it could happen.
Write for someone who has never bought a stock. Use short sentences and common words, about
9th-grade level. The first time you use a financial term, explain it in a few plain words in
brackets, for example "support (a price where buyers stepped in before)".
Never work out how many dollars someone would win or lose. The report adds those itself.
Price levels and percent moves are fine.
The SETUP STATUS in PRICE CHART DATA is decided by code from the chart rules. Never argue about
what the status is. Argue what it means and whether the company evidence backs it up.
Never give the stock a rating or tell the reader to buy, sell, hold or watch it.
Write in plain text with no markdown symbols.
"""


def ask_claude(system, prompt):
    r = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=system + "\n" + RULES,
        messages=[{"role": "user", "content": prompt}],
    )
    # Keep only the answer text. Skip any reasoning blocks the model sends back.
    return "".join(block.text for block in r.content if block.type == "text").strip()


def ask_chatgpt(system, prompt, model=OPENAI_MODEL):
    r = chatgpt.responses.create(
        model=model,
        instructions=system + "\n" + RULES,
        input=prompt,
    )
    return r.output_text


ASK = {"claude": ask_claude, "chatgpt": ask_chatgpt}
NAMES = {"claude": "Claude", "chatgpt": "ChatGPT"}


def run_debate(data, horizon):
    bull_key = BULL_MODEL
    bear_key = "chatgpt" if bull_key == "claude" else "claude"
    ask_bull, ask_bear = ASK[bull_key], ASK[bear_key]
    frame = (
        f"The reader is considering {HORIZONS[horizon]}. "
        "Argue for that time frame. Make at most 3 points about the company, the ones that matter most "
        "for this stock and time frame, plus what the chart setup means."
    )

    def edit(ask):
        # Rewrites stay with the side's own company. ChatGPT's use OpenAI's fast model.
        fast = (lambda s, p: ask_chatgpt(s, p, REWRITE_MODEL)) if ask is ask_chatgpt else ask
        return lambda prompt: fast("You are an editor who makes stock writing easy to read.", prompt)

    def speak(label, ask, system, prompt):
        # One speech plus its wording check. Any rewrite is done by the model that wrote it.
        with step(label):
            text = ask(system, prompt)
        return wording.fix(text, edit(ask), label)[0]

    # Openings: each side writes without seeing the other, so both run at the same time.
    with ThreadPoolExecutor(2) as pool:
        bull_job = pool.submit(
            speak, "bull case", ask_bull,
            "You are a bull analyst. Make the strongest honest case FOR owning this stock, "
            "using the numbers, the chart data and the research. Max 230 words. " + frame,
            data,
        )
        bear_job = pool.submit(
            speak, "bear case", ask_bear,
            "You are a bear analyst. Make the strongest honest case AGAINST owning this stock, "
            "using the numbers, the chart data and the research. Max 230 words. " + frame,
            data,
        )
        bull, bear = bull_job.result(), bear_job.result()

    # Replies: each side answers the other's opening. Neither sees the other's reply.
    with ThreadPoolExecutor(2) as pool:
        bull_job = pool.submit(
            speak, "bull reply", ask_bull,
            "You are the bull analyst. Below is your opening case and the bear's opening case. "
            "Attack the weakest points in the bear case and call out any number or fact it got wrong. "
            "Concede only the points where the bear is actually right. Max 170 words. " + frame,
            f"{data}\n\nYour bull case:\n{bull}\n\nBear case:\n{bear}",
        )
        bear_job = pool.submit(
            speak, "bear reply", ask_bear,
            "You are the bear analyst. Below is your opening case and the bull's opening case. "
            "Attack the weakest points in the bull case and call out any number or fact it got wrong. "
            "Concede only the points where the bull is actually right. Max 170 words. " + frame,
            f"{data}\n\nYour bear case:\n{bear}\n\nBull case:\n{bull}",
        )
        bull_reply, bear_reply = bull_job.result(), bear_job.result()

    return {
        "bull": bull, "bear": bear,
        "bull_reply": bull_reply, "bear_reply": bear_reply,
        "bull_name": NAMES[bull_key], "bear_name": NAMES[bear_key],
    }
