"""
Autoresearch runner for Ads OS - audit report prompt optimizer.

Applies the autoresearch methodology to the Claude system prompt in
src/claude_client.py. Runs the prompt against fixed test payloads,
scores outputs against binary evals, mutates one thing at a time,
and keeps changes that improve the score.

Usage:
    cd E:/PROJECTS/GrowthX/ads-os
    python autoresearch/runner.py

Outputs (inside autoresearch/results/):
    prompt_vX.txt     - versioned prompt snapshots
    results.json      - live data for dashboard
    results.tsv       - score log
    changelog.md      - mutation log
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
SRC_PROMPT_FILE = ROOT / "src" / "claude_client.py"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

RESULTS_JSON = RESULTS_DIR / "results.json"
RESULTS_TSV = RESULTS_DIR / "results.tsv"
CHANGELOG = RESULTS_DIR / "changelog.md"
PROMPT_SNAPSHOT_DIR = RESULTS_DIR / "prompt_snapshots"
PROMPT_SNAPSHOT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
RUNS_PER_EXPERIMENT = 2   # times each prompt variant is tested per test input
STOP_AT_PASS_RATE = 95.0  # stop loop when we hit this % for 3 consecutive experiments

# ---------------------------------------------------------------------------
# Test payloads - realistic data built from sample_data/ CSVs
# ---------------------------------------------------------------------------

TEST_INPUTS = [
    # Test 1: Google + Meta + Funnel + Keywords - full data, B2B SaaS
    {
        "name": "b2b_saas_full",
        "business_context": """Client: GrowthX SaaS
Business Type: B2B SaaS
Goals: Generate MQLs and SQLs, target SMB companies in India
Audience: Marketing managers at companies with 50-500 employees
Sales Cycle: 30-60 days
Currency: INR
ROAS Target: 3.0x minimum
CPL Target: Rs 500 max""",
        "payload": {
            "platforms": ["google", "meta"],
            "currency": "INR",
            "google": {
                "total_spend": 413500,
                "overall_roas": 2.14,
                "overall_cac": 6690,
                "overall_ctr": 3.1,
                "wasted_spend": 65500,
                "critical_count": 2,
                "warning_count": 2,
                "campaign_count": 8,
                "campaigns": [
                    {"name": "Brand Awareness - Mumbai", "spend": 42000, "conversions": 5, "conversion_value": 12600, "roas": 0.30, "cac": 8400, "ctr": 0.80, "cpc": 101, "severity": "critical", "wasted": True},
                    {"name": "Performance Max - Delhi", "spend": 95000, "conversions": 62, "conversion_value": 285200, "roas": 3.00, "cac": 1532, "ctr": 3.00, "cpc": 83, "severity": "ok", "wasted": False},
                    {"name": "Retargeting - All Cities", "spend": 28000, "conversions": 38, "conversion_value": 112800, "roas": 4.03, "cac": 737, "ctr": 3.00, "cpc": 44, "severity": "ok", "wasted": False},
                    {"name": "Competitor Keywords", "spend": 22000, "conversions": 4, "conversion_value": 8800, "roas": 0.40, "cac": 5500, "ctr": 2.00, "cpc": 73, "severity": "critical", "wasted": True},
                    {"name": "Search - Product Launch", "spend": 68000, "conversions": 55, "conversion_value": 198000, "roas": 2.91, "cac": 1236, "ctr": 3.00, "cpc": 52, "severity": "warning", "wasted": False},
                    {"name": "Dynamic Search Ads", "spend": 18500, "conversions": 2, "conversion_value": 5400, "roas": 0.29, "cac": 9250, "ctr": 2.00, "cpc": 97, "severity": "critical", "wasted": True},
                    {"name": "Shopping - Electronics", "spend": 125000, "conversions": 98, "conversion_value": 437500, "roas": 3.50, "cac": 1276, "ctr": 3.00, "cpc": 62, "severity": "ok", "wasted": False},
                    {"name": "Display - Remarketing", "spend": 15000, "conversions": 8, "conversion_value": 19200, "roas": 1.28, "cac": 1875, "ctr": 0.80, "cpc": 60, "severity": "warning", "wasted": False},
                ]
            },
            "meta": {
                "total_spend": 3267000,
                "overall_roas": 2.14,
                "overall_cac": 8750,
                "overall_ctr": 2.1,
                "wasted_spend": 418550,
                "critical_count": 2,
                "warning_count": 1,
                "campaign_count": 6,
                "campaigns": [
                    {"name": "Awareness - Lookalike Audience", "spend": 605000, "conversions": 132, "conversion_value": 248050, "roas": 0.41, "cac": 4583, "ctr": 2.00, "cpc": 340, "severity": "critical", "wasted": True},
                    {"name": "Retargeting - Website Visitors", "spend": 462000, "conversions": 638, "conversion_value": 1482920, "roas": 3.21, "cac": 724, "ctr": 4.00, "cpc": 340, "severity": "ok", "wasted": False},
                    {"name": "Conversion - Cold Traffic", "spend": 858000, "conversions": 374, "conversion_value": 1304160, "roas": 1.52, "cac": 2294, "ctr": 3.00, "cpc": 469, "severity": "warning", "wasted": False},
                    {"name": "Lead Gen - Free Trial", "spend": 385000, "conversions": 1045, "conversion_value": 1104950, "roas": 2.87, "cac": 368, "ctr": 3.00, "cpc": 458, "severity": "ok", "wasted": False},
                    {"name": "Video Views - Brand Story", "spend": 275000, "conversions": 88, "conversion_value": 170500, "roas": 0.62, "cac": 3125, "ctr": 1.50, "cpc": 126, "severity": "critical", "wasted": True},
                    {"name": "Catalog Sales - Top Products", "spend": 682000, "conversions": 836, "conversion_value": 2686880, "roas": 3.94, "cac": 816, "ctr": 4.00, "cpc": 406, "severity": "ok", "wasted": False},
                ]
            },
            "funnel_summary": {
                "stages_available": ["leads", "mqls", "sqls", "customers"],
                "total_leads": 1565,
                "total_mqls": 697,
                "total_sqls": 366,
                "total_customers": 183,
                "cost_per_lead": 2395,
                "cost_per_mql": 5378,
                "cost_per_sql": 10240,
                "cost_per_customer": 20437,
                "lead_to_mql_rate": 44.5,
                "mql_to_sql_rate": 52.5,
                "sql_to_customer_rate": 50.0,
                "overall_conversion_rate": 11.7,
            },
            "granular_context": """Keyword-level breakdown (top keywords by spend):
- buy shoes online [Exact] | Performance Max - Delhi | ROAS 4.38x | QS 9 | Spend Rs 18,900
- shoes online india [Phrase] | Performance Max - Delhi | ROAS 3.65x | QS 8 | Spend Rs 12,600
- sports shoes cheap [Broad] | Performance Max - Delhi | ROAS 1.92x | QS 4 | Spend Rs 4,800
- cheap laptop [Broad] | Shopping - Electronics | ROAS 3.84x | QS 5 | Spend Rs 10,000
- mobile under 20000 [Broad] | Shopping - Electronics | ROAS 2.98x | QS 5 | Spend Rs 8,000
- best shoes [Broad] | Performance Max - Delhi | ROAS 2.63x | QS 6 | Spend Rs 10,500
- buy shoes now [Broad] | Retargeting - All Cities | ROAS 0.00x | QS 3 | Spend Rs 1,000 (0 conversions)""",
            "granularity_level": "keyword",
        }
    },

    # Test 2: Google only, no funnel, warning-heavy - ecommerce
    {
        "name": "ecommerce_google_only",
        "business_context": """Client: Shopify Electronics Store
Business Type: E-commerce
Goals: Drive purchases, maintain ROAS above 2.5x
Currency: INR
ROAS Target: 2.5x minimum""",
        "payload": {
            "platforms": ["google"],
            "currency": "INR",
            "google": {
                "total_spend": 278500,
                "overall_roas": 1.87,
                "overall_cac": 3200,
                "overall_ctr": 2.4,
                "wasted_spend": 113500,
                "critical_count": 1,
                "warning_count": 3,
                "campaign_count": 5,
                "campaigns": [
                    {"name": "Dynamic Search Ads", "spend": 18500, "conversions": 2, "conversion_value": 5400, "roas": 0.29, "cac": 9250, "ctr": 2.00, "cpc": 97, "severity": "critical", "wasted": True},
                    {"name": "Display - Remarketing", "spend": 15000, "conversions": 8, "conversion_value": 19200, "roas": 1.28, "cac": 1875, "ctr": 0.80, "cpc": 60, "severity": "warning", "wasted": False},
                    {"name": "Shopping - Electronics", "spend": 125000, "conversions": 98, "conversion_value": 437500, "roas": 3.50, "cac": 1276, "ctr": 3.00, "cpc": 62, "severity": "ok", "wasted": False},
                    {"name": "Competitor Keywords", "spend": 22000, "conversions": 4, "conversion_value": 8800, "roas": 0.40, "cac": 5500, "ctr": 2.00, "cpc": 73, "severity": "critical", "wasted": True},
                    {"name": "Brand Awareness - Mumbai", "spend": 42000, "conversions": 5, "conversion_value": 12600, "roas": 0.30, "cac": 8400, "ctr": 0.80, "cpc": 101, "severity": "critical", "wasted": True},
                ]
            },
            "granularity_level": "campaign",
        }
    },

    # Test 3: Meta only + adset data - lead gen
    {
        "name": "leadgen_meta_only",
        "business_context": """Client: EdTech Lead Gen
Business Type: Lead Generation
Goals: Drive free trial signups, CPL target Rs 400
Currency: INR
CPL Target: Rs 400 max""",
        "payload": {
            "platforms": ["meta"],
            "currency": "INR",
            "meta": {
                "total_spend": 2267000,
                "overall_roas": 2.45,
                "overall_cac": 2170,
                "overall_ctr": 2.8,
                "wasted_spend": 880000,
                "critical_count": 2,
                "warning_count": 1,
                "campaign_count": 6,
                "campaigns": [
                    {"name": "Awareness - Lookalike Audience", "spend": 605000, "conversions": 132, "conversion_value": 248050, "roas": 0.41, "cac": 4583, "ctr": 2.00, "cpc": 340, "severity": "critical", "wasted": True},
                    {"name": "Retargeting - Website Visitors", "spend": 462000, "conversions": 638, "conversion_value": 1482920, "roas": 3.21, "cac": 724, "ctr": 4.00, "cpc": 340, "severity": "ok", "wasted": False},
                    {"name": "Conversion - Cold Traffic", "spend": 858000, "conversions": 374, "conversion_value": 1304160, "roas": 1.52, "cac": 2294, "ctr": 3.00, "cpc": 469, "severity": "warning", "wasted": False},
                    {"name": "Lead Gen - Free Trial", "spend": 385000, "conversions": 1045, "conversion_value": 1104950, "roas": 2.87, "cac": 368, "ctr": 3.00, "cpc": 458, "severity": "ok", "wasted": False},
                    {"name": "Video Views - Brand Story", "spend": 275000, "conversions": 88, "conversion_value": 170500, "roas": 0.62, "cac": 3125, "ctr": 1.50, "cpc": 126, "severity": "critical", "wasted": True},
                    {"name": "Catalog Sales - Top Products", "spend": 682000, "conversions": 836, "conversion_value": 2686880, "roas": 3.94, "cac": 816, "ctr": 4.00, "cpc": 406, "severity": "ok", "wasted": False},
                ]
            },
            "granularity_level": "campaign",
        }
    },
]

# ---------------------------------------------------------------------------
# Evals - binary yes/no checks applied to every output
# ---------------------------------------------------------------------------

EVALS = [
    {
        "name": "Specific entity names",
        "question": "Does every recommendation name a specific campaign, ad set, keyword, or ad creative - never just 'a campaign' or 'certain campaigns'?",
        "pass": "All recommendations reference a named campaign, ad set, keyword, or creative",
        "fail": "Any recommendation uses vague references like 'some campaigns', 'certain ad sets', or omits names entirely",
    },
    {
        "name": "Concrete actions",
        "question": "Does every recommendation state a specific action with a measurable change - e.g. 'pause', 'reduce budget by 25%', 'switch to exact match' - not 'consider', 'look into', or 'may want to'?",
        "pass": "Every recommendation ends with a concrete, implementable action and a quantified change where applicable",
        "fail": "Any recommendation uses hedging language like 'consider', 'you might', 'look into', or lacks a specific action",
    },
    {
        "name": "Confidence scores present",
        "question": "Does every recommendation in the Recommendations section include a confidence score in exactly this format: [Confidence: High], [Confidence: Medium], or [Confidence: Low]?",
        "pass": "Every recommendation has a [Confidence: X] tag",
        "fail": "Any recommendation is missing its confidence score tag, or the format is wrong",
    },
    {
        "name": "No filler language",
        "question": "Is the output completely free of these filler phrases: 'it is important to', 'it is worth noting', 'leverage', 'it is crucial', 'it should be noted', 'going forward', 'in order to'?",
        "pass": "None of these filler phrases appear anywhere in the output",
        "fail": "Any of these filler phrases appear in the output",
    },
    {
        "name": "Budget Reallocation has numbers",
        "question": "Does the Budget Reallocation section include specific percentage or currency amounts for each campaign it mentions - not just 'reduce' or 'increase' without a figure?",
        "pass": "Every budget action in that section has a specific % or Rs amount attached",
        "fail": "Any budget action is stated without a specific figure (e.g. just 'increase budget on X')",
    },
]

MAX_SCORE_PER_RUN = len(EVALS)
MAX_SCORE = MAX_SCORE_PER_RUN * RUNS_PER_EXPERIMENT * len(TEST_INPUTS)


# ---------------------------------------------------------------------------
# Prompt management
# ---------------------------------------------------------------------------

def load_current_prompt() -> str:
    """Extract SYSTEM_PROMPT string from claude_client.py."""
    source = SRC_PROMPT_FILE.read_text(encoding="utf-8")
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', source, re.DOTALL)
    if not match:
        raise ValueError("Could not find SYSTEM_PROMPT in claude_client.py")
    return match.group(1)


def save_prompt_snapshot(prompt: str, experiment_id: int) -> Path:
    path = PROMPT_SNAPSHOT_DIR / f"prompt_v{experiment_id}.txt"
    path.write_text(prompt, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Claude runner
# ---------------------------------------------------------------------------

def run_claude(prompt_text: str, data_payload: dict, business_context: str) -> str:
    """Run the given system prompt + data through Claude CLI and return output."""
    payload = {k: v for k, v in data_payload.items()
               if k not in ("granular_context", "granularity_level")}
    gran_context = data_payload.get("granular_context", "")
    data_json = json.dumps(payload, indent=2)

    context_section = (
        f"\n\nBusiness Context (provided by the marketer - use this to interpret campaign intent and goals):\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    gran_section = (
        f"\n\nGranular breakdown context (use for deeper keyword/ad group/ad set/ad level recommendations):\n{gran_context}"
        if gran_context else ""
    )

    full_prompt = f"{prompt_text}\n\nHere is the campaign performance data to analyze:\n\n{data_json}{context_section}{gran_section}"

    try:
        result = subprocess.run(
            ["claude", "--print", "--output-format", "text", full_prompt],
            capture_output=True, text=True, encoding="utf-8", timeout=180
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        print(f"    Claude error (rc={result.returncode}): {result.stderr.strip()[:200]}")
        return ""
    except subprocess.TimeoutExpired:
        print("    Claude timed out")
        return ""
    except Exception as e:
        print(f"    Claude exception: {e}")
        return ""


# ---------------------------------------------------------------------------
# Scoring - fast regex/text-based, no Claude calls needed
# ---------------------------------------------------------------------------

# Campaign names that appear in test payloads - used to check specificity
_KNOWN_CAMPAIGN_NAMES = [
    "brand awareness", "performance max", "retargeting", "competitor keywords",
    "search - product launch", "dynamic search", "shopping - electronics",
    "display - remarketing", "awareness - lookalike", "retargeting - website",
    "conversion - cold traffic", "lead gen - free trial", "video views",
    "catalog sales",
]

_FILLER_PHRASES = [
    "it is important to", "it is worth noting", "leverage", "it is crucial",
    "it should be noted", "going forward", "in order to",
]

_HEDGE_WORDS = [
    r"\bconsider\b", r"\byou might\b", r"\blook into\b", r"\bexplore\b",
    r"\bmay want to\b", r"\bmight want to\b",
]


def _extract_recommendations_section(output: str) -> str:
    """Pull just the ## Recommendations section text."""
    match = re.search(r"## Recommendations(.*?)(?=^## |\Z)", output, re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_budget_section(output: str) -> str:
    """Pull just the ## Budget Reallocation section text."""
    match = re.search(r"## Budget Reallocation(.*?)(?=^## |\Z)", output, re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else ""


def score_output(output: str, test_name: str) -> tuple[int, list[dict]]:
    """
    Score one output against all EVALS using fast regex/text checks.
    Returns (total_passes, eval_results list).
    """
    if not output:
        return 0, [{"name": e["name"], "passed": False} for e in EVALS]

    output_lower = output.lower()
    rec_section = _extract_recommendations_section(output)
    budget_section = _extract_budget_section(output)
    eval_results = []
    total = 0

    # EVAL 1: Specific entity names
    # Pass if at least 2 known campaign names appear in the recommendations section
    # (proxy for "naming specific entities rather than vague references")
    vague_patterns = [r"\bsome campaigns\b", r"\bcertain campaigns\b",
                      r"\bunderperforming campaigns\b(?! \()", r"\bthese campaigns\b"]
    has_vague = any(re.search(p, rec_section.lower()) for p in vague_patterns)
    campaign_hits = sum(1 for name in _KNOWN_CAMPAIGN_NAMES if name in rec_section.lower())
    passed = campaign_hits >= 2 and not has_vague
    eval_results.append({"name": "Specific entity names", "passed": passed})
    if passed:
        total += 1

    # EVAL 2: Concrete actions
    # Pass if hedge words are absent from the recommendations section
    hedge_found = any(re.search(p, rec_section.lower()) for p in _HEDGE_WORDS)
    # Also check for imperative openers (at least 40% of rec lines should start with a verb)
    rec_lines = [l.strip() for l in rec_section.split("\n") if l.strip().startswith(("-", "*", "1", "2", "3", "4", "5", "6", "7", "8", "9"))]
    imperative_verbs = ("pause", "increase", "decrease", "switch", "test", "add", "remove",
                        "restructure", "reduce", "reallocate", "stop", "launch", "enable",
                        "disable", "update", "fix", "move", "cut", "scale", "expand")
    if rec_lines:
        verb_count = sum(1 for l in rec_lines
                         if any(l.lstrip("-* 0123456789.").lower().startswith(v) for v in imperative_verbs)
                         or any(f"**{v}" in l.lower() for v in imperative_verbs))
        passed = not hedge_found and (verb_count / len(rec_lines)) >= 0.3
    else:
        passed = not hedge_found
    eval_results.append({"name": "Concrete actions", "passed": passed})
    if passed:
        total += 1

    # EVAL 3: Confidence scores present
    # Pass if [Confidence: High/Medium/Low] appears at least once per 3 rec lines
    conf_matches = re.findall(r"\[Confidence:\s*(High|Medium|Low)\]", output, re.IGNORECASE)
    if rec_lines:
        # At least one confidence tag per 3 recommendation lines
        passed = len(conf_matches) >= max(1, len(rec_lines) // 3)
    else:
        passed = len(conf_matches) >= 1
    eval_results.append({"name": "Confidence scores present", "passed": passed})
    if passed:
        total += 1

    # EVAL 4: No filler language
    passed = not any(phrase in output_lower for phrase in _FILLER_PHRASES)
    eval_results.append({"name": "No filler language", "passed": passed})
    if passed:
        total += 1

    # EVAL 5: Budget Reallocation has numbers
    # Pass if the budget section contains at least one % or Rs/currency figure
    has_pct = bool(re.search(r"\d+\s*%", budget_section))
    has_currency = bool(re.search(r"(Rs|INR|₹)\s*[\d,]+|\d+\s*(lakh|k\b)", budget_section, re.IGNORECASE))
    passed = bool(budget_section) and (has_pct or has_currency)
    eval_results.append({"name": "Budget Reallocation has numbers", "passed": passed})
    if passed:
        total += 1

    return total, eval_results


# ---------------------------------------------------------------------------
# Mutation engine - one targeted change per experiment
# ---------------------------------------------------------------------------

MUTATIONS = [
    {
        "id": 1,
        "description": "Add explicit anti-filler-language instruction with banned phrase list",
        "hypothesis": "The prompt has no explicit ban on filler phrases. Adding a banned phrase list should eliminate hedging language in outputs.",
        "find": "Output format: Clean markdown only. No preamble. Start directly with ## Executive Summary.",
        "replace": """Banned phrases - NEVER use these: 'it is important to', 'it is worth noting', 'leverage', 'it is crucial', 'it should be noted', 'going forward', 'in order to', 'consider', 'you might want to', 'it may be worth'. Replace with direct statements and imperative verbs.

Output format: Clean markdown only. No preamble. Start directly with ## Executive Summary.""",
    },
    {
        "id": 2,
        "description": "Strengthen Budget Reallocation to require specific % and Rs figures",
        "hypothesis": "The Budget Reallocation section often gives vague 'reduce' or 'increase' without a number. Adding a mandatory figure requirement should fix the eval failure.",
        "find": "## Budget Reallocation\nSpecific recommendation: which campaigns to reduce budget on, which to increase, and by approximately how much. If funnel data is present, prioritize campaigns by cost per customer, not just ROAS.",
        "replace": """## Budget Reallocation
For every campaign mentioned: state the exact action AND a specific figure. Format each line as: "Campaign Name: [increase/decrease/pause] by [X%] - reason in one sentence." If pausing, state what to do with freed budget and where to reallocate it. If funnel data is present, prioritize campaigns by cost per customer, not just ROAS.""",
    },
    {
        "id": 3,
        "description": "Add worked example showing correct recommendation format with confidence score",
        "hypothesis": "The prompt describes the confidence score format but does not show an example. A worked example reduces format deviation.",
        "find": "- Include a confidence score in brackets: [Confidence: High], [Confidence: Medium], or [Confidence: Low]\n  - High: clear data signal, direct causation, well-established best practice\n  - Medium: likely cause but requires testing to confirm, or limited data\n  - Low: hypothesis based on partial data, or multiple possible explanations",
        "replace": """- Include a confidence score in brackets: [Confidence: High], [Confidence: Medium], or [Confidence: Low]
  - High: clear data signal, direct causation, well-established best practice
  - Medium: likely cause but requires testing to confirm, or limited data
  - Low: hypothesis based on partial data, or multiple possible explanations
- Example of a correctly formatted recommendation:
  "Pause Competitor Keywords (Google) — ROAS 0.40x on Rs 22,000 spend means Rs 13,200 wasted. Pause immediately and reallocate to Performance Max - Delhi. [Confidence: High]" """,
    },
    {
        "id": 4,
        "description": "Add explicit instruction to never use vague campaign references",
        "hypothesis": "Recommendations sometimes say 'underperforming campaigns' without naming them. Adding a hard rule against this should improve specificity eval.",
        "find": "- Name the most specific entity possible: keyword, ad set, ad creative, or campaign - whichever level the data supports",
        "replace": """- Name the most specific entity possible: keyword, ad set, ad creative, or campaign - whichever level the data supports
- NEVER write 'certain campaigns', 'some ad sets', 'the underperforming campaigns', or any vague reference. If you are recommending an action, name the exact campaign/keyword/ad set. If you cannot name it, do not include that recommendation.""",
    },
    {
        "id": 5,
        "description": "Replace 'achievable within 2 weeks' with stronger action-verb mandate",
        "hypothesis": "The '2 weeks' constraint is too loose. Adding a mandate for imperative verbs (Pause/Increase/Switch/Test) as recommendation openers will sharpen action specificity.",
        "find": "- Be achievable within 2 weeks",
        "replace": """- Be achievable within 2 weeks
- Start with an imperative verb: Pause, Increase, Decrease, Switch, Test, Add, Remove, Restructure. Never start with 'Consider', 'Try', 'Look at', 'Explore', or 'Review' - these are not actions.""",
    },
    {
        "id": 6,
        "description": "Add explicit instruction to lead Executive Summary with the worst single number",
        "hypothesis": "Executive summaries sometimes bury the lead. Requiring the first sentence to name the single worst-performing campaign and its ROAS/CPL creates a stronger, more specific opener.",
        "find": "## Executive Summary\nONE paragraph, client-friendly language. Mention total spend, overall performance, and the single most critical issue.",
        "replace": """## Executive Summary
ONE paragraph, client-friendly language. The very first sentence must name the single worst-performing campaign and its key failing metric (e.g. "Brand Awareness - Mumbai is wasting Rs 42,000 at 0.30x ROAS with no path to profitability."). Then cover total spend, overall performance, and the primary action needed.""",
    },
    {
        "id": 7,
        "description": "Remove the 'approximately' qualifier from budget reallocation instruction",
        "hypothesis": "The word 'approximately' in the budget section may be giving Claude permission to be vague. Removing it reinforces the requirement for specific figures.",
        "find": "Specific recommendation: which campaigns to reduce budget on, which to increase, and by approximately how much.",
        "replace": "Specific recommendation: which campaigns to reduce budget on, which to increase, and by exactly how much (state a percentage or Rs amount for every campaign you mention).",
    },
]


# ---------------------------------------------------------------------------
# Results tracking
# ---------------------------------------------------------------------------

def init_results():
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(
            "experiment\tscore\tmax_score\tpass_rate\tstatus\tdescription\n",
            encoding="utf-8"
        )
    if not CHANGELOG.exists():
        CHANGELOG.write_text("# Autoresearch Changelog\n\n", encoding="utf-8")


def append_tsv(exp_id: int, score: int, status: str, description: str):
    pass_rate = (score / MAX_SCORE * 100) if MAX_SCORE > 0 else 0
    with open(RESULTS_TSV, "a", encoding="utf-8") as f:
        f.write(f"{exp_id}\t{score}\t{MAX_SCORE}\t{pass_rate:.1f}%\t{status}\t{description}\n")


def append_changelog(exp_id: int, status: str, score: int, description: str,
                     reasoning: str, result_notes: str, still_failing: str):
    pass_rate = (score / MAX_SCORE * 100) if MAX_SCORE > 0 else 0
    entry = f"""
## Experiment {exp_id} - {status.upper()}

**Score:** {score}/{MAX_SCORE} ({pass_rate:.1f}%)
**Change:** {description}
**Reasoning:** {reasoning}
**Result:** {result_notes}
**Still failing:** {still_failing}

"""
    with open(CHANGELOG, "a", encoding="utf-8") as f:
        f.write(entry)


def write_results_json(experiments: list, status: str, current_exp: int,
                       baseline_score: int, best_score: int, eval_totals: dict):
    eval_breakdown = [
        {
            "name": name,
            "pass_count": counts["pass"],
            "total": counts["total"]
        }
        for name, counts in eval_totals.items()
    ]
    data = {
        "skill_name": "ads-os audit prompt",
        "status": status,
        "current_experiment": current_exp,
        "baseline_score": round(baseline_score / MAX_SCORE * 100, 1) if MAX_SCORE else 0,
        "best_score": round(best_score / MAX_SCORE * 100, 1) if MAX_SCORE else 0,
        "max_score": MAX_SCORE,
        "runs_per_experiment": RUNS_PER_EXPERIMENT,
        "test_count": len(TEST_INPUTS),
        "eval_count": len(EVALS),
        "experiments": experiments,
        "eval_breakdown": eval_breakdown,
        "last_updated": datetime.now().isoformat(),
    }
    RESULTS_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core experiment runner
# ---------------------------------------------------------------------------

def run_experiment(prompt: str, experiment_id: int) -> tuple[int, dict]:
    """
    Run RUNS_PER_EXPERIMENT * len(TEST_INPUTS) evaluations.
    Returns (total_score, eval_totals).
    """
    eval_totals = {e["name"]: {"pass": 0, "total": 0} for e in EVALS}
    total_score = 0

    for test in TEST_INPUTS:
        print(f"    Test input: {test['name']}")
        for run in range(RUNS_PER_EXPERIMENT):
            print(f"      Run {run + 1}/{RUNS_PER_EXPERIMENT}...", end=" ", flush=True)
            output = run_claude(prompt, test["payload"], test["business_context"])
            score, eval_results = score_output(output, test["name"])
            total_score += score
            print(f"score {score}/{MAX_SCORE_PER_RUN}")
            for r in eval_results:
                eval_totals[r["name"]]["total"] += 1
                if r["passed"]:
                    eval_totals[r["name"]]["pass"] += 1

    return total_score, eval_totals


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Ads OS Autoresearch - Audit Prompt Optimizer")
    print(f"Test inputs: {len(TEST_INPUTS)} | Evals: {len(EVALS)} | Runs/experiment: {RUNS_PER_EXPERIMENT}")
    print(f"Max score per experiment: {MAX_SCORE}")
    print(f"Results: {RESULTS_DIR}")
    print("=" * 60)

    init_results()
    experiments = []
    eval_totals_global = {e["name"]: {"pass": 0, "total": 0} for e in EVALS}

    # --- Baseline ---
    print("\n[Experiment 0] Establishing baseline...")
    current_prompt = load_current_prompt()
    save_prompt_snapshot(current_prompt, 0)

    baseline_score, baseline_eval_totals = run_experiment(current_prompt, 0)
    baseline_pass_rate = baseline_score / MAX_SCORE * 100 if MAX_SCORE else 0

    print(f"\n  Baseline score: {baseline_score}/{MAX_SCORE} ({baseline_pass_rate:.1f}%)")
    for name, counts in baseline_eval_totals.items():
        pct = counts["pass"] / counts["total"] * 100 if counts["total"] else 0
        print(f"    {name}: {counts['pass']}/{counts['total']} ({pct:.0f}%)")
        eval_totals_global[name]["pass"] += counts["pass"]
        eval_totals_global[name]["total"] += counts["total"]

    append_tsv(0, baseline_score, "baseline", "original prompt - no changes")
    append_changelog(0, "baseline", baseline_score, "Original prompt - no changes",
                     "Starting point measurement", "Baseline established",
                     _identify_weakest_evals(baseline_eval_totals))

    experiments.append({
        "id": 0,
        "score": baseline_score,
        "max_score": MAX_SCORE,
        "pass_rate": round(baseline_pass_rate, 1),
        "status": "baseline",
        "description": "original prompt - no changes",
    })

    write_results_json(experiments, "running", 0, baseline_score, baseline_score, eval_totals_global)

    best_score = baseline_score
    best_prompt = current_prompt
    consecutive_high = 0

    # --- Mutation loop ---
    for mutation in MUTATIONS:
        exp_id = mutation["id"]
        print(f"\n[Experiment {exp_id}] {mutation['description']}")
        print(f"  Hypothesis: {mutation['hypothesis']}")

        # Apply mutation
        if mutation["find"] not in best_prompt:
            print(f"  SKIP: target text not found in current prompt (may have been changed by earlier mutation)")
            continue

        candidate_prompt = best_prompt.replace(mutation["find"], mutation["replace"], 1)
        save_prompt_snapshot(candidate_prompt, exp_id)

        # Run experiment
        write_results_json(experiments, f"running experiment {exp_id}", exp_id,
                           baseline_score, best_score, eval_totals_global)

        score, eval_totals = run_experiment(candidate_prompt, exp_id)
        pass_rate = score / MAX_SCORE * 100 if MAX_SCORE else 0

        print(f"\n  Score: {score}/{MAX_SCORE} ({pass_rate:.1f}%) vs best {best_score}/{MAX_SCORE}")

        # Determine result notes
        result_notes = []
        for name, counts in eval_totals.items():
            prev = eval_totals_global.get(name, {})
            if counts["total"] > 0:
                new_pct = counts["pass"] / counts["total"] * 100
                result_notes.append(f"{name}: {counts['pass']}/{counts['total']} ({new_pct:.0f}%)")
                eval_totals_global[name]["pass"] += counts["pass"]
                eval_totals_global[name]["total"] += counts["total"]

        if score > best_score:
            status = "keep"
            best_score = score
            best_prompt = candidate_prompt
            print(f"  KEEP - improvement of +{score - best_score + (score - best_score)} points")
        else:
            status = "discard"
            print(f"  DISCARD - no improvement")

        append_tsv(exp_id, score, status, mutation["description"])
        append_changelog(
            exp_id, status, score,
            mutation["description"],
            mutation["hypothesis"],
            " | ".join(result_notes),
            _identify_weakest_evals(eval_totals)
        )

        experiments.append({
            "id": exp_id,
            "score": score,
            "max_score": MAX_SCORE,
            "pass_rate": round(pass_rate, 1),
            "status": status,
            "description": mutation["description"],
        })

        write_results_json(experiments, "running", exp_id, baseline_score, best_score, eval_totals_global)

        # Check stopping condition
        if pass_rate >= STOP_AT_PASS_RATE:
            consecutive_high += 1
            if consecutive_high >= 3:
                print(f"\n  Reached {STOP_AT_PASS_RATE}% for 3 consecutive experiments - stopping.")
                break
        else:
            consecutive_high = 0

        # Brief pause between experiments to avoid rate limiting
        time.sleep(2)

    # --- Final report ---
    final_pass_rate = best_score / MAX_SCORE * 100 if MAX_SCORE else 0
    print("\n" + "=" * 60)
    print("AUTORESEARCH COMPLETE")
    print(f"Baseline: {baseline_score}/{MAX_SCORE} ({baseline_score / MAX_SCORE * 100:.1f}%)")
    print(f"Final:    {best_score}/{MAX_SCORE} ({final_pass_rate:.1f}%)")
    print(f"Improvement: +{best_score - baseline_score} points ({final_pass_rate - baseline_score / MAX_SCORE * 100:.1f}pp)")
    print(f"\nBest prompt saved to: {PROMPT_SNAPSHOT_DIR}/")
    print(f"Full log: {CHANGELOG}")
    print(f"Scores:   {RESULTS_TSV}")
    print("=" * 60)

    # Save final best prompt as a clearly named file
    best_path = RESULTS_DIR / "best_prompt.txt"
    best_path.write_text(best_prompt, encoding="utf-8")
    print(f"\nWinning prompt: {best_path}")
    print("To apply: copy contents of best_prompt.txt into SYSTEM_PROMPT in src/claude_client.py")

    write_results_json(experiments, "complete", len(experiments) - 1,
                       baseline_score, best_score, eval_totals_global)

    # Print per-eval final breakdown
    print("\nFinal eval breakdown:")
    for name, counts in eval_totals_global.items():
        if counts["total"] > 0:
            pct = counts["pass"] / counts["total"] * 100
            print(f"  {name}: {counts['pass']}/{counts['total']} ({pct:.0f}%)")


def _identify_weakest_evals(eval_totals: dict) -> str:
    weakest = []
    for name, counts in eval_totals.items():
        if counts["total"] > 0:
            pct = counts["pass"] / counts["total"] * 100
            if pct < 60:
                weakest.append(f"{name} ({pct:.0f}%)")
    return ", ".join(weakest) if weakest else "none - all evals passing above 60%"


if __name__ == "__main__":
    main()
