"""
Performance Narrator - generates client-ready narrative reports via Claude CLI.
"""

import json
import subprocess
from typing import Optional
from .claude_client import stream_prompt


NARRATOR_SYSTEM_PROMPT = """You are a senior performance marketing analyst writing a weekly performance narrative for a client report. Your tone must match the requested style exactly.

You receive structured JSON with platform metrics, week-over-week changes, funnel data, and detected anomalies.

Output clean markdown with EXACTLY these four sections:

## This Week in Numbers
## What Worked
## What Needs Attention
## Recommended Actions for Next Week

Tone guide:
- executive: 3-4 sentences per section, high-level, no jargon, suitable for C-suite
- detailed: full paragraph per section, metric-specific, technical, suitable for agency QBR
- urgent: lead with problems, direct language, short sentences, suitable for internal crisis brief

Currency format: use the currency symbol provided in the data (Rs for INR, $ for USD, etc.)
Do not include headers or preamble before ## This Week in Numbers.
Output markdown only."""


def stream_narrative(
    analysis_data: dict,
    anomalies: list,
    tone: str = "executive",
    date_range: str = None,
    currency: str = "INR",
    business_context: str = "",
):
    """Yields ('chunk', text) or ('done', full_text). Raises on failure."""
    payload = _build_payload(analysis_data, anomalies, tone, date_range, currency)
    context_section = (
        f"\n\nBusiness Context (use this to interpret campaign intent and tailor recommendations):\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    prompt = f"{NARRATOR_SYSTEM_PROMPT}\n\nData:\n{json.dumps(payload, indent=2)}{context_section}"

    for event_type, text in stream_prompt(prompt):
        if event_type == 'chunk':
            yield ('chunk', text)
        elif event_type == 'done':
            yield ('done', text)
            return


def generate_narrative(
    analysis_data: dict,
    anomalies: list,
    tone: str = "executive",
    date_range: str = None,
    currency: str = "INR",
    business_context: str = "",
) -> str:
    """Returns Claude narrative text. Raises on failure."""
    payload = _build_payload(analysis_data, anomalies, tone, date_range, currency)
    context_section = (
        f"\n\nBusiness Context (use this to interpret campaign intent and tailor recommendations):\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    prompt = f"{NARRATOR_SYSTEM_PROMPT}\n\nData:\n{json.dumps(payload, indent=2)}{context_section}"

    result = subprocess.run(
        ["claude", "--print", "--output-format", "text", prompt],
        capture_output=True, text=True, encoding="utf-8", timeout=90
    )
    if result.returncode != 0 or not result.stdout.strip():
        err = result.stderr.strip() if result.stderr else "No output returned"
        raise RuntimeError(f"Claude CLI error: {err}")
    return result.stdout.strip()


def _build_payload(analysis_data: dict, anomalies: list, tone: str,
                   date_range: str, currency: str) -> dict:
    payload: dict = {
        "tone": tone,
        "currency": currency,
        "date_range": date_range or "This week",
        "anomaly_count": len(anomalies),
        "anomalies": [
            {"campaign": a["campaign_name"], "metric": a["metric"],
             "description": a["description"], "severity": a["severity"]}
            for a in anomalies[:10]
        ],
    }

    for platform in ["google", "meta"]:
        pdata = analysis_data.get(platform)
        if not pdata:
            continue
        payload[platform] = {
            "total_spend":    pdata.get("total_spend"),
            "overall_roas":   pdata.get("overall_roas"),
            "overall_cac":    pdata.get("overall_cac"),
            "overall_ctr":    pdata.get("overall_ctr"),
            "wasted_spend":   pdata.get("wasted_spend"),
            "critical_count": pdata.get("critical_count"),
            "warning_count":  pdata.get("warning_count"),
            "campaign_count": pdata.get("campaign_count"),
            "top_campaigns":  sorted(
                [c for c in pdata.get("campaigns", []) if c["severity"] == "ok"],
                key=lambda x: x["roas"], reverse=True
            )[:3],
            "worst_campaigns": [c for c in pdata.get("campaigns", []) if c["severity"] == "critical"][:3],
        }

    fs = analysis_data.get("funnel_summary")
    if fs:
        payload["funnel"] = {
            "stages":                  fs.get("stages_available"),
            "cost_per_lead":           fs.get("cost_per_lead"),
            "cost_per_mql":            fs.get("cost_per_mql"),
            "cost_per_customer":       fs.get("cost_per_customer"),
            "lead_to_mql_rate":        fs.get("lead_to_mql_rate"),
            "overall_conversion_rate": fs.get("overall_conversion_rate"),
        }

    comp = analysis_data.get("comparison")
    if comp:
        payload["period_comparison"] = comp

    return payload


