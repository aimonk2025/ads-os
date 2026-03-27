"""
AI Profitability Copilot.
Builds context from DuckDB, sends question to Claude CLI, parses action triggers.
"""

import json
import subprocess
from .db import _q, _q1
from .context import format_client_context, parse_context_from_json
from .claude_client import stream_prompt


COPILOT_SYSTEM = """You are an expert ad performance analyst and strategist with deep knowledge of Google Ads, Meta Ads, and performance marketing. You have direct access to a client's real campaign data.

You answer questions clearly and concisely, grounded in the actual data provided. Always reference specific campaign names and real numbers from the data. Never make up figures.

When the user asks a strategic question (e.g. "what should I do with X campaign?"), give a direct actionable answer based on the data.

When the user asks a data question (e.g. "which campaign had worst ROAS?"), look through the data and answer precisely.

You may also trigger one of the following actions when it would directly help the user - include ONLY ONE action tag at the very end of your response if warranted, never in the middle:
- [ACTION:run_budget_agent] - if the user asks about budget reallocation, where to shift spend, or which campaigns to pause/scale
- [ACTION:generate_narrative] - if the user asks for a summary, weekly report, or performance narrative to share with a client
- [ACTION:run_forecast] - if the user asks about projected performance, what to expect next month, or revenue/ROAS forecasts

Format your answers in clean markdown. Be direct. Skip preamble. Answer the question first, then add context if needed."""


def build_context(conn, client_id: int) -> str:
    """Build a compact data context string from DuckDB for the given client."""

    # Latest upload info
    latest_upload = _q1(conn, """
        SELECT id, period_label, period_start, period_end, platforms, uploaded_at
        FROM uploads
        WHERE client_id = ?
        ORDER BY uploaded_at DESC LIMIT 1
    """, [client_id])

    if not latest_upload:
        return "No campaign data has been uploaded for this client yet."

    upload_id = latest_upload["id"]
    period = latest_upload.get("period_label") or str(latest_upload.get("period_start") or "recent")

    # Campaign metrics for latest upload
    campaigns = _q(conn, """
        SELECT campaign_name, platform, spend, conversions, conversion_value,
               roas, cac, ctr, cpc, severity, wasted_spend
        FROM campaigns
        WHERE upload_id = ?
        ORDER BY spend DESC
    """, [upload_id])

    # Account-level summary
    summary = _q1(conn, """
        SELECT
            SUM(spend)            AS total_spend,
            SUM(conversions)      AS total_conversions,
            SUM(conversion_value) AS total_revenue,
            CASE WHEN SUM(spend) > 0
                 THEN SUM(conversion_value) / SUM(spend) ELSE 0 END AS blended_roas,
            CASE WHEN SUM(conversions) > 0
                 THEN SUM(spend) / SUM(conversions) ELSE 0 END AS avg_cpa,
            AVG(ctr) AS avg_ctr,
            COUNT(DISTINCT campaign_name) AS campaign_count
        FROM campaigns WHERE upload_id = ?
    """, [upload_id]) or {}

    # Open anomalies
    anomalies = _q(conn, """
        SELECT campaign_name, platform, metric, current_value, baseline_value,
               pct_change, direction, severity, description
        FROM anomalies
        WHERE client_id = ? AND status = 'open'
        ORDER BY severity DESC, detected_at DESC LIMIT 10
    """, [client_id])

    # Open action items
    actions = _q(conn, """
        SELECT text, source, priority, campaign, platform
        FROM action_items
        WHERE client_id = ? AND status = 'todo'
        ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END
        LIMIT 10
    """, [client_id])

    # Historical trend (last 6 uploads)
    trend = _q(conn, """
        SELECT u.period_label, u.period_start,
               SUM(c.spend) AS spend,
               CASE WHEN SUM(c.spend) > 0
                    THEN SUM(c.conversion_value) / SUM(c.spend) ELSE 0 END AS roas,
               SUM(c.conversions) AS conversions
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
        GROUP BY u.id, u.period_label, u.period_start, u.uploaded_at
        ORDER BY COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) DESC
        LIMIT 6
    """, [client_id])

    # Client context
    client = _q1(conn, "SELECT name, currency, context FROM clients WHERE id = ?", [client_id])
    client_name = client.get("name", "Unknown") if client else "Unknown"
    currency = client.get("currency", "INR") if client else "INR"
    context_raw = client.get("context", "") if client else ""

    # Budget rules
    rules = _q1(conn, """
        SELECT google_min_roas, meta_min_roas, google_min_cpl, meta_min_cpl
        FROM budget_rules WHERE client_id = ?
    """, [client_id])

    # Build context string
    lines = [f"CLIENT: {client_name} | Currency: {currency} | Period: {period}"]

    if context_raw:
        briefing = format_client_context(parse_context_from_json(context_raw))
        if briefing:
            lines.append(f"\n{briefing}")

    lines.append(f"\nACCOUNT SUMMARY ({period}):")
    lines.append(f"  Total Spend: {currency} {summary.get('total_spend', 0):,.0f}")
    lines.append(f"  Total Revenue: {currency} {summary.get('total_revenue', 0):,.0f}")
    lines.append(f"  Blended ROAS: {summary.get('blended_roas', 0):.2f}x")
    lines.append(f"  Avg CPA: {currency} {summary.get('avg_cpa', 0):,.0f}")
    lines.append(f"  Avg CTR: {summary.get('avg_ctr', 0):.2f}%")
    lines.append(f"  Campaigns: {summary.get('campaign_count', 0)}")

    if rules:
        lines.append(f"\nPERFORMANCE TARGETS:")
        if rules.get("google_min_roas"):
            lines.append(f"  Google ROAS floor: {rules['google_min_roas']}x")
        if rules.get("meta_min_roas"):
            lines.append(f"  Meta ROAS floor: {rules['meta_min_roas']}x")
        if rules.get("google_min_cpl"):
            lines.append(f"  Google CPL ceiling: {currency} {rules['google_min_cpl']:,.0f}")
        if rules.get("meta_min_cpl"):
            lines.append(f"  Meta CPL ceiling: {currency} {rules['meta_min_cpl']:,.0f}")

    lines.append(f"\nCAMPAIGNS ({len(campaigns)} total, sorted by spend):")
    for c in campaigns:
        severity_tag = f" [{c['severity'].upper()}]" if c.get("severity") else ""
        lines.append(
            f"  [{c['platform'].upper()}] {c['campaign_name']}{severity_tag}"
            f" | Spend: {currency} {c['spend']:,.0f}"
            f" | ROAS: {c['roas']:.2f}x"
            f" | CPA: {currency} {c['cac']:,.0f}"
            f" | CTR: {c['ctr']:.2f}%"
            + (f" | Wasted: {currency} {c['wasted_spend']:,.0f}" if c.get("wasted_spend") else "")
        )

    if anomalies:
        lines.append(f"\nOPEN ANOMALIES ({len(anomalies)}):")
        for a in anomalies:
            lines.append(
                f"  [{a['severity'].upper()}] {a['campaign_name']} ({a['platform']}) - "
                f"{a['description']}"
            )

    if actions:
        lines.append(f"\nPENDING ACTION ITEMS ({len(actions)}):")
        for a in actions:
            lines.append(f"  [{a['priority'].upper()}] {a['text'][:120]}")

    if trend and len(trend) > 1:
        lines.append(f"\nHISTORICAL TREND (last {len(trend)} periods):")
        for t in reversed(trend):
            label = t.get("period_label") or str(t.get("period_start") or "")
            lines.append(
                f"  {label}: Spend {currency} {t['spend']:,.0f} | ROAS {t['roas']:.2f}x | "
                f"Conversions {t['conversions']:,.0f}"
            )

    return "\n".join(lines)


def parse_action(response: str) -> tuple[str, str | None]:
    """
    Extract optional action tag from Claude response.
    Returns (clean_response, action_key_or_None).
    """
    actions = ["run_budget_agent", "generate_narrative", "run_forecast"]
    for action in actions:
        tag = f"[ACTION:{action}]"
        if tag in response:
            clean = response.replace(tag, "").strip()
            return clean, action
    return response, None


def ask_copilot(conn, client_id: int, question: str, history: list) -> dict:
    """
    Send a question to Claude with client data context.
    history: list of {"role": "user"|"assistant", "content": str}
    Returns {"answer": str, "action": str|None, "used_claude": bool}
    """
    context = build_context(conn, client_id)

    # Build conversation for Claude
    history_text = ""
    if history:
        for msg in history[-6:]:  # last 6 messages for context window
            role = "User" if msg["role"] == "user" else "Assistant"
            history_text += f"\n{role}: {msg['content']}\n"

    full_prompt = (
        f"{COPILOT_SYSTEM}\n\n"
        f"=== LIVE CLIENT DATA ===\n{context}\n"
        f"=== END DATA ===\n"
        + (f"\nConversation so far:{history_text}" if history_text else "")
        + f"\nUser: {question}\nAssistant:"
    )

    try:
        result = subprocess.run(
            ["claude", "--print", "--output-format", "text", full_prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip()
            answer, action = parse_action(raw)
            return {"answer": answer, "action": action, "used_claude": True}
        else:
            err = result.stderr.strip() if result.stderr else "No output"
            return {
                "answer": f"Claude is not responding right now. Error: {err}",
                "action": None,
                "used_claude": False
            }
    except subprocess.TimeoutExpired:
        return {
            "answer": "Claude timed out. Please try a shorter question or try again.",
            "action": None,
            "used_claude": False
        }
    except FileNotFoundError:
        return {
            "answer": "Claude CLI is not installed or not in PATH. Please install Claude Code CLI.",
            "action": None,
            "used_claude": False
        }
    except Exception as e:
        return {
            "answer": f"An error occurred: {str(e)}",
            "action": None,
            "used_claude": False
        }


def stream_copilot(conn, client_id: int, question: str, history: list):
    """
    Stream a copilot answer via Claude CLI.
    Yields ('chunk', text) or ('done', (answer, action)).
    Raises on failure.
    """
    context = build_context(conn, client_id)

    history_text = ""
    if history:
        for msg in history[-6:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            history_text += f"\n{role}: {msg['content']}\n"

    full_prompt = (
        f"{COPILOT_SYSTEM}\n\n"
        f"=== LIVE CLIENT DATA ===\n{context}\n"
        f"=== END DATA ===\n"
        + (f"\nConversation so far:{history_text}" if history_text else "")
        + f"\nUser: {question}\nAssistant:"
    )

    full_text = []
    for event_type, text in stream_prompt(full_prompt):
        if event_type == 'chunk':
            full_text.append(text)
            yield 'chunk', text
        elif event_type == 'done':
            raw = text or "".join(full_text)
            answer, action = parse_action(raw)
            yield 'done', (answer, action)
            return
