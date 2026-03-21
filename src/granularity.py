"""
Granularity detection and aggregation layer.

Auto-detects data level (campaign / ad_group / keyword / ad / placement)
from column names. Aggregates up to campaign level for main audit.
Keeps granular breakdowns for deeper report sections.

Supported levels (in order of granularity, finest to coarsest):
  keyword   - has Campaign + Ad Group + Keyword columns
  ad        - has Campaign + Ad Group + Ad Name / Ad / Ad ID columns
  ad_group  - has Campaign + Ad Group columns (but no keyword/ad)
  campaign  - only Campaign column (current default)
  placement - has Placement / Site / App columns (display/DV360)
  unknown   - cannot determine

Each level aggregates cleanly to campaign level via groupby + sum/mean rules.
"""

import pandas as pd
from typing import Optional


# Column name variants per granularity dimension
ADGROUP_VARIANTS = [
    "ad group", "adgroup", "ad group name", "ad_group", "ad group id",
    "ad set", "ad set name", "adset", "ad set id",  # Meta uses "Ad Set"
]

KEYWORD_VARIANTS = [
    "keyword", "keywords", "search term", "search query",
    "keyword text", "query", "keyword (broad match type)",
]

AD_VARIANTS = [
    "ad", "ad name", "ad title", "ad id", "creative", "creative name",
    "ad description", "headline", "ad copy", "ad variant",
]

PLACEMENT_VARIANTS = [
    "placement", "site", "app", "display placement", "managed placement",
    "where ads showed", "domain",
]

DATE_VARIANTS = [
    "date", "day", "week", "month", "period", "report date",
]

# Columns to SUM when aggregating up
SUM_COLS = [
    "impressions", "clicks", "cost", "conversions", "conversion value",
    "amount spent", "results", "spend", "revenue", "wasted",
    "leads", "mqls", "sqls", "customers",
]

# Columns to compute as weighted average (weight = cost/spend)
WAVG_COLS = ["roas", "ctr", "cpc", "cac", "purchase roas"]

# Columns to take MAX (quality score, etc.)
MAX_COLS = ["quality score"]


def detect_level(df: pd.DataFrame) -> str:
    """
    Detect the granularity level of a DataFrame.
    Returns one of: 'keyword', 'ad', 'ad_group', 'campaign', 'placement', 'unknown'
    """
    cols = set(df.columns)

    def _has(variants):
        return any(v in cols for v in variants)

    if _has(KEYWORD_VARIANTS):
        return "keyword"
    if _has(AD_VARIANTS) and _has(ADGROUP_VARIANTS):
        return "ad"
    if _has(ADGROUP_VARIANTS):
        return "ad_group"
    if _has(PLACEMENT_VARIANTS):
        return "placement"

    # Check if there's a date column making it a time-series at campaign level
    campaign_cols = ["campaign", "campaign name"]
    if any(c in cols for c in campaign_cols):
        return "campaign"

    return "unknown"


def _find_col(df: pd.DataFrame, variants: list) -> Optional[str]:
    for v in variants:
        if v in df.columns:
            return v
    return None


def _safe_wavg(group: pd.DataFrame, value_col: str, weight_col: str) -> float:
    """Weighted average, falls back to simple mean if weight col missing."""
    if weight_col not in group.columns or group[weight_col].sum() == 0:
        return group[value_col].mean() if value_col in group.columns else 0.0
    w = group[weight_col]
    v = group[value_col]
    return (v * w).sum() / w.sum()


def _aggregate_to_campaign(df: pd.DataFrame,
                            campaign_col: str,
                            spend_col: str) -> pd.DataFrame:
    """
    Group by campaign, sum numeric cols, weighted-avg rate cols.
    Returns campaign-level DataFrame.
    """
    present_sum  = [c for c in SUM_COLS  if c in df.columns]
    present_wavg = [c for c in WAVG_COLS if c in df.columns]
    present_max  = [c for c in MAX_COLS  if c in df.columns]

    agg_dict = {}
    for c in present_sum:
        agg_dict[c] = "sum"
    for c in present_max:
        agg_dict[c] = "max"

    if not agg_dict:
        return df

    grouped = df.groupby(campaign_col, as_index=False).agg(agg_dict)

    # Add weighted averages back
    for wcol in present_wavg:
        wavg_vals = {}
        for camp, group in df.groupby(campaign_col):
            wavg_vals[camp] = _safe_wavg(group, wcol, spend_col)
        grouped[wcol] = grouped[campaign_col].map(wavg_vals)

    return grouped


def process(df: pd.DataFrame,
            platform: str = "google") -> dict:
    """
    Main entry point. Auto-detect level, aggregate to campaign,
    return structured result with all granularity levels preserved.

    Returns:
    {
        "level":          "keyword" | "ad" | "ad_group" | "campaign" | ...,
        "campaign_df":    pd.DataFrame,   # aggregated to campaign level
        "adgroup_df":     pd.DataFrame | None,
        "keyword_df":     pd.DataFrame | None,
        "ad_df":          pd.DataFrame | None,
        "placement_df":   pd.DataFrame | None,
        "campaign_col":   str,            # name of the campaign column
        "adgroup_col":    str | None,
        "keyword_col":    str | None,
        "ad_col":         str | None,
        "spend_col":      str,
        "granularity_note": str,          # human-readable summary
    }
    """
    level = detect_level(df)

    # Detect column names
    campaign_col  = _find_col(df, ["campaign", "campaign name", "campaign_name"])
    adgroup_col   = _find_col(df, ADGROUP_VARIANTS)
    keyword_col   = _find_col(df, KEYWORD_VARIANTS)
    ad_col        = _find_col(df, AD_VARIANTS)
    placement_col = _find_col(df, PLACEMENT_VARIANTS)
    spend_col     = _find_col(df, ["cost", "amount spent", "spend"])

    if not campaign_col or not spend_col:
        return {
            "level": "unknown",
            "campaign_df": df,
            "adgroup_df": None,
            "keyword_df": None,
            "ad_df": None,
            "placement_df": None,
            "campaign_col": campaign_col or "campaign",
            "adgroup_col": None,
            "keyword_col": None,
            "ad_col": None,
            "spend_col": spend_col or "cost",
            "granularity_note": "Could not determine data level",
        }

    result = {
        "level":          level,
        "campaign_df":    None,
        "adgroup_df":     None,
        "keyword_df":     None,
        "ad_df":          None,
        "placement_df":   None,
        "campaign_col":   campaign_col,
        "adgroup_col":    adgroup_col,
        "keyword_col":    keyword_col,
        "ad_col":         ad_col,
        "spend_col":      spend_col,
        "granularity_note": "",
    }

    if level == "keyword":
        result["keyword_df"]  = df.copy()
        result["adgroup_df"]  = _aggregate_to_campaign(df, adgroup_col, spend_col) if adgroup_col else None
        result["campaign_df"] = _aggregate_to_campaign(df, campaign_col, spend_col)
        result["granularity_note"] = (
            f"Keyword-level data detected ({len(df)} keywords across "
            f"{df[campaign_col].nunique()} campaigns, "
            f"{df[adgroup_col].nunique() if adgroup_col else '?'} ad groups). "
            f"Aggregated to campaign level for main audit."
        )

    elif level == "ad":
        result["ad_df"]       = df.copy()
        result["adgroup_df"]  = _aggregate_to_campaign(df, adgroup_col, spend_col) if adgroup_col else None
        result["campaign_df"] = _aggregate_to_campaign(df, campaign_col, spend_col)
        result["granularity_note"] = (
            f"Ad-level data detected ({len(df)} ads across "
            f"{df[campaign_col].nunique()} campaigns). "
            f"Aggregated to campaign level for main audit."
        )

    elif level == "ad_group":
        result["adgroup_df"]  = df.copy()
        result["campaign_df"] = _aggregate_to_campaign(df, campaign_col, spend_col)
        result["granularity_note"] = (
            f"Ad group level data detected ({len(df)} ad groups across "
            f"{df[campaign_col].nunique()} campaigns). "
            f"Aggregated to campaign level for main audit."
        )

    elif level == "placement":
        result["placement_df"] = df.copy()
        result["campaign_df"]  = _aggregate_to_campaign(df, campaign_col, spend_col)
        result["granularity_note"] = (
            f"Placement-level data detected ({len(df)} placements). "
            f"Aggregated to campaign level for main audit."
        )

    else:
        result["campaign_df"] = df.copy()
        result["granularity_note"] = (
            f"Campaign-level data ({len(df)} campaigns)."
        )

    return result


def build_granular_insights(gran: dict, spend_col: str = "cost") -> dict:
    """
    From a processed granularity result, compute insights at each available level.
    Returns a dict of insight lists ready for Claude and the report template.
    """
    insights: dict = {
        "level": gran["level"],
        "note":  gran["granularity_note"],
        "adgroup_insights":   [],
        "keyword_insights":   [],
        "ad_insights":        [],
        "placement_insights": [],
    }

    # Ad group insights
    df = gran.get("adgroup_df")
    if df is not None and gran.get("adgroup_col") and gran["level"] in ("keyword", "ad", "ad_group"):
        adg_col = gran["adgroup_col"]
        sc = gran["spend_col"]
        rows = []
        for _, row in df.iterrows():
            sp = float(row.get(sc, 0))
            conv = float(row.get("conversions", row.get("results", 0)) or 0)
            rev  = float(row.get("conversion value", row.get("purchase roas", 0) * sp) or 0)
            roas = round(rev / sp, 2) if sp else float(row.get("roas", row.get("purchase roas", 0)) or 0)
            cac  = round(sp / conv, 2) if conv else 0
            rows.append({
                "adgroup":    str(row.get(adg_col, "")),
                "campaign":   str(row.get(gran["campaign_col"], "")),
                "spend":      round(sp, 2),
                "roas":       roas,
                "cac":        cac,
                "ctr":        round(float(row.get("ctr", 0) or 0), 2),
                "conversions": int(conv),
                "severity":   "critical" if roas < 1 else "warning" if roas < 2 else "ok",
            })
        insights["adgroup_insights"] = sorted(rows, key=lambda x: x["roas"])

    # Keyword insights
    df = gran.get("keyword_df")
    if df is not None and gran.get("keyword_col"):
        kw_col = gran["keyword_col"]
        sc = gran["spend_col"]
        rows = []
        for _, row in df.iterrows():
            sp   = float(row.get(sc, 0) or 0)
            conv = float(row.get("conversions", 0) or 0)
            rev  = float(row.get("conversion value", 0) or 0)
            roas = round(rev / sp, 2) if sp else 0
            cpc  = round(sp / float(row.get("clicks", 1) or 1), 2)
            qs   = int(row.get("quality score", 0) or 0)
            rows.append({
                "keyword":    str(row.get(kw_col, "")),
                "match_type": str(row.get("match type", "")),
                "adgroup":    str(row.get(gran.get("adgroup_col", ""), "")),
                "campaign":   str(row.get(gran["campaign_col"], "")),
                "spend":      round(sp, 2),
                "roas":       roas,
                "cpc":        cpc,
                "ctr":        round(float(row.get("ctr", 0) or 0), 2),
                "conversions": int(conv),
                "quality_score": qs,
                "severity":   "critical" if roas < 1 else "warning" if roas < 2 else "ok",
            })
        # Sort by spend descending, show waste at top
        insights["keyword_insights"] = sorted(rows, key=lambda x: (-x["spend"], x["roas"]))

    # Ad insights
    df = gran.get("ad_df")
    if df is not None and gran.get("ad_col"):
        ad_col = gran["ad_col"]
        sc = gran["spend_col"]
        rows = []
        for _, row in df.iterrows():
            sp   = float(row.get(sc, 0) or 0)
            conv = float(row.get("conversions", 0) or 0)
            rev  = float(row.get("conversion value", 0) or 0)
            roas = round(rev / sp, 2) if sp else 0
            rows.append({
                "ad":         str(row.get(ad_col, "")),
                "ad_type":    str(row.get("ad type", "")),
                "adgroup":    str(row.get(gran.get("adgroup_col", ""), "")),
                "campaign":   str(row.get(gran["campaign_col"], "")),
                "spend":      round(sp, 2),
                "roas":       roas,
                "ctr":        round(float(row.get("ctr", 0) or 0), 2),
                "conversions": int(conv),
                "severity":   "critical" if roas < 1 else "warning" if roas < 2 else "ok",
            })
        insights["ad_insights"] = sorted(rows, key=lambda x: x["roas"])

    return insights


def granularity_to_claude_context(insights: dict) -> str:
    """
    Summarize granular insights as compact text for Claude's system prompt.
    Keeps token count low while giving Claude the key patterns.
    """
    parts = [f"Data level: {insights['level']}. {insights['note']}"]

    if insights.get("keyword_insights"):
        kws = insights["keyword_insights"]
        worst = [k for k in kws if k["severity"] == "critical"][:3]
        best  = sorted(kws, key=lambda x: -x["roas"])[:3]
        parts.append(f"\nTop wasted keywords ({len(worst)} critical): " +
                     ", ".join(f"{k['keyword']} (ROAS {k['roas']}x, Rs {k['spend']:,.0f})" for k in worst))
        parts.append(f"Top performing keywords: " +
                     ", ".join(f"{k['keyword']} (ROAS {k['roas']}x)" for k in best))

    if insights.get("adgroup_insights"):
        ags = insights["adgroup_insights"]
        worst = [a for a in ags if a["severity"] == "critical"][:3]
        if worst:
            parts.append(f"\nUnderperforming ad groups: " +
                         ", ".join(f"{a['adgroup']} in {a['campaign']} (ROAS {a['roas']}x)" for a in worst))

    if insights.get("ad_insights"):
        ads = insights["ad_insights"]
        worst = [a for a in ads if a["severity"] == "critical"][:3]
        best  = sorted(ads, key=lambda x: -x["roas"])[:3]
        if worst:
            parts.append(f"\nLow-performing ads: " +
                         ", ".join(f"{a['ad']} (ROAS {a['roas']}x)" for a in worst))
        if best:
            parts.append(f"Top-performing ads: " +
                         ", ".join(f"{a['ad']} (ROAS {a['roas']}x)" for a in best))

    return "\n".join(parts)
