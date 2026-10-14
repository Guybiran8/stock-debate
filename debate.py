from config import CLAUDE_MODEL, OPENAI_MODEL, chatgpt, claude
from research import HORIZONS

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


def ask_chatgpt(system, prompt):
    r = chatgpt.responses.create(
        model=OPENAI_MODEL,
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
        "Argue for that time frame and use the evidence that matters most for it."
    )

    # Openings: each side writes without seeing the other.
    bull = ask_bull(
        "You are a bull analyst. Make the strongest honest case FOR buying this stock, "
        "using the numbers, the chart data and the research. Max 230 words. " + frame,
        data,
    )
    bear = ask_bear(
        "You are a bear analyst. Make the strongest honest case AGAINST buying this stock, "
        "using the numbers, the chart data and the research. Max 230 words. " + frame,
        data,
    )

    # Replies: each side answers the other's opening. Neither sees the other's reply.
    bull_reply = ask_bull(
        "You are the bull analyst. Below is your opening case and the bear's opening case. "
        "Attack the weakest points in the bear case and call out any number or fact it got wrong. "
        "Concede only the points where the bear is actually right. Max 170 words. " + frame,
        f"{data}\n\nYour bull case:\n{bull}\n\nBear case:\n{bear}",
    )
    bear_reply = ask_bear(
        "You are the bear analyst. Below is your opening case and the bull's opening case. "
        "Attack the weakest points in the bull case and call out any number or fact it got wrong. "
        "Concede only the points where the bull is actually right. Max 170 words. " + frame,
        f"{data}\n\nYour bear case:\n{bear}\n\nBull case:\n{bull}",
    )

    return {
        "bull": bull, "bear": bear,
        "bull_reply": bull_reply, "bear_reply": bear_reply,
        "bull_name": NAMES[bull_key], "bear_name": NAMES[bear_key],
    }
