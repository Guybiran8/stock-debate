import html
import json
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

from config import POLY_KEY
from data import money, num, pct

CHART_GUIDE = """HOW TO READ THE CHART NUMBERS:
- Support is a price where buyers stepped in before and the stock bounced. More touches means a stronger level.
- Resistance is a price where sellers stepped in before and the stock turned down.
- When price falls below a support level, that level often acts as resistance afterward.
- The 50-day and 200-day averages are the average closing price over that many days. Price above both is a sign of an uptrend.
- RSI runs from 0 to 100 and measures how fast the price has moved. Above 70 is often called overbought. Below 30 is often called oversold.
- Volume above its 20-day average means more trading than usual, which makes a move more meaningful.
- All of these describe past behavior. They are not predictions."""


def poly_candles(ticker, mult, span, start, end):
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start}/{end}"
    r = requests.get(
        url,
        params={"adjusted": "true", "sort": "asc", "limit": 50000},
        headers={"Authorization": f"Bearer {POLY_KEY}"},
        timeout=30,
    )
    if r.status_code != 200:
        raise ValueError(f"Polygon returned HTTP {r.status_code} for {ticker} {mult}-{span} candles.")
    rows = r.json().get("results") or []
    return [
        {"t": x["t"] // 1000, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x["v"]}
        for x in rows
    ]


def candle_date(c):
    return datetime.fromtimestamp(c["t"], timezone.utc).date()


def add_recent_days(ticker, daily):
    # The free Polygon plan runs a day behind, so add any newer days from Yahoo.
    try:
        stock = yf.Ticker(ticker)
        recent = stock.history(period="5d", interval="1d", auto_adjust=True)
    except Exception:
        return daily, False
    last = candle_date(daily[-1])
    ny_today = datetime.now(ZoneInfo("America/New_York")).date()
    added = False
    for idx, row in recent.iterrows():
        d = idx.date()
        o, h, l, c, v = (row[k] for k in ("Open", "High", "Low", "Close", "Volume"))
        if d > last and c != c and d == ny_today:
            # Yahoo sometimes has today's row before its open/close are filled in.
            # Use the latest price so the chart is not a day behind.
            try:
                c = float(stock.fast_info["last_price"])
            except Exception:
                continue
            o = o if o == o else c
            h = max(h, c) if h == h else max(o, c)
            l = min(l, c) if l == l else min(o, c)
        if d > last and c == c:   # skips empty rows
            stamp = int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())
            daily.append({
                "t": stamp, "o": float(o), "h": float(h),
                "l": float(l), "c": float(c), "v": float(v) if v == v else 0.0,
            })
            added = True
    return daily, added


def sma(values, n):
    return sum(values[-n:]) / n if len(values) >= n else None


def sma_series(values, n):
    out = []
    for i in range(len(values)):
        out.append(sum(values[i - n + 1:i + 1]) / n if i >= n - 1 else None)
    return out


def rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    avg_gain, avg_loss = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (n - 1) + max(ch, 0)) / n
        avg_loss = (avg_loss * (n - 1) + max(-ch, 0)) / n
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def find_levels(daily, price, window=5, tol=0.015):
    # A pivot is a day whose high (or low) is the most extreme within 5 days on each side.
    pivots = []
    for i in range(window, len(daily) - window):
        seg = daily[i - window:i + window + 1]
        if daily[i]["h"] == max(x["h"] for x in seg):
            pivots.append(daily[i]["h"])
        if daily[i]["l"] == min(x["l"] for x in seg):
            pivots.append(daily[i]["l"])
    pivots.sort()

    # Pivots within 1.5% of each other count as the same level.
    clusters = []
    for p in pivots:
        if clusters and abs(p - clusters[-1][0]) / clusters[-1][0] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [(sum(c) / len(c), len(c)) for c in clusters if len(c) >= 2]

    supports = sorted([l for l in levels if l[0] < price], key=lambda l: -l[0])[:2]
    resistances = sorted([l for l in levels if l[0] > price], key=lambda l: l[0])[:2]
    return supports, resistances


# Shared with watchlist.py so the scan and the full reports describe levels the same way.
AT_HIGHS = 0.03          # within 3% of the 52-week high counts as "at highs"
MAX_RISK = 0.15          # a support only counts as nearby if it is within 15% below price
RISK_FLOOR_ATR = 1       # risk distance is never less than 1x the 14-day ATR
ATR_DAYS = 14
SWING_WINDOW = 5


def atr(daily, n=ATR_DAYS):
    # Simple average of the true range over the last n days.
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"]))
           for p, c in zip(daily[-n - 1:-1], daily[-n:])]
    return sum(trs) / len(trs)


def recent_swing_low(daily, price, window=SWING_WINDOW):
    # Most recent day whose low is the lowest of the window days on each side, below price.
    # Returns (low, index into daily) or None.
    for i in range(len(daily) - 1 - window, window - 1, -1):
        low = daily[i]["l"]
        if low < price and low == min(c["l"] for c in daily[i - window:i + window + 1]):
            return low, i
    return None


def risk_level(price, supports, swing_low, s20, max_risk=MAX_RISK):
    """Nearest of: closest tested support, most recent swing low, 20-day average.
    Only counts if below price and within max_risk. Returns (price, label) or None."""
    cands = []
    if supports:
        cands.append((supports[0][0], f"support level, touched {supports[0][1]} times"))
    if swing_low:
        cands.append(swing_low)
    if s20 is not None:
        cands.append((s20, "20-day average"))
    ok = [c for c in cands if c[0] < price and (price - c[0]) / price <= max_risk]
    return max(ok, key=lambda c: c[0]) if ok else None


def trend_label(price, s50, s200):
    if s50 and s200 and price > s50 > s200:
        return "Uptrend: price is above the 50-day average, and the 50-day is above the 200-day."
    if s50 and s200 and price < s50 < s200:
        return "Downtrend: price is below the 50-day average, and the 50-day is below the 200-day."
    if s50 and s200 and s50 > s200 and price < s50:
        return "Pullback: the 50-day average is above the 200-day, but price has dropped below the 50-day."
    return "Mixed: price and the two averages do not line up in one direction."


def nearby_levels(year, price, s20, day_label):
    """Support and resistance as the reports describe them. year is the last ~252 candles,
    day_label(candle) formats a candle's date. Lines are (price, label) pairs."""
    levels_below, levels_above = find_levels(year, price)
    high52 = max(c["h"] for c in year)
    day_range = atr(year)

    # Support: only nearby levels count (see risk_level).
    swing = recent_swing_low(year, price)
    swing = (swing[0], f"most recent swing low, {day_label(year[swing[1]])}") if swing else None
    risk = risk_level(price, levels_below, swing, s20)
    if risk:
        supports = [risk] + [(p, f"support level, touched {n} times") for p, n in levels_below
                             if p < risk[0] and (price - p) / price <= MAX_RISK][:1]
        gap = (price - risk[0]) / price
        floor = RISK_FLOOR_ATR * day_range / price
        support_text = "; ".join(f"${p:,.2f} ({label})" for p, label in supports)
        distance_text = f"{gap * 100:.1f}% below price"
        if gap < floor:
            distance_text += (f", less than one 14-day average true range, so count the risk as at least "
                              f"{floor * 100:.1f}%")
    else:
        supports = []
        support_text = f"no nearby support (nothing within {MAX_RISK * 100:.0f}% below price)"
        distance_text = "n/a"

    # Resistance: at or near the 52-week high there is no level above.
    at_highs = price >= high52 * (1 - AT_HIGHS)
    if at_highs:
        resistances = [(high52, "52-week high")]
        resistance_text = (f"none, at highs (price is within {AT_HIGHS * 100:.0f}% of the "
                           f"52-week high of ${high52:,.2f})")
    elif levels_above:
        resistances = [(p, f"touched {n} times") for p, n in levels_above]
        resistance_text = "; ".join(f"${p:,.2f} ({label})" for p, label in resistances)
    else:
        resistances = [(high52, "52-week high")]
        resistance_text = f"${high52:,.2f} (52-week high)"
    return {
        "supports": supports, "resistances": resistances, "at_highs": at_highs,
        "support_text": support_text, "distance_text": distance_text,
        "resistance_text": resistance_text, "atr": day_range, "high52": high52,
    }


def get_chart(ticker, today):
    daily = poly_candles(ticker, 1, "day", today - timedelta(days=730), today)
    if len(daily) < 60:
        raise ValueError(f"Not enough price history for {ticker}.")
    daily, added = add_recent_days(ticker, daily)

    closes = [c["c"] for c in daily]
    year = daily[-252:]
    price = closes[-1]
    last_day = candle_date(daily[-1])
    source = "Yahoo Finance" if added else "Polygon"
    stale = "" if last_day >= today - timedelta(days=1) else " WARNING: this price may be out of date."

    # The latest day is only partial if it is today in New York and the market has not closed yet.
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    is_partial = last_day == ny_now.date() and ny_now.weekday() < 5 and ny_now.time() < dtime(16, 0)
    partial = " (may be a partial day)" if is_partial else ""

    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    r = rsi(closes)
    vol20 = sum(c["v"] for c in daily[-21:-1]) / 20
    last_vol = daily[-1]["v"]
    high52, low52 = max(c["h"] for c in year), min(c["l"] for c in year)
    lv = nearby_levels(year, price, s20, lambda c: f"{candle_date(c):%b %d}")
    supports, resistances, day_range = lv["supports"], lv["resistances"], lv["atr"]
    support_text, distance_text, resistance_text = lv["support_text"], lv["distance_text"], lv["resistance_text"]
    trend = trend_label(price, s50, s200)

    def change(days):
        return None if len(closes) <= days else closes[-1] / closes[-1 - days] - 1

    lines = [
        f"Latest price: ${price:,.2f} (most recent price on {last_day:%b %d}, from {source}).{stale}",
        f"Candles run through: {last_day:%b %d, %Y}",
        f"Trend: {trend}",
        f"50-day average: {money(s50)}",
        f"200-day average: {money(s200)}",
        f"Change over 1 day: {pct(change(1))}",
        f"Change over 1 month: {pct(change(21))}",
        f"Change over 3 months: {pct(change(63))}",
        f"52-week high: ${high52:,.2f}",
        f"52-week low: ${low52:,.2f}",
        f"Nearest support below price: {support_text}",
        f"Distance to nearest support: {distance_text}",
        f"Nearest resistance above price: {resistance_text}",
        f"14-day average true range (typical daily move): ${day_range:,.2f} ({day_range / price * 100:.1f}% of price)",
        f"RSI (14-day): {num(r)}",
        f"Latest day's volume vs 20-day average: {last_vol / vol20:.2f}x{partial}",
    ]
    return {
        "text": "\n".join(lines),
        "daily": daily,
        "supports": supports,
        "resistances": resistances,
    }


def chart_html(ticker, chart):
    daily = chart["daily"]
    closes = [c["c"] for c in daily]
    ma50, ma200 = sma_series(closes, 50), sma_series(closes, 200)
    show = range(max(0, len(daily) - 250), len(daily))

    def day(c):
        return candle_date(c).strftime("%Y-%m-%d")

    candles = [{"time": day(daily[i]), "open": daily[i]["o"], "high": daily[i]["h"],
                "low": daily[i]["l"], "close": daily[i]["c"]} for i in show]
    volumes = [{"time": day(daily[i]), "value": daily[i]["v"],
                "color": "rgba(46,158,79,0.4)" if daily[i]["c"] >= daily[i]["o"] else "rgba(224,72,72,0.4)"}
               for i in show]
    line50 = [{"time": day(daily[i]), "value": ma50[i]} for i in show if ma50[i] is not None]
    line200 = [{"time": day(daily[i]), "value": ma200[i]} for i in show if ma200[i] is not None]
    levels = (
        [{"price": round(p, 2), "color": "#2e9e4f",
          "title": "Support" if label.startswith("support") else label.split(",")[0].capitalize()}
         for p, label in chart["supports"]]
        + [{"price": round(p, 2), "color": "#e04848",
            "title": "52-week high" if label == "52-week high" else "Resistance"}
           for p, label in chart["resistances"]]
    )
    box = f"chart_{ticker}_{len(daily)}"

    return f"""
    <div style='max-width:760px;font-family:sans-serif;margin-top:14px'>
      <div style='font-weight:700;margin-bottom:4px'>{html.escape(ticker)} daily candles, last 12 months</div>
      <div style='font-size:12px;opacity:.7;margin-bottom:6px'>
        Blue = 50-day average. Orange = 200-day average. Dashed green = support. Dashed red = resistance.
        Scroll to zoom, drag to move.
      </div>
      <div id='{box}' style='width:100%;height:420px;background:#ffffff'></div>
    </div>
    <script>
    (function() {{
      function draw() {{
        var el = document.getElementById('{box}');
        var chart = LightweightCharts.createChart(el, {{
          width: el.clientWidth || 740, height: 420,
          layout: {{ background: {{ color: '#ffffff' }}, textColor: '#222222' }},
          grid: {{ vertLines: {{ color: '#eeeeee' }}, horzLines: {{ color: '#eeeeee' }} }},
          rightPriceScale: {{ borderVisible: false }},
          timeScale: {{ borderVisible: false }}
        }});
        var candles = chart.addCandlestickSeries({{
          upColor: '#2e9e4f', downColor: '#e04848', borderVisible: false,
          wickUpColor: '#2e9e4f', wickDownColor: '#e04848'
        }});
        candles.setData({json.dumps(candles)});
        var vol = chart.addHistogramSeries({{ priceFormat: {{ type: 'volume' }}, priceScaleId: '' }});
        vol.priceScale().applyOptions({{ scaleMargins: {{ top: 0.82, bottom: 0 }} }});
        vol.setData({json.dumps(volumes)});
        var a = chart.addLineSeries({{ color: '#2962ff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }});
        a.setData({json.dumps(line50)});
        var b = chart.addLineSeries({{ color: '#f59f00', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }});
        b.setData({json.dumps(line200)});
        {json.dumps(levels)}.forEach(function(l) {{
          candles.createPriceLine({{ price: l.price, color: l.color, lineWidth: 1, lineStyle: 2,
                                     axisLabelVisible: true, title: l.title }});
        }});
        chart.timeScale().fitContent();
      }}
      if (window.LightweightCharts) {{ draw(); }}
      else {{
        var s = document.createElement('script');
        s.src = 'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js';
        s.onload = draw;
        document.head.appendChild(s);
      }}
    }})();
    </script>
    """
