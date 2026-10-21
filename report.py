import html
import re

from chart import chart_html
from wording import TIP_CSS, tip_html

HEADINGS = [
    "IN PLAIN ENGLISH", "WHERE THE EVIDENCE LEANS", "THE DECISION", "BEST BULL POINTS", "BEST BEAR POINTS",
    "WHERE THEY DISAGREE", "WHAT THE CHART SHOWS", "DATA WARNINGS",
    "CLAIMS THAT FAILED", "CHECK THIS YOURSELF",
]
COLORS = {"BEST BULL POINTS": "#2e9e4f", "BEST BEAR POINTS": "#e04848", "THE DECISION": "#2962ff"}
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
            weight = "600" if subs else "400"
            main = f"<span style='font-weight:{weight}'>{tip_html(text, seen)}</span>"
        sub_html = "".join(
            f"<div style='font-size:13px;margin:2px 0 0 0;opacity:.9'>"
            f"<b>{html.escape(lab)}</b> {tip_html(val, seen)}</div>"
            for lab, val in subs
        )
        items.append(f"<li style='margin:7px 0'>{main}{sub_html}</li>")
    return f"<ul style='margin:4px 0 0 0'>{''.join(items)}</ul>"


def _plain_html(points, money_sentence):
    text = " ".join(p[0] for p in points)
    parts = text.split("[MONEY]", 1)
    body = tip_html(parts[0], seen := set())
    money = f"<b>{tip_html(money_sentence, seen)}</b>"
    body += f" {money} {tip_html(parts[1], seen)}" if len(parts) == 2 else f" {money}"
    return (f"<div style='margin:12px 0 4px 0;padding:12px 14px;border-radius:8px;background:#2962ff14'>"
            f"<div style='font-weight:700;margin-bottom:4px'>IN PLAIN ENGLISH</div>"
            f"<div style='font-size:15px;line-height:1.6'>{body}</div></div>")


def _visible_section_html(title, points):
    if title == "WHERE THE EVIDENCE LEANS":
        lean = tip_html(" ".join(p[0] for p in points))
        return f"<div style='font-size:17px;font-weight:700;margin:6px 0 14px 0'>{lean}</div>"

    color = COLORS.get(title, "inherit")
    box = "border-left:3px solid #2962ff;padding-left:12px;" if title == "THE DECISION" else ""
    return (
        f"<div style='margin-top:14px;{box}'>"
        f"<div style='font-weight:700;color:{color}'>{title}</div>"
        f"{_points_html(title, points)}</div>"
    )


def _collapsible(title, inner, color="inherit", open_=False):
    return (
        f"<details style='margin-top:14px'{' open' if open_ else ''}>"
        f"<summary style='cursor:pointer;font-weight:700;color:{color}'>{title}</summary>"
        f"<div style='margin-top:6px'>{inner}</div>"
        f"</details>"
    )


def money_html(lines):
    seen = set()
    items = "".join(f"<li style='margin:5px 0'>{tip_html(l, seen)}</li>" for l in lines)
    return (f"<div style='margin-top:14px'><div style='font-weight:700'>WHAT THE MONEY LOOKS LIKE</div>"
            f"<ul style='margin:4px 0 0 0'>{items}</ul></div>")


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
            sub = "".join(f"<div style='font-size:13px;margin:2px 0 0 0;opacity:.9'><b>{lab}</b> "
                          f"{_linked(tip_html(val, seen))}</div>" for lab, val in rows)
            items.append(f"<li style='margin:9px 0'><span>{_linked(tip_html(f['fact'], seen))}</span>{sub}"
                         f"<div style='font-size:12px;opacity:.65;margin-top:2px'>{_source_html(f)}</div></li>")
        out.append(f"<div style='margin-top:8px;font-weight:600'>{tip_html(topic)}</div>"
                   f"<ul style='margin:2px 0 0 0'>{''.join(items)}</ul>")
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
                         + f"<pre style='white-space:pre-wrap'>{tip_html(decision_brief)}</pre>")

    details_html = facts_section
    if details_sections:
        details_html += "".join(_collapsible(t, _points_html(t, p), COLORS.get(t, "inherit"))
                                for t, p in details_sections)
    else:
        details_html += f"<pre style='white-space:pre-wrap'>{tip_html(details_brief)}</pre>"

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
        "<div style='font-size:13px;opacity:.8'>Each fact says how it could move the price and when.</div>"
        "<div style='margin-top:10px;font-weight:700'>Pulled from data by code</div>"
        + facts_html(hard, [])
        + "<div style='margin-top:14px;font-weight:700'>From Grok's web research</div>"
          "<div style='font-size:13px;opacity:.8'>The most important facts were checked a second time "
          "against their sources.</div>"
        + facts_html(research_out["facts"], topics)
        + (f"<div style='margin-top:10px;font-weight:600'>What traders are saying on X (opinions, not facts)</div>"
           f"<div style='white-space:pre-wrap;font-size:13px'>{tip_html(research_out['x'])}</div>"
           if research_out.get("x") else ""))

    page = f"""
    {TIP_CSS}
    <div style='border:1px solid #8886;border-radius:10px;padding:16px 20px;max-width:760px;
                font-family:sans-serif;font-size:14px;line-height:1.5'>
      <div style='font-size:20px;font-weight:700'>{html.escape(str(name))} ({html.escape(ticker)})</div>
      <div style='opacity:.7;font-size:12px'>{today} · {label} · Bull = {d['bull_name']} · Bear = {d['bear_name']} · Judge = Grok · Hover over dotted words for a plain explanation</div>
      {brief_to_html(decision_brief, details_brief, money_sentence, money_lines, facts_section)}
      <div style='opacity:.7;margin-top:14px;font-size:12px'>Research, not financial advice.</div>
    </div>
    {chart_html(ticker, chart) if chart else ""}
    <details style='max-width:760px;margin-top:12px;font-family:sans-serif'>
      <summary style='cursor:pointer;font-weight:700'>Show full details (data, chart numbers, research, full debate)</summary>
      <pre style='white-space:pre-wrap;font-size:13px'>{tip_html(details_dump)}</pre>
    </details>
    """
    return page
