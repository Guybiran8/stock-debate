"""Builds the portfolio's reports pages. Pure code, no AI, no cost.

reports.html: a search bar, filter buttons for today's scan groups (All, Top candidates, Watch
closely, Catalyst watch, Reject), a line with today's counts and a link to the full scan page,
and one compact card per stock (every ticker in watchlist.txt plus any stock with a report in
../portfolio/reports/). Each card shows the ticker, company name, a short status tag from
today's prices, the stock's scan group (none for stocks not on the watchlist, which only show
under All), and the latest AI report date or "No report yet". Only the part between the page's </header> and
</main> is generated; the nav, intro and footer are kept exactly as they are.

stocks/TICKER.html: one page per card with the daily chart (20/50/200-day averages, nearby
support and resistance), the status line, the latest AI report with its age (short-term
reports older than STALE_SHORT_DAYS are marked "may be out of date"), and older reports.
Report ages are recomputed in the browser, so they stay right on days the scan does not run.

STATUS TAG (first that applies): No chart data, At highs, Downtrend, Below 200-day,
Below 50-day, Uptrend, Mixed.

Run "python portfolio_page.py" to rebuild the pages alone.
"""
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from chart import find_levels, nearby_levels, sma_series, trend_label

ROOT = Path(__file__).parent
PORTFOLIO_DIR = ROOT.parent / "portfolio"
WATCHLIST_FILE = ROOT / "watchlist.txt"
REPORT_NAME = re.compile(r"^([A-Z][A-Z0-9.\-]*)-(short|long)-(\d{4}-\d{2}-\d{2})\.html$")
STALE_SHORT_DAYS = 5
CHART_DAYS = 250
BIG_DAY = 0.05          # a 5% day is worth mentioning
HEAVY_VOLUME = 2.0      # 2x the 20-day average
HEADER_END = "    </header>\n"
MAIN_END = "  </main>"
HORIZON_LABEL = {"short": "Short-term trade", "long": "Long-term hold"}
LIB = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"
# Shown at the top of reports.html, under the heading, in Guy's own words
LEAD = ("These are 45 stocks I picked. Click any one to follow its chart and reports, "
        "so it's easier to understand what the stock is doing.")


def find_reports(reports_dir):
    """Returns {ticker: [report, ...]} with each list newest first."""
    out = {}
    for f in Path(reports_dir).glob("*.html"):
        m = REPORT_NAME.match(f.name)
        if not m:
            continue
        ticker, horizon, day = m.group(1), m.group(2), date.fromisoformat(m.group(3))
        title = re.search(r"<title>(.*?)</title>", f.read_text(encoding="utf-8", errors="replace"), re.S)
        name = html.unescape(title.group(1)).strip() if title else ""
        name = name[: -len(f" ({ticker})")] if name.endswith(f" ({ticker})") else name
        out.setdefault(ticker, []).append({"file": f.name, "horizon": horizon, "date": day, "name": name})
    for reports in out.values():
        reports.sort(key=lambda r: (r["date"], r["horizon"] == "short"), reverse=True)
    return out


def load_watchlist(path=WATCHLIST_FILE):
    if not Path(path).exists():
        return []
    seen = []
    for t in Path(path).read_text(encoding="utf-8").split():
        t = t.strip().upper()
        if t and t not in seen:
            seen.append(t)
    return seen


def _r(x):
    return None if x is None else round(x, 4 if abs(x) < 10 else 2)


def todays_events(daily, ma20):
    """Plain-language notes about the latest day compared with the day before."""
    c, p = daily[-1], daily[-2]
    events = []
    change = c["c"] / p["c"] - 1
    if abs(change) >= BIG_DAY:
        events.append(f"moved {change:+.1%} today")

    prior = daily[-253:-1]
    prior_high, prior_low = max(x["h"] for x in prior), min(x["l"] for x in prior)
    broke_high = c["c"] > prior_high
    if broke_high:
        events.append(f"breakout: closed above the prior 52-week high of ${prior_high:,.2f}")
    elif c["h"] > prior_high:
        events.append("touched a new 52-week high")
    if c["c"] < prior_low:
        events.append(f"closed below the prior 52-week low of ${prior_low:,.2f}")

    # Levels as they stood yesterday, so "broke above" means it crossed one today.
    below, above = find_levels(prior, p["c"])
    if not broke_high:
        crossed = [lvl for lvl, _ in above if p["c"] < lvl <= c["c"]]
        if crossed:
            events.append(f"broke above resistance at ${max(crossed):,.2f}")
    lost = [lvl for lvl, _ in below if c["c"] < lvl <= p["c"]]
    if lost:
        events.append(f"fell below support at ${min(lost):,.2f}")

    if ma20[-1] is not None and ma20[-2] is not None:
        if p["c"] >= ma20[-2] and c["c"] < ma20[-1]:
            events.append("closed below the 20-day average")
        elif p["c"] < ma20[-2] and c["c"] >= ma20[-1]:
            events.append("closed back above the 20-day average")

    vol20 = sum(x["v"] for x in daily[-21:-1]) / 20
    if vol20 and c["v"] / vol20 >= HEAVY_VOLUME:
        events.append(f"volume {c['v'] / vol20:.1f}x the 20-day average")
    return events


def status_tag(price, ma50, ma200, at_highs):
    if at_highs:
        return "At highs"
    trend = trend_label(price, ma50, ma200).split(":")[0] if ma200 else ""
    if trend == "Downtrend":
        return "Downtrend"
    if ma200 and price < ma200:
        return "Below 200-day"
    if ma50 and price < ma50:
        return "Below 50-day"
    if trend == "Uptrend":
        return "Uptrend"
    return "Mixed"


def chart_data(daily):
    """Chart series, status line and tag for one stock. daily is a list of {d, o, h, l, c, v}, oldest first."""
    if len(daily) < 30:
        return None
    closes = [c["c"] for c in daily]
    ma20, ma50, ma200 = sma_series(closes, 20), sma_series(closes, 50), sma_series(closes, 200)
    year = daily[-252:]
    price = closes[-1]
    lv = nearby_levels(year, price, ma20[-1], lambda c: f"{c['d']:%b %d}")

    trend = trend_label(price, ma50[-1], ma200[-1]).split(":")[0] if ma200[-1] else "Not enough history for a trend"
    if lv["supports"]:
        sp = lv["supports"][0][0]
        support = f"support ${sp:,.2f} ({(sp / price - 1) * 100:+.1f}%)"
    else:
        support = "no nearby support"
    if lv["at_highs"]:
        resistance = f"at highs (52-week high ${lv['high52']:,.2f})"
    else:
        rp = lv["resistances"][0][0]
        label = "52-week high" if lv["resistances"][0][1] == "52-week high" else "resistance"
        resistance = f"{label} ${rp:,.2f} ({(rp / price - 1) * 100:+.1f}%)"
    events = todays_events(daily, ma20)
    status = f"{trend} · ${price:,.2f} · {support} · {resistance} · Today: " + (
        "; ".join(events) if events else "nothing unusual")

    show = range(max(0, len(daily) - CHART_DAYS), len(daily))
    day = lambda i: daily[i]["d"].isoformat()  # noqa: E731
    line = lambda ser: [[day(i), _r(ser[i])] for i in show if ser[i] is not None]  # noqa: E731
    levels = ([{"price": _r(p), "kind": "support", "title": "Support"} for p, _ in lv["supports"]]
              + [{"price": _r(p), "kind": "resistance",
                  "title": "52-week high" if label == "52-week high" else "Resistance"}
                 for p, label in lv["resistances"]])
    return {
        "candles": [[day(i), _r(daily[i]["o"]), _r(daily[i]["h"]), _r(daily[i]["l"]), _r(daily[i]["c"])] for i in show],
        "ma20": line(ma20), "ma50": line(ma50), "ma200": line(ma200),
        "levels": levels, "status": status, "asOf": daily[-1]["d"].isoformat(),
        "tag": status_tag(price, ma50[-1], ma200[-1], lv["at_highs"]),
    }


def _long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def _age_text(days):
    return "today" if days <= 0 else "1 day old" if days == 1 else f"{days} days old"


def _report_link(r, today, latest=False, prefix="../reports/"):
    days = (today - r["date"]).days
    stale = r["horizon"] == "short" and days > STALE_SHORT_DAYS
    label = f"{HORIZON_LABEL[r['horizon']]} · {_long_date(r['date'])}"
    out = (f'<a href="{prefix}{html.escape(r["file"])}">{html.escape(label)}</a> '
           f'<span class="age" data-date="{r["date"].isoformat()}" data-horizon="{r["horizon"]}">({_age_text(days)})</span>')
    if latest:
        out += f' <span class="stale"{"" if stale else " hidden"}>may be out of date</span>'
    return out


# ---------- reports.html (card grid) ----------

GROUPS = [("top", "Top candidates", "Top"), ("watch", "Watch closely", "Watch closely"),
          ("catalyst", "Catalyst watch", "Catalyst watch"), ("reject", "Reject", "Reject")]
GROUP_SLUG = {name: slug for slug, name, _ in GROUPS}
LATEST_SCAN = ROOT / "reports" / "latest-scan.json"

GRID_STYLE = """    <style>
      .reports-lead { max-width: 60ch; margin: 0 0 24px; font-size: 1.0625rem; color: var(--body, var(--text)); }
      .report-search { margin: 0 0 12px; }
      .report-search input {
        width: 100%; padding: 10px 16px; font: inherit; color: var(--text);
        background: var(--surface); border: 1px solid var(--border); border-radius: 999px;
      }
      .report-search input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
      .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
      .group-filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 10px; padding: 0; list-style: none; }
      .group-filters button {
        padding: 6px 14px; font: inherit; font-size: 0.9rem; color: var(--muted); cursor: pointer;
        background: var(--surface); border: 1px solid var(--border); border-radius: 999px;
      }
      .group-filters button:hover { color: var(--text); }
      .group-filters button[aria-pressed="true"] { color: var(--text); border-color: var(--accent); font-weight: 600; }
      .group-filters button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
      .scan-line { margin: 0 0 18px; font-size: 0.9rem; color: var(--muted); }
      .card .name { margin-top: 2px; font-size: 0.9rem; color: var(--muted); }
      .card .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
      .card .tags .tag { margin-top: 0; }
      .card .tag.group { color: var(--accent); border-color: var(--accent); }
      .cards li[hidden], .no-match[hidden] { display: none; }
      .no-match { color: var(--muted); margin: 0 0 40px; }
    </style>
"""

GRID_SCRIPT = """    <script>
    (function () {
      var input = document.getElementById('report-search');
      var buttons = Array.prototype.slice.call(document.querySelectorAll('.group-filters button'));
      var cards = Array.prototype.slice.call(document.querySelectorAll('.stock-cards li'));
      var none = document.querySelector('.no-match');
      var group = 'all';
      function apply() {
        var q = input.value.trim().toLowerCase(), shown = 0;
        cards.forEach(function (c) {
          var inGroup = group === 'all' || c.getAttribute('data-group') === group;
          var ok = inGroup && (!q || c.getAttribute('data-search').indexOf(q) !== -1);
          c.hidden = !ok; if (ok) shown++;
        });
        none.textContent = q ? 'No reports for that stock yet.' : 'No stocks in this group today.';
        none.hidden = shown > 0;
      }
      buttons.forEach(function (b) {
        b.addEventListener('click', function () {
          group = b.getAttribute('data-filter');
          buttons.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
          apply();
        });
      });
      input.addEventListener('input', apply);
    })();
    </script>
"""


def stock_card(ticker, name, tag, latest, group=None):
    when = f"Latest report: {_long_date(latest['date'])}" if latest else "No report yet"
    group_tag = f'<span class="tag group">{html.escape(group)}</span>' if group else ""
    return (
        f'      <li data-search="{html.escape(f"{ticker} {name}".lower())}" data-group="{GROUP_SLUG.get(group, "")}">\n'
        f'        <a class="card" href="stocks/{html.escape(ticker)}.html">\n'
        f'          <div class="ticker">{html.escape(ticker)}</div>\n'
        f'          <div class="name">{html.escape(name)}</div>\n'
        f'          <div class="tags"><span class="tag">{html.escape(tag)}</span>{group_tag}</div>\n'
        f'          <div class="date">{html.escape(when)}</div>\n'
        f'        </a>\n      </li>\n'
    )


def scan_line(scan):
    if not scan:
        return '    <p class="scan-line">No watchlist scan yet.</p>\n'
    counts = {name: 0 for _, name, _ in GROUPS}
    for g in scan["groups"].values():
        if g in counts:
            counts[g] += 1
    d = scan["date"]
    parts = " · ".join(f"{counts[name]} {short}" for _, name, short in GROUPS)
    return (f'    <p class="scan-line">Scan of {d:%b} {d.day}: {parts} · '
            '<a href="reports/watchlist.html">See full scan details</a></p>\n')


def grid_section(stocks, scan):
    groups = scan["groups"] if scan else {}
    buttons = ['      <li><button type="button" data-filter="all" aria-pressed="true">All</button></li>\n'] + [
        f'      <li><button type="button" data-filter="{slug}" aria-pressed="false">{name}</button></li>\n'
        for slug, name, _ in GROUPS]
    cards = "".join(stock_card(s["ticker"], s["name"], s["tag"], s["reports"][0] if s["reports"] else None,
                               groups.get(s["ticker"]))
                    for s in stocks)
    return (
        "\n    <!-- Generated by portfolio_page.py in the stock-debate repo. Everything from here to the end of\n"
        "         the main section is rebuilt by the daily watchlist scan, so edits here are overwritten. -->\n"
        + GRID_STYLE
        + f'    <p class="reports-lead">{html.escape(LEAD, quote=False)}</p>\n'
        + '    <div class="report-search">\n'
        '      <label class="visually-hidden" for="report-search">Search by ticker or company</label>\n'
        '      <input id="report-search" type="search" placeholder="Search by ticker or company" autocomplete="off">\n'
        '    </div>\n'
        '    <ul class="group-filters" aria-label="Filter by today\'s scan group">\n' + "".join(buttons) + '    </ul>\n'
        + scan_line(scan)
        + '    <ul class="cards stock-cards">\n' + cards + '    </ul>\n'
        '    <p class="no-match" hidden>No reports for that stock yet.</p>\n'
        + GRID_SCRIPT
    )


def save_scan(scan_date, groups, path=None):
    """Remembers today's groups so the page can be rebuilt on its own later."""
    path = path or LATEST_SCAN
    Path(path).parent.mkdir(exist_ok=True)
    Path(path).write_text(json.dumps({"date": scan_date.isoformat(), "groups": groups}, indent=1), encoding="utf-8")


def load_scan(path=None):
    path = path or LATEST_SCAN
    if not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {"date": date.fromisoformat(data["date"]), "groups": data["groups"]}


# ---------- stocks/TICKER.html ----------

STOCK_STYLE = """  <style>
    .back { display: inline-block; margin: 28px 0 0; color: var(--muted); text-decoration: none; }
    .back:hover { color: var(--text); }
    .stock-intro { padding-block: 16px 8px; }
    .stock-intro h1 { margin-bottom: 2px; }
    .stock-intro .name { color: var(--muted); margin: 0; }
    .stock-intro .tag { display: inline-block; margin-top: 10px; padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border); font-size: 0.85rem; color: var(--muted); }
    .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin: 16px 0; }
    .panel h2 { font-size: 0.8rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); margin: 0 0 10px; }
    .chart { width: 100%; height: 340px; }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 6px; font-size: 0.8rem; color: var(--muted); }
    .swatch { display: inline-block; width: 14px; height: 3px; margin-right: 5px; vertical-align: middle; border-radius: 2px; }
    .swatch.ma20 { background: #a259d9; }
    .swatch.ma50 { background: var(--accent); }
    .swatch.ma200 { background: #f59f00; }
    .swatch.sup { border-top: 2px dashed #2e9e4f; height: 0; }
    .swatch.res { border-top: 2px dashed #e04848; height: 0; }
    .status { margin: 12px 0 0; }
    .asof, .age, .muted { color: var(--muted); }
    .panel p { margin: 0; }
    .stale { margin-left: 4px; padding: 1px 8px; font-size: 0.8rem; font-weight: 600; color: var(--todo-text); background: var(--todo-bg); border: 1px solid var(--todo-border); border-radius: 999px; }
    .stale[hidden] { display: none; }
    .older summary { cursor: pointer; color: var(--muted); }
    .older ul { margin: 8px 0 0; padding-left: 20px; }
    @media (max-width: 480px) {
      .panel { padding: 14px; }
      .chart { height: 260px; }
    }
  </style>
"""

STOCK_SCRIPT = """  <script>
  (function () {
    // Report ages, recomputed with the viewer's date.
    var today = new Date(); today.setHours(0, 0, 0, 0);
    document.querySelectorAll('.age').forEach(function (el) {
      var p = el.getAttribute('data-date').split('-');
      var days = Math.round((today - new Date(+p[0], +p[1] - 1, +p[2])) / 86400000);
      el.textContent = '(' + (days <= 0 ? 'today' : days === 1 ? '1 day old' : days + ' days old') + ')';
      var flag = el.parentNode.querySelector('.stale');
      if (flag) flag.hidden = !(el.getAttribute('data-horizon') === 'short' && days > %STALE%);
    });

    var d = window.CHART, el = document.querySelector('.chart');
    if (!d || !el || !window.LightweightCharts) return;
    function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
    function theme() {
      return {
        layout: { background: { color: css('--surface') }, textColor: css('--muted') },
        grid: { vertLines: { color: css('--border') }, horzLines: { color: css('--border') } }
      };
    }
    function pts(rows) { return rows.map(function (r) { return { time: r[0], value: r[1] }; }); }
    var chart = LightweightCharts.createChart(el, Object.assign({
      autoSize: true,
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true }
    }, theme()));
    var candles = chart.addCandlestickSeries({
      upColor: '#2e9e4f', downColor: '#e04848', borderVisible: false,
      wickUpColor: '#2e9e4f', wickDownColor: '#e04848'
    });
    candles.setData(d.candles.map(function (c) { return { time: c[0], open: c[1], high: c[2], low: c[3], close: c[4] }; }));
    var opts = { lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    chart.addLineSeries(Object.assign({ color: '#a259d9' }, opts)).setData(pts(d.ma20));
    var ma50 = chart.addLineSeries(Object.assign({ color: css('--accent') }, opts));
    ma50.setData(pts(d.ma50));
    chart.addLineSeries(Object.assign({ color: '#f59f00' }, opts)).setData(pts(d.ma200));
    d.levels.forEach(function (l) {
      candles.createPriceLine({ price: l.price, color: l.kind === 'support' ? '#2e9e4f' : '#e04848',
                                lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: l.title });
    });
    chart.timeScale().fitContent();
    if (window.matchMedia) {
      var mq = window.matchMedia('(prefers-color-scheme: dark)');
      var recolor = function () { chart.applyOptions(theme()); ma50.applyOptions({ color: css('--accent') }); };
      if (mq.addEventListener) mq.addEventListener('change', recolor); else if (mq.addListener) mq.addListener(recolor);
    }
  })();
  </script>
""".replace("%STALE%", str(STALE_SHORT_DAYS))


def _one_level_up(fragment):
    # Relative links in the copied nav and footer need "../" from inside stocks/.
    return re.sub(r'href="(?!https?:|mailto:|#|/|\.\./)([^"]+)"', r'href="../\1"', fragment)


def site_chrome(reports_page):
    """Head bits, nav and footer copied from reports.html so stock pages match the site."""
    nav = re.search(r"  <nav class=\"nav\">.*?</nav>\n", reports_page, re.S)
    footer = re.search(r"  <footer>.*?</footer>\n", reports_page, re.S)
    title = re.search(r"<title>(.*?)</title>", reports_page, re.S)
    site = title.group(1).split("·")[-1].strip() if title else ""
    return (_one_level_up(nav.group(0)) if nav else "", _one_level_up(footer.group(0)) if footer else "", site)


def stock_page(stock, today, chrome):
    nav, footer, site = chrome
    ticker, name, chart, reports = stock["ticker"], stock["name"], stock["chart"], stock["reports"]
    title = f"{ticker} · {site}" if site else ticker
    if chart:
        as_of = date.fromisoformat(chart["asOf"])
        chart_html = (
            f'      <div class="chart" role="img" aria-label="{html.escape(ticker)} daily candles for the last 12 months"></div>\n'
            '      <div class="legend"><span><i class="swatch ma20"></i>20-day</span>'
            '<span><i class="swatch ma50"></i>50-day</span><span><i class="swatch ma200"></i>200-day</span>'
            '<span><i class="swatch sup"></i>Support</span><span><i class="swatch res"></i>Resistance</span></div>\n'
            f'      <p class="status">{html.escape(chart["status"])} <span class="asof">(as of {as_of:%b} {as_of.day})</span></p>\n'
        )
        data = json.dumps(chart, separators=(",", ":")).replace("</", "<\\/")
        scripts = f'  <script src="{LIB}"></script>\n  <script>window.CHART = {data};</script>\n'
    else:
        chart_html = '      <p class="status">Chart data is not available today.</p>\n'
        scripts = ""
    if reports:
        latest = f"      <p>{_report_link(reports[0], today, latest=True)}</p>\n"
    else:
        latest = '      <p class="muted">No AI report yet.</p>\n'
    older = reports[1:]
    if older:
        items = "".join(f"<li>{_report_link(r, today)}</li>" for r in older)
        older_html = f'      <details class="older"><summary>Older reports ({len(older)})</summary><ul>{items}</ul></details>\n'
    else:
        older_html = '      <p class="muted">No older reports yet.</p>\n'
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'  <title>{html.escape(title)}</title>\n  <link rel="stylesheet" href="../style.css">\n'
        + STOCK_STYLE + '</head>\n<body>\n'
        '  <!-- Generated by portfolio_page.py in the stock-debate repo. Rebuilt by the daily scan. -->\n'
        + nav
        + '\n  <main class="wrap">\n'
        '    <a class="back" href="../reports.html">&larr; All reports</a>\n'
        '    <header class="stock-intro">\n'
        f'      <h1>{html.escape(ticker)}</h1>\n'
        f'      <p class="name">{html.escape(name)}</p>\n'
        f'      <div class="tag">{html.escape(stock["tag"])}</div>\n'
        '    </header>\n'
        '    <section class="panel">\n      <h2>Chart today (updated daily)</h2>\n' + chart_html + '    </section>\n'
        '    <section class="panel">\n      <h2>Latest AI report</h2>\n' + latest + '    </section>\n'
        '    <section class="panel">\n      <h2>Older reports</h2>\n' + older_html + '    </section>\n'
        '  </main>\n\n'
        + footer + scripts + STOCK_SCRIPT + '</body>\n</html>\n'
    )


def build(portfolio=PORTFOLIO_DIR, today=None, prices=None, names=None, download=None,
          fetch_names=None, watchlist=None, scan=None):
    """Rebuilds reports.html and every stocks/TICKER.html. prices is an optional
    {ticker: candles} dict and names an optional {ticker: company name} dict already
    fetched (for example by the watchlist scan); anything missing is downloaded.
    scan is {"date": date, "groups": {ticker: group}} from today's scan; if it is not
    given, the last saved scan (reports/latest-scan.json) is used."""
    portfolio = Path(portfolio)
    today = today or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    page_file, reports_dir, stocks_dir = portfolio / "reports.html", portfolio / "reports", portfolio / "stocks"
    page = page_file.read_text(encoding="utf-8")
    if page.count(HEADER_END) != 1 or page.count(MAIN_END) != 1:
        raise RuntimeError("reports.html layout changed, so it was not rebuilt.")

    reports = find_reports(reports_dir)
    watch = load_watchlist() if watchlist is None else watchlist
    tickers = list(dict.fromkeys(watch + sorted(reports)))

    prices = dict(prices or {})
    missing = [t for t in tickers if not prices.get(t)]
    if missing:
        if download is None:
            from watchlist import download_prices as download
        prices.update(download(missing))

    names = dict(names or {})
    for t, rs in reports.items():
        if rs[0]["name"]:
            names[t] = rs[0]["name"]
    need = [t for t in tickers if not names.get(t)]
    if need:
        if fetch_names is None:
            from watchlist import fetch_profiles
            fetch_names = lambda ts: {t: p.get("name") for t, p in fetch_profiles(ts).items()}  # noqa: E731
        names.update({t: n for t, n in fetch_names(need).items() if n})

    stocks = []
    for t in tickers:
        chart = chart_data(prices[t]) if prices.get(t) else None
        stocks.append({"ticker": t, "name": names.get(t) or "", "chart": chart,
                       "tag": chart["tag"] if chart else "No chart data", "reports": reports.get(t, [])})
    # Stocks with reports first (newest report first), then the rest by ticker.
    stocks.sort(key=lambda s: (0, -s["reports"][0]["date"].toordinal(), s["ticker"]) if s["reports"]
                else (1, 0, s["ticker"]))

    scan = scan if scan is not None else load_scan()

    top = page[: page.index(HEADER_END) + len(HEADER_END)]
    bottom = page[page.index(MAIN_END):]
    new_page = top + grid_section(stocks, scan) + bottom
    page_file.write_text(new_page, encoding="utf-8", newline="")

    stocks_dir.mkdir(exist_ok=True)
    chrome = site_chrome(new_page)
    keep = set()
    for s in stocks:
        f = stocks_dir / f"{s['ticker']}.html"
        f.write_text(stock_page(s, today, chrome), encoding="utf-8", newline="")
        keep.add(f.name)
    for old in stocks_dir.glob("*.html"):
        if old.name not in keep:
            old.unlink()
    old_data = reports_dir / "charts-data.js"   # used by the previous design
    if old_data.exists():
        old_data.unlink()
    return {"cards": [s["ticker"] for s in stocks],
            "no_chart": [s["ticker"] for s in stocks if not s["chart"]],
            "no_name": [s["ticker"] for s in stocks if not s["name"]]}


if __name__ == "__main__":
    print(build())
