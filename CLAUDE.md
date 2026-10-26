# Notes for working on this repo

## Only the VPS scan builds the portfolio's report pages

Laptop sessions (in this repo or in the portfolio repo) must never rebuild or commit `reports.html`,
`stocks/` or `reports/watchlist*` in the portfolio. Only the daily scan on the VPS
(`stock-debate-daily.sh` → `watchlist.py`) builds those files.

Why: on Oct 21, 2026 a laptop session rebuilt them with `portfolio_page.py` and pushed. The Oct 22 scan
then conflicted on the same lines, aborted its rebase and pushed nothing, so the site showed stale prices.

To change how those pages look: change `portfolio_page.py` here, push it, and let the next scan (or
`stock-debate-daily.sh --now` on the VPS) rebuild and push them. Edits to other portfolio files
(index.html, style.css, project pages) are fine from the laptop.

## The daily scan

- Runs on the VPS (root@68.183.155.202) from cron, weekdays at 17:15 New York time.
- `/root/.local/bin/stock-debate-daily.sh` is a symlink to `stock-debate-daily.sh` in this repo.
- Log: `/root/.local/state/stock-debate/scan.log`. A run that didn't push ends with a `FAILED` line.
- `scan_check.py` then checks that https://guybiran8.github.io/reports.html shows "Scan of <today>" and
  sends a Telegram message through the trading agent's bot if not.
- `price_check.py` runs last: it compares Yahoo closes with Tiingo's (key `TIINGO_API_KEY` in `.env`, VPS only)
  and sends a Telegram alert on a gap over 1% or a date mismatch. It checks half the watchlist per run (max 45,
  free plan is 50 requests/hour, 500 symbols/month); state is in `/root/.local/state/stock-debate/price-check.json`.
  Tiingo's free plan is internal use only: never show Tiingo data on the website or in reports.
