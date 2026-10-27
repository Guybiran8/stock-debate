"""Setup status and evidence, decided by code from the daily candles. Pure code, no AI.
The AI only explains the result; it never picks the status.

SETUP STATUS (checked in this order)
1. Confirmed breakout: in the last BREAKOUT_DAYS sessions the price closed above the pivot it was
   under before (the nearest tested resistance, or the 52-week high), on at least BREAKOUT_VOLUME
   times its 20-day average volume. Today's close is still above that pivot, and the price is
   above the 50-day average, which is above the 200-day.
2. Setup failed: on one of the last FAILED_DAYS sessions the stock was an early setup or a
   confirmed breakout, and today's close is below that day's fail level. Worked out by running
   the same checks on each of those days, so nothing has to be stored.
3. Early setup: all three of
     a. uptrend: price > 50-day > 200-day (the watchlist's minimum for "Watch closely")
     b. near the pivot: within NEAR_PIVOT under resistance, or within AT_HIGHS of the 52-week high
     c. a fail level: the watchlist's risk level (nearest of tested support, swing low, 20-day
        average), within MAX_RISK below price
   plus at least 2 of these 3 tightening checks:
     d. range tightening: 10-day average range / 50-day average range <= MAX_CONTRACTION
     e. higher lows: each of the last SWING_COUNT swing lows in SWING_LOOKBACK days is above the one before
     f. volume drying up: 10-day average volume / 50-day average volume < 1
4. No setup: everything else.
A confirmed breakout's fail level is the pivot itself: a close back under it ends the breakout.

EVIDENCE
The judge gives only a company lean: Supportive, Against or Mixed. Then:
- Too early to tell if a dated event is within EVENT_DAYS calendar days and its average one-day move
  is bigger than the distance to the nearest level (the pivot above or the fail level below).
  An event with no known average move does not trigger this.
- The chart side is positive for an early setup or a confirmed breakout, negative for a failed
  setup or a downtrend (price < 50-day < 200-day), and neutral otherwise.
- Positive with Supportive, or negative with Against: agree. Positive with Against, or negative
  with Supportive: disagree. A Mixed lean or a neutral chart: too early to tell.

Levels and limits come from chart.py, the same ones the watchlist scan uses.
"""
from chart import (AT_HIGHS, MAX_CONTRACTION, MAX_RISK, avg_range, candle_date, find_levels,
                   recent_swing_low, risk_level, sma, swing_lows, volume_trend)

EARLY, BREAKOUT, FAILED, NONE = "Early setup", "Confirmed breakout", "Setup failed", "No setup"
AGREE, DISAGREE, TOO_EARLY = "Chart and company agree", "Chart and company disagree", "Too early to tell"
LEANS = ("Supportive", "Against", "Mixed")

NEAR_PIVOT = 0.05
BREAKOUT_VOLUME = 1.4
BREAKOUT_DAYS = 5
FAILED_DAYS = 10
SWING_COUNT = 3
SWING_LOOKBACK = 60
MIN_TIGHTENING = 2
EVENT_DAYS = 10            # same as the watchlist's EARNINGS_SOON_DAYS
MIN_DAYS = 210             # a 200-day average plus a little room


def _day(c):
    return c["d"] if "d" in c else candle_date(c)


def _d(x):
    return f"${x:,.2f}"


def _event(next_event):
    # Event names come as "the next earnings report" or "FDA decision"; the templates add their own "the".
    name = next_event[1]
    return name[4:] if name.lower().startswith("the ") else name


def _pivot(year, price):
    """(price, label) of the line a breakout has to clear, from the candles up to today."""
    _, resistances = find_levels(year, price)
    high52 = max(c["h"] for c in year)
    if price >= high52 * (1 - AT_HIGHS) or not resistances:
        return high52, "52-week high"
    return resistances[0][0], f"resistance, touched {resistances[0][1]} times"


def _breakout(daily, s50, s200):
    """The most recent close above its earlier pivot in the last BREAKOUT_DAYS sessions, or None."""
    price = daily[-1]["c"]
    if not (price > s50 > s200):
        return None
    for back in range(1, BREAKOUT_DAYS + 1):
        i = len(daily) - back                    # the breakout day
        before = daily[:i]                       # candles up to the day before
        if len(before) < 60:
            break
        prev = before[-1]["c"]
        pivot, label = _pivot(before[-252:], prev)
        if not (prev <= pivot < daily[i]["c"]) or price <= pivot:
            continue
        vol20 = sum(c["v"] for c in before[-20:]) / 20
        ratio = daily[i]["v"] / vol20 if vol20 else 0
        if ratio >= BREAKOUT_VOLUME:
            return {"pivot": (pivot, label), "day": _day(daily[i]), "volume": ratio}
    return None


def _checks(daily):
    """Every check for the last candle in daily, without the failed-setup look-back."""
    closes = [c["c"] for c in daily]
    price, year = closes[-1], daily[-252:]
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    supports, _ = find_levels(year, price)
    pivot = _pivot(year, price)
    swing = recent_swing_low(year, price)
    swing = (swing[0], f"swing low on {_day(year[swing[1]]):%b %d}") if swing else None
    risk = risk_level(price, supports, swing, s20)
    contraction = avg_range(daily, 10) / avg_range(daily, 50)
    lows = swing_lows(daily, SWING_LOOKBACK)[-SWING_COUNT:]
    rising = len(lows) == SWING_COUNT and all(a[0] < b[0] for a, b in zip(lows, lows[1:]))
    vol = volume_trend(daily)
    at_highs = price >= max(c["h"] for c in year) * (1 - AT_HIGHS)
    dist = (pivot[0] - price) / price
    out = {
        "price": price, "s50": s50, "s200": s200, "pivot": pivot, "dist_pivot": dist,
        "fail": risk, "contraction": contraction, "vol_trend": vol,
        "lows": [(low, _day(daily[i])) for low, i in lows],
        "uptrend": price > s50 > s200, "downtrend": price < s50 < s200,
        "near": at_highs or 0 <= dist <= NEAR_PIVOT,
        "tight_range": contraction <= MAX_CONTRACTION, "higher_lows": rising,
        "quiet_volume": vol is not None and vol < 1,
    }
    out["tightening"] = sum((out["tight_range"], out["higher_lows"], out["quiet_volume"]))
    out["breakout"] = _breakout(daily, s50, s200)
    if out["breakout"]:
        out["status"], out["fail"] = BREAKOUT, (out["breakout"]["pivot"][0], "the breakout line")
    elif out["uptrend"] and out["near"] and risk and out["tightening"] >= MIN_TIGHTENING:
        out["status"] = EARLY
    else:
        out["status"] = NONE
    return out


def evaluate(daily):
    """The setup for the last candle. Returns the checks dict with "status", "rows" (the checklist
    the report shows), "reason" and "failed" (the earlier setup it broke, or None)."""
    if len(daily) < MIN_DAYS:
        return {"status": NONE, "rows": [], "failed": None, "fail": None, "pivot": None,
                "reason": f"Only {len(daily)} trading days of prices, so no setup can be measured."}
    now = _checks(daily)
    now["failed"] = None
    if now["status"] == NONE:
        for back in range(1, FAILED_DAYS + 1):
            then = _checks(daily[:-back])
            if then["status"] in (EARLY, BREAKOUT) and then["fail"] and now["price"] < then["fail"][0]:
                now["status"] = FAILED
                now["failed"] = {"was": then["status"], "day": _day(daily[-back - 1]), "fail": then["fail"]}
                break
    now["rows"] = rows(now)
    now["reason"] = reason(now)
    return now


def rows(s):
    """The checklist: (name, rule, value, passed)."""
    p = s["pivot"]
    lows = " → ".join(f"{_d(low)} {day:%b %d}" for low, day in s["lows"]) or "none in the last 60 days"
    return [
        ("Uptrend", "price above the 50-day average, and the 50-day above the 200-day",
         f"{_d(s['price'])} vs {_d(s['s50'])} vs {_d(s['s200'])}", s["uptrend"]),
        ("Near resistance", f"within {NEAR_PIVOT:.0%} under resistance, or within {AT_HIGHS:.0%} of the 52-week high",
         (f"the {p[1].split(',')[0]} of {_d(p[0])} is {s['dist_pivot']:.1%} above the price" if s["dist_pivot"] >= 0
          else f"price is above the {p[1].split(',')[0]} of {_d(p[0])}"), s["near"]),
        ("Fail level nearby", f"a support level within {MAX_RISK:.0%} below price",
         f"{_d(s['fail'][0])}, {(s['price'] - s['fail'][0]) / s['price']:.1%} below" if s["fail"] else "none", bool(s["fail"])),
        ("Range tightening", f"10-day average daily range ÷ 50-day average range is {MAX_CONTRACTION:.2f} or less",
         f"{s['contraction']:.2f}", s["tight_range"]),
        ("Higher lows", f"each of the last {SWING_COUNT} swing lows is above the one before", lows, s["higher_lows"]),
        ("Volume drying up", "10-day average volume ÷ 50-day average is under 1.0",
         f"{s['vol_trend']:.2f}" if s["vol_trend"] is not None else "n/a", s["quiet_volume"]),
    ]


def reason(s):
    """One plain sentence on why the status is what it is."""
    if s["status"] == BREAKOUT:
        b = s["breakout"]
        return (f"It closed above the {b['pivot'][1].split(',')[0]} of {_d(b['pivot'][0])} on {b['day']:%b %d} "
                f"on {b['volume']:.1f} times its normal volume, and is still above it.")
    if s["status"] == FAILED:
        f = s["failed"]
        if f["was"] == BREAKOUT:
            return (f"It was a confirmed breakout on {f['day']:%b %d}, and has since closed back below "
                    f"its breakout level of {_d(f['fail'][0])}, which ends a breakout.")
        return (f"It was an early setup on {f['day']:%b %d}, and has since closed below "
                f"the fail level that setup had then, {_d(f['fail'][0])}.")
    passed = sum(r[3] for r in s["rows"])
    if s["status"] == EARLY:
        return (f"{passed} of the 6 checks pass, including {s['tightening']} of the 3 tightening checks "
                "(range, lows, volume).")
    needed = [r[0].lower() for r in s["rows"][:3] if not r[3]]
    if needed:
        return f"{passed} of the 6 checks pass. Missing: {', '.join(needed)}."
    some = f"only {s['tightening']} of the 3" if s["tightening"] else "none of the 3"
    return (f"{passed} of the 6 checks pass, but {some} tightening checks "
            f"(range, lows, volume); a setup needs {MIN_TIGHTENING}.")


def changes(s, next_event=None):
    """What would change the status: code-written lines with the exact levels."""
    if not s.get("rows"):
        return []
    out, pivot, fail = [], s["pivot"], s["fail"]
    if s["status"] in (NONE, FAILED):
        need = []
        if not s["uptrend"]:
            need.append(f"the price gets above the 50-day average ({_d(s['s50'])}) with the 50-day above the "
                        f"200-day ({_d(s['s200'])})")
        if not s["near"]:
            need.append(f"the price gets within {NEAR_PIVOT:.0%} of {_d(pivot[0])}")
        if not fail:
            need.append(f"a support level forms within {MAX_RISK:.0%} below the price")
        tight = []
        if not s["tight_range"]:
            tight.append(f"daily moves shrink to {MAX_CONTRACTION:.2f} of normal or less (now {s['contraction']:.2f})")
        if not s["higher_lows"]:
            tight.append(f"each of the next swing lows stops higher than the last one"
                         + (f" ({_d(s['lows'][-1][0])})" if s["lows"] else ""))
        if not s["quiet_volume"] and s["vol_trend"] is not None:
            tight.append(f"trading gets quieter than its 50-day average (now {s['vol_trend']:.2f} times)")
        short = MIN_TIGHTENING - s["tightening"]
        if short > 0:
            need.append(("" if short == len(tight) else f"{short} of these: ") + "; ".join(tight))
        if need:
            out.append("Becomes an early setup if " + ", and ".join(need) + ".")
    if s["status"] != BREAKOUT:
        out.append(f"Becomes a confirmed breakout on a close above {_d(pivot[0])} on at least "
                   f"{BREAKOUT_VOLUME} times its 20-day average volume.")
    if s["status"] in (EARLY, BREAKOUT) and fail:
        out.append(f"The setup fails on a close below {_d(fail[0])} ({fail[1]}).")
    elif fail and s["uptrend"]:
        out.append(f"The uptrend weakens on a close below {_d(fail[0])} ({fail[1]}).")
    if next_event:
        out.append(f"The {_event(next_event)} on {next_event[0]:%b %d, %Y} can do any of these in one day "
                   "(see What changes the picture).")
    return out


def nearest_level(s):
    """Distance from price to the nearer of the pivot above and the fail level below, or None."""
    gaps = []
    if s.get("pivot") and s["pivot"][0] > s["price"]:
        gaps.append(s["pivot"][0] / s["price"] - 1)
    if s.get("fail"):
        gaps.append(1 - s["fail"][0] / s["price"])
    return min(gaps) if gaps else None


def event_override(s, next_event, event_move, today):
    """The too-early reason when a near event usually moves the stock more than the nearest level, else None."""
    if not next_event or event_move is None or not s.get("rows"):
        return None
    days = (next_event[0] - today).days
    gap = nearest_level(s)
    if 0 <= days <= EVENT_DAYS and gap is not None and event_move > gap:
        return (f"the {_event(next_event)} on {next_event[0]:%b %d} usually moves it {event_move:.1%} in a day, more than "
                f"the {gap:.1%} to the nearest level")
    return None


def chart_side(s):
    if s["status"] in (EARLY, BREAKOUT):
        return "positive"
    if s["status"] == FAILED or s.get("downtrend"):
        return "negative"
    return "neutral"


def evidence(s, lean, next_event=None, event_move=None, today=None):
    """(label, reason) from the code's setup and the judge's company lean."""
    override = event_override(s, next_event, event_move, today) if today else None
    side = chart_side(s)
    chart_word = {EARLY: "early setup", BREAKOUT: "confirmed breakout", FAILED: "setup failed",
                  NONE: "no setup"}[s["status"]] + (", downtrend" if s["status"] == NONE and s.get("downtrend") else "")
    base = f"Chart: {chart_word}. Company: {(lean or 'Mixed').lower()}."
    if override:
        return TOO_EARLY, f"{base} Too early because {override}."
    if lean not in ("Supportive", "Against") or side == "neutral":
        return TOO_EARLY, base
    agree = (side == "positive") == (lean == "Supportive")
    return (AGREE if agree else DISAGREE), base


def evidence_table(s, next_event=None, event_move=None, today=None):
    """What the evidence label will be for each company lean, with the code's reason, for the judge's prompt."""
    return "\n".join(f"- If COMPANY LEAN is {lean}: {' '.join(evidence(s, lean, next_event, event_move, today))}"
                     for lean in LEANS)


def text(s):
    """The setup block for the AI's PRICE CHART DATA."""
    lines = [f"SETUP STATUS (decided by code, do not change it): {s['status']}. {s['reason']}"]
    for name, rule, value, ok in s.get("rows", []):
        lines.append(f"- {name} ({rule}): {value}. {'Pass' if ok else 'Fail'}.")
    if s.get("pivot"):
        lines.append(f"- Breakout line: {_d(s['pivot'][0])} ({s['pivot'][1]}).")
    if s.get("fail"):
        lines.append(f"- Fail level: {_d(s['fail'][0])} ({s['fail'][1]}).")
    return "\n".join(lines)
