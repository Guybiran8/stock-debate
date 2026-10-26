"""Private price-accuracy alarm. Runs on the VPS after the daily scan and its live check: compares the
latest Yahoo close of some watchlist stocks with Tiingo's end-of-day close and sends a Telegram message
(same bot as scan_check.py) if a close differs by more than MAX_GAP or the dates don't match.

Tiingo's free plan is for internal use only, so its prices go to the log and the Telegram message and
nowhere else: this script writes nothing to the repo, the portfolio or the reports.

HOW MANY STOCKS
- Free plan: 50 requests an hour, 500 symbols a month. Each stock is one request.
- Each run checks half the watchlist (rounded up), at most MAX_PER_RUN (45), so every stock is checked
  every 2 runs. The ones checked longest ago (or never) go first.
- Request times and this month's symbols are kept in STATE_FILE, so a manual run and the scheduled one
  together stay under MAX_PER_HOUR, and a new symbol is skipped once MAX_SYMBOLS_MONTH is reached.

WHICH CLOSE
- The scan runs at 17:15 New York time, before Tiingo usually has the day's close (about 17:30).
  So on a weekday between 16:00 and SETTLED (18:00) the check waits until SETTLED.
- Before SETTLED the check uses the previous session; today's Yahoo bar is dropped.

If Tiingo or Yahoo fails, the error type is logged and the run moves on. It always exits 0, so it never
fails the scan. The Tiingo key (TIINGO_API_KEY in .env) is sent in a header and never printed.

Usage: price_check.py [--no-wait] [--no-alert]
"""
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).parent
STATE_FILE = Path.home() / ".local/state/stock-debate/price-check.json"
NY = ZoneInfo("America/New_York")

MAX_GAP = 0.01               # 1%
MAX_PER_RUN = 45
MAX_PER_HOUR = 45            # Tiingo allows 50; a few spare for a manual look
MAX_SYMBOLS_MONTH = 450      # Tiingo allows 500
SETTLED = dtime(18, 0)
MARKET_CLOSE = dtime(16, 0)
PAUSE_SECONDS = 1.0


def tiingo_key(path=ROOT / ".env"):
    from dotenv import dotenv_values
    return dotenv_values(path).get("TIINGO_API_KEY")


def load_state(path=STATE_FILE):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state, path=STATE_FILE):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def recent_requests(state, now):
    return [t for t in state.get("requests", []) if now - t < 3600]


def month_symbols(state, today):
    m = state.get("month", {})
    return set(m.get("symbols", [])) if m.get("month") == f"{today:%Y-%m}" else set()


def pick_tickers(tickers, state, now, today):
    """The stocks to check this run: half the list (rounded up), stalest first, within the hourly
    request budget and the monthly symbol budget."""
    budget = min(MAX_PER_RUN, math.ceil(len(tickers) / 2), MAX_PER_HOUR - len(recent_requests(state, now)))
    checked = state.get("checked", {})
    order = sorted(range(len(tickers)), key=lambda i: (checked.get(tickers[i], ""), i))
    symbols, out = month_symbols(state, today), []
    for i in order:
        t = tickers[i]
        if len(out) >= budget:
            break
        if t not in symbols and len(symbols) >= MAX_SYMBOLS_MONTH:
            continue
        symbols.add(t)
        out.append(t)
    return out


def last_settled_day(now_et):
    """Bars dated after this are left out: today's bar only counts once Tiingo has had time to post it."""
    return now_et.date() if now_et.time() >= SETTLED else now_et.date() - timedelta(days=1)


def wait_until_settled(now_et, sleep=time.sleep):
    if now_et.weekday() < 5 and MARKET_CLOSE <= now_et.time() < SETTLED:
        seconds = (datetime.combine(now_et.date(), SETTLED, NY) - now_et).total_seconds()
        print(f"price check: waiting {seconds / 60:.0f} min until {SETTLED:%H:%M} New York time for Tiingo's close")
        sleep(seconds)
        return True
    return False


def yahoo_closes(tickers, last_day):
    """{ticker: (date, close)} for the newest Yahoo bar on or before last_day."""
    import yfinance as yf
    frame = yf.download(tickers, period="10d", interval="1d", auto_adjust=False, group_by="ticker",
                        progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            closes = frame[t]["Close"].dropna()
        except KeyError:
            continue
        closes = closes[[d.date() <= last_day for d in closes.index]]
        if len(closes):
            out[t] = (closes.index[-1].date(), float(closes.iloc[-1]))
    return out


def tiingo_closes(ticker, key, start, get=requests.get):
    """{date: close} from Tiingo's end-of-day prices since start. Raises on an HTTP or format error."""
    r = get(f"https://api.tiingo.com/tiingo/daily/{ticker.replace('.', '-')}/prices",
            params={"startDate": start.isoformat()},
            headers={"Authorization": f"Token {key}", "Content-Type": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return {date.fromisoformat(row["date"][:10]): float(row["close"]) for row in r.json()}


def compare(ticker, yahoo, tiingo):
    """A problem line, or None if the latest dates match and the closes are within MAX_GAP."""
    y_day, y_close = yahoo
    if not tiingo:
        return f"{ticker}: Yahoo ${y_close:.2f} ({y_day:%b} {y_day.day}), Tiingo has no prices"
    t_day = max(tiingo)
    # Same-day prices when Tiingo has Yahoo's day, else Tiingo's latest
    on = y_day if y_day in tiingo else t_day
    t_close = tiingo[on]
    gap = y_close / t_close - 1
    if t_day == y_day and abs(gap) <= MAX_GAP:
        return None
    line = (f"{ticker}: Yahoo ${y_close:.2f} ({y_day:%b} {y_day.day}) vs Tiingo ${t_close:.2f} "
            f"({on:%b} {on.day}), gap {gap:+.1%}")
    if t_day != y_day:
        line += f"; dates differ, Tiingo's latest is {t_day:%b} {t_day.day}"
    return line


def alert_text(problems, checked, last_day):
    return "\n".join([f"Price check (closes up to {last_day:%b} {last_day.day}): {len(problems)} of {checked} "
                      f"stocks differ between Yahoo and Tiingo by more than {MAX_GAP:.0%} or on the date."]
                     + problems)


def run(tickers, now_et, key, state, yahoo=yahoo_closes, tiingo=tiingo_closes, alert=None, sleep=time.sleep,
        clock=time.time, pause=PAUSE_SECONDS):
    """Checks this run's stocks, updates state in place and returns the problem lines."""
    today = now_et.date()
    last_day = last_settled_day(now_et)
    picked = pick_tickers(tickers, state, clock(), today)
    if not picked:
        print("price check: no request budget left this hour or month; nothing checked")
        return []
    try:
        ycl = yahoo(picked, last_day)
    except Exception as e:
        print(f"price check: Yahoo download error ({type(e).__name__}); nothing checked")
        return []

    requests_log = recent_requests(state, clock())
    month = {"month": f"{today:%Y-%m}", "symbols": sorted(month_symbols(state, today))}
    problems, done, errors = [], 0, []
    for n, t in enumerate(picked):
        if t not in ycl:
            errors.append(f"{t} (no Yahoo close)")
            continue
        if n:
            sleep(pause)
        requests_log.append(clock())
        if t not in month["symbols"]:
            month["symbols"] = sorted(month["symbols"] + [t])
        try:
            closes = tiingo(t, key, ycl[t][0] - timedelta(days=7))
        except Exception as e:
            # The type, or the HTTP status, only: an exception's text could carry the request
            errors.append(f"{t} ({e if isinstance(e, RuntimeError) else type(e).__name__})")
            if "HTTP 429" in str(e):
                print("price check: Tiingo rate limit reached; stopping this run")
                break
            continue
        done += 1
        state.setdefault("checked", {})[t] = today.isoformat()
        line = compare(t, ycl[t], closes)
        if line:
            problems.append(line)
    state["requests"], state["month"] = requests_log, month

    print(f"price check: {done} of {len(picked)} checked against Tiingo, {len(problems)} differ")
    for line in problems:
        print(f"  {line}")
    if errors:
        print(f"price check: Tiingo errors (moved on): {', '.join(errors)}")
    if problems and alert:
        alert(alert_text(problems, done, last_day))
    return problems


def main(argv):
    try:
        import watchlist
        from scan_check import send_telegram
        key = tiingo_key()
        if not key:
            print("price check: no TIINGO_API_KEY in .env; skipped")
            return 0
        now_et = datetime.now(NY)
        if "--no-wait" not in argv and wait_until_settled(now_et):
            now_et = datetime.now(NY)
        state = load_state()
        run(watchlist.load_watchlist(), now_et, key, state, alert=None if "--no-alert" in argv else send_telegram)
        save_state(state)
    except Exception as e:
        print(f"price check: stopped by {type(e).__name__}; the scan is not affected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
