#!/bin/bash
# Daily stock-debate scan on the VPS: git pull, then watchlist.py, appending to a size-capped log, then
# scan_check.py, which checks the live site and sends a Telegram alert if the push failed or the page is stale,
# then price_check.py, which compares Yahoo closes with Tiingo's and sends a Telegram alert if they differ.
# Installed as a symlink: /root/.local/bin/stock-debate-daily.sh -> /root/stock-debate/stock-debate-daily.sh
# Cron fires at 21:15 and 22:15 UTC on weekdays; only the one that lands on 17:xx New York
# time runs, so daylight saving is handled without CRON_TZ.
# Usage: stock-debate-daily.sh [--now] [watchlist.py args, e.g. --no-push]
set -u
export PATH=/usr/local/bin:/usr/bin:/bin

REPO=/root/stock-debate
TOKEN_FILE=/root/.config/stock-debate/portfolio-token
LOG_DIR=/root/.local/state/stock-debate
LOG=$LOG_DIR/scan.log
MAX_LOG_BYTES=$((5 * 1024 * 1024))  # roll to scan.log.1 past this, so at most ~10 MB on disk

if [ "${1:-}" = "--now" ]; then
    shift
elif [ "$(TZ=America/New_York date +%H)" != 17 ]; then
    exit 0
fi

mkdir -p "$LOG_DIR"
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt "$MAX_LOG_BYTES" ]; then
    mv -f "$LOG" "$LOG.1"
fi
exec >>"$LOG" 2>&1
exec 9>"$LOG_DIR/scan.lock"
if ! flock -n 9; then
    echo "$(date -Is) FAILED: another scan is still running; skipping"
    exit 1
fi

echo "===== $(TZ=America/New_York date '+%F %T %Z') scan start (args: ${*:-none}) ====="
if ! GIT_TERMINAL_PROMPT=0 git -C "$REPO" pull --ff-only -q; then
    echo "git pull failed; scanning with the code already checked out"
fi
echo "code at $(git -C "$REPO" log -1 --format='%h %s')"

cd "$REPO" || exit 1
# The token goes only to the scan, where push_portfolio's gh credential helper picks it up.
GH_TOKEN="$(cat "$TOKEN_FILE")" PYTHONUNBUFFERED=1 timeout 2h .venv/bin/python watchlist.py "$@"
status=$?
if [ $status -eq 0 ]; then
    echo "===== $(TZ=America/New_York date '+%F %T %Z') scan end (exit 0) ====="
else
    echo "===== $(TZ=America/New_York date '+%F %T %Z') scan end (exit $status) FAILED ====="
fi

# A --no-push run isn't meant to update the site, so there is nothing to check
case " $* " in
    *" --no-push "*) ;;
    *) PYTHONUNBUFFERED=1 .venv/bin/python scan_check.py "$status" "$LOG" ;;
esac

# Private Yahoo vs Tiingo price alarm. It waits for Tiingo's close, always exits 0, and its outcome
# never changes the scan's exit status.
PYTHONUNBUFFERED=1 timeout 90m .venv/bin/python price_check.py || echo "price check: timed out or crashed (exit $?)"
exit $status
