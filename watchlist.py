"""Daily watchlist scan. The scan is pure code with no AI calls. Full AI reports run only as
described under AUTO REPORTS. No trade execution, no entry or stop advice.

HOW EACH STOCK IS TAGGED
- Catalyst-driven if any of these is true:
    * Yahoo lists its industry as biotechnology or a drug maker (always catalyst-driven).
    * Its next earnings date (Yahoo) is within EARNINGS_SOON_DAYS calendar days.
    * It moved BIG_MOVE or more in a single day during the last 5 sessions.
- Otherwise technical-driven.

WHAT COUNTS AS A VERIFIED CATALYST DATE
- A row in catalysts.csv (ticker, date, event, source_url) with a future date and a source URL.
- For stocks that are not biotech or drug makers, a Yahoo earnings date that Yahoo does
  not mark as an estimate also counts. For biotech and drug makers it does not, because
  their big moves come from trial results and FDA decisions, which Yahoo does not list.

HOW EACH STOCK IS GROUPED (checked in this order)
1. Skipped: missing data or fewer than MIN_HISTORY trading days. Reported, never scored.
2. Reject: average daily dollar volume (20 days) under MIN_DOLLAR_VOLUME.
3. Catalyst-driven with no verified catalyst date: Catalyst watch. Never Top.
4. Top candidates: passes every one of these:
     a. price > 20-day > 50-day > 200-day average
     b. 50-day and 200-day averages both rising
     c. beating SPY over both 20 and 60 days
     d. room to move (upside distance / risk distance) >= MIN_ROOM, and a nearby risk level exists
     e. volatility contraction (10-day average range / 50-day average range) <= MAX_CONTRACTION
5. Catalyst-driven with a verified date that misses Top: Catalyst watch.
6. Watch closely: price above the 50-day and the 50-day above the 200-day, but it misses
   at least one Top rule. The rules it misses are listed.
7. Reject: everything else (no uptrend). The reasons are listed.
There is no quota. Any group can be empty.

AUTO REPORTS
- After the scan, the full short-term AI report runs on Top candidates only, highest score
  first. Score = room to move; ties go to the stronger 60-day result vs SPY.
- At most MAX_AUTO_REPORTS (2) per run and per day. Every started report counts, even one that
  fails, and the count is kept in reports/auto-reports-YYYY-MM-DD.txt. A stock already run
  today is not run again. With 0 Top candidates nothing runs, and the page says so.
- Run "python watchlist.py --no-reports" for the free scan only.

PUBLISHING
- Each run pulls ../portfolio (rebase) first, copies the watchlist page and new reports into it,
  rebuilds its reports pages, commits, pulls with rebase again and pushes. Before committing,
  every staged file goes through secret_scan.py (the .env key values and common key formats).
  If it finds anything, the files are unstaged and nothing is committed or pushed.
- reports.html, stocks/ and reports/watchlist* are built only by this scan. If a rebase conflicts
  only in those files, the scan's version is kept. A conflict in any other file aborts the rebase.
- Whenever a push was wanted but didn't happen, the run prints a FAILED line and exits with 1.
- Run with "--no-push" to update the portfolio files without committing or pushing.

ROOM TO MOVE
- Upside: distance to the nearest resistance above. A stock at or within AT_HIGHS (3%) of
  its 52-week high has no resistance above, so its upside is estimated as 2x the 14-day
  average true range and it is flagged "At highs / breakout". It can still reach Top.
  Otherwise, with no tested resistance, the 52-week high is used.
- Risk: the nearest, below price and no more than MAX_RISK (15%) below it, of:
  the closest tested support level (2+ touches), the most recent swing low (a day whose
  low is the lowest of the 5 days on each side), or the 20-day average.
  If none qualifies, the stock is marked "no nearby support" and cannot be Top.
  The risk distance is never less than RISK_FLOOR_ATR x the 14-day ATR, so a stock sitting
  just above its 20-day average does not get an inflated room number.
- Room = upside distance / risk distance.

Levels and averages reuse chart.py, so they match the full reports.
"""
import csv
import html
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

from data import clean_name
from chart import (AT_HIGHS, MAX_CONTRACTION, MAX_RISK, RISK_FLOOR_ATR, atr, avg_range,
                   find_levels, recent_swing_low, risk_level, sma, sma_series)

ROOT = Path(__file__).parent
WATCHLIST_FILE = ROOT / "watchlist.txt"
CATALYST_FILE = ROOT / "catalysts.csv"
REPORTS_DIR = ROOT / "reports"
BENCHMARK = "SPY"

MIN_HISTORY = 220            # 200-day average plus 20 days to measure its slope
MIN_DOLLAR_VOLUME = 10e6     # $10M a day
MIN_ROOM = 1.5
EARNINGS_SOON_DAYS = 10
BIG_MOVE = 0.15              # 15% in one day
MOVE_NOTE = 0.03             # a day's move this big is named on the stock's reports-page card
ATR_MULTIPLE = 2             # upside estimate at highs; AT_HIGHS, MAX_RISK, RISK_FLOOR_ATR and MAX_CONTRACTION come from chart.py
CATALYST_INDUSTRIES = ("biotechnology", "drug manufacturers", "pharmaceutical")

GROUPS = ["Top candidates", "Watch closely", "Catalyst watch", "Reject"]

MAX_AUTO_REPORTS = 2
PORTFOLIO_DIR = ROOT.parent / "portfolio"


def load_watchlist(path=WATCHLIST_FILE):
    seen, out = set(), []
    for line in Path(path).read_text(encoding="utf-8").split():
        t = line.strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def load_catalysts(path=CATALYST_FILE, today=None):
    # Only future-dated rows with a source URL count.
    today = today or date.today()
    out = {}
    if not Path(path).exists():
        return out
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat((row.get("date") or "").strip())
            except ValueError:
                continue
            url = (row.get("source_url") or "").strip()
            t = (row.get("ticker") or "").strip().upper()
            if t and url.startswith("http") and d >= today:
                if t not in out or d < out[t]["date"]:
                    out[t] = {"date": d, "event": (row.get("event") or "").strip(), "source": url}
    return out


def download_prices(tickers):
    # One batched Yahoo request for every ticker plus the benchmark.
    frame = yf.download(tickers + [BENCHMARK], period="2y", interval="1d", auto_adjust=True,
                        group_by="ticker", progress=False, threads=True)
    out = {}
    for t in tickers + [BENCHMARK]:
        try:
            df = frame[t].dropna(subset=["Open", "High", "Low", "Close"])
        except KeyError:
            out[t] = []
            continue
        out[t] = [
            {"d": idx.date(), "o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]),
             "c": float(r["Close"]), "v": float(r["Volume"]) if r["Volume"] == r["Volume"] else 0.0}
            for idx, r in df.iterrows()
        ]
    return out


def _profile(ticker):
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {"industry": None, "earnings": None, "earnings_estimate": None, "name": None}
    now = datetime.now(timezone.utc).timestamp()
    stamps = [info.get("earningsTimestampStart"), info.get("earningsTimestamp")]
    future = [s for s in stamps if s and s > now]
    earnings = datetime.fromtimestamp(min(future), timezone.utc).date() if future else None
    return {
        "industry": info.get("industry"),
        "earnings": earnings,
        "earnings_estimate": info.get("isEarningsDateEstimate") if earnings else None,
        "name": clean_name(info.get("shortName") or info.get("longName")),
    }


def fetch_profiles(tickers):
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(tickers, pool.map(_profile, tickers)))


def _ret(closes, n):
    return closes[-1] / closes[-1 - n] - 1


def compute_metrics(daily, spy):
    """All numbers come from the candles. Returns None if there is not enough data."""
    if len(daily) < MIN_HISTORY or len(spy) < 61:
        return None
    closes = [c["c"] for c in daily]
    price = closes[-1]
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    ser50, ser200 = sma_series(closes, 50), sma_series(closes, 200)

    # Relative strength uses the same dates for the stock and SPY.
    spy_by_day = {c["d"]: c["c"] for c in spy}
    paired = [(c["c"], spy_by_day[c["d"]]) for c in daily if c["d"] in spy_by_day]
    if len(paired) < 61:
        return None
    sc, bc = [p[0] for p in paired], [p[1] for p in paired]

    year = daily[-252:]
    high52, low52 = max(c["h"] for c in year), min(c["l"] for c in year)
    supports, resistances = find_levels(year, price)
    sup, sup_label = (supports[0][0], f"touched {supports[0][1]} times") if supports else (low52, "52-week low")
    day_range = atr(daily)
    at_highs = price >= high52 * (1 - AT_HIGHS)
    if at_highs:
        res, res_label = price + ATR_MULTIPLE * day_range, f"estimate: {ATR_MULTIPLE}x 14-day ATR, at highs"
    elif resistances:
        res, res_label = resistances[0][0], f"touched {resistances[0][1]} times"
    else:
        res, res_label = high52, "52-week high"
    to_res, to_sup = (res - price) / price, (price - sup) / price

    swing = recent_swing_low(year, price)
    swing = (swing[0], f"swing low on {year[swing[1]]['d']}") if swing else None
    risk = risk_level(price, supports, swing, s20)
    to_risk = (price - risk[0]) / price if risk else None
    risk_floored = bool(risk) and to_risk < RISK_FLOOR_ATR * day_range / price
    if risk_floored:
        to_risk = RISK_FLOOR_ATR * day_range / price
    room = to_res / to_risk if to_risk else None

    last_day = daily[-1]["d"]
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    partial = last_day == ny_now.date() and ny_now.weekday() < 5 and ny_now.time() < dtime(16, 0)
    vol20 = sum(c["v"] for c in daily[-21:-1]) / 20

    return {
        "price": price, "date": last_day, "partial_day": partial,
        "sma20": s20, "sma50": s50, "sma200": s200,
        "vs20": price / s20 - 1, "vs50": price / s50 - 1, "vs200": price / s200 - 1,
        "slope20": s20 / sma(closes[:-5], 20) - 1,
        "slope50": ser50[-1] / ser50[-11] - 1,
        "slope200": ser200[-1] / ser200[-21] - 1,
        "rs20": _ret(sc, 20) - _ret(bc, 20),
        "rs60": _ret(sc, 60) - _ret(bc, 60),
        "vol_ratio": daily[-1]["v"] / vol20 if vol20 else None,
        "contraction": avg_range(daily, 10) / avg_range(daily, 50),
        "support": sup, "support_label": sup_label, "to_support": to_sup,
        "resistance": res, "resistance_label": res_label, "to_resistance": to_res,
        "at_highs": at_highs, "high52": high52, "atr": day_range,
        "risk": risk[0] if risk else None, "risk_label": risk[1] if risk else "no nearby support",
        "to_risk": to_risk, "risk_floored": risk_floored, "room": room,
        "dollar_volume": sum(c["c"] * c["v"] for c in daily[-20:]) / 20,
        "max_move_5d": max(abs(closes[i] / closes[i - 1] - 1) for i in range(len(closes) - 5, len(closes))),
        "move_5d": max((closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 5, len(closes))), key=abs),
        "change_1d": price / closes[-2] - 1,
    }


def tag_stock(m, profile, today):
    industry = (profile.get("industry") or "").lower()
    reasons = []
    is_bio = any(k in industry for k in CATALYST_INDUSTRIES)
    if is_bio:
        reasons.append(f"industry: {profile['industry']}")
    e = profile.get("earnings")
    if e and 0 <= (e - today).days <= EARNINGS_SOON_DAYS:
        reasons.append(f"earnings {e}")
    if m["max_move_5d"] >= BIG_MOVE:
        reasons.append(f"{m.get('move_5d', m['max_move_5d']):+.0%} one-day move in last 5 sessions")
    return ("catalyst-driven" if reasons else "technical-driven"), is_bio, reasons


def verified_catalyst(ticker, is_bio, profile, catalysts):
    if ticker in catalysts:
        c = catalysts[ticker]
        return f"{c['date']} {c['event']} ({c['source']})"
    if not is_bio and profile.get("earnings") and profile.get("earnings_estimate") is False:
        return f"earnings {profile['earnings']} (Yahoo, not marked as estimate)"
    return None


def top_failures(m):
    fails = []
    if not (m["price"] > m["sma20"] > m["sma50"] > m["sma200"]):
        fails.append("averages not stacked (price > 20 > 50 > 200)")
    if m["slope50"] <= 0 or m["slope200"] <= 0:
        fails.append("50-day or 200-day not rising")
    if m["rs20"] <= 0 or m["rs60"] <= 0:
        fails.append("not beating SPY over both 20 and 60 days")
    if m["risk"] is None:
        fails.append(f"no nearby support (none within {MAX_RISK:.0%} below)")
    elif m["room"] is None or m["room"] < MIN_ROOM:
        fails.append(f"room to move under {MIN_ROOM}")
    if m["contraction"] > MAX_CONTRACTION:
        fails.append(f"range not contracting (over {MAX_CONTRACTION})")
    return fails


def classify(ticker, m, profile, catalysts, today):
    tag, is_bio, tag_reasons = tag_stock(m, profile, today)
    catalyst = verified_catalyst(ticker, is_bio, profile, catalysts) if tag == "catalyst-driven" else None
    flags = ["At highs / breakout"] if m.get("at_highs") else []
    base = {"ticker": ticker, "tag": tag, "tag_reasons": tag_reasons, "flags": flags, "catalyst": catalyst, "metrics": m}

    if m["dollar_volume"] < MIN_DOLLAR_VOLUME:
        return {**base, "group": "Reject", "why": [f"dollar volume under ${MIN_DOLLAR_VOLUME / 1e6:.0f}M a day"]}
    if tag == "catalyst-driven" and not catalyst:
        return {**base, "group": "Catalyst watch", "why": ["no verified catalyst date"]}
    fails = top_failures(m)
    if not fails:
        return {**base, "group": "Top candidates", "why": ["passes every Top rule"]}
    if tag == "catalyst-driven":
        return {**base, "group": "Catalyst watch", "why": fails}
    if m["price"] > m["sma50"] > m["sma200"]:
        return {**base, "group": "Watch closely", "why": fails}
    return {**base, "group": "Reject", "why": ["no uptrend (needs price > 50-day > 200-day)"] + fails}


def card_reason(r):
    """One short line for the stock's card on reports.html: why it is in its group, from the scan's own numbers."""
    m, parts = r["metrics"], []
    day = m.get("change_1d")
    if day is not None and abs(day) >= MOVE_NOTE:
        parts.append(f"{day:+.0%} today")
    for reason in r["tag_reasons"]:
        if reason.endswith("in last 5 sessions") and day is not None and abs(day) >= BIG_MOVE:
            continue   # the big move was today, already named above with its direction
        parts.append(reason)
    if m.get("vol_ratio") and m["vol_ratio"] >= 1.5:
        parts.append(f"volume {m['vol_ratio']:.1f}x normal")
    parts.append(("misses " if r["group"] == "Watch closely" else "") + "; ".join(r["why"]))
    return " · ".join(parts)


def score_all(tickers, prices, profiles, catalysts, today):
    """Returns (results, skipped). Every ticker ends up in exactly one of the two."""
    spy = prices.get(BENCHMARK) or []
    if len(spy) < 61:
        raise RuntimeError(f"No usable {BENCHMARK} data, so relative strength cannot be measured.")
    results, skipped = [], []
    for t in tickers:
        daily = prices.get(t) or []
        m = compute_metrics(daily, spy) if daily else None
        if m is None:
            skipped.append((t, f"only {len(daily)} trading days of data" if daily else "no price data"))
            continue
        results.append(classify(t, m, profiles.get(t) or {}, catalysts, today))
    return results, skipped


def _pct(x):
    return "n/a" if x is None else f"{x:+.1%}"


def stock_line(r):
    m = r["metrics"]
    room = "n/a" if m["room"] is None else f"{m['room']:.1f}"
    vol = "n/a" if m["vol_ratio"] is None else f"{m['vol_ratio']:.2f}x" + (" (partial day)" if m["partial_day"] else "")
    parts = [
        f"${m['price']:,.2f} on {m['date']}",
        f"vs 20/50/200-day {_pct(m['vs20'])} / {_pct(m['vs50'])} / {_pct(m['vs200'])}",
        f"slope 20/50/200 {_pct(m['slope20'])} / {_pct(m['slope50'])} / {_pct(m['slope200'])}",
        f"RS vs SPY 20d {_pct(m['rs20'])}, 60d {_pct(m['rs60'])}",
        f"volume {vol}",
        f"contraction {m['contraction']:.2f}",
        f"support ${m['support']:,.2f} ({m['support_label']}, {_pct(-m['to_support'])})",
        f"resistance ${m['resistance']:,.2f} ({m['resistance_label']}, {_pct(m['to_resistance'])})",
        ("risk: no nearby support" if m["risk"] is None
         else f"risk ${m['risk']:,.2f} ({m['risk_label']}), distance {_pct(-m['to_risk'])}"
              + (f" (floored at {RISK_FLOOR_ATR}x ATR)" if m.get("risk_floored") else "")),
        f"room {room}",
        f"$ volume ${m['dollar_volume'] / 1e6:,.0f}M/day",
    ]
    why = "; ".join(r["why"])
    tag = r["tag"] + (f" ({', '.join(r['tag_reasons'])})" if r["tag_reasons"] else "")
    tag += "".join(f" · {f}" for f in r.get("flags", []))
    cat = f" · catalyst: {r['catalyst']}" if r["catalyst"] else ""
    return (
        f"<li style='margin:6px 0'><b>{html.escape(r['ticker'])}</b> · {html.escape(tag)}{html.escape(cat)}"
        f"<br><span style='font-size:12px'>{html.escape(' · '.join(parts))}</span>"
        f"<br><span style='font-size:12px;opacity:.8'>Why: {html.escape(why)}</span></li>"
    )


def score(r):
    m = r["metrics"]
    return (m["room"] or 0, m["rs60"])


def auto_reports(results, today, runner, log_dir=REPORTS_DIR, limit=MAX_AUTO_REPORTS):
    """Runs runner(ticker) on Top candidates, best score first, within the daily limit.
    Returns a list of (result, outcome, report_path)."""
    top = sorted((r for r in results if r["group"] == "Top candidates"), key=score, reverse=True)
    log = Path(log_dir) / f"auto-reports-{today}.txt"
    done = log.read_text(encoding="utf-8").split() if log.exists() else []
    outcomes = []
    for r in top:
        t = r["ticker"]
        if t in done:
            outcomes.append((r, "already run today", None))
            continue
        if len(done) >= limit:
            outcomes.append((r, f"not run: daily limit of {limit} reached", None))
            continue
        # Count it before running, so a crash cannot lead to more than the limit.
        done.append(t)
        Path(log_dir).mkdir(exist_ok=True)
        log.write_text("\n".join(done) + "\n", encoding="utf-8")
        try:
            outcomes.append((r, "report run", runner(t)))
        except Exception as e:
            outcomes.append((r, f"report failed: {type(e).__name__}: {e}", None))
    return outcomes


def _run_full_report(ticker):
    from main import analyze  # imported here so the free scan never loads the AI clients
    return analyze(ticker, "short", open_browser=False)


def auto_section(results, outcomes, ran_reports):
    top = [r for r in results if r["group"] == "Top candidates"]
    if not ran_reports:
        body = "<li>Full reports were turned off for this run (--no-reports).</li>"
    elif not top:
        body = "<li>0 Top candidates today, so no full reports were run.</li>"
    else:
        items = []
        for r, outcome, path in outcomes:
            room, rs = score(r)
            link = f" · <a href='{html.escape(Path(path).name)}'>open report</a>" if path else ""
            items.append(f"<li><b>{html.escape(r['ticker'])}</b> · score {room:.1f} (room), RS 60d {rs:+.1%} · "
                         f"{html.escape(outcome)}{link}</li>")
        body = "".join(items)
    return (f"<h2 style='margin-top:22px'>Full reports (max {MAX_AUTO_REPORTS} a day)</h2>"
            f"<ul style='padding-left:18px'>{body}</ul>")


def build_page(results, skipped, today, auto_html=""):
    sections = [auto_html] if auto_html else []
    for g in GROUPS:
        rows = [r for r in results if r["group"] == g]
        if g == "Top candidates":
            rows.sort(key=score, reverse=True)
        body = "".join(stock_line(r) for r in rows) or "<li>None today.</li>"
        sections.append(f"<h2 style='margin-top:22px'>{g} ({len(rows)})</h2><ul style='padding-left:18px'>{body}</ul>")
    if skipped:
        items = "".join(f"<li>{html.escape(t)}: {html.escape(why)}</li>" for t, why in skipped)
        sections.append(f"<h2 style='margin-top:22px'>Skipped ({len(skipped)})</h2><ul>{items}</ul>")
    rules = html.escape(__doc__.strip())
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Watchlist {today}</title></head>"
        "<body style='font-family:sans-serif;font-size:14px;line-height:1.45;max-width:980px;margin:16px auto;padding:0 16px'>"
        f"<h1 style='margin-bottom:2px'>Watchlist scan · {today}</h1>"
        "<div style='opacity:.7;font-size:12px'>Calculated from Yahoo Finance prices. No AI in the scan itself. "
        "Not trade advice: no entries, stops or orders. Chart levels describe past behavior.</div>"
        + "".join(sections)
        + f"<details style='margin-top:22px'><summary style='cursor:pointer;font-weight:700'>Rules</summary>"
        f"<pre style='white-space:pre-wrap;font-size:12px'>{rules}</pre></details>"
        "</body></html>"
    )


def publish_to_portfolio(page_path, report_paths, today, portfolio=PORTFOLIO_DIR, prices=None, names=None,
                         download=None, fetch_names=None, scan=None):
    """Copies the watchlist page and any new full reports into the portfolio, then rebuilds
    reports.html and every stocks/TICKER.html page. Nothing is committed or pushed."""
    import portfolio_page

    portfolio = Path(portfolio)
    if not (portfolio / "reports.html").exists():
        print(f"Portfolio not found at {portfolio}, skipped publishing.")
        return []
    dest = portfolio / "reports"
    dest.mkdir(exist_ok=True)
    page_html = Path(page_path).read_text(encoding="utf-8")
    (dest / Path(page_path).name).write_text(page_html, encoding="utf-8")
    (dest / "watchlist.html").write_text(page_html, encoding="utf-8")
    copied = [Path(page_path).name, "watchlist.html"]
    for rp in report_paths:
        rp = Path(rp)
        (dest / rp.name).write_text(rp.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(rp.name)

    built = portfolio_page.build(portfolio, today, prices=prices, names=names, download=download,
                                 fetch_names=fetch_names, scan=scan)
    print(f"Rebuilt reports.html with {len(built['cards'])} cards and the stock pages"
          + (f"; no chart data for {', '.join(built['no_chart'])}" if built["no_chart"] else "")
          + (f"; no company name for {', '.join(built['no_name'])}" if built["no_name"] else ""))
    return copied + ["reports.html", "stocks/"]


def _git_remote(repo, *args):
    # Runs git with the GitHub CLI's login so it never waits on a hidden password prompt.
    gh = (shutil.which("gh") or "C:/Program Files/GitHub CLI/gh.exe").replace("\\", "/")
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "credential.helper=", "-c", f'credential.helper=!"{gh}" auth git-credential',
         *args],
        capture_output=True, text=True, timeout=120, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def _git_push(repo):
    return _git_remote(repo, "push", "-q", "origin", "HEAD")


def _git_pull(repo):
    # Puts the scan commit on top of anything pushed from elsewhere (like the laptop), so the push isn't rejected.
    return _git_remote(repo, "pull", "--rebase", "-q", "origin", "HEAD")


def _rebase_in_progress(repo):
    git_dir = Path(repo) / ".git"
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


def is_generated(path):
    # Portfolio files only the scan builds; in a conflict the scan's copy is the right one.
    return path == "reports.html" or path.startswith("stocks/") or path.startswith("reports/watchlist")


def _settle_rebase(repo):
    """Finishes a rebase that stopped on conflicts by keeping the scan's version of generated files.
    If any other file conflicts, the rebase is aborted. Returns the files that blocked it ([] if it finished)."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                              env={**os.environ, "GIT_EDITOR": "true"})

    while _rebase_in_progress(repo):
        conflicted = git("diff", "--name-only", "--diff-filter=U").stdout.splitlines()
        other = [f for f in conflicted if not is_generated(f)]
        if other or not conflicted:
            git("rebase", "--abort")
            return other or ["(the rebase stopped without a conflicted file)"]
        for f in conflicted:
            # During a rebase "theirs" is the commit being replayed, which is the scan's
            if git("checkout", "--theirs", "--", f).returncode:
                git("rm", "-q", "--", f)      # the scan deleted it
            else:
                git("add", "--", f)
        # If the upstream copy already matches the scan's, this step has nothing left to commit
        step = git("rebase", "--skip" if git("diff", "--cached", "--quiet").returncode == 0 else "--continue")
        if step.returncode and _rebase_in_progress(repo) and not git("diff", "--name-only", "--diff-filter=U").stdout:
            git("rebase", "--abort")
            return [f"(rebase --continue failed: {step.stderr.strip() or step.stdout.strip()})"]
    return []


def _pull_rebase(portfolio, pull):
    """Pulls with rebase, settling conflicts in generated files. Returns None if it worked, else why not."""
    try:
        pulled = pull(portfolio)
    except subprocess.TimeoutExpired:
        return "the pull timed out"
    if not pulled.returncode:
        return None
    if not _rebase_in_progress(portfolio):
        return pulled.stderr.strip() or pulled.stdout.strip()
    blocked = _settle_rebase(portfolio)
    if blocked:
        return f"rebase conflict outside the generated files ({', '.join(blocked)}), so the rebase was aborted"
    print("Pull hit a conflict only in generated files; kept the scan's version.")
    return None


def sync_portfolio(portfolio=PORTFOLIO_DIR, pull=_git_pull):
    """Pulls the portfolio before the pages are built, so they are built on top of the latest site."""
    portfolio = Path(portfolio)
    if not (portfolio / ".git").exists():
        return
    dirty = subprocess.run(["git", "-C", str(portfolio), "status", "--porcelain"], capture_output=True, text=True).stdout
    if dirty:
        print("Portfolio has uncommitted changes, so it wasn't pulled before building; it is pulled again before the push.")
        return
    why = _pull_rebase(portfolio, pull)
    print("Pulled the portfolio before building." if why is None else f"Pull before building failed: {why}")


def push_portfolio(today, portfolio=PORTFOLIO_DIR, secrets=None, push=_git_push, pull=_git_pull):
    """Commits the portfolio, pulls with rebase, then pushes, but only if the secret scan finds nothing.
    A rebase conflict only in generated files keeps the scan's version; any other conflict aborts the rebase
    and nothing is pushed (the commit stays local).
    Returns "pushed", "no changes", "blocked", "no repo", "commit failed", "pull failed" or "push failed"."""
    import secret_scan

    portfolio = Path(portfolio)
    if not (portfolio / ".git").exists():
        print(f"{portfolio} is not a git repo, so nothing was pushed.")
        return "no repo"

    def git(*args):
        return subprocess.run(["git", "-C", str(portfolio), *args], capture_output=True, text=True)

    git("add", "-A")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("Portfolio has no changes, so nothing was pushed.")
        return "no changes"
    secrets = secret_scan.env_values(ROOT / ".env") if secrets is None else secrets
    found = secret_scan.scan_staged(portfolio, secrets)
    if found:
        git("reset", "-q")
        print("STOPPED: the secret scan found something, so nothing was committed or pushed.")
        for f, hits in found:
            print(f"  {f}: {', '.join(hits)}")
        return "blocked"
    commit = git("commit", "-q", "-m", f"Daily scan {today}")
    if commit.returncode:
        print(f"Commit failed: {commit.stderr.strip() or commit.stdout.strip()}")
        return "commit failed"
    why = _pull_rebase(portfolio, pull)
    if why:
        print(f"Pull failed: {why}. Nothing was pushed; the commit is saved locally.")
        return "pull failed"
    try:
        result = push(portfolio)
    except subprocess.TimeoutExpired:
        print("Push timed out. The commit is saved locally and will go out with the next push.")
        return "push failed"
    if result.returncode:
        print(f"Push failed: {result.stderr.strip()}. The commit is saved locally and will go out with the next push.")
        return "push failed"
    print("Secret scan clean. Pushed the portfolio; GitHub Pages updates in a minute or two.")
    return "pushed"


def run(ran_reports=True, runner=_run_full_report, push=True):
    """Returns (results, skipped, outcomes, push status). The status is "pushed", "no changes", "skipped"
    (--no-push) or why the push didn't happen."""
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    if push:
        sync_portfolio()
    tickers = load_watchlist()
    print(f"Scanning {len(tickers)} tickers...")
    prices = download_prices(tickers)
    profiles = fetch_profiles(tickers)
    results, skipped = score_all(tickers, prices, profiles, load_catalysts(today=today), today)

    for g in GROUPS:
        rows = [r for r in results if r["group"] == g]
        print(f"\n{g} ({len(rows)}):")
        for r in rows:
            flag = " [At highs / breakout]" if r["flags"] else ""
            print(f"  {r['ticker']:<5} {r['tag']:<16} {'; '.join(r['why'])}{flag}")
    for t, why in skipped:
        print(f"  SKIPPED {t}: {why}")

    outcomes = auto_reports(results, today, runner) if ran_reports else []
    if ran_reports and not outcomes:
        print("\n0 Top candidates, so no full reports were run.")
    for r, outcome, path in outcomes:
        print(f"\nFull report {r['ticker']}: {outcome}" + (f" ({path})" if path else ""))

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"watchlist-{today}.html"
    out.write_text(build_page(results, skipped, today, auto_section(results, outcomes, ran_reports)),
                   encoding="utf-8")
    print(f"\nSaved {out}")

    names = {t: p.get("name") for t, p in profiles.items() if p.get("name")}
    import portfolio_page
    groups = {r["ticker"]: r["group"] for r in results}
    reasons = {r["ticker"]: card_reason(r) for r in results}
    portfolio_page.save_scan(today, groups, reasons=reasons)
    copied = publish_to_portfolio(out, [p for _, o, p in outcomes if p], today, prices=prices, names=names,
                                  scan={"date": today, "groups": groups, "reasons": reasons})
    status = "skipped"
    if copied:
        print(f"Copied to portfolio: {', '.join(copied)}")
    if push:
        status = push_portfolio(today) if copied else "not published"
        if status not in ("pushed", "no changes"):
            print(f"FAILED: the portfolio was not pushed ({status}). The live site still shows the last pushed scan.")
    return results, skipped, outcomes, status


if __name__ == "__main__":
    status = run(ran_reports="--no-reports" not in sys.argv, push="--no-push" not in sys.argv)[3]
    sys.exit(0 if status in ("pushed", "no changes", "skipped") else 1)
