"""Runs after each daily scan on the VPS: checks that the live reports page shows today's scan and
sends a Telegram message through the trading agent's bot if the scan failed or the page is stale.

Usage: scan_check.py EXIT_STATUS [LOG_FILE]
  EXIT_STATUS is the scan's exit status (non-zero means the push didn't happen).
  LOG_FILE, if given, adds the FAILED lines of the last run to the message.
  scan_check.py --test sends one test message and exits.

The bot token and chat id are read from /root/trading-agent/.env and never printed.
"""
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

LIVE_PAGE = "https://guybiran8.github.io/reports.html"
TELEGRAM_ENV = Path("/root/trading-agent/.env")
WAIT_SECONDS = 600       # GitHub Pages usually updates in a minute or two
POLL_SECONDS = 30


def scan_label(d):
    # Same wording portfolio_page.py writes: "Scan of Oct 23"
    return f"Scan of {d:%b} {d.day}"


def live_scan_label(html):
    m = re.search(r'class="scan-line">\s*(Scan of [A-Z][a-z]{2} \d{1,2})', html)
    return m.group(1) if m else None


def fetch_live(url=LIVE_PAGE):
    # The query string skips the CDN's cached copy
    r = requests.get(url, params={"t": int(time.time())}, timeout=30, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.text


def wait_for_live(today, fetch=fetch_live, wait=WAIT_SECONDS, poll=POLL_SECONDS, sleep=time.sleep):
    """Returns the scan label the live page shows, waiting up to `wait` seconds for today's."""
    want, seen = scan_label(today), None
    deadline = time.monotonic() + wait
    while True:
        try:
            seen = live_scan_label(fetch())
        except Exception as e:
            seen = seen or f"(page not loaded: {type(e).__name__})"
        if seen == want or time.monotonic() >= deadline:
            return seen
        sleep(poll)


def failed_lines(log_file):
    """FAILED lines from the last run in the scan log."""
    if not log_file or not Path(log_file).exists():
        return []
    text = Path(log_file).read_text(encoding="utf-8", errors="replace")
    last = text.rsplit("scan start", 1)[-1]
    return [ln.strip() for ln in last.splitlines() if "FAILED" in ln][:5]


def problem(today, status, live, failures):
    """The alert text, or None if the scan pushed and the live page shows today."""
    want = scan_label(today)
    if status == 0 and live == want:
        return None
    lines = [f"Stock-debate daily scan {today:%b} {today.day}: something went wrong."]
    if status != 0:
        lines.append(f"The scan exited with {status}, so the push didn't happen.")
    if live != want:
        lines.append(f"The live reports page shows \"{live or 'no scan line'}\" instead of \"{want}\".")
    lines += failures
    lines.append("Log: /root/.local/state/stock-debate/scan.log")
    return "\n".join(lines)


def telegram_settings(path=TELEGRAM_ENV):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out.get("TELEGRAM_BOT_TOKEN"), out.get("TELEGRAM_CHAT_ID")


def send_telegram(text, settings=telegram_settings, post=requests.post):
    token, chat = settings()
    if not token or not chat:
        print("Telegram alert not sent: no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in the trading agent's .env")
        return False
    try:
        r = post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text}, timeout=30)
        ok = r.status_code == 200
    except Exception as e:
        # The exception text can contain the URL, and with it the token, so only its type is printed
        print(f"Telegram alert not sent: {type(e).__name__}")
        return False
    print("Telegram alert sent." if ok else f"Telegram alert not sent: HTTP {r.status_code}")
    return ok


def main(argv):
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()   # the date the scan uses
    if argv[1:2] == ["--test"]:
        return 0 if send_telegram(f"Test from the stock-debate scan check ({today}). Alerts will arrive here.") else 1
    status = int(argv[1]) if len(argv) > 1 else 0
    live = wait_for_live(today, wait=WAIT_SECONDS if status == 0 else 0)
    print(f"Live check: the reports page shows \"{live}\"; today is \"{scan_label(today)}\".")
    text = problem(today, status, live, failed_lines(argv[2] if len(argv) > 2 else None))
    if text:
        print("FAILED: live check found a problem; sending a Telegram alert.")
        send_telegram(text)
        return 1
    print("Live check OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
