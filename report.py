import html
import re

from chart import chart_html
from wording import tip_html

HEADINGS = [
    "IN PLAIN ENGLISH", "WHERE THE EVIDENCE LEANS", "THE DECISION", "BEST BULL POINTS", "BEST BEAR POINTS",
    "WHERE THEY DISAGREE", "WHAT THE CHART SHOWS", "DATA WARNINGS",
    "CLAIMS THAT FAILED", "CHECK THIS YOURSELF",
]
# Colors and layout live in the portfolio's reports/report.css; these are its class names.
CLASSES = {"BEST BULL POINTS": "bull", "BEST BEAR POINTS": "bear", "THE DECISION": "decision"}
SUB_LABELS = ["Evidence:", "Why it matters:", "Other side:"]


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


def _points_html(title, points):
    seen = set()
    items = []
    for text, subs in points:
        if title == "THE DECISION" and ":" in text:
            lab, rest = text.split(":", 1)
            main = f"<b>{html.escape(lab)}:</b>{tip_html(rest, seen)}"
        else:
            lead = " class='lead'" if subs else ""
            main = f"<span{lead}>{tip_html(text, seen)}</span>"
        sub_html = "".join(
            f"<div class='sub'><b>{html.escape(lab)}</b> {tip_html(val, seen)}</div>"
            for lab, val in subs
        )
        items.append(f"<li>{main}{sub_html}</li>")
    return f"<ul class='points'>{''.join(items)}</ul>"


def _plain_html(points, money_sentence):
    text = " ".join(p[0] for p in points)
    parts = text.split("[MONEY]", 1)
    body = tip_html(parts[0], seen := set())
    money = f"<b>{tip_html(money_sentence, seen)}</b>"
    body += f" {money} {tip_html(parts[1], seen)}" if len(parts) == 2 else f" {money}"
    return (f"<div class='block plain'><div class='label'>IN PLAIN ENGLISH</div>"
            f"<div class='plain-body'>{body}</div></div>")


def _visible_section_html(title, points):
    if title == "WHERE THE EVIDENCE LEANS":
        lean = tip_html(" ".join(p[0] for p in points))
        return f"<div class='lean'>{lean}</div>"

    return (
        f"<div class='block {CLASSES.get(title, '')}'>"
        f"<div class='label'>{title}</div>"
        f"{_points_html(title, points)}</div>"
    )


def _collapsible(title, inner, kind="", open_=False):
    summary = f"<summary class='{kind}'>" if kind else "<summary>"
    return (
        f"<details class='block fold'{' open' if open_ else ''}>"
        f"{summary}{title}</summary>"
        f"<div class='fold-body'>{inner}</div>"
        f"</details>"
    )


def money_html(lines):
    seen = set()
    items = "".join(f"<li>{tip_html(l, seen)}</li>" for l in lines)
    return (f"<div class='block money'><div class='label'>WHAT THE MONEY LOOKS LIKE</div>"
            f"<ul class='points'>{items}</ul></div>")


def _source_html(f):
    src = f.get("source", "")
    url = re.match(r"https?://\S+", src)
    shown = (f"<a href='{html.escape(src, quote=True)}' target='_blank' rel='noopener'>"
             f"{html.escape(re.sub(r'^https?://(www\\.)?', '', src)[:60])}</a>") if url else html.escape(src)
    if "pulled" in f:   # a code-pulled fact: its date can be an upcoming event, so show when it was pulled
        return shown + (f" · pulled {f['pulled']:%b %d, %Y}" if f["pulled"] else "")
    bits =[shown] + ([html.escape(f["date"])] if f.get("date") else []) + ([html.escape(f["check"])] if f.get("check") else [])
    return " · ".join(bits)


def _linked(html_text):
    # Turns bare URLs in already-escaped text into links.
    return re.sub(r"https?://[^\s<>()']+", lambda m: f"<a href='{m.group(0)}' target='_blank' rel='noopener'>"
                  f"{re.sub(r'^https?://(www[.])?', '', m.group(0))[:40]}</a>", html_text)


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
                          f"{_linked(tip_html(val, seen))}</div>" for lab, val in rows)
            items.append(f"<li><span>{_linked(tip_html(f['fact'], seen))}</span>{sub}"
                         f"<div class='source'>{_source_html(f)}</div></li>")
        out.append(f"<div class='topic'>{tip_html(topic)}</div>"
                   f"<ul class='facts'>{''.join(items)}</ul>")
    return "".join(out)


def brief_to_html(decision_brief, details_brief, money_sentence, money_lines, facts_section):
    decision_sections = _parse_sections(decision_brief)
    details_sections = _parse_sections(details_brief)

    # If the judge did not follow the format, show the plain text instead.
    if decision_sections:
        plain = [p for t, p in decision_sections if t == "IN PLAIN ENGLISH"]
        decision_html = _plain_html(plain[0] if plain else [], money_sentence) + money_html(money_lines)
        decision_html += "".join(_visible_section_html(t, p) for t, p in decision_sections if t != "IN PLAIN ENGLISH")
    else:
        decision_html = (money_html(money_lines)
                         + f"<pre class='raw'>{tip_html(decision_brief)}</pre>")

    details_html = facts_section
    if details_sections:
        details_html += "".join(_collapsible(t, _points_html(t, p), CLASSES.get(t, ""))
                                for t, p in details_sections)
    else:
        details_html += f"<pre class='raw'>{tip_html(details_brief)}</pre>"

    return decision_html + details_html


def build_page(ticker, name, today, label, facts, chart_text, chart, research_out, d, decision_brief,
               details_brief, money_sentence, money_lines, hard, topics, research_text):
    details_dump = "\n\n".join([
        "REAL DATA\n" + facts,
        "PRICE CHART DATA\n" + chart_text,
        "RESEARCH (hard facts pulled by code, then Grok)\n" + research_text,
        f"BULL CASE ({d['bull_name']})\n" + d["bull"],
        f"BEAR CASE ({d['bear_name']})\n" + d["bear"],
        f"BULL REPLY ({d['bull_name']})\n" + d["bull_reply"],
        f"BEAR REPLY ({d['bear_name']})\n" + d["bear_reply"],
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

    page = f"""
    <div class='report'>
      <div class='report-title'>{html.escape(str(name))} ({html.escape(ticker)})</div>
      <div class='report-meta'>{today} · {label} · Bull = {d['bull_name']} · Bear = {d['bear_name']} · Judge = Grok · Hover over dotted words for a plain explanation</div>
      {brief_to_html(decision_brief, details_brief, money_sentence, money_lines, facts_section)}
      <div class='disclaimer'>Research, not financial advice.</div>
    </div>
    {chart_html(ticker, chart) if chart else ""}
    <details class='block full-details'>
      <summary>Show full details (data, chart numbers, research, full debate)</summary>
      <pre class='dump'>{tip_html(details_dump)}</pre>
    </details>
    """
    return page


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
