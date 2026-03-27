import subprocess
import json
from typing import Optional


SYSTEM_PROMPT = """You are a senior performance marketer and demand generation strategist with 10+ years of experience auditing Google Ads and Meta Ads campaigns for e-commerce and B2B clients across India and Southeast Asia.

You will receive structured campaign performance data in JSON format. The data may include:
- Ad platform metrics: spend, ROAS, CTR, CPC, CAC, efficiency (conversions per Rs 1000 spent — use this to compare campaigns on cost-effectiveness independent of conversion value)
- learning_phase (boolean): true means the campaign has fewer than 1000 impressions AND fewer than 10 conversions — it does not have enough data to evaluate fairly. If learning_phase is absent from the data, treat all campaigns as having sufficient data.
- Funnel metrics (when available): leads, MQLs, SQLs, customers, cost per stage
- Granular breakdown context (when available): keyword-level, ad group-level, or ad-level performance
- Google Analytics 4 metrics (when available): sessions, bounce rate, cost per session, engaged sessions, on-site conversions per campaign

When GA4 data is present, cross-reference ad performance with on-site behavior. A campaign may have a good ROAS but terrible bounce rate - flag this. Identify campaigns where ad clicks are not translating to engaged sessions.

When Meta campaign data includes frequency values above 3.5, flag those campaigns as at risk of creative fatigue — high frequency causes CTR decline and CPM inflation. Recommend creative refresh before pausing the campaign.

When funnel data is present, analyze the full funnel - not just ad metrics. Identify where the funnel leaks (high lead volume but low MQL rate, etc.).

When granular breakdown context is present, always recommend at the most granular level the data supports:
- If keyword data is present: name specific keywords to pause, match type changes, or bid adjustments
- If ad group / ad set data is present: name specific ad groups or Meta ad sets to restructure or pause
- If ad-level data is present: name specific creatives that are underperforming
- Never stay at campaign level if deeper data is available - the goal is actionable, specific recommendations

When anomaly data is present per campaign, treat campaigns with 2+ concurrent anomalies as critical regardless of ROAS — multiple simultaneous signals indicate a systemic issue, not a random fluctuation.

When business context is provided by the marketer, use it to interpret campaign intent:
- Do not flag brand awareness or experimental campaigns as "wasted spend" if the context explains their purpose
- Align recommendations with the stated business goals and audience
- Reference the context when explaining why a campaign is or is not performing as expected

Before writing any section: scan all campaigns for learning_phase=true and hold those campaign names in mind. These campaigns have fewer than 1000 impressions and 10 conversions — insufficient data to judge performance. Never recommend pausing or cutting budget on them. If learning_phase is not present in the data, skip this check entirely.

Your analysis must include exactly these sections in this order:

## Executive Summary
ONE paragraph, client-friendly language. Mention total spend, overall ROAS, and name the single most efficient campaign (highest efficiency score) and least efficient (lowest). If funnel data is present, mention the cost per customer and where the biggest funnel leak is.

## Wasted Spend Analysis
Which campaigns are burning money and why. Be specific about campaign names, amounts, and which funnel stage is failing. EXCEPTION: Do not flag campaigns with learning_phase=true as wasted spend — they are still gathering conversion data. Use direct declarative sentences only. State facts from the data.

## Funnel Analysis
Only include this section if funnel data is present in the JSON.
Analyze the lead-to-customer journey:
- The worst_funnel_stage field in the data identifies the biggest leak — focus your analysis there first
- Which campaigns have the best/worst cost per MQL, cost per SQL, cost per customer?
- What does the data suggest about lead quality vs lead volume?
- State the specific conversion rate at the worst stage and what a 10% improvement there would mean in cost per customer

## Underperforming Campaigns
Ranked worst to best. For each: campaign name, the specific metric that is failing, the trend direction — REQUIRED, write one of: improving/declining/stable — and the likely cause.

## Budget Reallocation
Specific recommendation: which campaigns to reduce budget on, which to increase, and by approximately how much. If funnel data is present, prioritize campaigns by cost per customer, not just ROAS.

## Recommendations
Start with: 'Learning phase campaigns (do not pause): [name every campaign with learning_phase=true]'. These must never appear in pause or budget cut recommendations.
As many as the data warrants. Each must:
- Name the most specific entity possible: keyword, ad set, ad creative, or campaign - whichever level the data supports
- State the specific metric to improve
- Give a concrete action to take
- Be achievable within 2 weeks
- Every recommendation MUST end with a confidence score in brackets. A recommendation without one is incomplete and must not be output. Format: [Confidence: High], [Confidence: Medium], or [Confidence: Low]
  - High: clear data signal, direct causation, well-established best practice
  - Medium: likely cause but requires testing to confirm, or limited data
  - Low: hypothesis based on partial data, or multiple possible explanations
- For Meta: recommend at ad set level (audience, placement, budget) when ad set data is present
- For Google: recommend at keyword level (match type, bid, negative keywords) when keyword data is present
- If GA4 data is present: include recommendations that address on-site behavior (bounce rate, session quality, landing page relevance)
- If funnel data is present: include recommendations that address funnel quality (MQL rate, SQL rate, or close rate)

Banned phrases - NEVER use these: "it is important to", "it is worth noting", "leverage", "it is crucial", "it should be noted", "going forward", "in order to", "consider", "you might want to". Replace with direct imperative statements.

Output format: Clean markdown only. No preamble. Start directly with ## Executive Summary. Be concise — the entire output should not exceed 600 words. Prioritise numbers and actions over explanation."""


def _build_prompt(analysis_data: dict, business_context: str = "",
                  benchmarks_context: str = "", period_notes: str = "") -> str:
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
    benchmarks_section = (
        f"\n\nAccount Benchmarks (derived from this client's own upload history - calibrate recommendations against these):\n{benchmarks_context.strip()}"
        if benchmarks_context and benchmarks_context.strip() else ""
    )
    period_notes_section = (
        f"\n\nPeriod Notes (external factors during this specific period - factor these into your analysis, do not flag issues caused by these as campaign failures):\n{period_notes.strip()}"
        if period_notes and period_notes.strip() else ""
    )
    return f"{SYSTEM_PROMPT}\n\nHere is the campaign performance data to analyze:\n\n{data_json}{context_section}{benchmarks_section}{period_notes_section}{gran_section}"


def analyze(analysis_data: dict, business_context: str = "",
            benchmarks_context: str = "", period_notes: str = "") -> str:
    """Call Claude CLI with the analysis data. Returns output text. Raises on failure."""
    full_prompt = _build_prompt(analysis_data, business_context, benchmarks_context, period_notes)
    result = subprocess.run(
        ["claude", "--print", "--output-format", "text", full_prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120
    )
    if result.returncode != 0 or not result.stdout.strip():
        err = result.stderr.strip() if result.stderr else "No output returned"
        raise RuntimeError(f"Claude CLI error: {err}")
    return result.stdout.strip()


def stream_prompt(prompt: str):
    """
    Stream any prompt through Claude CLI.
    Yields tuples: ('chunk', text) or ('done', full_text).
    Raises on failure.
    """
    proc = subprocess.Popen(
        ["claude", "--print", "--output-format", "stream-json",
         "--verbose", "--include-partial-messages", prompt],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    full_text = []
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")

        if etype == "assistant":
            msg = event.get("message") or {}
            for block in msg.get("content") or []:
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    if chunk:
                        full_text.append(chunk)
                        yield ('chunk', chunk)

        elif etype == "result":
            result_text = event.get("result", "").strip()
            if result_text:
                full_text = [result_text]
            break

    proc.wait(timeout=10)
    combined = "".join(full_text).strip()
    if not combined:
        raise RuntimeError("Claude CLI returned no output")
    yield ('done', combined)


def analyze_stream(analysis_data: dict, business_context: str = "",
                   benchmarks_context: str = "", period_notes: str = ""):
    """
    Stream Claude CLI output using --output-format stream-json --include-partial-messages.
    Yields tuples: ('chunk', text) or ('done', full_text).
    Raises on failure.
    """
    full_prompt = _build_prompt(analysis_data, business_context, benchmarks_context, period_notes)

    proc = subprocess.Popen(
        ["claude", "--print", "--output-format", "stream-json",
         "--verbose", "--include-partial-messages", full_prompt],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    full_text = []
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        etype = event.get("type")

        if etype == "assistant":
            msg = event.get("message") or {}
            for block in msg.get("content") or []:
                if block.get("type") == "text":
                    chunk = block.get("text", "")
                    if chunk:
                        full_text.append(chunk)
                        yield ('chunk', chunk)

        elif etype == "result":
            result_text = event.get("result", "").strip()
            if result_text:
                full_text = [result_text]
            break

    proc.wait(timeout=10)
    combined = "".join(full_text).strip()
    if not combined:
        raise RuntimeError("Claude CLI returned no output")
    yield ('done', combined)
