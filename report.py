"""Builds the report page. Top to bottom:
1. The big picture: the judge's Chart, Company and Link lines, with the evidence label from code.
2. Conclusion: setup status and evidence (both from setup_status.py) and what would change them.
3. Chart lane: the judge's short explanation, then the code's checklist, levels, scenarios and chart.
4. Company lane: the judge's drivers, risks and what the price assumes.
5. What changes the picture: the judge's dated events.
6. Full details, collapsed: money examples, every fact, the details judges and the raw debate.
Sections 1 to 5 share one tooltip set, so each term is explained once on the visible page.
"""
import html
import re

from chart import chart_html
from scenarios import scenario_tiles
from wording import KINDS, tip_html

DECISION_HEADINGS = ["THE BIG PICTURE", "COMPANY LEAN", "CHART EXPLAINED", "COMPANY DRIVERS", "MAIN RISKS",
                     "WHAT THE PRICE ASSUMES", "WHAT CHANGES THE PICTURE"]
DETAIL_HEADINGS = ["BEST BULL POINTS", "BEST BEAR POINTS", "WHERE THEY DISAGREE", "DATA WARNINGS",
                   "CLAIMS THAT FAILED", "CHECK THIS YOURSELF"]
HEADINGS = DECISION_HEADINGS + DETAIL_HEADINGS
# Colors and layout live in the portfolio's reports/report.css; these are its class names.
CLASSES = {"BEST BULL POINTS": "bull", "BEST BEAR POINTS": "bear"}
SUB_LABELS = ["Evidence:", "Why it matters:", "Other side:", "Detail:"]
STATUS_CLASS = {"Early setup": "status-early", "Confirmed breakout": "status-breakout",
                "Setup failed": "status-failed", "No setup": "status-none"}
EVIDENCE_CLASS = {"Chart and company agree": "ev-agree", "Chart and company disagree": "ev-disagree",
                  "Too early to tell": "ev-early"}


def _parse_sections(brief):
    # Each section is [title, points]. Each point is [text, [(label, text), ...]].
    sections, current = [], None
    for raw in brief.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.upper().rstrip(":") in HEADINGS:
            current = [line.upper().rstrip(":"), []]
            sections.append(current)
            continue
        if current is None:
            continue
        clean = line.lstrip("-• ").strip()
        label = next((s for s in SUB_LABELS if clean.lower().startswith(s.lower())), None)
        if label and current[1]:
            current[1][-1][1].append((label, clean[len(label):].strip()))
        else:
            current[1].append([clean, []])
    return sections


def _points_html(points, seen=None):
    seen = set() if seen is None else seen
    items = []
    for text, subs in points:
        lead = " class='lead'" if subs else ""
        sub_html = "".join(
            f"<div class='sub'>{'' if lab == 'Detail:' else f'<b>{html.escape(lab)}</b> '}{tip_html(val, seen)}</div>"
            for lab, val in subs)
        items.append(f"<li><span{lead}>{tip_html(text, seen)}</span>{sub_html}</li>")
    return f"<ul class='points'>{''.join(items)}</ul>"


def _collapsible(title, inner, kind="", open_=False, extra=""):
    summary = f"<summary class='{kind}'>" if kind else "<summary>"
    return (
        f"<details class='block fold{extra}'{' open' if open_ else ''}>"
        f"{summary}{title}</summary>"
        f"<div class='fold-body'>{inner}</div>"
        f"</details>"
    )


def _label(n, title):
    return f"<div class='label'><span class='section-num'>{n}</span>{title}</div>"


def _paragraph(points, seen):
    return f"<p class='para'>{tip_html(' '.join(p[0] for p in points), seen)}</p>" if points else ""


def money_html(lines):
    seen = set()
    items = "".join(f"<li>{tip_html(l, seen)}</li>" for l in lines)
    return (f"<div class='block money'><div class='label'>WHAT THE MONEY LOOKS LIKE</div>"
            f"<ul class='points'>{items}</ul></div>")


def kinds_legend():
    dots = " ".join(f"<span class='kind {cls}' aria-hidden='true'></span>{html.escape(label.split(':')[0])}"
                    for cls, label in KINDS.values())
    return f"<div class='kinds-legend'>Dots after numbers: {dots}. Hover over a dot for its meaning.</div>"


def link_rest(link, label):
    """The judge's Link line without what TOGETHER and the conclusion already say: the evidence label,
    a "Chart: x. Company: y." echo and "Too early because". Often nothing is left."""
    rest = re.sub(rf"^{re.escape(label)}\b[.,:;]?\s*", "", link.strip(), flags=re.I)
    rest = re.sub(r"^Chart:[^.]*\.\s*Company:[^.]*\.?\s*", "", rest, flags=re.I)
    rest = re.sub(r"^too early because\s+", "", rest, flags=re.I)
    return rest[:1].upper() + rest[1:]


def big_picture_html(points, evidence, seen):
    lines = {}
    for text, _ in points:
        key, _, rest = text.partition(":")
        if key.strip().lower() in ("chart", "company", "link") and rest.strip():
            lines[key.strip().lower()] = rest.strip()
    together = f"<b>{html.escape(evidence[0])}.</b>"
    rows = "".join(f"<dt>{k.upper()}</dt><dd>{tip_html(lines[k], seen)}</dd>" for k in ("chart", "company") if k in lines)
    rows += f"<dt>TOGETHER</dt><dd>{together}</dd>"
    rest = link_rest(lines.get("link", ""), evidence[0])
    link = f"<div class='link'>{tip_html(rest, seen)}</div>" if rest else ""
    return f"<div class='block picture'>{_label(1, 'THE BIG PICTURE')}<dl>{rows}</dl>{link}</div>"


def conclusion_html(setup, evidence, changes, seen):
    status = setup["status"] if setup else "No setup"
    why = setup["reason"] if setup else "No chart data, so no setup can be measured."
    items = "".join(f"<li>{tip_html(line, seen)}</li>" for line in changes)
    return (
        f"<div class='block decision'>{_label(2, 'CONCLUSION')}"
        "<div class='verdict'>"
        f"<div class='cell'><div class='k'>SETUP STATUS</div><div class='v {STATUS_CLASS[status]}'>{status}</div>"
        f"<div class='by'>Decided by code from the chart. {tip_html(why, seen)}</div></div>"
        f"<div class='cell'><div class='k'>EVIDENCE</div><div class='v {EVIDENCE_CLASS[evidence[0]]}'>{evidence[0]}</div>"
        f"<div class='by'>{tip_html(evidence[1], seen)}</div></div>"
        "</div>"
        + (f"<div class='sub-label'>What would change this</div><ul class='points'>{items}</ul>" if items else "")
        + "</div>")


def _calc(text, seen):
    # Every number on the chart lane is worked out by code.
    return tip_html(text, seen) + tip_html(" [C]")


def chart_lane_html(ticker, chart, setup, explained, money_sentence, seen):
    if not chart:
        return f"<div class='block lane-chart'>{_label(3, 'CHART LANE')}<p class='para'>No chart data.</p></div>"
    rows = "".join(
        f"<tr><td><b>{tip_html(name, seen)}</b><div class='rule'>{tip_html(rule, seen)}</div></td>"
        f"<td class='num'>{html.escape(value)}</td>"
        f"<td class='mark'>{'<span class=ok>✓</span>' if ok else '<span class=no>✗</span>'}"
        f"<span class='sr'>{'pass' if ok else 'fail'}</span></td></tr>"
        for name, rule, value, ok in (setup or {}).get("rows", []))
    table = ("<table class='checks'><thead><tr><th>Check (same rules as the watchlist scan)</th>"
             f"<th>{html.escape(ticker)} now</th><th></th></tr></thead><tbody>{rows}</tbody></table>"
             "<div class='legend'>Every value in this table is a calculation by code from daily prices.</div>"
             if rows else "")
    fail = setup.get("fail") if setup else None
    vol = chart.get("volume_vs_20d")
    vol_trend = setup.get("vol_trend") if setup else None
    levels = [
        ("Resistance", chart.get("resistance_text", "")),
        ("Support", chart.get("support_text", "")),
        ("Fail level now", f"a close below ${fail[0]:,.2f} ({fail[1]})" if fail else "no nearby level"),
        ("Volume", (f"latest day {vol:.2f} times its 20-day average" if vol else "n/a")
         + (f"; last 10 days {vol_trend:.2f} times the 50-day average" if vol_trend else "")),
    ]
    levels_html = "".join(f"<dt>{k}</dt><dd>{_calc(v, seen)}</dd>" for k, v in levels if v)
    tiles, ratio = scenario_tiles(chart)
    tile_html = "".join(
        f"<div><div class='n'>${p:,.2f} <span class='{'up' if move >= 0 else 'down'}'>{move:+.1%}</span></div>"
        f"<div class='d'>{tip_html(label, seen)}</div></div>" for p, move, label in tiles)
    if ratio is not None:
        tile_html += (f"<div><div class='n'>{ratio:.2f} : 1</div><div class='d'>possible gain for every $1 of "
                      "possible loss between those two prices</div></div>")
    return (
        f"<div class='block lane-chart'>{_label(3, 'CHART LANE')}"
        f"{_paragraph(explained, seen)}{table}"
        f"<div class='sub-label'>Levels</div><dl class='levels'>{levels_html}</dl>"
        "<div class='sub-label'>Upside vs downside (scenarios, not targets)</div>"
        f"<div class='scen'>{tile_html}</div>"
        f"<p class='note'>{tip_html(money_sentence, seen)} These come from past price levels, not predictions. "
        "You can lose more if the price falls through a level.</p>"
        f"{chart_html(ticker, chart)}</div>")


def company_lane_html(parts, label, seen):
    out = [f"<div class='block lane-company'>{_label(4, 'COMPANY LANE · ' + html.escape(label.lower()))}"]
    for title, sub in (("COMPANY DRIVERS", "The drivers that matter most"), ("MAIN RISKS", "Main risks")):
        if parts.get(title):
            out.append(f"<div class='sub-label'>{sub}</div>{_points_html(parts[title], seen)}")
    if parts.get("WHAT THE PRICE ASSUMES"):
        out.append(f"<div class='sub-label'>What the price already assumes</div>{_paragraph(parts['WHAT THE PRICE ASSUMES'], seen)}")
    return "".join(out) + "</div>"


def dates_html(points, seen):
    items = []
    for text, _ in points:
        when, _, what = text.partition(":")
        if not what:
            when, what = "", text
        what = what.strip()
        what = what[:1].upper() + what[1:]
        items.append(f"<li><span class='when'>{html.escape(when.strip())}</span><span>{tip_html(what.strip(), seen)}</span></li>")
    return (f"<div class='block'>{_label(5, 'WHAT CHANGES THE PICTURE')}<ul class='dates'>{''.join(items)}</ul></div>"
            if items else "")


def _source_html(f):
    src = f.get("source", "")
    url = re.match(r"https?://\S+", src)
    shown = (f"<a href='{html.escape(src, quote=True)}' target='_blank' rel='noopener'>"
             f"{html.escape(re.sub(r'^https?://(www\\.)?', '', src)[:60])}</a>") if url else html.escape(src)
    if f.get("kinds"):   # a code-pulled fact says what kind of numbers it holds
        shown += " " + tip_html(" ".join(f"[{k}]" for k in f["kinds"]))
    if "pulled" in f:   # a code-pulled fact: its date can be an upcoming event, so show when it was pulled
        return shown + (f" · pulled {f['pulled']:%b %d, %Y}" if f["pulled"] else "")
    bits = [shown] + ([html.escape(f["date"])] if f.get("date") else []) + ([html.escape(f["check"])] if f.get("check") else [])
    return " · ".join(bits)


_URL = re.compile(r"(https?://[^\s<>()']+)")


def _linked(text, seen):
    """tip_html for text with bare URLs in it: the URLs become links, and only the words between them
    get tooltips. A tooltip inside a URL ("...-results-revenue-...") would cut the link short."""
    parts = _URL.split(text)
    return "".join(
        tip_html(part, seen) if i % 2 == 0 else
        f"<a href='{html.escape(part, quote=True)}' target='_blank' rel='noopener'>"
        f"{html.escape(re.sub(r'^https?://(www[.])?', '', part)[:40])}</a>"
        for i, part in enumerate(parts))


def facts_html(facts, topics):
    """Research facts grouped by topic, each with how and when it could move the price."""
    order = topics + [t for t in dict.fromkeys(f["topic"] for f in facts) if t not in topics]
    out = []
    for topic in order:
        group = [f for f in facts if f["topic"] == topic]
        if not group:
            continue
        items = []
        for f in group:
            seen = set()
            rows = [("How it could move the price:", f["effect"]), ("When:", f["when"])]
            if f.get("priced_in"):
                rows.append(("Already in the price:", f["priced_in"]))
            sub = "".join(f"<div class='sub'><b>{lab}</b> "
                          f"{_linked(val, seen)}</div>" for lab, val in rows)
            items.append(f"<li><span>{_linked(f['fact'], seen)}</span>{sub}"
                         f"<div class='source'>{_source_html(f)}</div></li>")
        out.append(f"<div class='topic'>{tip_html(topic)}</div>"
                   f"<ul class='facts'>{''.join(items)}</ul>")
    return "".join(out)


def visible_html(ticker, label, chart, setup, evidence, changes, decision_brief, money_sentence):
    """Sections 1 to 5. If the judge did not follow the format, its text is shown as it came."""
    seen = set()
    parts = {t: p for t, p in _parse_sections(decision_brief) if t in DECISION_HEADINGS}
    if "THE BIG PICTURE" in parts:
        top = big_picture_html(parts["THE BIG PICTURE"], evidence, seen)
    else:
        top = (f"<div class='block picture'>{_label(1, 'THE BIG PICTURE')}"
               f"<pre class='raw'>{tip_html(decision_brief, seen)}</pre></div>")
    return (top
            + conclusion_html(setup, evidence, changes, seen)
            + chart_lane_html(ticker, chart, setup, parts.get("CHART EXPLAINED", []), money_sentence, seen)
            + company_lane_html(parts, label, seen)
            + dates_html(parts.get("WHAT CHANGES THE PICTURE", []), seen))


def details_html(details_brief, money_lines, facts_section, dump):
    """Section 6: everything else, in one collapsed block."""
    sections = _parse_sections(details_brief)
    inner = money_html(money_lines) + facts_section
    if sections:
        inner += "".join(_collapsible(t, _points_html(p), CLASSES.get(t, "")) for t, p in sections)
    else:
        inner += f"<pre class='raw'>{tip_html(details_brief)}</pre>"
    inner += (f"<details class='block full-details'><summary>Raw data, chart numbers, research text and the full "
              f"debate</summary><pre class='dump'>{tip_html(dump)}</pre></details>")
    return _collapsible("<span class='section-num'>6</span>FULL DETAILS · the debate, every fact, data warnings, "
                        "money examples", inner, extra=" details-all")


def build_page(ticker, name, today, label, facts, chart_text, chart, research_out, d, decision_brief,
               details_brief, money_sentence, money_lines, hard, topics, research_text,
               setup=None, evidence=("Too early to tell", ""), changes=()):
    dump = "\n\n".join([
        "REAL DATA\n" + facts,
        "PRICE CHART DATA\n" + chart_text,
        "RESEARCH (hard facts pulled by code, then Grok)\n" + research_text,
        f"BULL CASE ({d['bull_name']})\n" + d["bull"],
        f"BEAR CASE ({d['bear_name']})\n" + d["bear"],
        f"BULL REPLY ({d['bull_name']})\n" + d["bull_reply"],
        f"BEAR REPLY ({d['bear_name']})\n" + d["bear_reply"],
        "JUDGE'S BRIEF (as written, before layout)\n" + decision_brief,
    ])
    facts_section = _collapsible(
        "WHAT COULD MOVE THE PRICE",
        "<div class='note'>Each fact says how it could move the price and when.</div>"
        "<div class='group-label'>Pulled from data by code</div>"
        + facts_html(hard, [])
        + "<div class='group-label'>From Grok's web research</div>"
          "<div class='note'>The most important facts were checked a second time "
          "against their sources.</div>"
        + facts_html(research_out["facts"], topics)
        + (f"<div class='topic'>What traders are saying on X (opinions, not facts)</div>"
           f"<div class='x-says'>{tip_html(research_out['x'])}</div>"
           if research_out.get("x") else ""))

    return f"""
    <div class='report'>
      <div class='report-title'>{html.escape(str(name))} ({html.escape(ticker)})</div>
      <div class='report-meta'>{today} · {label} · Bull = {d['bull_name']} · Bear = {d['bear_name']} · Judge = Grok · Setup status and evidence by code · Hover over dotted words for a plain explanation</div>
      {kinds_legend()}
      {visible_html(ticker, label, chart, setup, evidence, list(changes), decision_brief, money_sentence)}
      {details_html(details_brief, money_lines, facts_section, dump)}
      <div class='disclaimer'>Research, not financial advice.</div>
    </div>
    """


# Reports are published to the portfolio's reports/ folder, so these paths are relative to it.
REPORT_HEAD = (
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<link rel='stylesheet' href='../style.css'><link rel='stylesheet' href='report.css'>"
)
REPORT_NAV = (
    "<nav class='nav'><div class='wrap'><a class='home' href='../index.html'>Guy Biran</a>"
    "<ul><li><a href='../index.html#projects'>Projects</a></li>"
    "<li><a href='../reports.html' aria-current='page'>Reports</a></li></ul></div></nav>"
)


def page_html(ticker, name, body):
    """The full report file: build_page's body inside the site's head, nav and reading column."""
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(str(name))} ({html.escape(ticker)})</title>{REPORT_HEAD}</head><body>"
        f"{REPORT_NAV}<main class='report-page'>{body}</main></body></html>"
    )
