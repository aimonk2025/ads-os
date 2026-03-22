import subprocess
import json
from typing import Optional


SYSTEM_PROMPT = """You are a senior performance marketer and demand generation strategist with 10+ years of experience auditing Google Ads and Meta Ads campaigns for e-commerce and B2B clients across India and Southeast Asia.

You will receive structured campaign performance data in JSON format. The data may include:
- Ad platform metrics: spend, ROAS, CTR, CPC, CAC
- Funnel metrics (when available): leads, MQLs, SQLs, customers, cost per stage
- Granular breakdown context (when available): keyword-level, ad group-level, or ad-level performance
- Google Analytics 4 metrics (when available): sessions, bounce rate, cost per session, engaged sessions, on-site conversions per campaign

When GA4 data is present, cross-reference ad performance with on-site behavior. A campaign may have a good ROAS but terrible bounce rate - flag this. Identify campaigns where ad clicks are not translating to engaged sessions.

When funnel data is present, analyze the full funnel - not just ad metrics. Identify where the funnel leaks (high lead volume but low MQL rate, etc.).

When granular breakdown context is present, always recommend at the most granular level the data supports:
- If keyword data is present: name specific keywords to pause, match type changes, or bid adjustments
- If ad group / ad set data is present: name specific ad groups or Meta ad sets to restructure or pause
- If ad-level data is present: name specific creatives that are underperforming
- Never stay at campaign level if deeper data is available - the goal is actionable, specific recommendations

When business context is provided by the marketer, use it to interpret campaign intent:
- Do not flag brand awareness or experimental campaigns as "wasted spend" if the context explains their purpose
- Align recommendations with the stated business goals and audience
- Reference the context when explaining why a campaign is or is not performing as expected

Your analysis must include exactly these sections in this order:

## Executive Summary
ONE paragraph, client-friendly language. Mention total spend, overall performance, and the single most critical issue. If funnel data is present, mention the cost per customer and where the biggest funnel leak is.

## Wasted Spend Analysis
Which campaigns are burning money and why. Be specific about campaign names, amounts, and which funnel stage is failing.

## Funnel Analysis
Only include this section if funnel data is present in the JSON.
Analyze the lead-to-customer journey:
- Where is the funnel leaking most (worst conversion rate between stages)?
- Which campaigns have the best/worst cost per MQL, cost per SQL, cost per customer?
- What does the data suggest about lead quality vs lead volume?

## Underperforming Campaigns
Ranked worst to best. For each: campaign name, the specific metric that is failing, and the likely cause.

## Budget Reallocation
Specific recommendation: which campaigns to reduce budget on, which to increase, and by approximately how much. If funnel data is present, prioritize campaigns by cost per customer, not just ROAS.

## Recommendations
As many as the data warrants. Each must:
- Name the most specific entity possible: keyword, ad set, ad creative, or campaign - whichever level the data supports
- State the specific metric to improve
- Give a concrete action to take
- Be achievable within 2 weeks
- Include a confidence score in brackets: [Confidence: High], [Confidence: Medium], or [Confidence: Low]
  - High: clear data signal, direct causation, well-established best practice
  - Medium: likely cause but requires testing to confirm, or limited data
  - Low: hypothesis based on partial data, or multiple possible explanations
- For Meta: recommend at ad set level (audience, placement, budget) when ad set data is present
- For Google: recommend at keyword level (match type, bid, negative keywords) when keyword data is present
- If GA4 data is present: include recommendations that address on-site behavior (bounce rate, session quality, landing page relevance)
- If funnel data is present: include recommendations that address funnel quality (MQL rate, SQL rate, or close rate)

Output format: Clean markdown only. No preamble. Start directly with ## Executive Summary."""


def is_claude_available() -> bool:
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def analyze(analysis_data: dict, business_context: str = "") -> tuple[str, bool]:
    """
    Call Claude CLI with the analysis data.
    Returns (output_text, used_claude) tuple.
    used_claude is False if fallback was used.
    """
    if not is_claude_available():
        print("  WARNING: Claude CLI not found - using fallback analysis template")
        return fallback_template(analysis_data), False

    # Inject granular context as plain text, keep JSON payload compact
    payload = {k: v for k, v in analysis_data.items()
               if k not in ("granular_context", "granularity_level")}
    gran_context = analysis_data.get("granular_context", "")

    data_json = json.dumps(payload, indent=2)

    context_section = (
        f"\n\nBusiness Context (provided by the marketer - use this to interpret campaign intent and goals):\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    gran_section = (
        f"\n\nGranular breakdown context (use for deeper keyword/ad group/ad set/ad level recommendations):\n{gran_context}"
        if gran_context else ""
    )
    full_prompt = f"{SYSTEM_PROMPT}\n\nHere is the campaign performance data to analyze:\n\n{data_json}{context_section}{gran_section}"

    try:
        result = subprocess.run(
            ["claude", "--print", full_prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), True
        else:
            err = result.stderr.strip() if result.stderr else "No output returned"
            print(f"  WARNING: Claude returned error: {err} - using fallback")
            return fallback_template(analysis_data), False
    except subprocess.TimeoutExpired:
        print("  WARNING: Claude timed out after 90s - using fallback")
        return fallback_template(analysis_data), False
    except Exception as e:
        print(f"  WARNING: Claude call failed ({e}) - using fallback")
        return fallback_template(analysis_data), False


def fallback_template(data: dict) -> str:
    """Generate a basic markdown report from raw metrics when Claude is unavailable."""
    lines = []

    lines.append("## Executive Summary")
    platforms = data.get("platforms", [])
    total_spend = 0
    roas_vals = []

    if data.get("google"):
        g = data["google"]
        total_spend += g["total_spend"]
        roas_vals.append(g["overall_roas"])

    if data.get("meta"):
        m = data["meta"]
        total_spend += m["total_spend"]
        roas_vals.append(m["overall_roas"])

    avg_roas = sum(roas_vals) / len(roas_vals) if roas_vals else 0
    lines.append(
        f"This audit covers {', '.join(p.title() for p in platforms)} Ads with a total spend of "
        f"Rs {total_spend:,.0f}. The blended ROAS is {avg_roas:.2f}x. "
        f"Immediate action is required on campaigns with ROAS below 1.0x to stop wasted spend."
    )
    lines.append("")

    lines.append("## Wasted Spend Analysis")
    for platform in platforms:
        pdata = data.get(platform, {})
        if not pdata:
            continue
        critical = [c for c in pdata.get("campaigns", []) if c["severity"] == "critical"]
        if critical:
            lines.append(f"**{platform.title()} Ads - Critical Campaigns:**")
            for c in critical:
                lines.append(f"- **{c['name']}**: ROAS {c['roas']}x, Spend Rs {c['spend']:,.0f} (100% wasted)")
        else:
            lines.append(f"No critical campaigns found in {platform.title()} Ads.")
    lines.append("")

    lines.append("## Underperforming Campaigns")
    all_campaigns = []
    for platform in platforms:
        pdata = data.get(platform, {})
        if pdata:
            for c in pdata.get("campaigns", []):
                all_campaigns.append((platform, c))
    all_campaigns.sort(key=lambda x: x[1]["roas"])
    for i, (platform, c) in enumerate(all_campaigns[:5], 1):
        lines.append(f"{i}. **{c['name']}** ({platform.title()}) - ROAS: {c['roas']}x, CAC: Rs {c['cac']:,.0f}")
    lines.append("")

    # Funnel analysis section
    funnel = data.get("funnel_summary")
    if funnel and funnel.get("stages_available"):
        lines.append("## Funnel Analysis")
        stages = funnel.get("stages_available", [])
        if "leads" in stages:
            lines.append(f"- **Total Leads:** {funnel.get('total_leads', 0):,.0f} | Cost per Lead: Rs {funnel.get('cost_per_lead', 0):,.0f}")
        if "mqls" in stages:
            lines.append(f"- **Total MQLs:** {funnel.get('total_mqls', 0):,.0f} | Cost per MQL: Rs {funnel.get('cost_per_mql', 0):,.0f}")
            if funnel.get("lead_to_mql_rate"):
                lines.append(f"- Lead to MQL rate: {funnel['lead_to_mql_rate']}%")
        if "sqls" in stages:
            lines.append(f"- **Total SQLs:** {funnel.get('total_sqls', 0):,.0f} | Cost per SQL: Rs {funnel.get('cost_per_sql', 0):,.0f}")
            if funnel.get("mql_to_sql_rate"):
                lines.append(f"- MQL to SQL rate: {funnel['mql_to_sql_rate']}%")
        if "customers" in stages:
            lines.append(f"- **Total Customers:** {funnel.get('total_customers', 0):,.0f} | Cost per Customer: Rs {funnel.get('cost_per_customer', 0):,.0f}")
            if funnel.get("sql_to_customer_rate"):
                lines.append(f"- SQL to Customer rate: {funnel['sql_to_customer_rate']}%")
        if funnel.get("overall_conversion_rate"):
            lines.append(f"- Overall lead-to-customer rate: {funnel['overall_conversion_rate']}%")
        lines.append("")

    lines.append("## Budget Reallocation")
    lines.append(
        "Pause all campaigns with ROAS below 1.0x immediately. Reallocate that budget to your "
        "top-performing campaigns. Review campaigns with ROAS between 1.0-2.0x and set tighter "
        "audience targeting before scaling."
    )
    if funnel and "customers" in (funnel.get("stages_available") or []):
        lines.append(
            " Prioritize campaigns with the lowest cost per customer when reallocating budget."
        )
    lines.append("")

    lines.append("## Recommendations")
    critical_all = []
    warning_all = []
    has_google = bool(data.get("google"))
    has_meta = bool(data.get("meta"))
    has_funnel = bool(data.get("funnel_summary") and data["funnel_summary"].get("stages_available"))
    has_ga4 = any(
        c.get("ga") for platform in platforms
        for c in (data.get(platform) or {}).get("campaigns", [])
    )
    has_keywords = bool(data.get("granularity_level") in ("keyword", "ad_group"))

    for platform in platforms:
        pdata = data.get(platform, {})
        if pdata:
            for c in pdata.get("campaigns", []):
                if c["severity"] == "critical":
                    critical_all.append((platform, c))
                elif c["severity"] == "warning":
                    warning_all.append((platform, c))

    recs = []
    if critical_all:
        c = critical_all[0][1]
        recs.append(f"**Pause {c['name']}** ({critical_all[0][0].title()}) - ROAS is {c['roas']}x. Pause immediately and audit targeting before reactivating. [Confidence: High]")
    if warning_all:
        c = warning_all[0][1]
        recs.append(f"**Reduce budget on {c['name']}** ({warning_all[0][0].title()}) - ROAS {c['roas']}x is below target. Cut spend by 25% while testing optimizations. [Confidence: High]")
    if has_keywords:
        recs.append("**Review keyword match types** - Switch broad match keywords on low-ROAS campaigns to phrase or exact match to reduce irrelevant traffic. [Confidence: Medium]")
    if has_google and not has_keywords:
        recs.append("**Upload keyword-level data** - Run a search terms report in Google Ads and upload for more specific recommendations. [Confidence: High]")
    if has_ga4:
        recs.append("**Address high bounce rates** - Campaigns with bounce rate above 70% likely have landing page or audience mismatch. Review and align ad copy with landing page. [Confidence: Medium]")
    if has_funnel:
        recs.append("**Focus on funnel leakage** - Identify the stage with the worst conversion rate and invest in fixing it before scaling spend. [Confidence: Medium]")
    if has_google:
        recs.append("**Enable Target ROAS bidding** - For Google campaigns with 30+ conversions/month, switch to Target ROAS to let Smart Bidding optimize toward your goal. [Confidence: Medium]")
    if has_meta:
        recs.append("**Test broad audience targeting** - Meta's Advantage+ Audiences often outperform narrow interest targeting. Run a 30-day test with Advantage+ enabled. [Confidence: Low]")
    if not recs:
        recs.append("**Upload additional data** - Connect GA4 data, keyword reports, or funnel data for more specific recommendations. [Confidence: High]")

    for i, rec in enumerate(recs, 1):
        lines.append(f"{i}. {rec}")

    return "\n".join(lines)
