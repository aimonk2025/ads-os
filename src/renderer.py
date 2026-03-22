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


def _parse_md_table(block: str) -> str:
    """Convert a markdown table block into a styled HTML table."""
    lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
    # filter out separator rows like |---|---|
    rows = [l for l in lines if not re.match(r"^\|[-| :]+\|$", l)]
    if len(rows) < 2:
        return block
    def parse_row(line):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return cells
    header_cells = parse_row(rows[0])
    thead = "<tr>" + "".join(f"<th>{c}</th>" for c in header_cells) + "</tr>"
    tbody_rows = []
    for row in rows[1:]:
        cells = parse_row(row)
        tbody_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<div class="md-table-wrap">'
        f"<table><thead>{thead}</thead><tbody>{''.join(tbody_rows)}</tbody></table>"
        "</div>"
    )


def _parse_tsv_table(block: str) -> str:
    """Convert a tab-separated table block into a styled HTML table."""
    lines = [l for l in block.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return block
    def parse_row(line):
        return [c.strip() for c in line.split("\t")]
    header_cells = parse_row(lines[0])
    thead = "<tr>" + "".join(f"<th>{c}</th>" for c in header_cells) + "</tr>"
    tbody_rows = []
    for row in lines[1:]:
        cells = parse_row(row)
        tbody_rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (
        '<div class="md-table-wrap">'
        f"<table><thead>{thead}</thead><tbody>{''.join(tbody_rows)}</tbody></table>"
        "</div>"
    )


_SEVERITY_BADGE = {
    "HIGH":   '<span class="severity-badge severity-high">HIGH</span>',
    "MEDIUM": '<span class="severity-badge severity-medium">MEDIUM</span>',
    "LOW":    '<span class="severity-badge severity-low">LOW</span>',
}


# Known UTF-8-as-Latin-1 mojibake sequences produced by Windows subprocess encoding issues
_ENCODING_FIXES = {
    "â‚¹": "₹",
    "â€"": "-",
    "â€™": "'",
    "â€œ": '"',
    "â€\x9d": '"',
    "â€˜": "'",
    "â€¦": "...",
    "Ã©": "é",
    "Ã ": "à",
}


def fix_encoding_artifacts(text: str) -> str:
    """Replace known UTF-8-as-Latin-1 mojibake sequences with correct characters."""
    for bad, good in _ENCODING_FIXES.items():
        text = text.replace(bad, good)
    return text


def markdown_to_html(text: str) -> str:
    """Convert basic markdown to HTML."""
    if not text:
        return ""
    # Fix encoding artifacts before any other processing
    text = fix_encoding_artifacts(text)
    # Strip all fenced code blocks - internal data, never shown to users
    text = re.sub(r"```[^\n]*\n[\s\S]*?```", "", text)
    # Also strip any orphan fence lines (``` with no closing match)
    text = re.sub(r"^```[^\n]*$", "", text, flags=re.MULTILINE)
    # Headers
    text = re.sub(r"^#### (.+)$", r"<h4>\1</h4>", text, flags=re.MULTILINE)
    text = re.sub(r"^### (.+)$", r"<h3>\1</h3>", text, flags=re.MULTILINE)
    text = re.sub(r"^## (.+)$", r"<h2>\1</h2>", text, flags=re.MULTILINE)
    text = re.sub(r"^# (.+)$", r"<h1>\1</h1>", text, flags=re.MULTILINE)
    # Bold / italic
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code (backtick-wrapped) -> <code>
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Horizontal rule
    text = re.sub(r"^---+$", r"<hr>", text, flags=re.MULTILINE)
    # Severity labels: bare line OR wrapped in <strong> -> styled badge
    for label, badge in _SEVERITY_BADGE.items():
        text = re.sub(rf"^{label}$", badge, text, flags=re.MULTILINE)
        text = re.sub(rf"<strong>{label}</strong>", badge, text)

    # Parse pipe markdown tables before line-by-line processing
    def replace_table(m):
        return _parse_md_table(m.group(0))
    text = re.sub(r"(\|.+\|\n(?:\|[-| :]+\|\n)?(?:\|.+\|\n?)+)", replace_table, text)

    # Parse tab-separated tables - allow optional blank line between header and rows
    def replace_tsv_table(m):
        return _parse_tsv_table(m.group(0))
    text = re.sub(r"((?:[^\n<]+\t[^\n]+\n){1}(?:\n?(?:[^\n<]+\t[^\n]+\n))+)", replace_tsv_table, text)

    # Bullet lists
    lines = text.split("\n")
    result = []
    in_ul = False
    in_ol = False
    for line in lines:
        if re.match(r"^[-*] ", line):
            if in_ol:
                result.append("</ol>")
                in_ol = False
            if not in_ul:
                result.append("<ul>")
                in_ul = True
            result.append(f"<li>{line[2:]}</li>")
        elif re.match(r"^\d+\. ", line):
            if in_ul:
                result.append("</ul>")
                in_ul = False
            if not in_ol:
                result.append("<ol>")
                in_ol = True
            result.append(f"<li>{re.sub(r'^\\d+\\.\\s*', '', line)}</li>")
        else:
            if in_ul:
                result.append("</ul>")
                in_ul = False
            if in_ol:
                result.append("</ol>")
                in_ol = False
            result.append(line)
    if in_ul:
        result.append("</ul>")
    if in_ol:
        result.append("</ol>")
    text = "\n".join(result)

    # Wrap plain text lines as paragraphs (skip lines that are already HTML)
    paragraphs = re.split(r"\n{2,}", text)
    final = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if re.match(r"^<(h[1-4]|ul|ol|li|hr|div|table|tr|thead|tbody|span)", p):
            final.append(p)
        else:
            lines_in_p = [l for l in p.split("\n") if l.strip()]
            for line in lines_in_p:
                if re.match(r"^<", line):
                    final.append(line)
                else:
                    final.append(f"<p>{line}</p>")
    return "\n".join(final)


def parse_claude_sections(markdown: str) -> dict:
    """Split Claude output into named sections."""
    markdown = fix_encoding_artifacts(markdown)
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
    """Parse numbered recommendations into list of {title, bullets, prose} dicts."""
    recs = []
    if not raw:
        return recs

    # Split on lines that start with a number+dot (1. 2. etc.) at the beginning of the line
    lines = raw.strip().split("\n")
    blocks = []
    current = []
    for line in lines:
        if re.match(r"^\d+\.\s+\S", line) and current:
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append(current)

    for block in blocks:
        block_lines = [l.strip() for l in block if l.strip()]
        if not block_lines:
            continue
        # First line is the title - strip leading number
        title_line = re.sub(r"^\d+\.\s*", "", block_lines[0])
        title = re.sub(r"\*\*(.+?)\*\*", r"\1", title_line).strip()

        bullets = []
        prose = []
        for line in block_lines[1:]:
            # "- Campaign: X" or "- Metric: Y" style → pill tag
            m = re.match(r"^[-*]\s*\*?\*?([^:*\n]+?)\*?\*?:\s*(.+)$", line)
            if m:
                label = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(1)).strip()
                value = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(2)).strip().rstrip("*")
                bullets.append({"label": label, "value": value})
            elif re.match(r"^[-*]\s+", line):
                prose.append(re.sub(r"^[-*]\s+", "", re.sub(r"\*\*(.+?)\*\*", r"\1", line)).strip())
            else:
                cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", line).strip()
                if cleaned:
                    prose.append(cleaned)

        if title:
            recs.append({"title": title, "bullets": bullets, "prose": prose})

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

    has_ga = analysis_data.get("has_ga", False)
    if not has_ga:
        for platform in [google, meta]:
            if platform:
                for c in platform.get("campaigns", []):
                    if c.get("ga"):
                        has_ga = True
                        break

    return {
        "generated_at": analysis_data["generated_at"],
        "platforms": analysis_data["platforms"],
        "compare_mode": analysis_data["compare_mode"],
        "has_ga": has_ga,
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
        "recommendations": markdown_to_html(sections["recommendations_raw"]),
    }


def render_report(analysis_data: dict, claude_output: str, output_path: str,
                  branding: dict = None, extra: dict = None) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    env.filters["inr"] = format_inr
    env.filters["roas_fmt"] = roas_fmt
    env.filters["pct_fmt"] = pct_fmt

    template = env.get_template("report.html")
    context = prepare_template_context(analysis_data, claude_output)
    context["branding"] = branding or {}
    if extra:
        context.update(extra)
    html = template.render(**context)

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
