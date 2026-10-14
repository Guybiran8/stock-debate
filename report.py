import html

from chart import chart_html

HEADINGS = [
    "WHERE THE EVIDENCE LEANS", "THE DECISION", "BEST BULL POINTS", "BEST BEAR POINTS",
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
    items = []
    for text, subs in points:
        if title == "THE DECISION" and ":" in text:
            lab, rest = text.split(":", 1)
            main = f"<b>{html.escape(lab)}:</b>{html.escape(rest)}"
        else:
            weight = "600" if subs else "400"
            main = f"<span style='font-weight:{weight}'>{html.escape(text)}</span>"
        sub_html = "".join(
            f"<div style='font-size:13px;margin:2px 0 0 0;opacity:.9'>"
            f"<b>{html.escape(lab)}</b> {html.escape(val)}</div>"
            for lab, val in subs
        )
        items.append(f"<li style='margin:7px 0'>{main}{sub_html}</li>")
    return f"<ul style='margin:4px 0 0 0'>{''.join(items)}</ul>"


def _visible_section_html(title, points):
    if title == "WHERE THE EVIDENCE LEANS":
        lean = html.escape(" ".join(p[0] for p in points))
        return f"<div style='font-size:17px;font-weight:700;margin:6px 0 14px 0'>{lean}</div>"

    color = COLORS.get(title, "inherit")
    box = "border-left:3px solid #2962ff;padding-left:12px;" if title == "THE DECISION" else ""
    return (
        f"<div style='margin-top:14px;{box}'>"
        f"<div style='font-weight:700;color:{color}'>{title}</div>"
        f"{_points_html(title, points)}</div>"
    )


def _collapsible_section_html(title, points):
    color = COLORS.get(title, "inherit")
    return (
        f"<details style='margin-top:14px'>"
        f"<summary style='cursor:pointer;font-weight:700;color:{color}'>{title}</summary>"
        f"<div style='margin-top:6px'>{_points_html(title, points)}</div>"
        f"</details>"
    )


def brief_to_html(decision_brief, details_brief):
    decision_sections = _parse_sections(decision_brief)
    details_sections = _parse_sections(details_brief)

    # If the judge did not follow the format, show the plain text instead.
    if decision_sections:
        decision_html = "".join(_visible_section_html(t, p) for t, p in decision_sections)
    else:
        decision_html = f"<pre style='white-space:pre-wrap'>{html.escape(decision_brief)}</pre>"

    if details_sections:
        details_html = "".join(_collapsible_section_html(t, p) for t, p in details_sections)
    else:
        details_html = f"<pre style='white-space:pre-wrap'>{html.escape(details_brief)}</pre>"

    return decision_html + details_html


def build_page(ticker, name, today, label, facts, chart_text, chart, findings, d, decision_brief, details_brief):
    details_dump = "\n\n".join([
        "REAL DATA\n" + facts,
        "PRICE CHART DATA\n" + chart_text,
        "RESEARCH (Grok)\n" + findings,
        f"BULL CASE ({d['bull_name']})\n" + d["bull"],
        f"BEAR CASE ({d['bear_name']})\n" + d["bear"],
        f"BULL REPLY ({d['bull_name']})\n" + d["bull_reply"],
        f"BEAR REPLY ({d['bear_name']})\n" + d["bear_reply"],
    ])

    page = f"""
    <div style='border:1px solid #8886;border-radius:10px;padding:16px 20px;max-width:760px;
                font-family:sans-serif;font-size:14px;line-height:1.5'>
      <div style='font-size:20px;font-weight:700'>{html.escape(str(name))} ({html.escape(ticker)})</div>
      <div style='opacity:.7;font-size:12px'>{today} · {label} · Bull = {d['bull_name']} · Bear = {d['bear_name']} · Judge = Grok</div>
      {brief_to_html(decision_brief, details_brief)}
      <div style='opacity:.7;margin-top:14px;font-size:12px'>Research, not financial advice.</div>
    </div>
    {chart_html(ticker, chart) if chart else ""}
    <details style='max-width:760px;margin-top:12px;font-family:sans-serif'>
      <summary style='cursor:pointer;font-weight:700'>Show full details (data, chart numbers, research, full debate)</summary>
      <pre style='white-space:pre-wrap;font-size:13px'>{html.escape(details_dump)}</pre>
    </details>
    """
    return page
