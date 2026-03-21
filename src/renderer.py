import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


TEMPLATE_DIR = Path(__file__).parent / "templates"


def format_inr(value: float) -> str:
    """Format a number in Indian Rupee notation: Rs 1,23,456"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if value == 0:
        return "Rs 0"
    is_negative = value < 0
    value = abs(value)
    s = f"{int(value):,}"
    # Convert to Indian numbering (group last 3, then groups of 2)
    parts = s.split(",")
    if len(parts) > 1:
        # Re-format with Indian system
        num_str = str(int(value))
        if len(num_str) > 3:
            last3 = num_str[-3:]
            rest = num_str[:-3]
            groups = []
            while len(rest) > 2:
                groups.insert(0, rest[-2:])
                rest = rest[:-2]
            if rest:
                groups.insert(0, rest)
            s = ",".join(groups) + "," + last3
        else:
            s = num_str
    prefix = "-Rs " if is_negative else "Rs "
    return f"{prefix}{s}"


def roas_fmt(value: float) -> str:
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return "N/A"


def pct_fmt(value: float) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def markdown_to_html(text: str) -> str:
    """Convert basic markdown to HTML."""
    if not text:
        return ""
    # Headers
    text = re.sub(r"^## (.+)$", r"<h2>\1</h2>", text, flags=re.MULTILINE)
    text = re.sub(r"^### (.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Bullet lists
    lines = text.split("\n")
    result = []
    in_list = False
    for line in lines:
        if re.match(r"^[-*] ", line):
            if not in_list:
                result.append("<ul>")
                in_list = True
            result.append(f"<li>{line[2:]}</li>")
        elif re.match(r"^\d+\. ", line):
            if not in_list:
                result.append("<ol>")
                in_list = True
            result.append(f"<li>{re.sub(r'^\d+\. ', '', line)}</li>")
        else:
            if in_list:
                result.append("</ul>" if result[-2] == "<ul>" or (len(result) > 2 and "<li>" in result[-1]) else "</ol>")
                in_list = False
            result.append(line)
    if in_list:
        result.append("</ul>")
    text = "\n".join(result)
    # Wrap paragraphs
    paragraphs = text.split("\n\n")
    final = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith("<h") or p.startswith("<ul") or p.startswith("<ol") or p.startswith("<li"):
            final.append(p)
        else:
            lines_in_p = [l for l in p.split("\n") if l.strip()]
            for line in lines_in_p:
                if line.startswith("<"):
                    final.append(line)
                else:
                    final.append(f"<p>{line}</p>")
    return "\n".join(final)


def parse_claude_sections(markdown: str) -> dict:
    """Split Claude output into named sections."""
    sections = {
        "executive_summary": "",
        "wasted_spend": "",
        "underperformers_text": "",
        "reallocation": "",
        "recommendations_raw": "",
    }

    # Map section headers to keys
    section_map = {
        "executive summary": "executive_summary",
        "wasted spend": "wasted_spend",
        "wasted spend analysis": "wasted_spend",
        "underperforming campaigns": "underperformers_text",
        "budget reallocation": "reallocation",
        "5 recommendations": "recommendations_raw",
        "recommendations": "recommendations_raw",
    }

    current_key = None
    current_lines: list = []

    for line in markdown.split("\n"):
        header_match = re.match(r"^#{1,3}\s+(.+)$", line)
        if header_match:
            if current_key and current_lines:
                sections[current_key] = "\n".join(current_lines).strip()
            heading_text = header_match.group(1).lower().strip()
            matched_key = None
            for pattern, key in section_map.items():
                if pattern in heading_text:
                    matched_key = key
                    break
            current_key = matched_key
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(line)

    if current_key and current_lines:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def parse_recommendations(raw: str) -> list:
    """Parse numbered recommendations into list of {title, body} dicts."""
    recs = []
    if not raw:
        return recs

    # Split on numbered items: "1.", "2.", etc.
    parts = re.split(r"\n(?=\d+\.)", raw.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Remove leading number
        part = re.sub(r"^\d+\.\s*", "", part)
        lines = part.strip().split("\n")
        title_line = lines[0].strip()
        # Remove bold markers from title
        title = re.sub(r"\*\*(.+?)\*\*", r"\1", title_line).strip()
        body_lines = lines[1:] if len(lines) > 1 else []
        body = " ".join(l.strip() for l in body_lines if l.strip())
        body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
        if title:
            recs.append({"title": title, "body": body})

    return recs[:5]


def build_underperformers(analysis_data: dict) -> list:
    """Build sorted underperformer list from analysis data (critical + warning, worst first)."""
    items = []
    for platform in ["google", "meta"]:
        pdata = analysis_data.get(platform)
        if not pdata:
            continue
        for c in pdata.get("campaigns", []):
            if c["severity"] in ("critical", "warning"):
                items.append({**c, "platform": platform})
    # Sort: critical first, then by ROAS ascending
    items.sort(key=lambda x: (0 if x["severity"] == "critical" else 1, x["roas"]))
    return items


def prepare_template_context(analysis_data: dict, claude_output: str) -> dict:
    sections = parse_claude_sections(claude_output)

    google = None
    if analysis_data.get("google"):
        google = {**analysis_data["google"]}

    meta = None
    if analysis_data.get("meta"):
        meta = {**analysis_data["meta"]}

    # Pull granular insights if present (injected by web/app.py audit route)
    granular_insights = analysis_data.get("granular_insights") or {}

    return {
        "generated_at": analysis_data["generated_at"],
        "platforms": analysis_data["platforms"],
        "compare_mode": analysis_data["compare_mode"],
        "google": google,
        "meta": meta,
        "cross_platform": analysis_data.get("cross_platform"),
        "comparison": analysis_data.get("comparison"),
        "funnel_summary": analysis_data.get("funnel_summary"),
        "granularity_level": analysis_data.get("granularity_level", "campaign"),
        "granularity_note": analysis_data.get("granularity_note", ""),
        "adgroup_insights": granular_insights.get("adgroup_insights", []),
        "keyword_insights": granular_insights.get("keyword_insights", []),
        "ad_insights": granular_insights.get("ad_insights", []),
        "executive_summary": markdown_to_html(sections["executive_summary"] or sections.get("wasted_spend", "")),
        "reallocation": markdown_to_html(sections["reallocation"]),
        "underperformers": build_underperformers(analysis_data),
        "recommendations": parse_recommendations(sections["recommendations_raw"]),
    }


def render_report(analysis_data: dict, claude_output: str, output_path: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    env.filters["inr"] = format_inr
    env.filters["roas_fmt"] = roas_fmt
    env.filters["pct_fmt"] = pct_fmt

    template = env.get_template("report.html")
    context = prepare_template_context(analysis_data, claude_output)
    html = template.render(**context)

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
