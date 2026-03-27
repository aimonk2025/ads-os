"""
Shared data - test payloads, evals, mutations, scoring logic.
Imported by both experiment.py and orchestrate.py.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC_PROMPT_FILE = ROOT / "src" / "claude_client.py"
ROUND = "round10"

RESULTS_DIR = Path(__file__).parent / f"results_{ROUND}"
RESULTS_DIR.mkdir(exist_ok=True)
PROMPT_SNAPSHOT_DIR = RESULTS_DIR / "prompt_snapshots"
PROMPT_SNAPSHOT_DIR.mkdir(exist_ok=True)

RUNS_PER_EXPERIMENT = 2

# ---------------------------------------------------------------------------
# Test payloads
# ---------------------------------------------------------------------------

TEST_INPUTS = [
    {
        "name": "b2b_saas_full",
        "business_context": "Client: GrowthX SaaS\nBusiness Type: B2B SaaS\nGoals: Generate MQLs and SQLs\nCurrency: INR\nROAS Target: 3.0x minimum\nCPL Target: Rs 500 max",
        "payload": {
            "platforms": ["google", "meta"],
            "currency": "INR",
            "google": {
                "total_spend": 413500, "overall_roas": 2.14, "overall_cac": 6690,
                "overall_ctr": 3.1, "wasted_spend": 65500, "critical_count": 2,
                "warning_count": 2, "campaign_count": 8,
                "campaigns": [
                    {"name": "Brand Awareness - Mumbai", "spend": 42000, "conversions": 5, "conversion_value": 12600, "roas": 0.30, "cac": 8400, "ctr": 0.80, "cpc": 101, "severity": "critical", "wasted": True, "efficiency": 0.30, "trend": "declining", "learning_phase": False},
                    {"name": "Performance Max - Delhi", "spend": 95000, "conversions": 62, "conversion_value": 285200, "roas": 3.00, "cac": 1532, "ctr": 3.00, "cpc": 83, "severity": "ok", "wasted": False, "efficiency": 3.00, "trend": "stable", "learning_phase": True},
                    {"name": "Retargeting - All Cities", "spend": 28000, "conversions": 38, "conversion_value": 112800, "roas": 4.03, "cac": 737, "ctr": 3.00, "cpc": 44, "severity": "ok", "wasted": False, "efficiency": 4.03, "trend": "improving", "learning_phase": False},
                    {"name": "Competitor Keywords", "spend": 22000, "conversions": 4, "conversion_value": 8800, "roas": 0.40, "cac": 5500, "ctr": 2.00, "cpc": 73, "severity": "critical", "wasted": True, "efficiency": 0.40, "trend": "declining", "learning_phase": False},
                    {"name": "Search - Product Launch", "spend": 68000, "conversions": 55, "conversion_value": 198000, "roas": 2.91, "cac": 1236, "ctr": 3.00, "cpc": 52, "severity": "warning", "wasted": False, "efficiency": 2.91, "trend": "improving", "learning_phase": False},
                    {"name": "Dynamic Search Ads", "spend": 18500, "conversions": 2, "conversion_value": 5400, "roas": 0.29, "cac": 9250, "ctr": 2.00, "cpc": 97, "severity": "critical", "wasted": True, "efficiency": 0.29, "trend": "stable", "learning_phase": False},
                    {"name": "Shopping - Electronics", "spend": 125000, "conversions": 98, "conversion_value": 437500, "roas": 3.50, "cac": 1276, "ctr": 3.00, "cpc": 62, "severity": "ok", "wasted": False, "efficiency": 3.50, "trend": "improving", "learning_phase": False},
                    {"name": "Display - Remarketing", "spend": 15000, "conversions": 8, "conversion_value": 19200, "roas": 1.28, "cac": 1875, "ctr": 0.80, "cpc": 60, "severity": "warning", "wasted": False, "efficiency": 1.28, "trend": "stable", "learning_phase": False},
                ]
            },
            "meta": {
                "total_spend": 3267000, "overall_roas": 2.14, "overall_cac": 8750,
                "overall_ctr": 2.1, "wasted_spend": 418550, "critical_count": 2,
                "warning_count": 1, "campaign_count": 6,
                "campaigns": [
                    {"name": "Awareness - Lookalike Audience", "spend": 605000, "conversions": 132, "conversion_value": 248050, "roas": 0.41, "cac": 4583, "ctr": 2.00, "cpc": 340, "severity": "critical", "wasted": True, "efficiency": 0.41, "trend": "declining", "learning_phase": False, "frequency": 4.2},
                    {"name": "Retargeting - Website Visitors", "spend": 462000, "conversions": 638, "conversion_value": 1482920, "roas": 3.21, "cac": 724, "ctr": 4.00, "cpc": 340, "severity": "ok", "wasted": False, "efficiency": 3.21, "trend": "improving", "learning_phase": False, "frequency": 2.1},
                    {"name": "Conversion - Cold Traffic", "spend": 858000, "conversions": 374, "conversion_value": 1304160, "roas": 1.52, "cac": 2294, "ctr": 3.00, "cpc": 469, "severity": "warning", "wasted": False, "efficiency": 1.52, "trend": "stable", "learning_phase": True, "frequency": 1.8},
                    {"name": "Lead Gen - Free Trial", "spend": 385000, "conversions": 1045, "conversion_value": 1104950, "roas": 2.87, "cac": 368, "ctr": 3.00, "cpc": 458, "severity": "ok", "wasted": False, "efficiency": 2.87, "trend": "improving", "learning_phase": False, "frequency": 2.4},
                    {"name": "Video Views - Brand Story", "spend": 275000, "conversions": 88, "conversion_value": 170500, "roas": 0.62, "cac": 3125, "ctr": 1.50, "cpc": 126, "severity": "critical", "wasted": True, "efficiency": 0.62, "trend": "declining", "learning_phase": False, "frequency": 5.1},
                    {"name": "Catalog Sales - Top Products", "spend": 682000, "conversions": 836, "conversion_value": 2686880, "roas": 3.94, "cac": 816, "ctr": 4.00, "cpc": 406, "severity": "ok", "wasted": False, "efficiency": 3.94, "trend": "improving", "learning_phase": False, "frequency": 2.9},
                ]
            },
            "funnel_summary": {
                "stages_available": ["leads", "mqls", "sqls", "customers"],
                "total_leads": 1565, "total_mqls": 697, "total_sqls": 366, "total_customers": 183,
                "cost_per_lead": 2395, "cost_per_mql": 5378, "cost_per_sql": 10240, "cost_per_customer": 20437,
                "lead_to_mql_rate": 44.5, "mql_to_sql_rate": 52.5, "sql_to_customer_rate": 50.0,
                "overall_conversion_rate": 11.7,
                "worst_funnel_stage": "lead_to_mql", "worst_funnel_rate": 44.5,
            },
            "anomalies": [
                {"campaign": "Awareness - Lookalike Audience", "metric": "ctr", "value": 0.38, "baseline_value": 2.1, "severity": "critical", "seasonal": False},
                {"campaign": "Video Views - Brand Story", "metric": "frequency", "value": 5.1, "baseline_value": 2.5, "severity": "warning", "seasonal": False},
            ],
            "granular_context": "Keyword-level breakdown:\n- buy shoes online [Exact] | Performance Max - Delhi | ROAS 4.38x | QS 9 | Spend Rs 18,900\n- sports shoes cheap [Broad] | Performance Max - Delhi | ROAS 1.92x | QS 4 | Spend Rs 4,800\n- buy shoes now [Broad] | Retargeting - All Cities | ROAS 0.00x | QS 3 | Spend Rs 1,000 (0 conversions)",
            "granularity_level": "keyword",
        }
    },
    {
        "name": "ecommerce_google_only",
        "business_context": "Client: Shopify Electronics Store\nBusiness Type: E-commerce\nGoals: Drive purchases, ROAS above 2.5x\nCurrency: INR",
        "payload": {
            "platforms": ["google"],
            "currency": "INR",
            "google": {
                "total_spend": 278500, "overall_roas": 1.87, "overall_cac": 3200,
                "overall_ctr": 2.4, "wasted_spend": 113500, "critical_count": 3,
                "warning_count": 1, "campaign_count": 5,
                "campaigns": [
                    {"name": "Dynamic Search Ads", "spend": 18500, "conversions": 2, "conversion_value": 5400, "roas": 0.29, "cac": 9250, "ctr": 2.00, "cpc": 97, "severity": "critical", "wasted": True, "efficiency": 0.29, "trend": "declining", "learning_phase": False},
                    {"name": "Display - Remarketing", "spend": 15000, "conversions": 8, "conversion_value": 19200, "roas": 1.28, "cac": 1875, "ctr": 0.80, "cpc": 60, "severity": "warning", "wasted": False, "efficiency": 1.28, "trend": "stable", "learning_phase": False},
                    {"name": "Shopping - Electronics", "spend": 125000, "conversions": 98, "conversion_value": 437500, "roas": 3.50, "cac": 1276, "ctr": 3.00, "cpc": 62, "severity": "ok", "wasted": False, "efficiency": 3.50, "trend": "improving", "learning_phase": False},
                    {"name": "Competitor Keywords", "spend": 22000, "conversions": 4, "conversion_value": 8800, "roas": 0.40, "cac": 5500, "ctr": 2.00, "cpc": 73, "severity": "critical", "wasted": True, "efficiency": 0.40, "trend": "stable", "learning_phase": False},
                    {"name": "Brand Awareness - Mumbai", "spend": 42000, "conversions": 5, "conversion_value": 12600, "roas": 0.30, "cac": 8400, "ctr": 0.80, "cpc": 101, "severity": "critical", "wasted": True, "efficiency": 0.30, "trend": "declining", "learning_phase": True},
                ]
            },
            "anomalies": [
                {"campaign": "Dynamic Search Ads", "metric": "roas", "value": 0.29, "baseline_value": 1.87, "severity": "critical", "seasonal": False},
                {"campaign": "Brand Awareness - Mumbai", "metric": "ctr", "value": 0.80, "baseline_value": 2.4, "severity": "warning", "seasonal": True},
            ],
            "granularity_level": "campaign",
        }
    },
    {
        "name": "leadgen_meta_only",
        "business_context": "Client: EdTech Lead Gen\nBusiness Type: Lead Generation\nGoals: Free trial signups, CPL target Rs 400\nCurrency: INR",
        "payload": {
            "platforms": ["meta"],
            "currency": "INR",
            "meta": {
                "total_spend": 2267000, "overall_roas": 2.45, "overall_cac": 2170,
                "overall_ctr": 2.8, "wasted_spend": 880000, "critical_count": 2,
                "warning_count": 1, "campaign_count": 6,
                "campaigns": [
                    {"name": "Awareness - Lookalike Audience", "spend": 605000, "conversions": 132, "conversion_value": 248050, "roas": 0.41, "cac": 4583, "ctr": 2.00, "cpc": 340, "severity": "critical", "wasted": True, "efficiency": 0.41, "trend": "declining", "learning_phase": False, "frequency": 4.2},
                    {"name": "Retargeting - Website Visitors", "spend": 462000, "conversions": 638, "conversion_value": 1482920, "roas": 3.21, "cac": 724, "ctr": 4.00, "cpc": 340, "severity": "ok", "wasted": False, "efficiency": 3.21, "trend": "improving", "learning_phase": False, "frequency": 2.1},
                    {"name": "Conversion - Cold Traffic", "spend": 858000, "conversions": 374, "conversion_value": 1304160, "roas": 1.52, "cac": 2294, "ctr": 3.00, "cpc": 469, "severity": "warning", "wasted": False, "efficiency": 1.52, "trend": "stable", "learning_phase": True, "frequency": 1.8},
                    {"name": "Lead Gen - Free Trial", "spend": 385000, "conversions": 1045, "conversion_value": 1104950, "roas": 2.87, "cac": 368, "ctr": 3.00, "cpc": 458, "severity": "ok", "wasted": False, "efficiency": 2.87, "trend": "improving", "learning_phase": False, "frequency": 2.4},
                    {"name": "Video Views - Brand Story", "spend": 275000, "conversions": 88, "conversion_value": 170500, "roas": 0.62, "cac": 3125, "ctr": 1.50, "cpc": 126, "severity": "critical", "wasted": True, "efficiency": 0.62, "trend": "declining", "learning_phase": False, "frequency": 5.1},
                    {"name": "Catalog Sales - Top Products", "spend": 682000, "conversions": 836, "conversion_value": 2686880, "roas": 3.94, "cac": 816, "ctr": 4.00, "cpc": 406, "severity": "ok", "wasted": False, "efficiency": 3.94, "trend": "improving", "learning_phase": False, "frequency": 2.9},
                ]
            },
            "funnel_summary": {
                "stages_available": ["leads", "mqls"],
                "total_leads": 2575, "total_mqls": 1147,
                "cost_per_lead": 880, "cost_per_mql": 1977,
                "lead_to_mql_rate": 44.5,
                "overall_conversion_rate": 44.5,
                "worst_funnel_stage": "lead_to_mql", "worst_funnel_rate": 44.5,
            },
            "anomalies": [
                {"campaign": "Video Views - Brand Story", "metric": "frequency", "value": 5.1, "baseline_value": 2.5, "severity": "warning", "seasonal": False},
                {"campaign": "Awareness - Lookalike Audience", "metric": "roas", "value": 0.41, "baseline_value": 2.45, "severity": "critical", "seasonal": False},
            ],
            "granularity_level": "campaign",
        }
    },
]

# ---------------------------------------------------------------------------
# Evals
# ---------------------------------------------------------------------------

EVALS = [
    {"name": "Trend direction referenced"},
    {"name": "Learning phase campaign protected"},
    {"name": "Efficiency metric used"},
    {"name": "Worst funnel stage named"},
    {"name": "High frequency fatigue flagged"},
]

MAX_SCORE_PER_RUN = len(EVALS)
MAX_SCORE = MAX_SCORE_PER_RUN * RUNS_PER_EXPERIMENT * len(TEST_INPUTS)

# ---------------------------------------------------------------------------
# Mutations - each is independent, applied against the baseline prompt
# ---------------------------------------------------------------------------

MUTATIONS = [
    # Round 10: Close quality gaps — learning phase protected (95%) and trend direction (98%)
    # Strategy: strengthen mandatory signals for the two failing evals without hurting speed
    {
        "id": 0,
        "description": "Baseline - re-measure quality at current prompt state",
        "hypothesis": "Re-establish baseline after Round 9 winner was applied.",
        "find": None,
        "replace": None,
    },
    {
        "id": 1,
        "description": "Strengthen learning phase guard — explicit 'do NOT pause or cut' sentence",
        "hypothesis": "The current guard says 'never recommend pausing or cutting budget' but Claude sometimes still implies budget cuts indirectly. Adding an explicit DO NOT sentence before the exception list should close that gap.",
        "find": "## Wasted Spend Analysis\nWhich campaigns are burning money and why. Be specific about campaign names, amounts, and which funnel stage is failing. EXCEPTION: Do not flag campaigns with learning_phase=true as wasted spend — they are still gathering conversion data. Use direct declarative sentences only. State facts from the data.",
        "replace": "## Wasted Spend Analysis\nWhich campaigns are burning money and why. Be specific about campaign names, amounts, and which funnel stage is failing. EXCEPTION: Do NOT flag campaigns with learning_phase=true as wasted spend, do NOT recommend pausing them, and do NOT recommend cutting their budget — they are still gathering conversion data. Use direct declarative sentences only. State facts from the data.",
    },
    {
        "id": 2,
        "description": "Make trend direction mandatory in Underperforming — add MUST keyword",
        "hypothesis": "Under the 500-word limit, Claude sometimes drops the trend direction to save words. Changing 'the trend direction' to 'the trend direction (REQUIRED)' should prevent omission.",
        "find": "## Underperforming Campaigns\nRanked worst to best. For each: campaign name, the specific metric that is failing, the trend direction (improving/declining/stable from the data), and the likely cause.",
        "replace": "## Underperforming Campaigns\nRanked worst to best. For each: campaign name, the specific metric that is failing, the trend direction — REQUIRED, write one of: improving/declining/stable — and the likely cause.",
    },
    {
        "id": 3,
        "description": "Relax word limit to 600 — give Claude room to include all required signals",
        "hypothesis": "The 500-word limit may be too tight, forcing Claude to drop trend words or skip the learning phase mention. Relaxing to 600 words keeps conciseness while reducing signal omission.",
        "find": "Be concise — the entire output should not exceed 500 words. Prioritise numbers and actions over explanation.",
        "replace": "Be concise — the entire output should not exceed 600 words. Prioritise numbers and actions over explanation.",
    },
    {
        "id": 4,
        "description": "Combine exp1 (stronger learning guard) + exp2 (mandatory trend direction)",
        "hypothesis": "Both fixes target different evals and different sections so they shouldn't interfere. Combined they should close both the learning phase and trend direction gaps simultaneously.",
        "find": "## Wasted Spend Analysis\nWhich campaigns are burning money and why. Be specific about campaign names, amounts, and which funnel stage is failing. EXCEPTION: Do not flag campaigns with learning_phase=true as wasted spend — they are still gathering conversion data. Use direct declarative sentences only. State facts from the data.",
        "replace": "## Wasted Spend Analysis\nWhich campaigns are burning money and why. Be specific about campaign names, amounts, and which funnel stage is failing. EXCEPTION: Do NOT flag campaigns with learning_phase=true as wasted spend, do NOT recommend pausing them, and do NOT recommend cutting their budget — they are still gathering conversion data. Use direct declarative sentences only. State facts from the data.",
        "find2": "## Underperforming Campaigns\nRanked worst to best. For each: campaign name, the specific metric that is failing, the trend direction (improving/declining/stable from the data), and the likely cause.",
        "replace2": "## Underperforming Campaigns\nRanked worst to best. For each: campaign name, the specific metric that is failing, the trend direction — REQUIRED, write one of: improving/declining/stable — and the likely cause.",
    },
    {
        "id": 5,
        "description": "Combine exp2 (mandatory trend) + exp3 (600-word limit) — more room for required signals",
        "hypothesis": "Mandatory trend direction + slightly relaxed word limit should eliminate both trend omissions without sacrificing speed meaningfully.",
        "find": "## Underperforming Campaigns\nRanked worst to best. For each: campaign name, the specific metric that is failing, the trend direction (improving/declining/stable from the data), and the likely cause.",
        "replace": "## Underperforming Campaigns\nRanked worst to best. For each: campaign name, the specific metric that is failing, the trend direction — REQUIRED, write one of: improving/declining/stable — and the likely cause.",
        "find2": "Be concise — the entire output should not exceed 500 words. Prioritise numbers and actions over explanation.",
        "replace2": "Be concise — the entire output should not exceed 600 words. Prioritise numbers and actions over explanation.",
    },
]

# ---------------------------------------------------------------------------
# Prompt loading
# ---------------------------------------------------------------------------

def load_baseline_prompt() -> str:
    source = SRC_PROMPT_FILE.read_text(encoding="utf-8")
    match = re.search(r'SYSTEM_PROMPT\s*=\s*"""(.*?)"""', source, re.DOTALL)
    if not match:
        raise ValueError("Could not find SYSTEM_PROMPT in claude_client.py")
    return match.group(1)

# ---------------------------------------------------------------------------
# Claude runner
# ---------------------------------------------------------------------------

def run_claude(prompt_text: str, data_payload: dict, business_context: str) -> tuple[str, float]:
    """Returns (output_text, elapsed_seconds)."""
    import time
    payload = {k: v for k, v in data_payload.items()
               if k not in ("granular_context", "granularity_level")}
    gran_context = data_payload.get("granular_context", "")
    data_json = json.dumps(payload, indent=2)
    context_section = (
        f"\n\nBusiness Context:\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    gran_section = (
        f"\n\nGranular breakdown context:\n{gran_context}" if gran_context else ""
    )
    full_prompt = f"{prompt_text}\n\nHere is the campaign performance data to analyze:\n\n{data_json}{context_section}{gran_section}"

    start = time.time()
    try:
        result = subprocess.run(
            ["claude", "--print", "--output-format", "text", full_prompt],
            capture_output=True, text=True, encoding="utf-8", timeout=180
        )
        elapsed = round(time.time() - start, 1)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), elapsed
        return "", elapsed
    except subprocess.TimeoutExpired:
        print("    Claude timed out")
        return "", 180.0
    except Exception as e:
        print(f"    Claude error: {e}")
        return "", round(time.time() - start, 1)

# ---------------------------------------------------------------------------
# Scoring - fast regex/text, zero Claude calls
# ---------------------------------------------------------------------------

_KNOWN_NAMES = [
    "brand awareness", "performance max", "retargeting", "competitor keywords",
    "search - product launch", "dynamic search", "shopping - electronics",
    "display - remarketing", "awareness - lookalike", "retargeting - website",
    "conversion - cold traffic", "lead gen - free trial", "video views", "catalog sales",
]
_FILLER_PHRASES = [
    "it is important to", "it is worth noting", "leverage", "it is crucial",
    "it should be noted", "going forward", "in order to",
]
_HEDGE_PATTERNS = [
    r"\bconsider\b", r"\byou might\b", r"\blook into\b",
    r"\bmay want to\b", r"\bmight want to\b",
]
_IMPERATIVE_VERBS = (
    "pause", "increase", "decrease", "switch", "test", "add", "remove",
    "restructure", "reduce", "reallocate", "stop", "launch", "enable",
    "disable", "update", "fix", "move", "cut", "scale", "expand",
)


def _section(output: str, heading: str) -> str:
    m = re.search(rf"## {heading}(.*?)(?=^## |\Z)", output, re.DOTALL | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _has_funnel_data(payload: dict) -> bool:
    """True if the test payload contains funnel data with at least 2 stages."""
    fs = payload.get("funnel_summary")
    return bool(fs and len(fs.get("stages_available", [])) >= 2)


def _has_meta_with_frequency(payload: dict) -> bool:
    """True if the test payload has Meta campaigns with at least one frequency > 3.5."""
    meta = payload.get("meta", {})
    return any(
        (c.get("frequency") or 0) > 3.5
        for c in meta.get("campaigns", [])
    )


def _has_learning_phase_campaigns(payload: dict) -> bool:
    """True if any campaign in the payload has learning_phase=true."""
    for platform in ["google", "meta"]:
        for c in payload.get(platform, {}).get("campaigns", []):
            if c.get("learning_phase"):
                return True
    return False


def score_output(output: str, payload: dict = None) -> tuple[int, list[dict]]:
    """
    Score Claude output against 5 evals.
    Pass payload to enable N/A skipping for evals that require specific data.
    N/A evals count as passed (not penalised) so the max score stays consistent.
    """
    if not output:
        return 0, [{"name": e["name"], "passed": False} for e in EVALS]

    low = output.lower()
    results = []
    total = 0
    payload = payload or {}

    # 1. Trend direction referenced - output mentions improving/declining/stable near a campaign name
    trend_words = re.findall(r"\b(improving|declining|stable)\b", low)
    campaign_near_trend = any(
        n in low[max(0, m.start()-200):m.start()+200]
        for m in re.finditer(r"\b(improving|declining|stable)\b", low)
        for n in _KNOWN_NAMES
    )
    passed = len(trend_words) >= 1 and campaign_near_trend
    results.append({"name": "Trend direction referenced", "passed": passed})
    if passed: total += 1

    # 2. Learning phase campaign protected
    # Skip (auto-pass) if no learning_phase campaigns exist in the payload
    if not _has_learning_phase_campaigns(payload):
        results.append({"name": "Learning phase campaign protected", "passed": True, "skipped": True})
        total += 1
    else:
        has_learning_mention = bool(re.search(r"learning.phase", low))
        generic_violation = bool(re.search(
            r"\bpause\b.{0,40}learning.phase|\bstop\b.{0,40}learning.phase",
            low
        ))
        passed = has_learning_mention and not generic_violation
        results.append({"name": "Learning phase campaign protected", "passed": passed})
        if passed: total += 1

    # 3. Efficiency metric used
    passed = bool(re.search(r"\befficiency\b", low))
    results.append({"name": "Efficiency metric used", "passed": passed})
    if passed: total += 1

    # 4. Worst funnel stage named
    # Skip (auto-pass) if no funnel data in payload
    if not _has_funnel_data(payload):
        results.append({"name": "Worst funnel stage named", "passed": True, "skipped": True})
        total += 1
    else:
        funnel_stage_patterns = [
            r"lead.to.mql", r"mql.to.sql", r"sql.to.customer",
            r"worst.funnel", r"funnel.bottleneck", r"dropping off at",
            r"lead.mql", r"mql.sql",
        ]
        passed = any(re.search(p, low) for p in funnel_stage_patterns)
        results.append({"name": "Worst funnel stage named", "passed": passed})
        if passed: total += 1

    # 5. High frequency fatigue flagged
    # Skip (auto-pass) if no Meta campaigns with frequency > 3.5
    if not _has_meta_with_frequency(payload):
        results.append({"name": "High frequency fatigue flagged", "passed": True, "skipped": True})
        total += 1
    else:
        freq_mention = re.search(r"frequenc\w+\s+(?:of\s+)?(\d+\.?\d*)", low)
        freq_fatigue = bool(re.search(r"(frequenc\w+.{0,60}(fatigue|refresh|audience)|fatigue.{0,60}frequenc\w+)", low))
        passed = bool(freq_mention or freq_fatigue) and bool(re.search(r"frequenc", low))
        results.append({"name": "High frequency fatigue flagged", "passed": passed})
        if passed: total += 1

    return total, results
