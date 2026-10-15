"""Builds ../portfolio/reports.html.

The page has the daily watchlist card pinned on top, a search bar, and one block per stock
that has an AI report in ../portfolio/reports/. Each block shows:
- "Chart today (updated daily)": candles, 20/50/200-day averages, nearby support and
  resistance, and a one-line status. Pure code from Yahoo prices, no AI, no cost.
- "Latest AI report" with its date and age. Short-term reports older than STALE_SHORT_DAYS
  are marked "may be out of date". The age is recomputed in the browser, so it stays right
  even on days the scan does not run.
- "Older reports", newest first.

Only the part between the page's </header> and </main> is generated. The nav, intro and
footer are kept exactly as they are. Run "python portfolio_page.py" to rebuild it alone.
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
REPORT_NAME = re.compile(r"^([A-Z][A-Z0-9.\-]*)-(short|long)-(\d{4}-\d{2}-\d{2})\.html$")
STALE_SHORT_DAYS = 5
CHART_DAYS = 250
BIG_DAY = 0.05          # a 5% day is worth mentioning
HEAVY_VOLUME = 2.0      # 2x the 20-day average
HEADER_END = "    </header>\n"
MAIN_END = "  </main>"
HORIZON_LABEL = {"short": "Short-term trade", "long": "Long-term hold"}


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


def chart_data(daily):
    """Chart series and status for one stock. daily is a list of {d, o, h, l, c, v}, oldest first."""
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
    }


def _long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def _age_text(days):
    return "today" if days <= 0 else "1 day old" if days == 1 else f"{days} days old"


def _report_link(r, today, latest=False):
    days = (today - r["date"]).days
    stale = r["horizon"] == "short" and days > STALE_SHORT_DAYS
    label = f"{HORIZON_LABEL[r['horizon']]} · {_long_date(r['date'])}"
    out = (f'<a href="reports/{html.escape(r["file"])}">{html.escape(label)}</a> '
           f'<span class="age" data-date="{r["date"].isoformat()}" data-horizon="{r["horizon"]}">({_age_text(days)})</span>')
    if latest:
        out += f' <span class="stale"{"" if stale else " hidden"}>may be out of date</span>'
    return out


def stock_block(ticker, reports, chart, today):
    name = next((r["name"] for r in reports if r["name"]), "")
    search = f"{ticker} {name}".lower()
    if chart:
        as_of = date.fromisoformat(chart["asOf"])
        chart_html = (
            f'      <div class="chart" data-ticker="{html.escape(ticker)}" role="img" '
            f'aria-label="{html.escape(ticker)} daily candles for the last 12 months"></div>\n'
            '      <div class="legend"><span><i class="swatch ma20"></i>20-day</span>'
            '<span><i class="swatch ma50"></i>50-day</span><span><i class="swatch ma200"></i>200-day</span>'
            '<span><i class="swatch sup"></i>Support</span><span><i class="swatch res"></i>Resistance</span></div>\n'
            f'      <p class="status">{html.escape(chart["status"])} '
            f'<span class="asof">(as of {as_of:%b} {as_of.day})</span></p>\n'
        )
    else:
        chart_html = '      <p class="status">Chart data is not available today.</p>\n'
    older = reports[1:]
    if older:
        items = "".join(f"<li>{_report_link(r, today)}</li>" for r in older)
        older_html = f'      <details class="older"><summary>Older reports ({len(older)})</summary><ul>{items}</ul></details>\n'
    else:
        older_html = '      <p class="muted">No older reports yet.</p>\n'
    return (
        f'    <section class="stock" id="stock-{html.escape(ticker)}" data-search="{html.escape(search)}">\n'
        f'      <div class="stock-head"><span class="ticker">{html.escape(ticker)}</span>'
        f'<span class="name">{html.escape(name)}</span></div>\n'
        '      <h3>Chart today (updated daily)</h3>\n'
        + chart_html
        + '      <h3>Latest AI report</h3>\n'
        f'      <p class="latest">{_report_link(reports[0], today, latest=True)}</p>\n'
        '      <h3>Older reports</h3>\n'
        + older_html
        + '    </section>\n'
    )


STYLE = """    <style>
      .watchlist-card { margin: 0 0 20px; }
      .report-search { margin: 0 0 8px; }
      .report-search input {
        width: 100%; padding: 10px 16px; font: inherit; color: var(--text);
        background: var(--surface); border: 1px solid var(--border); border-radius: 999px;
      }
      .report-search input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
      .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
      .no-match { color: var(--muted); margin: 16px 0; }
      .stock { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin: 16px 0; }
      .stock[hidden], .no-match[hidden], .stale[hidden] { display: none; }
      .stock-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
      .stock-head .ticker { font-size: 1.4rem; font-weight: 700; }
      .stock-head .name { color: var(--muted); }
      .stock h3 { font-size: 0.8rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); margin: 18px 0 8px; }
      .chart { width: 100%; height: 300px; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 6px; font-size: 0.8rem; color: var(--muted); }
      .swatch { display: inline-block; width: 14px; height: 3px; margin-right: 5px; vertical-align: middle; border-radius: 2px; }
      .swatch.ma20 { background: #a259d9; }
      .swatch.ma50 { background: var(--accent); }
      .swatch.ma200 { background: #f59f00; }
      .swatch.sup { border-top: 2px dashed #2e9e4f; height: 0; }
      .swatch.res { border-top: 2px dashed #e04848; height: 0; }
      .status { margin: 10px 0 0; font-size: 0.95rem; }
      .asof, .age, .muted { color: var(--muted); }
      .latest, .muted { margin: 0; }
      .stale { margin-left: 4px; padding: 1px 8px; font-size: 0.8rem; font-weight: 600; color: var(--todo-text); background: var(--todo-bg); border: 1px solid var(--todo-border); border-radius: 999px; }
      .older summary { cursor: pointer; color: var(--muted); }
      .older ul { margin: 8px 0 0; padding-left: 20px; }
      @media (max-width: 480px) {
        .stock { padding: 14px; }
        .chart { height: 240px; }
      }
    </style>
"""

SCRIPT = """    <script>
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

      // Search by ticker or company name.
      var input = document.getElementById('report-search');
      var blocks = Array.prototype.slice.call(document.querySelectorAll('.stock'));
      var none = document.querySelector('.no-match');
      input.addEventListener('input', function () {
        var q = input.value.trim().toLowerCase(), shown = 0;
        blocks.forEach(function (b) {
          var ok = !q || b.getAttribute('data-search').indexOf(q) !== -1;
          b.hidden = !ok; if (ok) shown++;
        });
        none.hidden = shown > 0;
      });

      // Charts, drawn when they scroll into view and recolored when the theme changes.
      var data = window.CHART_DATA || {}, drawn = [];
      function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
      function theme() {
        return {
          layout: { background: { color: css('--surface') }, textColor: css('--muted') },
          grid: { vertLines: { color: css('--border') }, horzLines: { color: css('--border') } }
        };
      }
      function pts(rows) { return rows.map(function (r) { return { time: r[0], value: r[1] }; }); }
      function draw(el) {
        var d = data[el.getAttribute('data-ticker')];
        if (!d || !window.LightweightCharts) return;
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
        drawn.push({ chart: chart, ma50: ma50 });
      }
      var els = document.querySelectorAll('.chart');
      if ('IntersectionObserver' in window) {
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); draw(e.target); } });
        }, { rootMargin: '200px' });
        els.forEach(function (el) { io.observe(el); });
      } else {
        els.forEach(draw);
      }
      if (window.matchMedia) {
        var mq = window.matchMedia('(prefers-color-scheme: dark)');
        var recolor = function () {
          drawn.forEach(function (x) { x.chart.applyOptions(theme()); x.ma50.applyOptions({ color: css('--accent') }); });
        };
        if (mq.addEventListener) mq.addEventListener('change', recolor); else if (mq.addListener) mq.addListener(recolor);
      }
    })();
    </script>
""".replace("%STALE%", str(STALE_SHORT_DAYS))


def build_section(reports, charts, today, watchlist_date):
    order = sorted(reports, key=lambda t: (-reports[t][0]["date"].toordinal(), t))
    watch_card = ""
    if watchlist_date:
        watch_card = (
            '    <a class="card watchlist-card" href="reports/watchlist.html">\n'
            '      <div class="ticker">Daily watchlist</div>\n'
            '      <div class="tag">Watchlist scan</div>\n'
            f'      <div class="date">{_long_date(watchlist_date)}</div>\n'
            '    </a>\n'
        )
    blocks = "".join(stock_block(t, reports[t], charts.get(t), today) for t in order)
    return (
        "\n    <!-- Generated by portfolio_page.py in the stock-debate repo. Everything from here to the end of\n"
        "         the main section is rebuilt by the daily watchlist scan, so edits here are overwritten. -->\n"
        + STYLE
        + watch_card
        + '    <div class="report-search">\n'
        '      <label class="visually-hidden" for="report-search">Search reports by ticker or company</label>\n'
        '      <input id="report-search" type="search" placeholder="Search by ticker or company" autocomplete="off">\n'
        '    </div>\n'
        '    <p class="no-match" hidden>No reports for that stock yet.</p>\n'
        + blocks
        + '    <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>\n'
        f'    <script src="reports/charts-data.js?v={today.isoformat()}"></script>\n'
        + SCRIPT
    )


def build(portfolio=PORTFOLIO_DIR, today=None, prices=None, download=None):
    """Rebuilds reports.html and reports/charts-data.js. prices is an optional
    {ticker: candles} dict already downloaded (for example by the watchlist scan)."""
    portfolio = Path(portfolio)
    today = today or datetime.now(ZoneInfo("America/Los_Angeles")).date()
    page_file, reports_dir = portfolio / "reports.html", portfolio / "reports"
    page = page_file.read_text(encoding="utf-8")
    if page.count(HEADER_END) != 1 or page.count(MAIN_END) != 1:
        raise RuntimeError("reports.html layout changed, so it was not rebuilt.")

    reports = find_reports(reports_dir)
    prices = dict(prices or {})
    missing = [t for t in reports if not prices.get(t)]
    if missing:
        if download is None:
            from watchlist import download_prices as download
        prices.update(download(missing))
    charts = {t: chart_data(prices[t]) for t in reports if prices.get(t)}
    charts = {t: c for t, c in charts.items() if c}

    scans = sorted(reports_dir.glob("watchlist-*.html"))
    watchlist_date = None
    if (reports_dir / "watchlist.html").exists() and scans:
        watchlist_date = date.fromisoformat(scans[-1].stem.replace("watchlist-", ""))

    top = page[: page.index(HEADER_END) + len(HEADER_END)]
    bottom = page[page.index(MAIN_END):]
    page_file.write_text(top + build_section(reports, charts, today, watchlist_date) + bottom,
                         encoding="utf-8", newline="")
    (reports_dir / "charts-data.js").write_text(
        "window.CHART_DATA = " + json.dumps(charts, separators=(",", ":")) + ";\n", encoding="utf-8")
    return {"blocks": sorted(reports), "charts": sorted(charts),
            "no_chart": sorted(set(reports) - set(charts))}


if __name__ == "__main__":
    print(build())
