"""
Client Onboarding Wizard.
Tracks per-client onboarding progress across 6 steps.
Generates a Claude-powered client brief HTML report.
"""

import json
from datetime import datetime
from pathlib import Path

from .db import _q, _q1, get_client, save_budget_rules, update_client


REPORTS_DIR = Path(__file__).parent.parent / "reports"

STEPS = [
    {"id": 1, "label": "Basic Info",          "description": "Client name, industry, business type, currency"},
    {"id": 2, "label": "Campaign Goals",       "description": "ROAS targets, CPL targets, campaign type tags"},
    {"id": 3, "label": "Platform Setup",       "description": "Which platforms: Google / Meta / GA4"},
    {"id": 4, "label": "Data Collection",      "description": "Which CSVs to export from each platform"},
    {"id": 5, "label": "Budget Rules",         "description": "ROAS floors and CPL ceilings for the budget agent"},
    {"id": 6, "label": "First Upload",         "description": "Upload your first dataset to Ads OS"},
]

TOTAL_STEPS = len(STEPS)


def get_onboarding_status(conn, client_id: int) -> dict:
    """Return onboarding progress for a client."""
    row = _q1(conn, "SELECT * FROM onboarding_status WHERE client_id = ?", [client_id])
    if not row:
        return {
            "client_id": client_id,
            "steps_completed": [],
            "completed_at": None,
            "pct_complete": 0,
            "is_complete": False,
        }
    steps = json.loads(row["steps_completed"] or "[]")
    pct = round(len(steps) / TOTAL_STEPS * 100)
    return {
        "client_id": client_id,
        "steps_completed": steps,
        "completed_at": row.get("completed_at"),
        "pct_complete": pct,
        "is_complete": len(steps) >= TOTAL_STEPS,
    }


def complete_step(conn, client_id: int, step: int) -> dict:
    """Mark a step as completed for a client. Idempotent."""
    row = _q1(conn, "SELECT * FROM onboarding_status WHERE client_id = ?", [client_id])
    if row:
        steps = json.loads(row["steps_completed"] or "[]")
    else:
        steps = []

    if step not in steps:
        steps.append(step)
        steps.sort()

    all_done = len(steps) >= TOTAL_STEPS
    completed_at = datetime.now().isoformat() if all_done else None

    if row:
        conn.execute(
            "UPDATE onboarding_status SET steps_completed = ?, completed_at = ? WHERE client_id = ?",
            [json.dumps(steps), completed_at, client_id]
        )
    else:
        conn.execute(
            "INSERT INTO onboarding_status (client_id, steps_completed, completed_at) VALUES (?, ?, ?)",
            [client_id, json.dumps(steps), completed_at]
        )
    conn.commit()
    return get_onboarding_status(conn, client_id)


def save_step_data(conn, client_id: int, step: int, data: dict) -> dict:
    """
    Apply step data to the DB, then mark the step complete.

    Step 1: name, currency, industry -> updates clients row (via update_client)
    Step 2: roas_target, cpl_target, notes -> updates client context
    Step 3: platforms -> updates client context
    Step 5: google_min_roas, meta_min_roas, google_min_cpl, meta_min_cpl -> saves budget_rules
    Steps 4, 6: informational only, just mark complete
    """
    if step == 1:
        name = data.get("name", "").strip()
        currency = data.get("currency", "INR")
        client = get_client(conn, client_id)
        ctx_raw = client.get("context") or "{}"
        try:
            ctx = json.loads(ctx_raw)
        except Exception:
            ctx = {}
        if data.get("industry"):
            ctx["industry"] = data["industry"]
        if data.get("business_type"):
            ctx["business_type"] = data["business_type"]
        update_client(conn, client_id, name or client["name"], currency, json.dumps(ctx))

    elif step == 2:
        client = get_client(conn, client_id)
        ctx_raw = client.get("context") or "{}"
        try:
            ctx = json.loads(ctx_raw)
        except Exception:
            ctx = {}
        if data.get("roas_target"):
            ctx["roas_target"] = data["roas_target"]
        if data.get("cpl_target"):
            ctx["cpl_target"] = data["cpl_target"]
        if data.get("campaign_tags"):
            ctx["campaign_tags"] = data["campaign_tags"]
        if data.get("goals_notes"):
            ctx["goals_notes"] = data["goals_notes"]
        update_client(conn, client_id, client["name"], client.get("currency", "INR"), json.dumps(ctx))

    elif step == 3:
        client = get_client(conn, client_id)
        ctx_raw = client.get("context") or "{}"
        try:
            ctx = json.loads(ctx_raw)
        except Exception:
            ctx = {}
        platforms = data.get("platforms", [])
        ctx["platforms"] = platforms
        update_client(conn, client_id, client["name"], client.get("currency", "INR"), json.dumps(ctx))

    elif step == 5:
        rules = {
            "google_min_roas": float(data.get("google_min_roas") or 2.0),
            "meta_min_roas":   float(data.get("meta_min_roas") or 2.0),
            "google_min_cpl":  float(data["google_min_cpl"]) if data.get("google_min_cpl") else None,
            "meta_min_cpl":    float(data["meta_min_cpl"]) if data.get("meta_min_cpl") else None,
            "max_shift_pct":   20.0,
        }
        save_budget_rules(conn, client_id, rules)

    return complete_step(conn, client_id, step)


def generate_client_brief(conn, client_id: int) -> str:
    """
    Generate a 1-page client brief HTML using Claude.
    Returns path to saved HTML file.
    """
    import subprocess

    client = get_client(conn, client_id)
    if not client:
        raise ValueError(f"Client {client_id} not found")

    ctx_raw = client.get("context") or "{}"
    try:
        ctx = json.loads(ctx_raw)
    except Exception:
        ctx = {}

    rules_row = _q1(conn, "SELECT * FROM budget_rules WHERE client_id = ?", [client_id])
    rules = rules_row or {}

    status = get_onboarding_status(conn, client_id)

    prompt = f"""You are an expert digital marketing consultant. Create a clean, professional 1-page client brief for an ad management client.

Client: {client['name']}
Currency: {client.get('currency', 'INR')}
Industry: {ctx.get('industry', 'Not specified')}
Business Type: {ctx.get('business_type', 'Not specified')}
Platforms: {', '.join(ctx.get('platforms', [])) or 'Not specified'}
ROAS Target: {ctx.get('roas_target', 'Not set')}
CPL Target: {ctx.get('cpl_target', 'Not set')}
Campaign Goals: {ctx.get('goals_notes', 'Not specified')}
Campaign Tags: {ctx.get('campaign_tags', 'Not specified')}
Google Min ROAS: {rules.get('google_min_roas', 2.0)}x
Meta Min ROAS: {rules.get('meta_min_roas', 2.0)}x
Google CPL Ceiling: {rules.get('google_min_cpl', 'Not set')}
Meta CPL Ceiling: {rules.get('meta_min_cpl', 'Not set')}
Onboarding Complete: {status['pct_complete']}%

Write a structured client brief with these sections:
1. Client Overview (2-3 sentences about who they are and what they sell)
2. Campaign Objectives (bullet points with ROAS, CPL, volume targets)
3. Platform Strategy (which platforms and why, based on their setup)
4. KPI Benchmarks (table: Metric | Target | Alert Threshold)
5. Tracking Setup (what Ads OS will monitor for them)
6. Next Steps (3-4 bullet points of immediate priorities)

Keep it professional, concise, and actionable. This is a document they will share with their client."""

    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, timeout=90
        )
        brief_text = result.stdout.strip() or "Brief generation failed - please try again."
    except Exception as e:
        brief_text = f"Brief generation error: {e}"

    # Convert markdown to HTML
    lines = brief_text.split("\n")
    html_lines = []
    for line in lines:
        if line.startswith("# "):
            html_lines.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            html_lines.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- ") or line.startswith("* "):
            html_lines.append(f"<li>{line[2:]}</li>")
        elif line.startswith("|"):
            html_lines.append(f"<tr><td>{'</td><td>'.join(c.strip() for c in line.split('|')[1:-1])}</td></tr>")
        elif line.strip() == "" and html_lines and html_lines[-1].startswith("<li>"):
            html_lines.append("</ul><br>")
        elif line.strip() == "":
            html_lines.append("<br>")
        else:
            import re
            line = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            html_lines.append(f"<p>{line}</p>")

    content_html = "\n".join(html_lines)
    # Wrap loose <li> in <ul>
    content_html = content_html.replace("</ul><br>\n<li>", "<li>")
    content_html = content_html.replace("<br>\n<li>", "<ul>\n<li>")
    content_html = content_html.replace("</ul><br>", "</ul>")

    now_str = datetime.now().strftime("%d %b %Y")
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Client Brief - {client['name']}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #fff; color: #1a202c; max-width: 900px; margin: 0 auto; padding: 40px; }}
  .header {{ background: linear-gradient(135deg,#667eea 0%,#764ba2 100%); color: #fff; padding: 32px; border-radius: 12px; margin-bottom: 32px; }}
  .header h1 {{ font-size: 26px; font-weight: 700; }}
  .header p {{ font-size: 13px; opacity: .8; margin-top: 6px; }}
  h2 {{ font-size: 16px; font-weight: 700; color: #667eea; margin: 28px 0 10px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
  h3 {{ font-size: 14px; font-weight: 600; margin: 16px 0 6px; }}
  p {{ font-size: 13px; line-height: 1.7; margin: 6px 0; color: #4a5568; }}
  ul {{ margin: 8px 0 8px 20px; }}
  li {{ font-size: 13px; line-height: 1.7; color: #4a5568; margin: 3px 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  th {{ background: #f7fafc; font-weight: 600; padding: 8px 12px; text-align: left; border: 1px solid #e2e8f0; }}
  td {{ padding: 8px 12px; border: 1px solid #e2e8f0; color: #4a5568; }}
  .footer {{ text-align: center; color: #a0aec0; font-size: 11px; margin-top: 48px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Client Brief - {client['name']}</h1>
  <p>Prepared by Ads OS &bull; {now_str}</p>
</div>
{content_html}
<div class="footer">Confidential &bull; Ads OS &bull; {now_str}</div>
</body>
</html>"""

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"client_brief_{client_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    out_path = REPORTS_DIR / filename
    out_path.write_text(html, encoding="utf-8")
    return f"/report/{filename}"
