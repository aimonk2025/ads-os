import pandas as pd
from typing import Optional
from src.funnel_loader import (
    merge_funnel_into_campaigns,
    calculate_funnel_metrics,
    build_funnel_summary,
)


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    if b == 0:
        return default
    return a / b


def assign_severity(roas: float) -> str:
    if roas < 1.0:
        return "critical"
    elif roas < 2.0:
        return "warning"
    return "ok"


def calculate_delta(current: float, previous: float) -> dict:
    if previous == 0:
        return {"value": current, "pct": 0.0, "direction": "flat"}
    diff = current - previous
    pct = round((diff / previous) * 100, 1)
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    return {"value": round(diff, 2), "pct": abs(pct), "direction": direction}


def calculate_google_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["roas"] = df.apply(lambda r: safe_divide(r["conversion value"], r["cost"]), axis=1)
    df["cac"] = df.apply(lambda r: safe_divide(r["cost"], r["conversions"]), axis=1)
    df["ctr"] = df.apply(lambda r: safe_divide(r["clicks"], r["impressions"]) * 100, axis=1)
    df["cpc"] = df.apply(lambda r: safe_divide(r["cost"], r["clicks"]), axis=1)
    df["efficiency"] = df.apply(lambda r: safe_divide(r["conversions"], r["cost"]) * 1000, axis=1)
    df["severity"] = df["roas"].apply(assign_severity)
    # critical = fully wasted, warning = partially wasted (50% of spend attributed to underperformance)
    df["wasted"] = df.apply(
        lambda r: r["cost"] if r["severity"] == "critical" else (r["cost"] * 0.5 if r["severity"] == "warning" else 0.0),
        axis=1,
    )
    return df


def calculate_meta_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Meta gives purchase roas directly
    df["roas"] = df["purchase roas"]
    df["cac"] = df.apply(lambda r: safe_divide(r["amount spent"], r["results"]), axis=1)
    df["ctr"] = df.apply(lambda r: safe_divide(r["clicks"], r["impressions"]) * 100, axis=1)
    df["cpc"] = df.apply(lambda r: safe_divide(r["amount spent"], r["clicks"]), axis=1)
    df["efficiency"] = df.apply(lambda r: safe_divide(r["results"], r["amount spent"]) * 1000, axis=1)
    df["severity"] = df["roas"].apply(assign_severity)
    df["wasted"] = df.apply(
        lambda r: r["amount spent"] if r["severity"] == "critical" else (r["amount spent"] * 0.5 if r["severity"] == "warning" else 0.0),
        axis=1,
    )
    return df


def _campaigns_to_list(df: pd.DataFrame, name_col: str, spend_col: str, revenue_col: Optional[str]) -> list:
    rows = []
    for _, row in df.iterrows():
        reach     = float(row["reach"]) if "reach" in df.columns and not pd.isna(row.get("reach", float("nan"))) else None
        frequency = float(row["frequency"]) if "frequency" in df.columns and not pd.isna(row.get("frequency", float("nan"))) else None
        rows.append({
            "name": str(row[name_col]),
            "spend": float(row[spend_col]),
            "revenue": float(row[revenue_col]) if revenue_col else None,
            "roas": round(float(row["roas"]), 2),
            "cac": round(float(row["cac"]), 2),
            "ctr": round(float(row["ctr"]), 2),
            "cpc": round(float(row["cpc"]), 2),
            "efficiency": round(float(row["efficiency"]), 3),
            "conversions": int(row.get("conversions", row.get("results", 0))),
            "severity": row["severity"],
            "wasted": float(row["wasted"]),
            "reach": reach,
            "frequency": round(frequency, 2) if frequency else None,
        })
    return sorted(rows, key=lambda x: x["roas"])


def _platform_summary(df: pd.DataFrame, spend_col: str, revenue_col: Optional[str],
                       conversion_col: str, name_col: str) -> dict:
    total_spend = float(df[spend_col].sum())
    total_revenue = float(df[revenue_col].sum()) if revenue_col else None
    total_conversions = float(df[conversion_col].sum())
    overall_roas = round(safe_divide(total_revenue or 0, total_spend), 2) if revenue_col else round(float(df["roas"].mean()), 2)
    wasted = float(df["wasted"].sum())
    campaigns = _campaigns_to_list(df, name_col, spend_col, revenue_col)
    critical = [c for c in campaigns if c["severity"] == "critical"]
    warning = [c for c in campaigns if c["severity"] == "warning"]
    ok = [c for c in campaigns if c["severity"] == "ok"]
    return {
        "total_spend": round(total_spend, 2),
        "total_revenue": round(total_revenue, 2) if total_revenue else None,
        "overall_roas": overall_roas,
        "total_conversions": round(total_conversions, 0),
        "overall_cac": round(safe_divide(total_spend, total_conversions), 2),
        "overall_ctr": round(float(df["ctr"].mean()), 2),
        "wasted_spend": round(wasted, 2),
        "campaign_count": len(campaigns),
        "critical_count": len(critical),
        "warning_count": len(warning),
        "ok_count": len(ok),
        "campaigns": campaigns,
    }


def build_summary(
    google_df: Optional[pd.DataFrame] = None,
    meta_df: Optional[pd.DataFrame] = None,
    compare_mode: bool = False,
    prev_google_df: Optional[pd.DataFrame] = None,
    prev_meta_df: Optional[pd.DataFrame] = None,
    funnel: Optional[dict] = None,
) -> dict:
    from datetime import datetime

    result: dict = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": [],
        "compare_mode": compare_mode,
        "google": None,
        "meta": None,
        "cross_platform": None,
        "comparison": None,
    }

    if google_df is not None:
        result["platforms"].append("google")
        result["google"] = _platform_summary(
            google_df, "cost", "conversion value", "conversions", "campaign"
        )

    if meta_df is not None:
        result["platforms"].append("meta")
        meta_rev_col = "purchase value" if "purchase value" in meta_df.columns else None
        result["meta"] = _platform_summary(
            meta_df, "amount spent", meta_rev_col, "results", "campaign name"
        )

    # Funnel enrichment
    if funnel:
        for platform, spend_key in [("google", "spend"), ("meta", "spend")]:
            pdata = result.get(platform)
            if not pdata:
                continue
            campaigns = pdata["campaigns"]
            campaigns = merge_funnel_into_campaigns(campaigns, funnel, spend_key)
            campaigns = calculate_funnel_metrics(campaigns, spend_key)
            pdata["campaigns"] = campaigns
            pdata["funnel_summary"] = build_funnel_summary(campaigns)

        # Combined funnel summary across both platforms
        all_campaigns = []
        for platform in ["google", "meta"]:
            pdata = result.get(platform)
            if pdata:
                all_campaigns.extend(pdata["campaigns"])
        result["funnel_summary"] = build_funnel_summary(all_campaigns)
    else:
        result["funnel_summary"] = None

    # Cross-platform comparison
    if google_df is not None and meta_df is not None:
        g = result["google"]
        m = result["meta"]
        better = "google" if g["overall_roas"] >= m["overall_roas"] else "meta"
        result["cross_platform"] = {
            "google_spend": g["total_spend"],
            "meta_spend": m["total_spend"],
            "google_roas": g["overall_roas"],
            "meta_roas": m["overall_roas"],
            "google_cac": g["overall_cac"],
            "meta_cac": m["overall_cac"],
            "google_wasted": g["wasted_spend"],
            "meta_wasted": m["wasted_spend"],
            "better_platform": better,
            "total_spend": round(g["total_spend"] + m["total_spend"], 2),
            "total_wasted": round(g["wasted_spend"] + m["wasted_spend"], 2),
        }

    # Period comparison
    if compare_mode:
        comparison: dict = {"period_label": "Current vs Previous Period"}
        if google_df is not None and prev_google_df is not None:
            prev_google_df = calculate_google_metrics(prev_google_df)
            prev_g = _platform_summary(prev_google_df, "cost", "conversion value", "conversions", "campaign")
            g = result["google"]
            comparison["google_spend_delta"] = calculate_delta(g["total_spend"], prev_g["total_spend"])
            comparison["google_roas_delta"] = calculate_delta(g["overall_roas"], prev_g["overall_roas"])
            comparison["google_cac_delta"] = calculate_delta(g["overall_cac"], prev_g["overall_cac"])
            comparison["google_conversions_delta"] = calculate_delta(g["total_conversions"], prev_g["total_conversions"])
        if meta_df is not None and prev_meta_df is not None:
            prev_meta_df = calculate_meta_metrics(prev_meta_df)
            prev_meta_rev_col = "purchase value" if "purchase value" in prev_meta_df.columns else None
            prev_m = _platform_summary(prev_meta_df, "amount spent", prev_meta_rev_col, "results", "campaign name")
            m = result["meta"]
            comparison["meta_spend_delta"] = calculate_delta(m["total_spend"], prev_m["total_spend"])
            comparison["meta_roas_delta"] = calculate_delta(m["overall_roas"], prev_m["overall_roas"])
            comparison["meta_cac_delta"] = calculate_delta(m["overall_cac"], prev_m["overall_cac"])
            comparison["meta_conversions_delta"] = calculate_delta(m["total_conversions"], prev_m["total_conversions"])
        result["comparison"] = comparison

    return result
