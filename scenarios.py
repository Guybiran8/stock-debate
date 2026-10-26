"""Every sentence about dollars won or lost is written here, from templates, never by AI.

All examples use SHARES shares bought at the latest price. Levels come from chart.py
(the same support and resistance the chart and the watchlist use).
"""
from chart import MAX_RISK, RISK_FLOOR_ATR, candle_date

SHARES = 10


def _d(x):
    return f"${x:,.2f}" if abs(x) < 1000 else f"${x:,.0f}"


def _gain(x):
    return f"+{_d(x)}" if x >= 0 else f"-{_d(-x)}"


def _support_label(kind):
    # The nearest level can be a tested support, a swing low or the 20-day average (chart.risk_level).
    if kind == "20-day average":
        return "the 20-day average, the average closing price over the last 20 trading days"
    if kind.startswith("most recent swing low"):
        return "the most recent swing low, a low point where the price turned back up"
    return "the nearest support, a price where buyers stepped in before"


def target_and_stop(chart):
    """(up price, up label, down price, down label) for the main example."""
    price = chart["price"]
    if chart["at_highs"]:
        up = price + 2 * chart["atr"]
        up_label = ("a guess of two normal days of movement above today, because it is near its highest price "
                    "of the past year and no past ceiling sits above it")
    else:
        up, kind = chart["resistances"][0]
        up_label = ("the next resistance, a price where sellers stepped in before" if "touched" in kind
                    else "its highest price of the past year")
    floor = price - RISK_FLOOR_ATR * chart["atr"]
    if chart["supports"] and chart["supports"][0][0] > floor:
        # Same rule as the watchlist: never count the risk as less than one normal day's move.
        down = floor
        down_label = (f"one normal day's move down; {_support_label(chart['supports'][0][1])}, "
                      f"is at {_d(chart['supports'][0][0])}, closer than that")
    elif chart["supports"]:
        down = chart["supports"][0][0]
        down_label = _support_label(chart["supports"][0][1])
    else:
        down = price * (1 - MAX_RISK)
        down_label = (f"a {MAX_RISK * 100:.0f}% drop, because there is no support (a price where buyers stepped in "
                      f"before) within {MAX_RISK * 100:.0f}% below")
    return up, up_label, down, down_label


def scenario_tiles(chart):
    """[(price, percent move, label), ...] for the up and down scenarios, plus the gain-per-$1-of-loss
    ratio (None when either side is empty). Same prices as main_sentence."""
    price = chart["price"]
    up, up_label, down, down_label = target_and_stop(chart)
    ratio = (up - price) / (price - down) if price > down and up > price else None
    return [(up, up / price - 1, "Up scenario: " + up_label),
            (down, down / price - 1, "Down scenario: " + down_label)], ratio


def main_sentence(chart, shares=SHARES, labels=False):
    """The 10-share example. The chart lane shows it under the scenario tiles, which already say what
    each price is, so only the full details (labels=True) repeat that."""
    price = chart["price"]
    up, up_label, down, down_label = target_and_stop(chart)
    start = f"If you buy {shares} shares at {_d(price)} (about {_d(price * shares)} in total): "
    if not labels:
        return start + f"about {_gain((up - price) * shares)} at {_d(up)} and about {_gain((down - price) * shares)} at {_d(down)}."
    return (start + f"about {_gain((up - price) * shares)} if it reaches {_d(up)} ({up_label}), and about "
            f"{_gain((down - price) * shares)} if it falls to {_d(down)} ({down_label}).")


def money_lines(chart, horizon, target=None, earnings=None, shares=SHARES):
    """The full money section. earnings is (average move, number of reports, next date or None)."""
    price, daily = chart["price"], chart["daily"]
    up, _, down, _ = target_and_stop(chart)
    lines = [main_sentence(chart, shares, labels=True)]
    if price > down and up > price:
        lines.append(f"That is about {_d((up - price) / (price - down))} of possible gain for every $1 of possible loss "
                     "between those two prices.")
    lines.append(f"On a normal day the price moves about {_d(chart['atr'])} per share (its average true range), "
                 f"which is about {_d(chart['atr'] * shares)} up or down on {shares} shares.")
    if earnings:
        avg, n, nxt = earnings
        when = f"The next report is on {nxt:%b %d, %Y}. " if nxt else ""
        lines.append(f"{when}After its last {n} earnings reports (the company's results for the last three months) "
                     f"it moved {avg * 100:.1f}% on average in one day. The same move from today would be about "
                     f"{_d(price * avg * shares)} up or down on {shares} shares.")
    year = daily[-253:]
    moves = [(abs(c["c"] / p["c"] - 1), c["c"] / p["c"] - 1, candle_date(c)) for p, c in zip(year, year[1:])]
    if moves:
        _, big, day = max(moves)
        lines.append(f"Its biggest one-day move in the past year was {big * 100:+.1f}% on {day:%b %d, %Y}. "
                     f"A move that size from today would be about {_gain(price * big * shares)} on {shares} shares.")
    if horizon == "long":
        if target:
            lines.append(f"If it reached {_d(target)}, the average price target of analysts (people at banks who "
                         f"study the company; a target is where they think the price will be in about a year, an "
                         f"opinion that is often wrong): about {_gain((target - price) * shares)} on {shares} shares.")
        lines.append(f"If it fell back to its lowest price of the past year, {_d(chart['low52'])}: about "
                     f"{_gain((chart['low52'] - price) * shares)} on {shares} shares.")
    lines.append("These are examples from past price levels, not predictions. You can lose more than the "
                 "drop shown if the price falls through a level.")
    return lines
