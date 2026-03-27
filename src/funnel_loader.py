"""
Funnel loader - adaptively loads MQL/SQL/Customer data from any CSV structure.

Supports three join levels:
  - campaign: has a campaign/campaign name column - joined per campaign
  - aggregate: totals only (no campaign column) - distributed proportionally across all campaigns
  - date: has a date/period column - matched by period label

Auto-detects which columns are present and calculates what it can.
Missing stages are marked as None (pending) rather than failing.
"""

import pandas as pd
from pathlib import Path
from typing import Optional
from src.cleaner import clean_funnel, format_cleaning_report


# Possible column name variants for each funnel stage
STAGE_VARIANTS = {
    "leads":     ["leads", "lead", "total leads", "all leads", "form fills", "enquiries", "inquiries"],
    "mqls":      ["mql", "mqls", "marketing qualified leads", "marketing qualified", "qualified leads"],
    "sqls":      ["sql", "sqls", "sales qualified leads", "sales qualified", "opportunities"],
    "customers": ["customers", "customer", "closed", "closed won", "conversions", "new customers", "sales"],
}

# Possible join key column names
CAMPAIGN_KEY_VARIANTS = ["campaign", "campaign name", "campaign_name", "ad campaign", "name"]
DATE_KEY_VARIANTS = ["date", "period", "month", "week", "date range"]


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [col.strip().lower() for col in df.columns]
    return df


def _detect_column(df: pd.DataFrame, variants: list) -> Optional[str]:
    """Return first matching column name from variants list."""
    for v in variants:
        if v in df.columns:
            return v
    return None


def _detect_stage_columns(df: pd.DataFrame) -> dict:
    """Return dict of stage -> actual column name (or None if not present)."""
    return {
        stage: _detect_column(df, variants)
        for stage, variants in STAGE_VARIANTS.items()
    }


def _detect_join_level(df: pd.DataFrame) -> str:
    """Auto-detect whether funnel data is campaign-level, date-level, or aggregate."""
    if _detect_column(df, CAMPAIGN_KEY_VARIANTS):
        return "campaign"
    if _detect_column(df, DATE_KEY_VARIANTS):
        return "date"
    return "aggregate"


def _to_numeric_col(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(
        df[col].astype(str).str.replace(",", "").str.strip(),
        errors="coerce"
    ).fillna(0)


def load_funnel(filepath: str, return_report: bool = False, pre_rename: dict = None) -> dict:
    """
    Load funnel CSV and return a structured dict:
    {
        "join_level": "campaign" | "aggregate" | "date",
        "stages_available": ["leads", "mqls", ...],  # which stages have data
        "campaign_data": {campaign_name: {leads, mqls, sqls, customers}},  # if campaign-level
        "aggregate": {leads, mqls, sqls, customers},  # always present (totals)
    }
    pre_rename: optional dict {original_col_name: canonical_col_name} applied before fuzzy matching.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Funnel file not found: {filepath}")

    df, report_lines = clean_funnel(filepath, pre_rename=pre_rename)
    cleaning_report = format_cleaning_report(report_lines, "Funnel")

    join_level = _detect_join_level(df)
    stage_cols = _detect_stage_columns(df)
    stages_available = [s for s, col in stage_cols.items() if col is not None]

    if not stages_available:
        raise ValueError(
            f"Funnel CSV has no recognizable funnel columns.\n"
            f"Found columns: {list(df.columns)}\n"
            f"Expected at least one of: leads, mqls, sqls, customers (and variants)"
        )

    # Build per-campaign data if campaign-level
    campaign_data: dict = {}
    if join_level == "campaign":
        key_col = _detect_column(df, CAMPAIGN_KEY_VARIANTS)
        for _, row in df.iterrows():
            name = str(row[key_col]).strip()
            if not name or name.lower() in ("nan", "total", "grand total", ""):
                continue
            entry: dict = {}
            for stage, col in stage_cols.items():
                entry[stage] = float(_to_numeric_col(df, col)[_].item() if col else 0) if col else None
            # Re-read directly from row for accuracy
            for stage, col in stage_cols.items():
                if col and col in row.index:
                    try:
                        entry[stage] = float(str(row[col]).replace(",", "")) if str(row[col]).strip() not in ("", "nan") else None
                    except (ValueError, TypeError):
                        entry[stage] = None
            campaign_data[name] = entry

    # Build aggregates
    agg: dict = {}
    for stage, col in stage_cols.items():
        if col:
            agg[stage] = float(_to_numeric_col(df, col).sum())
        else:
            agg[stage] = None

    return {
        "join_level": join_level,
        "stages_available": stages_available,
        "campaign_data": campaign_data,
        "aggregate": agg,
        "cleaning_report": cleaning_report,
        "raw_df": df,
    }


def merge_funnel_into_campaigns(campaigns: list, funnel: dict, platform_spend_key: str = "spend") -> list:
    """
    Merge funnel data into campaign list from calculator.py.

    Handles:
    - campaign-level: join by campaign name (fuzzy match)
    - aggregate: distribute proportionally by spend
    """
    if not funnel:
        return campaigns

    join_level = funnel["join_level"]
    stages = funnel["stages_available"]
    result = []

    if join_level == "campaign":
        campaign_data = funnel["campaign_data"]
        # Build lookup with normalized keys for fuzzy matching
        lookup = {k.lower().strip(): v for k, v in campaign_data.items()}

        for c in campaigns:
            c = dict(c)
            name_key = c["name"].lower().strip()
            # Try exact match first, then partial
            funnel_row = lookup.get(name_key)
            if not funnel_row:
                for k, v in lookup.items():
                    if k in name_key or name_key in k:
                        funnel_row = v
                        break
            if funnel_row:
                c["funnel"] = {s: funnel_row.get(s) for s in ["leads", "mqls", "sqls", "customers"]}
            else:
                c["funnel"] = {s: None for s in ["leads", "mqls", "sqls", "customers"]}
            result.append(c)

    elif join_level == "aggregate":
        # Distribute aggregate totals proportionally by spend
        total_spend = sum(c[platform_spend_key] for c in campaigns if c[platform_spend_key])
        agg = funnel["aggregate"]
        for c in campaigns:
            c = dict(c)
            spend_share = safe_divide_f(c[platform_spend_key], total_spend)
            c["funnel"] = {
                s: round(agg[s] * spend_share) if agg.get(s) is not None else None
                for s in ["leads", "mqls", "sqls", "customers"]
            }
            result.append(c)

    else:
        # date-level: attach aggregate to all campaigns, mark as date-level
        agg = funnel["aggregate"]
        for c in campaigns:
            c = dict(c)
            c["funnel"] = {s: agg.get(s) for s in ["leads", "mqls", "sqls", "customers"]}
            result.append(c)

    return result


def safe_divide_f(a: float, b: float) -> float:
    return a / b if b else 0.0


def calculate_funnel_metrics(campaigns: list, spend_key: str = "spend") -> list:
    """
    Add cost_per_lead, cost_per_mql, cost_per_sql, cost_per_customer,
    mql_rate, sql_rate, close_rate to each campaign that has funnel data.
    """
    result = []
    for c in campaigns:
        c = dict(c)
        f = c.get("funnel", {})
        spend = c.get(spend_key, 0) or 0

        leads = f.get("leads")
        mqls = f.get("mqls")
        sqls = f.get("sqls")
        customers = f.get("customers")

        c["cost_per_lead"]     = round(safe_divide_f(spend, leads), 2)     if leads     else None
        c["cost_per_mql"]      = round(safe_divide_f(spend, mqls), 2)      if mqls      else None
        c["cost_per_sql"]      = round(safe_divide_f(spend, sqls), 2)      if sqls      else None
        c["cost_per_customer"] = round(safe_divide_f(spend, customers), 2) if customers else None

        c["mql_rate"]   = round(safe_divide_f(mqls or 0, leads or 0) * 100, 1)   if leads  else None
        c["sql_rate"]   = round(safe_divide_f(sqls or 0, mqls or 0) * 100, 1)    if mqls   else None
        c["close_rate"] = round(safe_divide_f(customers or 0, sqls or 0) * 100, 1) if sqls else None

        result.append(c)
    return result


def build_funnel_summary(campaigns: list) -> Optional[dict]:
    """
    Aggregate funnel metrics across all campaigns that have funnel data.
    Returns None if no funnel data present.
    """
    has_funnel = any(c.get("funnel") for c in campaigns)
    if not has_funnel:
        return None

    totals: dict = {"leads": 0, "mqls": 0, "sqls": 0, "customers": 0, "spend": 0}
    stage_counts: dict = {"leads": 0, "mqls": 0, "sqls": 0, "customers": 0}

    for c in campaigns:
        f = c.get("funnel", {})
        totals["spend"] += c.get("spend", 0) or 0
        for stage in ["leads", "mqls", "sqls", "customers"]:
            val = f.get(stage)
            if val is not None:
                totals[stage] += val
                stage_counts[stage] += 1

    # Only include stages that have at least some data
    available = [s for s in ["leads", "mqls", "sqls", "customers"] if stage_counts[s] > 0]

    summary: dict = {
        "stages_available": available,
        "total_spend": totals["spend"],
    }

    # Singular stage names for cost keys
    stage_singular = {"leads": "lead", "mqls": "mql", "sqls": "sql", "customers": "customer"}

    for stage in available:
        summary[f"total_{stage}"] = totals[stage]
        singular = stage_singular[stage]
        summary[f"cost_per_{singular}"] = round(
            safe_divide_f(totals["spend"], totals[stage]), 2
        ) if totals[stage] else None

    # Conversion rates
    if "leads" in available and "mqls" in available and totals["leads"]:
        summary["lead_to_mql_rate"] = round(safe_divide_f(totals["mqls"], totals["leads"]) * 100, 1)
    if "mqls" in available and "sqls" in available and totals["mqls"]:
        summary["mql_to_sql_rate"] = round(safe_divide_f(totals["sqls"], totals["mqls"]) * 100, 1)
    if "sqls" in available and "customers" in available and totals["sqls"]:
        summary["sql_to_customer_rate"] = round(safe_divide_f(totals["customers"], totals["sqls"]) * 100, 1)
    if "leads" in available and "customers" in available and totals["leads"]:
        summary["overall_conversion_rate"] = round(safe_divide_f(totals["customers"], totals["leads"]) * 100, 2)

    # Find worst funnel stage
    lead_to_mql_rate = summary.get("lead_to_mql_rate")
    mql_to_sql_rate = summary.get("mql_to_sql_rate")
    sql_to_customer_rate = summary.get("sql_to_customer_rate")
    worst_stage = None
    worst_rate = 100.0
    if lead_to_mql_rate is not None and lead_to_mql_rate < worst_rate:
        worst_rate = lead_to_mql_rate
        worst_stage = "lead_to_mql"
    if mql_to_sql_rate is not None and mql_to_sql_rate < worst_rate:
        worst_rate = mql_to_sql_rate
        worst_stage = "mql_to_sql"
    if sql_to_customer_rate is not None and sql_to_customer_rate < worst_rate:
        worst_rate = sql_to_customer_rate
        worst_stage = "sql_to_customer"
    summary["worst_funnel_stage"] = worst_stage
    summary["worst_funnel_rate"] = round(worst_rate, 1)

    return summary
