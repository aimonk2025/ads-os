"""
Performance Narrator - generates client-ready narrative reports via Claude CLI.
"""

import json
import subprocess
from typing import Optional


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


def generate_narrative(
    analysis_data: dict,
    anomalies: list,
    tone: str = "executive",
    date_range: str = None,
    currency: str = "INR",
    business_context: str = "",
) -> tuple[str, bool]:
    payload = _build_payload(analysis_data, anomalies, tone, date_range, currency)
    context_section = (
        f"\n\nBusiness Context (use this to interpret campaign intent and tailor recommendations):\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    prompt = f"{NARRATOR_SYSTEM_PROMPT}\n\nData:\n{json.dumps(payload, indent=2)}{context_section}"

    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, timeout=90
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), True
        return _fallback_narrative(payload, tone, currency), False
    except Exception:
        return _fallback_narrative(payload, tone, currency), False


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


def _fallback_narrative(payload: dict, tone: str, currency: str) -> str:
    sym = "Rs" if currency == "INR" else currency

    lines = []
    lines.append("## This Week in Numbers")

    spend_parts = []
    for platform in ["google", "meta"]:
        p = payload.get(platform)
        if p:
            spend_parts.append(
                f"{platform.title()} Ads: {sym} {p['total_spend']:,.0f} spend, "
                f"{p['overall_roas']:.2f}x ROAS"
            )
    if spend_parts:
        lines.append(" | ".join(spend_parts) + ".")

    funnel = payload.get("funnel")
    if funnel:
        if funnel.get("cost_per_lead"):
            lines.append(f"Cost per Lead: {sym} {funnel['cost_per_lead']:,.0f}.")
        if funnel.get("cost_per_customer"):
            lines.append(f"Cost per Customer: {sym} {funnel['cost_per_customer']:,.0f}.")
    lines.append("")

    lines.append("## What Worked")
    for platform in ["google", "meta"]:
        p = payload.get(platform)
        if p and p.get("top_campaigns"):
            for c in p["top_campaigns"][:2]:
                lines.append(f"- **{c['name']}** ({platform.title()}): {c['roas']:.2f}x ROAS")
    if not any(payload.get(p, {}).get("top_campaigns") for p in ["google", "meta"]):
        lines.append("No campaigns exceeded the 2x ROAS threshold this period.")
    lines.append("")

    lines.append("## What Needs Attention")
    anomalies = payload.get("anomalies", [])
    if anomalies:
        for a in anomalies[:3]:
            lines.append(f"- **{a['campaign']}**: {a['description']}")
    else:
        for platform in ["google", "meta"]:
            p = payload.get(platform)
            if p and p.get("worst_campaigns"):
                for c in p["worst_campaigns"][:2]:
                    lines.append(f"- **{c['name']}** ({platform.title()}): {c['roas']:.2f}x ROAS - below threshold")
    lines.append("")

    lines.append("## Recommended Actions for Next Week")
    lines.append("1. Pause campaigns with ROAS below 1.0x immediately to stop wasted spend.")
    lines.append("2. Increase budget on campaigns exceeding 3x ROAS by 10-15%.")
    lines.append("3. Review audience targeting on underperforming campaigns before reactivating.")
    if funnel and funnel.get("lead_to_mql_rate") and funnel["lead_to_mql_rate"] < 30:
        lines.append("4. Improve lead qualification - current lead-to-MQL rate is below 30%.")
    lines.append("")

    return "\n".join(lines)
