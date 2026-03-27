"""
Client business context - structured fields that feed into all Claude prompts.
"""

# Attribution model descriptions for Claude
ATTRIBUTION_MODEL_DESCRIPTIONS = {
    "last_click":    "Last-click attribution - all credit goes to the final ad clicked before conversion. Brand/branded campaigns will look stronger than they are. Top-of-funnel campaigns (awareness, prospecting) will be undercounted.",
    "first_click":   "First-click attribution - all credit goes to the first ad that introduced the user. Top-of-funnel campaigns will look stronger. Retargeting and bottom-of-funnel campaigns will be undercounted.",
    "linear":        "Linear attribution - credit split equally across all touchpoints. No single campaign is over- or under-credited, but the model may dilute the real impact of high-intent clicks.",
    "time_decay":    "Time-decay attribution - recent touchpoints get more credit. Retargeting and remarketing campaigns will appear stronger. Long sales cycle campaigns (brand, content) will be undercounted.",
    "data_driven":   "Data-driven attribution (Google) - ML-based, distributes credit based on actual contribution to conversion paths. Most accurate but requires sufficient conversion volume (50+ conversions/month).",
    "position_based":"Position-based (40/20/40) attribution - 40% credit to first and last touch, 20% split across middle. Balanced between awareness and conversion credit.",
    "unknown":       "Attribution model not specified. Interpret ROAS with caution - cross-channel comparisons may be unreliable.",
}

# Benchmark ROAS/CPL ranges by business type (rough industry benchmarks)
BENCHMARK_RANGES = {
    "ecommerce": {
        "label": "E-commerce / D2C",
        "google_roas": {"low": 2.0, "median": 4.5, "high": 8.0},
        "meta_roas":   {"low": 1.5, "median": 3.0, "high": 6.0},
        "ctr_google":  {"low": 1.5, "median": 3.5, "high": 7.0},
        "ctr_meta":    {"low": 0.5, "median": 1.2, "high": 2.5},
        "note": "Benchmarks vary widely by category (fashion vs electronics vs FMCG). Use as rough orientation only.",
    },
    "b2b_saas": {
        "label": "B2B SaaS",
        "google_cpl": {"low": 800, "median": 2500, "high": 6000},
        "meta_cpl":   {"low": 600, "median": 1800, "high": 4500},
        "ctr_google": {"low": 2.0, "median": 4.5, "high": 9.0},
        "ctr_meta":   {"low": 0.3, "median": 0.8, "high": 1.8},
        "note": "CPL varies heavily by ICP (SMB vs enterprise) and geography. High CPL is acceptable if SQL rate is strong.",
    },
    "lead_gen": {
        "label": "Lead Generation",
        "google_cpl": {"low": 300, "median": 900, "high": 2500},
        "meta_cpl":   {"low": 200, "median": 600, "high": 1500},
        "ctr_google": {"low": 2.5, "median": 5.0, "high": 10.0},
        "ctr_meta":   {"low": 0.5, "median": 1.2, "high": 2.8},
        "note": "Lead gen CPL benchmarks assume standard form fills. Highly qualified lead flows (demos, appointments) will naturally cost more.",
    },
    "app": {
        "label": "Mobile App",
        "google_cpi": {"low": 20, "median": 60, "high": 150},
        "meta_cpi":   {"low": 15, "median": 45, "high": 120},
        "ctr_google": {"low": 1.0, "median": 2.5, "high": 5.0},
        "ctr_meta":   {"low": 0.4, "median": 1.0, "high": 2.2},
        "note": "CPI benchmarks are for app installs. In-app purchase ROAS targets depend on LTV model.",
    },
    "local": {
        "label": "Local / Brick-and-Mortar",
        "google_cpl": {"low": 150, "median": 400, "high": 900},
        "meta_cpl":   {"low": 100, "median": 280, "high": 650},
        "ctr_google": {"low": 3.0, "median": 6.0, "high": 12.0},
        "ctr_meta":   {"low": 0.6, "median": 1.5, "high": 3.5},
        "note": "Local businesses often undercount conversions (store visits, calls). Reported ROAS understates true impact.",
    },
}

# Business type definitions with implied assumptions Claude should make
BUSINESS_TYPE_PROFILES = {
    "ecommerce": {
        "label": "E-commerce / D2C",
        "sales_cycle": None,  # Not applicable - impulse/short purchase
        "implied": (
            "This is an e-commerce or D2C business. Purchases are typically impulse or "
            "short-consideration. ROAS and revenue are primary metrics. "
            "Do not reference sales cycles. Focus on cart abandonment, product-level "
            "ROAS, and retargeting efficiency."
        ),
        "show_fields": ["goal", "audience", "seasonal", "avg_order_value", "campaign_notes", "budget_notes"],
    },
    "b2b_saas": {
        "label": "B2B SaaS",
        "sales_cycle": "long",
        "implied": (
            "This is a B2B SaaS business with a long sales cycle (typically 1-6 months). "
            "Lead quality matters more than lead volume. MQL and SQL rates are critical. "
            "ROAS may not be directly measurable - focus on cost per MQL, cost per SQL, "
            "and pipeline value. Brand awareness campaigns support long-term pipeline."
        ),
        "show_fields": ["goal", "audience", "sales_cycle_weeks", "seasonal", "campaign_notes", "budget_notes"],
    },
    "lead_gen": {
        "label": "Lead Generation",
        "sales_cycle": "medium",
        "implied": (
            "This is a lead generation business. CPL and lead volume are primary metrics. "
            "Funnel conversion rates (lead to MQL to SQL) are important. "
            "Sales cycle is typically days to weeks."
        ),
        "show_fields": ["goal", "audience", "sales_cycle_weeks", "seasonal", "campaign_notes", "budget_notes"],
    },
    "app": {
        "label": "Mobile App",
        "sales_cycle": None,
        "implied": (
            "This is a mobile app business. Cost per install (CPI) and in-app conversion rates "
            "are primary metrics. ROAS is measured via in-app purchases or subscriptions. "
            "Retargeting lapsed users is often as important as new user acquisition."
        ),
        "show_fields": ["goal", "audience", "seasonal", "campaign_notes", "budget_notes"],
    },
    "local": {
        "label": "Local / Brick-and-Mortar",
        "sales_cycle": None,
        "implied": (
            "This is a local or brick-and-mortar business. Store visits, calls, and local "
            "awareness are primary goals. Geographic targeting is critical. "
            "Online ROAS may undercount in-store conversions."
        ),
        "show_fields": ["goal", "audience", "seasonal", "campaign_notes", "budget_notes"],
    },
    "other": {
        "label": "Other",
        "sales_cycle": "unknown",
        "implied": "",
        "show_fields": ["goal", "audience", "sales_cycle_weeks", "seasonal", "campaign_notes", "budget_notes"],
    },
}


USER_TYPE_LABELS = {
    "in_house":  "In-house marketer",
    "agency":    "Agency",
    "freelancer": "Freelancer",
}

USER_TYPE_INSTRUCTIONS = {
    "in_house":   "The user is an in-house marketer. Explain the reasoning behind recommendations clearly — they may not have deep paid media expertise.",
    "agency":     "The user is an agency. Write sharp, directive recommendations with no hand-holding. Skip theory, go straight to actions and numbers.",
    "freelancer": "The user is a freelancer. Prioritise the highest-impact recommendations tightly — they have limited bandwidth.",
}

MATURITY_LABELS = {
    "new":     "New account (0-6 months)",
    "growing": "Growing account (6-18 months)",
    "mature":  "Mature account (18+ months)",
}

MATURITY_INSTRUCTIONS = {
    "new":     "This is a new account. Focus on foundational fixes and learning phase campaigns. Do not suggest advanced bid strategies or audience layering.",
    "growing": "This account is in a growth phase. Balance foundational health with optimisation. Flag structural issues alongside performance improvements.",
    "mature":  "This is a mature account. Skip basic setup issues. Focus on marginal optimisation, budget efficiency, and strategic reallocation.",
}


def format_client_context(context: dict) -> str:
    """
    Convert structured client context dict into a formatted briefing block
    for injection into Claude prompts.

    Returns empty string if no context provided.
    """
    if not context:
        return ""

    business_type = context.get("business_type", "other")
    profile = BUSINESS_TYPE_PROFILES.get(business_type, BUSINESS_TYPE_PROFILES["other"])

    lines = ["=== CLIENT BRIEFING ==="]

    # Business type + implied assumptions
    lines.append(f"Business type: {profile['label']}")
    if profile["implied"]:
        lines.append(f"Implied context: {profile['implied']}")

    # Account maturity
    maturity = context.get("account_maturity", "").strip()
    if maturity and maturity in MATURITY_LABELS:
        lines.append(f"Account maturity: {MATURITY_LABELS[maturity]}")
        lines.append(f"Maturity guidance: {MATURITY_INSTRUCTIONS[maturity]}")

    # User type
    user_type = context.get("user_type", "").strip()
    if user_type and user_type in USER_TYPE_LABELS:
        lines.append(f"Report recipient: {USER_TYPE_LABELS[user_type]}")
        lines.append(f"Tone guidance: {USER_TYPE_INSTRUCTIONS[user_type]}")

    # Monthly budget
    monthly_budget = context.get("monthly_budget_total", "").strip() if context.get("monthly_budget_total") else ""
    if monthly_budget:
        budget_line = f"Monthly budget: {monthly_budget}"
        google_budget = (context.get("monthly_budget_google") or "").strip()
        meta_budget   = (context.get("monthly_budget_meta") or "").strip()
        if google_budget or meta_budget:
            splits = []
            if google_budget: splits.append(f"Google {google_budget}")
            if meta_budget:   splits.append(f"Meta {meta_budget}")
            budget_line += f" ({', '.join(splits)})"
        lines.append(budget_line)

    # Primary goal
    goal = context.get("goal", "").strip()
    if goal:
        lines.append(f"Primary goal: {goal}")

    # Target audience
    audience = context.get("audience", "").strip()
    if audience:
        lines.append(f"Target audience: {audience}")

    # Sales cycle - only for business types where it applies
    if profile["sales_cycle"] is not None and context.get("sales_cycle_weeks"):
        lines.append(f"Sales cycle: {context['sales_cycle_weeks']} weeks")

    # Average order value (ecommerce)
    aov = context.get("avg_order_value", "").strip()
    if aov:
        lines.append(f"Average order value: {aov}")

    # Seasonal context
    seasonal = context.get("seasonal", "").strip()
    if seasonal:
        lines.append(f"Seasonal context: {seasonal}")

    # Campaign-level notes
    campaign_notes = context.get("campaign_notes", "").strip()
    if campaign_notes:
        lines.append(f"Campaign notes (critical - use these to interpret individual campaigns):\n{campaign_notes}")

    # Budget constraints
    budget_notes = context.get("budget_notes", "").strip()
    if budget_notes:
        lines.append(f"Budget constraints: {budget_notes}")

    # Campaign type tags
    tags = context.get("campaign_tags", {})
    if tags:
        tag_lines = [f"  - {name}: [{tag}]" for name, tag in tags.items() if tag]
        if tag_lines:
            lines.append("Campaign type tags (respect these when evaluating performance):\n" + "\n".join(tag_lines))

    # Campaign targets
    targets = context.get("campaign_targets", [])
    if targets:
        target_lines = []
        for t in targets:
            parts = [f"  - {t.get('name', '?')}:"]
            if t.get("target_roas"):
                parts.append(f"target ROAS {t['target_roas']}x")
            if t.get("target_cpl"):
                parts.append(f"target CPL {t['target_cpl']}")
            if t.get("target_cpa"):
                parts.append(f"target CPA {t['target_cpa']}")
            target_lines.append(" ".join(parts))
        if target_lines:
            lines.append("Per-campaign performance targets (flag if missing these goals):\n" + "\n".join(target_lines))

    # Attribution model
    attribution = context.get("attribution_model", "").strip()
    if attribution and attribution != "unknown":
        desc = ATTRIBUTION_MODEL_DESCRIPTIONS.get(attribution, "")
        lines.append(f"Attribution model: {attribution}" + (f"\nAttribution note: {desc}" if desc else ""))

    # Benchmark ranges for the business type
    benchmarks = BENCHMARK_RANGES.get(business_type)
    if benchmarks:
        blines = [
            f"Industry benchmarks for {benchmarks['label']} (target direction — compare against account history below):",
            "  Use these as the goal to close toward, not as a pass/fail threshold.",
        ]
        if benchmarks.get("google_roas"):
            r = benchmarks["google_roas"]
            blines.append(f"  Google ROAS target range: low {r['low']}x | median {r['median']}x | high {r['high']}x")
        if benchmarks.get("meta_roas"):
            r = benchmarks["meta_roas"]
            blines.append(f"  Meta ROAS target range: low {r['low']}x | median {r['median']}x | high {r['high']}x")
        if benchmarks.get("google_cpl"):
            r = benchmarks["google_cpl"]
            blines.append(f"  Google CPL target range: low {r['low']} | median {r['median']} | high {r['high']}")
        if benchmarks.get("meta_cpl"):
            r = benchmarks["meta_cpl"]
            blines.append(f"  Meta CPL target range: low {r['low']} | median {r['median']} | high {r['high']}")
        if benchmarks.get("google_cpi"):
            r = benchmarks["google_cpi"]
            blines.append(f"  Google CPI target range: low {r['low']} | median {r['median']} | high {r['high']}")
        if benchmarks.get("meta_cpi"):
            r = benchmarks["meta_cpi"]
            blines.append(f"  Meta CPI target range: low {r['low']} | median {r['median']} | high {r['high']}")
        if benchmarks.get("note"):
            blines.append(f"  Note: {benchmarks['note']}")
        lines.append("\n".join(blines))

    # Competitive context
    competitive = context.get("competitive_context", "").strip()
    if competitive:
        lines.append(f"Competitive context: {competitive}")

    # Free-form additional context
    extra = context.get("extra", "").strip()
    if extra:
        lines.append(f"Additional context: {extra}")

    if len(lines) <= 2:
        return ""  # Only header + type, nothing meaningful

    lines.append("=== END BRIEFING ===")
    return "\n".join(lines)


def parse_context_from_json(raw: str) -> dict:
    """Parse context JSON string from DB. Returns empty dict on failure."""
    import json
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        # Legacy: if it's a plain string (old format), wrap it as extra
        return {"extra": raw, "business_type": "other"}
