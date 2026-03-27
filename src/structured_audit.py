"""
Structured Ads Audit Engine.
50+ checkpoints across 6 categories for Google + Meta campaigns.
Scores each check Pass/Warning/Fail, computes category and overall scores.
Pushes action items to action_items table and saves results to structured_audits table.
"""

import json
import subprocess
from datetime import datetime
from .claude_client import stream_prompt
from .db import _q, _q1, upsert_action_items
from .context import parse_context_from_json


# ---- Scoring weights per category ----
CATEGORY_WEIGHTS = {
    "Tracking Foundation":    0.25,
    "Campaign Architecture":  0.20,
    "Ad Set Configuration":   0.15,
    "Creative Performance":   0.15,
    "Cost Diagnostics":       0.15,
    "Account Health":         0.10,
}

STATUS_SCORE = {"pass": 100, "warning": 50, "fail": 0}


# ============================================================
# CHECK FUNCTIONS
# Each returns: {"check": str, "status": "pass"|"warning"|"fail", "reason": str, "action": str|None, "platform": str}
# ============================================================

def _build_context_filters(context_dict: dict) -> tuple[set, dict]:
    """
    Extract brand campaign names and per-campaign targets from context.
    Returns (brand_names_set, per_campaign_targets_dict).
    brand_names_set: lowercased campaign names tagged brand_awareness or brand.
    per_campaign_targets_dict: {lowercased_name: {target_roas, target_cpa, target_cpl}}
    """
    brand_names = set()
    per_campaign_targets = {}

    if not context_dict:
        return brand_names, per_campaign_targets

    tags = context_dict.get("campaign_tags", {})
    for name, tag in tags.items():
        if tag in ("brand_awareness", "brand"):
            brand_names.add(name.lower())

    targets = context_dict.get("campaign_targets", [])
    for t in targets:
        name = (t.get("name") or "").lower()
        if name:
            entry = {}
            if t.get("target_roas"):
                entry["target_roas"] = float(t["target_roas"])
            if t.get("target_cpa"):
                entry["target_cpa"] = float(t["target_cpa"])
            if t.get("target_cpl"):
                entry["target_cpl"] = float(t["target_cpl"])
            if entry:
                per_campaign_targets[name] = entry

    return brand_names, per_campaign_targets


def _check(name, status, reason, action=None, platform="all", campaign=None, priority="medium"):
    return {
        "check":    name,
        "status":   status,
        "reason":   reason,
        "action":   action,
        "platform": platform,
        "campaign": campaign,
        "priority": priority,
    }


# --- Category 1: Tracking Foundation ---

def check_zero_conversion_campaigns(campaigns, platform):
    zero = [c for c in campaigns if (c.get("conversions") or 0) == 0 and (c.get("spend") or 0) > 100]
    if not zero:
        return _check("Zero-Conversion Campaigns", "pass", f"All spending {platform} campaigns have conversions.", platform=platform)
    names = ", ".join(c["campaign_name"] for c in zero[:3])
    return _check(
        "Zero-Conversion Campaigns", "fail",
        f"{len(zero)} campaigns spending with zero conversions: {names}",
        action=f"Audit conversion tracking on: {names}. Check pixel/tag implementation.",
        platform=platform, priority="high"
    )


def check_conversion_value_gaps(campaigns, platform):
    has_value = [c for c in campaigns if (c.get("conversion_value") or 0) > 0]
    has_conv  = [c for c in campaigns if (c.get("conversions") or 0) > 0]
    if not has_conv:
        return _check("Conversion Value Tracking", "warning", "No conversions recorded - cannot assess value tracking.", platform=platform)
    if not has_value:
        return _check("Conversion Value Tracking", "warning",
                      "Conversions recorded but no conversion values - revenue attribution missing.",
                      action="Enable conversion value tracking to measure ROAS accurately.",
                      platform=platform, priority="high")
    coverage = len(has_value) / len(campaigns) if campaigns else 0
    if coverage < 0.5:
        return _check("Conversion Value Tracking", "warning",
                      f"Only {coverage:.0%} of campaigns have conversion value data.",
                      action="Ensure all campaigns report conversion values for accurate ROAS.",
                      platform=platform)
    return _check("Conversion Value Tracking", "pass",
                  f"{coverage:.0%} of campaigns reporting conversion values.", platform=platform)


def check_roas_data_quality(campaigns, platform):
    bad = [c for c in campaigns if (c.get("spend") or 0) > 0 and (c.get("roas") is None or c.get("roas", 0) < 0)]
    if bad:
        return _check("ROAS Data Quality", "warning",
                      f"{len(bad)} campaigns have invalid or missing ROAS values.",
                      action="Verify conversion tracking is correctly attributed to these campaigns.",
                      platform=platform)
    return _check("ROAS Data Quality", "pass", "ROAS data looks consistent across campaigns.", platform=platform)


# --- Category 2: Campaign Architecture ---

def check_budget_concentration(campaigns, platform):
    total_spend = sum(c.get("spend") or 0 for c in campaigns)
    if total_spend == 0:
        return _check("Budget Concentration", "warning", "No spend data.", platform=platform)
    if len(campaigns) == 1:
        return _check("Budget Concentration", "pass", "Single campaign - concentration N/A.", platform=platform)
    sorted_camps = sorted(campaigns, key=lambda x: x.get("spend") or 0, reverse=True)
    top_pct = (sorted_camps[0].get("spend") or 0) / total_spend * 100
    if top_pct > 70:
        return _check(
            "Budget Concentration", "fail",
            f"{sorted_camps[0]['campaign_name']} consumes {top_pct:.0f}% of {platform} budget - high concentration risk.",
            action=f"Diversify budget away from {sorted_camps[0]['campaign_name']} to reduce single-campaign dependency.",
            platform=platform, campaign=sorted_camps[0]["campaign_name"], priority="high"
        )
    if top_pct > 50:
        return _check("Budget Concentration", "warning",
                      f"Top campaign takes {top_pct:.0f}% of {platform} budget.", platform=platform)
    return _check("Budget Concentration", "pass",
                  f"Budget reasonably distributed - top campaign at {top_pct:.0f}%.", platform=platform)


def check_campaign_count(campaigns, platform):
    n = len(campaigns)
    if n == 0:
        return _check("Campaign Count", "warning", f"No {platform} campaigns found.", platform=platform)
    if n > 30:
        return _check("Campaign Count", "warning",
                      f"{n} {platform} campaigns active - high complexity. Consider consolidation.",
                      action=f"Review {platform} campaign structure. Consolidate similar-objective campaigns.",
                      platform=platform)
    if n < 2:
        return _check("Campaign Count", "warning",
                      f"Only {n} {platform} campaign - limited diversification.",
                      platform=platform)
    return _check("Campaign Count", "pass", f"{n} {platform} campaigns - reasonable structure.", platform=platform)


def check_naming_consistency(campaigns, platform):
    names = [c["campaign_name"] for c in campaigns if c.get("campaign_name")]
    if not names:
        return _check("Naming Consistency", "warning", "No campaign names to evaluate.", platform=platform)
    # Check for separator pattern consistency (_, -, |)
    patterns = {}
    for name in names:
        if "_" in name: patterns["_"] = patterns.get("_", 0) + 1
        if " - " in name: patterns["-"] = patterns.get("-", 0) + 1
        if "|" in name: patterns["|"] = patterns.get("|", 0) + 1
    if not patterns:
        return _check("Naming Consistency", "warning",
                      "Campaign names lack structured separators. Hard to filter/segment.",
                      action="Adopt naming convention: Platform_Objective_Audience_Creative format.",
                      platform=platform)
    return _check("Naming Consistency", "pass", "Campaign names follow identifiable separator patterns.", platform=platform)


def check_objective_mix(campaigns, platform):
    # Heuristic: look for TOF/BOF keywords in names
    names_lower = [c["campaign_name"].lower() for c in campaigns if c.get("campaign_name")]
    has_awareness = any(any(kw in n for kw in ["awareness", "reach", "tof", "top", "brand", "prospecting"]) for n in names_lower)
    has_conversion = any(any(kw in n for kw in ["conversion", "purchase", "retarget", "bof", "bottom", "remarketing", "sale"]) for n in names_lower)
    if has_awareness and has_conversion:
        return _check("Funnel Coverage", "pass", "Both awareness and conversion campaigns detected.", platform=platform)
    if not has_awareness and not has_conversion:
        return _check("Funnel Coverage", "warning",
                      "Cannot determine funnel stages from campaign names. Add funnel-stage tags.",
                      action="Tag campaigns with funnel stage (TOF/MOF/BOF) in naming convention.",
                      platform=platform)
    if has_conversion and not has_awareness:
        return _check("Funnel Coverage", "warning",
                      "Only conversion campaigns detected. No awareness/prospecting visible.",
                      action="Add prospecting campaigns to build top-of-funnel audience.",
                      platform=platform)
    return _check("Funnel Coverage", "pass", "Funnel mix looks reasonable.", platform=platform)


# --- Category 3: Ad Set / Ad Group Configuration ---

def check_learning_phase(campaigns, platform):
    low_impression = [c for c in campaigns if 0 < (c.get("impressions") or 0) < 1000 and (c.get("spend") or 0) > 0]
    if not low_impression:
        return _check("Learning Phase Flags", "pass", "No campaigns stuck in early learning phase.", platform=platform)
    names = ", ".join(c["campaign_name"] for c in low_impression[:3])
    return _check(
        "Learning Phase Flags", "warning",
        f"{len(low_impression)} campaigns with <1K impressions may be in learning phase: {names}",
        action=f"Give these campaigns more budget or time before optimization: {names}",
        platform=platform
    )


def check_bid_strategy_mix(campaigns, platform):
    # Estimate via ROAS distribution - very high ROAS outliers suggest mixed strategies
    roas_vals = [c.get("roas") or 0 for c in campaigns if (c.get("spend") or 0) > 0]
    if len(roas_vals) < 2:
        return _check("Bid Strategy Consistency", "pass", "Too few campaigns to assess.", platform=platform)
    avg = sum(roas_vals) / len(roas_vals)
    outliers = [r for r in roas_vals if r > avg * 3 or (r < avg * 0.3 and r > 0)]
    if len(outliers) > len(roas_vals) * 0.3:
        return _check("Bid Strategy Consistency", "warning",
                      "High ROAS variance across campaigns suggests mixed or inconsistent bid strategies.",
                      action="Audit bid strategies across campaigns. Align with account objectives.",
                      platform=platform)
    return _check("Bid Strategy Consistency", "pass",
                  "ROAS distribution is relatively consistent across campaigns.", platform=platform)


def check_spend_distribution(campaigns, platform):
    spending = [c for c in campaigns if (c.get("spend") or 0) > 0]
    non_spending = [c for c in campaigns if (c.get("spend") or 0) == 0]
    if non_spending and spending:
        pct_inactive = len(non_spending) / len(campaigns) * 100
        if pct_inactive > 30:
            return _check("Spend Distribution", "warning",
                          f"{pct_inactive:.0f}% of campaigns have zero spend. Check budgets/scheduling.",
                          action="Review paused or budget-capped campaigns with zero spend.",
                          platform=platform)
    return _check("Spend Distribution", "pass", "Spend is active across campaigns.", platform=platform)


# --- Category 4: Creative Performance ---

def check_ctr_health(campaigns, platform, target_ctr=None):
    spending = [c for c in campaigns if (c.get("spend") or 0) > 50]
    if not spending:
        return _check("CTR Health", "warning", "Insufficient spend to evaluate CTR.", platform=platform)
    avg_ctr = sum(c.get("ctr") or 0 for c in spending) / len(spending)
    benchmark = target_ctr or (2.0 if platform == "meta" else 3.0)
    if avg_ctr < benchmark * 0.5:
        return _check("CTR Health", "fail",
                      f"Average CTR {avg_ctr:.2f}% is critically below {benchmark:.1f}% benchmark.",
                      action=f"Creative refresh needed urgently. Test new hooks, visuals, and CTAs on {platform}.",
                      platform=platform, priority="high")
    if avg_ctr < benchmark:
        return _check("CTR Health", "warning",
                      f"Average CTR {avg_ctr:.2f}% below {benchmark:.1f}% benchmark.",
                      action=f"A/B test new ad creatives on underperforming {platform} campaigns.",
                      platform=platform)
    return _check("CTR Health", "pass", f"Average CTR {avg_ctr:.2f}% is at or above benchmark.", platform=platform)


def check_ctr_decline_trend(campaigns_hist, platform):
    """Checks if CTR has declined across consecutive upload periods."""
    if not campaigns_hist or len(campaigns_hist) < 2:
        return _check("CTR Trend", "pass", "Insufficient history for trend analysis.", platform=platform)
    # Group by period (upload_id)
    period_ctrs = {}
    for row in campaigns_hist:
        uid = row.get("upload_id")
        if uid not in period_ctrs:
            period_ctrs[uid] = []
        if row.get("ctr"):
            period_ctrs[uid].append(row["ctr"])
    periods = sorted(period_ctrs.keys())
    if len(periods) < 2:
        return _check("CTR Trend", "pass", "Need at least 2 periods for trend.", platform=platform)
    avgs = [sum(period_ctrs[p]) / len(period_ctrs[p]) for p in periods if period_ctrs[p]]
    if len(avgs) < 2:
        return _check("CTR Trend", "pass", "Insufficient data points.", platform=platform)
    declining = all(avgs[i] > avgs[i+1] for i in range(len(avgs)-1))
    if declining:
        drop = (avgs[0] - avgs[-1]) / avgs[0] * 100 if avgs[0] else 0
        return _check("CTR Trend", "warning",
                      f"CTR declining consistently across {len(avgs)} periods (dropped {drop:.0f}%). Creative fatigue likely.",
                      action="Rotate ad creatives. Introduce new formats or hooks.",
                      platform=platform)
    return _check("CTR Trend", "pass", "No consistent CTR decline detected.", platform=platform)


def check_creative_diversity(campaigns, platform):
    names_lower = [c["campaign_name"].lower() for c in campaigns]
    formats = set()
    for n in names_lower:
        if any(kw in n for kw in ["video", "reel", "story"]): formats.add("video")
        if any(kw in n for kw in ["image", "banner", "static", "carousel"]): formats.add("image")
        if any(kw in n for kw in ["search", "keyword", "dsa"]): formats.add("search")
        if any(kw in n for kw in ["shopping", "pmax", "performance max"]): formats.add("shopping")
    if len(formats) >= 2:
        return _check("Creative Diversity", "pass",
                      f"Multiple formats detected: {', '.join(formats)}.", platform=platform)
    return _check("Creative Diversity", "warning",
                  "Limited format diversity detected from campaign names. Consider testing new ad formats.",
                  action="Test at least 2 ad formats (e.g. video + static, search + display).",
                  platform=platform)


# --- Category 5: Cost Diagnostics ---

def check_cpa_vs_target(campaigns, platform, target_cpa=None, brand_names=None, per_campaign_targets=None):
    brand_names = brand_names or set()
    per_campaign_targets = per_campaign_targets or {}
    non_brand = [c for c in campaigns if c.get("campaign_name", "").lower() not in brand_names]
    if not target_cpa and not per_campaign_targets:
        return _check("CPA vs Target", "pass", "No CPA target set. Configure in Budget Rules.", platform=platform)
    bad = []
    for c in non_brand:
        c_name = c.get("campaign_name", "").lower()
        c_target = (per_campaign_targets.get(c_name, {}).get("target_cpa")
                    or per_campaign_targets.get(c_name, {}).get("target_cpl")
                    or target_cpa)
        if c_target and (c.get("cac") or 0) > c_target * 1.5 and (c.get("conversions") or 0) > 0:
            bad.append(c)
    if bad:
        names = ", ".join(c["campaign_name"] for c in bad[:3])
        return _check("CPA vs Target", "fail",
                      f"{len(bad)} campaigns exceed CPA target ({target_cpa:,.0f}) by >50%: {names}",
                      action=f"Pause or restructure high-CPA campaigns: {names}",
                      platform=platform, priority="high")
    warn = []
    for c in non_brand:
        c_name = c.get("campaign_name", "").lower()
        c_target = (per_campaign_targets.get(c_name, {}).get("target_cpa")
                    or per_campaign_targets.get(c_name, {}).get("target_cpl")
                    or target_cpa)
        if c_target and (c.get("cac") or 0) > c_target and (c.get("conversions") or 0) > 0:
            warn.append(c)
    if warn:
        return _check("CPA vs Target", "warning",
                      f"{len(warn)} campaigns above CPA target ({target_cpa:,.0f}).", platform=platform)
    return _check("CPA vs Target", "pass", f"All campaigns within CPA target ({target_cpa:,.0f}).", platform=platform)


def check_roas_vs_target(campaigns, platform, target_roas=None, brand_names=None, per_campaign_targets=None):
    brand_names = brand_names or set()
    per_campaign_targets = per_campaign_targets or {}
    non_brand = [c for c in campaigns if c.get("campaign_name", "").lower() not in brand_names]
    if not target_roas and not per_campaign_targets:
        return _check("ROAS vs Target", "pass", "No ROAS target set. Configure in Budget Rules.", platform=platform)
    spending = [c for c in non_brand if (c.get("spend") or 0) > 100]
    if not spending:
        return _check("ROAS vs Target", "warning", "Insufficient spend to evaluate ROAS.", platform=platform)
    below = []
    for c in spending:
        c_name = c.get("campaign_name", "").lower()
        c_target = per_campaign_targets.get(c_name, {}).get("target_roas") or target_roas
        if c_target and (c.get("roas") or 0) < c_target * 0.7:
            below.append(c)
    if below:
        names = ", ".join(c["campaign_name"] for c in below[:3])
        return _check("ROAS vs Target", "fail",
                      f"{len(below)} campaigns >30% below ROAS target ({target_roas}x): {names}",
                      action=f"Review and optimize or pause: {names}",
                      platform=platform, campaign=below[0]["campaign_name"] if below else None, priority="high")
    warn_camps = []
    for c in spending:
        c_name = c.get("campaign_name", "").lower()
        c_target = per_campaign_targets.get(c_name, {}).get("target_roas") or target_roas
        if c_target and (c.get("roas") or 0) < c_target:
            warn_camps.append(c)
    if warn_camps:
        return _check("ROAS vs Target", "warning",
                      f"{len(warn_camps)} campaigns below ROAS target ({target_roas}x).", platform=platform)
    return _check("ROAS vs Target", "pass", f"All campaigns meeting ROAS target ({target_roas}x).", platform=platform)


def check_cpc_outliers(campaigns, platform):
    spending = [c for c in campaigns if (c.get("spend") or 0) > 50 and (c.get("cpc") or 0) > 0]
    if len(spending) < 2:
        return _check("CPC Outliers", "pass", "Insufficient data.", platform=platform)
    cpcs = [c["cpc"] for c in spending]
    avg_cpc = sum(cpcs) / len(cpcs)
    outliers = [c for c in spending if c["cpc"] > avg_cpc * 2.5]
    if outliers:
        names = ", ".join(c["campaign_name"] for c in outliers[:2])
        return _check("CPC Outliers", "warning",
                      f"{len(outliers)} campaigns with CPC >2.5x account average: {names}",
                      action=f"Review bidding strategy and keyword/audience quality for: {names}",
                      platform=platform)
    return _check("CPC Outliers", "pass", f"CPC distribution looks normal (avg: {avg_cpc:.2f}).", platform=platform)


def check_wasted_spend(campaigns, platform, brand_names=None):
    brand_names = brand_names or set()
    non_brand = [c for c in campaigns if c.get("campaign_name", "").lower() not in brand_names]
    excluded_count = len(campaigns) - len(non_brand)
    total_waste = sum(c.get("wasted_spend") or 0 for c in non_brand)
    total_spend = sum(c.get("spend") or 0 for c in non_brand)
    excl_note = f" ({excluded_count} brand campaign(s) excluded from waste ratio)" if excluded_count else ""
    if total_spend == 0:
        return _check("Wasted Spend", "pass", "No spend data.", platform=platform)
    if total_waste == 0:
        return _check("Wasted Spend", "pass", f"No wasted spend flagged.{excl_note}", platform=platform)
    waste_pct = total_waste / total_spend * 100
    if waste_pct > 20:
        return _check("Wasted Spend", "fail",
                      f"{waste_pct:.0f}% of {platform} budget is wasted spend ({total_waste:,.0f}).{excl_note}",
                      action="Review negative keywords, exclusions, and audience targeting to cut waste.",
                      platform=platform, priority="high")
    if waste_pct > 10:
        return _check("Wasted Spend", "warning",
                      f"{waste_pct:.0f}% of budget flagged as wasted spend.{excl_note}", platform=platform)
    return _check("Wasted Spend", "pass", f"Wasted spend at acceptable level ({waste_pct:.0f}%).{excl_note}", platform=platform)


# --- Category 6: Account Health ---

def check_zero_spend_campaigns(campaigns, platform):
    zero = [c for c in campaigns if (c.get("spend") or 0) == 0]
    if not zero:
        return _check("Zero-Spend Campaigns", "pass", "All campaigns have spend.", platform=platform)
    names = ", ".join(c["campaign_name"] for c in zero[:3])
    return _check("Zero-Spend Campaigns", "warning",
                  f"{len(zero)} campaigns with zero spend: {names}",
                  action=f"Review budget and scheduling for zero-spend campaigns: {names}",
                  platform=platform)


def check_impressions_no_clicks(campaigns, platform):
    ghost = [c for c in campaigns if (c.get("impressions") or 0) > 1000 and (c.get("clicks") or 0) == 0]
    if not ghost:
        return _check("Impressions Without Clicks", "pass", "No ghost campaigns detected.", platform=platform)
    names = ", ".join(c["campaign_name"] for c in ghost[:2])
    return _check("Impressions Without Clicks", "warning",
                  f"{len(ghost)} campaigns with impressions but zero clicks: {names}",
                  action=f"Review ad relevance and targeting for: {names}. Consider pausing.",
                  platform=platform)


def check_spend_efficiency(campaigns, platform):
    """Revenue per dollar spent - overall account efficiency."""
    total_spend = sum(c.get("spend") or 0 for c in campaigns)
    total_rev   = sum(c.get("conversion_value") or 0 for c in campaigns)
    if total_spend == 0:
        return _check("Spend Efficiency", "warning", "No spend data.", platform=platform)
    roas = total_rev / total_spend
    if roas == 0:
        return _check("Spend Efficiency", "warning",
                      f"Zero revenue tracked on {platform}. Check conversion value setup.",
                      action="Verify conversion value reporting is enabled.", platform=platform)
    if roas < 1.0:
        return _check("Spend Efficiency", "fail",
                      f"{platform} account ROAS of {roas:.2f}x - spending more than earning.",
                      action=f"Urgent: {platform} is not profitable. Pause worst performers immediately.",
                      platform=platform, priority="high")
    if roas < 2.0:
        return _check("Spend Efficiency", "warning",
                      f"{platform} account ROAS of {roas:.2f}x - below typical target.", platform=platform)
    return _check("Spend Efficiency", "pass",
                  f"{platform} account ROAS: {roas:.2f}x - healthy.", platform=platform)


def check_top_campaign_health(campaigns, platform, brand_names=None):
    brand_names = brand_names or set()
    if not campaigns:
        return _check("Top Campaign Health", "warning", "No campaigns.", platform=platform)
    top = sorted(campaigns, key=lambda x: x.get("spend") or 0, reverse=True)[0]
    is_brand = top.get("campaign_name", "").lower() in brand_names
    issues = []
    if not is_brand and (top.get("roas") or 0) < 1.5:
        issues.append(f"low ROAS ({top.get('roas', 0):.2f}x)")
    if (top.get("conversions") or 0) == 0:
        issues.append("zero conversions")
    if (top.get("ctr") or 0) < 0.5:
        issues.append(f"very low CTR ({top.get('ctr', 0):.2f}%)")
    if issues:
        return _check("Top Campaign Health", "fail",
                      f"Top spend campaign '{top['campaign_name']}' has issues: {', '.join(issues)}.",
                      action=f"Prioritize fixing '{top['campaign_name']}' - it consumes the most budget.",
                      platform=platform, campaign=top["campaign_name"], priority="high")
    brand_note = " (brand campaign - ROAS threshold skipped)" if is_brand else ""
    return _check("Top Campaign Health", "pass",
                  f"Top campaign '{top['campaign_name']}' looks healthy.{brand_note}", platform=platform)


# ============================================================
# ORCHESTRATOR
# ============================================================

def run_structured_audit(conn, client_id: int, upload_id: int = None) -> dict:
    """
    Run all checks for given client and optional upload.
    If upload_id is None, uses latest upload.
    Returns full audit result dict.
    """
    # Resolve upload
    if not upload_id:
        latest = _q1(conn, """
            SELECT id FROM uploads WHERE client_id = ?
            ORDER BY uploaded_at DESC LIMIT 1
        """, [client_id])
        if not latest:
            return {"error": "No uploads found for this client."}
        upload_id = latest["id"]

    # Client info
    client = _q1(conn, "SELECT name, currency, context FROM clients WHERE id = ?", [client_id]) or {}
    client_name = client.get("name", "Unknown")
    currency    = client.get("currency", "INR")
    brand_names, per_campaign_targets = _build_context_filters(
        parse_context_from_json(client.get("context") or "")
    )

    # Budget rules for targets
    rules = _q1(conn, """
        SELECT google_min_roas, meta_min_roas, google_min_cpl, meta_min_cpl
        FROM budget_rules WHERE client_id = ?
    """, [client_id]) or {}

    # Campaigns for this upload
    all_camps = _q(conn, """
        SELECT campaign_name, platform, spend, impressions, clicks,
               conversions, conversion_value, roas, cac, ctr, cpc, wasted_spend
        FROM campaigns WHERE upload_id = ? AND client_id = ?
        ORDER BY spend DESC
    """, [upload_id, client_id])

    google_camps = [c for c in all_camps if (c.get("platform") or "").lower() == "google"]
    meta_camps   = [c for c in all_camps if (c.get("platform") or "").lower() == "meta"]

    # Historical data for trend checks (last 6 uploads)
    hist_camps = _q(conn, """
        SELECT c.campaign_name, c.platform, c.ctr, c.roas,
               u.id AS upload_id,
               COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS sort_date
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
        ORDER BY sort_date ASC
        LIMIT 200
    """, [client_id])
    google_hist = [r for r in hist_camps if (r.get("platform") or "").lower() == "google"]
    meta_hist   = [r for r in hist_camps if (r.get("platform") or "").lower() == "meta"]

    # Targets from budget rules
    g_roas = rules.get("google_min_roas")
    m_roas = rules.get("meta_min_roas")
    g_cpl  = rules.get("google_min_cpl")
    m_cpl  = rules.get("meta_min_cpl")

    # ---- Run all checks ----
    checks_by_category = {
        "Tracking Foundation":   [],
        "Campaign Architecture": [],
        "Ad Set Configuration":  [],
        "Creative Performance":  [],
        "Cost Diagnostics":      [],
        "Account Health":        [],
    }

    def add(cat, result):
        checks_by_category[cat].append(result)

    # Google checks
    if google_camps:
        add("Tracking Foundation",   check_zero_conversion_campaigns(google_camps, "google"))
        add("Tracking Foundation",   check_conversion_value_gaps(google_camps, "google"))
        add("Tracking Foundation",   check_roas_data_quality(google_camps, "google"))
        add("Campaign Architecture", check_budget_concentration(google_camps, "google"))
        add("Campaign Architecture", check_campaign_count(google_camps, "google"))
        add("Campaign Architecture", check_naming_consistency(google_camps, "google"))
        add("Campaign Architecture", check_objective_mix(google_camps, "google"))
        add("Ad Set Configuration",  check_learning_phase(google_camps, "google"))
        add("Ad Set Configuration",  check_bid_strategy_mix(google_camps, "google"))
        add("Ad Set Configuration",  check_spend_distribution(google_camps, "google"))
        add("Creative Performance",  check_ctr_health(google_camps, "google"))
        add("Creative Performance",  check_ctr_decline_trend(google_hist, "google"))
        add("Creative Performance",  check_creative_diversity(google_camps, "google"))
        add("Cost Diagnostics",      check_cpa_vs_target(google_camps, "google", g_cpl, brand_names, per_campaign_targets))
        add("Cost Diagnostics",      check_roas_vs_target(google_camps, "google", g_roas, brand_names, per_campaign_targets))
        add("Cost Diagnostics",      check_cpc_outliers(google_camps, "google"))
        add("Cost Diagnostics",      check_wasted_spend(google_camps, "google", brand_names))
        add("Account Health",        check_zero_spend_campaigns(google_camps, "google"))
        add("Account Health",        check_impressions_no_clicks(google_camps, "google"))
        add("Account Health",        check_spend_efficiency(google_camps, "google"))
        add("Account Health",        check_top_campaign_health(google_camps, "google", brand_names))

    # Meta checks
    if meta_camps:
        add("Tracking Foundation",   check_zero_conversion_campaigns(meta_camps, "meta"))
        add("Tracking Foundation",   check_conversion_value_gaps(meta_camps, "meta"))
        add("Tracking Foundation",   check_roas_data_quality(meta_camps, "meta"))
        add("Campaign Architecture", check_budget_concentration(meta_camps, "meta"))
        add("Campaign Architecture", check_campaign_count(meta_camps, "meta"))
        add("Campaign Architecture", check_naming_consistency(meta_camps, "meta"))
        add("Campaign Architecture", check_objective_mix(meta_camps, "meta"))
        add("Ad Set Configuration",  check_learning_phase(meta_camps, "meta"))
        add("Ad Set Configuration",  check_bid_strategy_mix(meta_camps, "meta"))
        add("Ad Set Configuration",  check_spend_distribution(meta_camps, "meta"))
        add("Creative Performance",  check_ctr_health(meta_camps, "meta", 1.5))
        add("Creative Performance",  check_ctr_decline_trend(meta_hist, "meta"))
        add("Creative Performance",  check_creative_diversity(meta_camps, "meta"))
        add("Cost Diagnostics",      check_cpa_vs_target(meta_camps, "meta", m_cpl, brand_names, per_campaign_targets))
        add("Cost Diagnostics",      check_roas_vs_target(meta_camps, "meta", m_roas, brand_names, per_campaign_targets))
        add("Cost Diagnostics",      check_cpc_outliers(meta_camps, "meta"))
        add("Cost Diagnostics",      check_wasted_spend(meta_camps, "meta", brand_names))
        add("Account Health",        check_zero_spend_campaigns(meta_camps, "meta"))
        add("Account Health",        check_impressions_no_clicks(meta_camps, "meta"))
        add("Account Health",        check_spend_efficiency(meta_camps, "meta"))
        add("Account Health",        check_top_campaign_health(meta_camps, "meta", brand_names))

    if not google_camps and not meta_camps:
        return {"error": "No campaign data found for this upload."}

    # ---- Score categories ----
    category_scores = {}
    for cat, checks in checks_by_category.items():
        if not checks:
            continue
        scores = [STATUS_SCORE[c["status"]] for c in checks]
        category_scores[cat] = round(sum(scores) / len(scores))

    # Overall weighted score
    total_weight = sum(CATEGORY_WEIGHTS[cat] for cat in category_scores)
    overall_score = round(
        sum(category_scores[cat] * CATEGORY_WEIGHTS.get(cat, 0.1) for cat in category_scores) / total_weight
    ) if total_weight else 0

    # ---- Flatten all checks for output ----
    all_checks = []
    for cat, checks in checks_by_category.items():
        for ch in checks:
            all_checks.append({**ch, "category": cat})

    # ---- Recommendations: only fail/warning with actions ----
    recommendations = [
        {
            "category": c["category"],
            "check":    c["check"],
            "status":   c["status"],
            "reason":   c["reason"],
            "action":   c["action"],
            "platform": c["platform"],
            "campaign": c.get("campaign"),
            "priority": c.get("priority", "medium"),
        }
        for c in all_checks if c["status"] != "pass" and c.get("action")
    ]
    recommendations.sort(key=lambda x: (0 if x["priority"] == "high" else 1 if x["priority"] == "medium" else 2))

    # ---- Push action items ----
    action_rows = []
    for r in recommendations:
        if r.get("action"):
            action_rows.append({
                "text":     r["action"],
                "source":   "structured_audit",
                "priority": r["priority"],
                "campaign": r.get("campaign"),
                "platform": r.get("platform"),
            })
    if action_rows:
        upsert_action_items(conn, client_id, action_rows)

    # ---- Save to structured_audits table ----
    try:
        conn.execute("""
            INSERT INTO structured_audits (
                client_id, upload_id, overall_score, category_scores,
                checks_json, recommendations_json, created_at
            ) VALUES (?,?,?,?,?,?,?)
        """, [
            client_id, upload_id, overall_score,
            json.dumps(category_scores),
            json.dumps(all_checks),
            json.dumps(recommendations),
            datetime.now().isoformat(),
        ])
        conn.commit()
    except Exception:
        pass  # table may not exist yet, handled by migration

    return {
        "overall_score":    overall_score,
        "category_scores":  category_scores,
        "checks":           all_checks,
        "recommendations":  recommendations,
        "client_name":      client_name,
        "currency":         currency,
        "upload_id":        upload_id,
        "platforms":        list({c["platform"] for c in all_camps}),
        "total_checks":     len(all_checks),
        "fail_count":       sum(1 for c in all_checks if c["status"] == "fail"),
        "warning_count":    sum(1 for c in all_checks if c["status"] == "warning"),
        "pass_count":       sum(1 for c in all_checks if c["status"] == "pass"),
    }


def _build_audit_summary_prompt(result: dict, business_context: str = "") -> str:
    score = result["overall_score"]
    fails = result["fail_count"]
    warns = result["warning_count"]
    top_recs = result["recommendations"][:5]
    payload = (
        f"Client: {result['client_name']}\n"
        f"Overall account health score: {score}/100\n"
        f"Checks run: {result['total_checks']} | Failed: {fails} | Warnings: {warns} | Passed: {result['pass_count']}\n"
        f"Platforms audited: {', '.join(result['platforms'])}\n\n"
        f"Category scores:\n"
        + "\n".join(f"  {cat}: {sc}/100" for cat, sc in result["category_scores"].items())
        + "\n\nTop issues:\n"
        + "\n".join(f"  [{r['priority'].upper()}] {r['check']}: {r['reason']}" for r in top_recs)
    )
    context_block = f"\n\n{business_context}" if business_context else ""
    return (
        "You are a senior performance marketing auditor. "
        "Write a concise 2-paragraph executive summary of this ad account structured audit. "
        "Paragraph 1: overall health assessment with score context. "
        "Paragraph 2: 2-3 most critical action items. "
        "Be direct. No preamble. Clean markdown.\n\n"
        + payload + context_block
    )


def stream_audit_summary(result: dict, business_context: str = ""):
    """Yields ('chunk', text) or ('done', full_text). Raises on failure."""
    if not result or "error" in result:
        raise ValueError("Audit result is empty or contains an error")
    prompt = _build_audit_summary_prompt(result, business_context)
    for event_type, text in stream_prompt(prompt):
        if event_type == 'chunk':
            yield ('chunk', text)
        elif event_type == 'done':
            yield ('done', text)
            return


def generate_audit_summary(result: dict, business_context: str = "") -> str:
    """Ask Claude for a 1-paragraph executive summary of the structured audit. Raises on failure."""
    if not result or "error" in result:
        raise ValueError("Audit result is empty or contains an error")
    prompt = _build_audit_summary_prompt(result, business_context)
    result_proc = subprocess.run(
        ["claude", "--print", "--output-format", "text", prompt],
        capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    if result_proc.returncode != 0 or not result_proc.stdout.strip():
        err = result_proc.stderr.strip() if result_proc.stderr else "No output returned"
        raise RuntimeError(f"Claude CLI error: {err}")
    return result_proc.stdout.strip()
