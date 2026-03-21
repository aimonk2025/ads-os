"""
Client business context - structured fields that feed into all Claude prompts.
"""

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
