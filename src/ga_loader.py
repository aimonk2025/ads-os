"""
GA4 CSV Loader.

GA4 exports have metadata rows at the top before the actual data:
  Row 1: # Google Analytics 4 Property: ...
  Row 2: (blank or date range)
  Row 3: (blank)
  Row 4+: actual headers + data

We skip all rows starting with '#' and blank rows until we hit the header row.

Canonical columns we look for (GA4 uses verbose names):
  - session_source_medium / session_campaign_name  -> join key
  - sessions
  - engaged_sessions
  - bounce_rate
  - conversions (or key_events)
  - total_revenue (or purchase_revenue)
  - avg_session_duration
"""

import re
import io
import chardet
import pandas as pd
from pathlib import Path


# Fuzzy column name map: lowercase normalized -> canonical key
_COL_MAP = {
    # Campaign dimension (join key)
    "session campaign name": "campaign",
    "session campaign": "campaign",
    "campaign": "campaign",
    "campaign name": "campaign",
    "utm campaign": "campaign",
    "utm_campaign": "campaign",
    # Source/medium (fallback join key)
    "session source / medium": "source_medium",
    "session source/medium": "source_medium",
    "source / medium": "source_medium",
    "source/medium": "source_medium",
    "session default channel group": "channel_group",
    "default channel grouping": "channel_group",
    "channel grouping": "channel_group",
    # Traffic metrics
    "sessions": "sessions",
    "users": "users",
    "total users": "users",
    "new users": "new_users",
    "engaged sessions": "engaged_sessions",
    "engagement rate": "engagement_rate",
    "bounce rate": "bounce_rate",
    "average session duration": "avg_session_duration",
    "avg. session duration": "avg_session_duration",
    "pages / session": "pages_per_session",
    "pages per session": "pages_per_session",
    "screen page views per session": "pages_per_session",
    # Conversion metrics
    "conversions": "conversions",
    "key events": "conversions",
    "goal completions": "conversions",
    "transactions": "transactions",
    "purchases": "transactions",
    "purchase revenue": "revenue",
    "total revenue": "revenue",
    "revenue": "revenue",
    "ecommerce revenue": "revenue",
}


def _normalize_col(col: str) -> str:
    return re.sub(r"\s+", " ", col.strip().lower())


def _detect_encoding(filepath: str) -> str:
    with open(filepath, "rb") as f:
        raw = f.read(32768)
    result = chardet.detect(raw)
    return result.get("encoding") or "utf-8"


def _find_data_start(lines: list) -> int:
    """Return index of the header row (first non-comment, non-blank line)."""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return i
    return 0


def load_ga4(filepath: str, pre_rename: dict = None) -> dict:
    """
    Load a GA4 CSV export.

    Returns:
        {
          "df": pd.DataFrame with canonical columns,
          "join_key": "campaign" | "source_medium" | "channel_group",
          "cleaning_report": str,
          "has_campaign": bool,
        }
    Raises ValueError if file is unreadable or has no usable data.
    pre_rename: optional dict {original_col_name: canonical_col_name} applied before column mapping.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"GA4 file not found: {filepath}")

    enc = _detect_encoding(filepath)
    try:
        with open(filepath, "r", encoding=enc, errors="replace") as f:
            raw_lines = f.readlines()
    except Exception as e:
        raise ValueError(f"Cannot read GA4 file: {e}")

    report_lines = [f"GA4 CSV: detected encoding {enc}"]

    start = _find_data_start(raw_lines)
    if start > 0:
        report_lines.append(f"Skipped {start} metadata/comment rows at top")

    data_text = "".join(raw_lines[start:])
    try:
        df = pd.read_csv(io.StringIO(data_text))
    except Exception as e:
        raise ValueError(f"Cannot parse GA4 CSV: {e}")

    if df.empty:
        raise ValueError("GA4 CSV has no data rows.")

    # Strip trailing summary rows (GA4 adds a "Total" row at the bottom)
    if df.iloc[-1].astype(str).str.lower().str.contains("total").any():
        df = df.iloc[:-1]
        report_lines.append("Removed trailing 'Total' summary row")

    # Apply manual column mapping if provided (before built-in mapping)
    if pre_rename:
        df = df.rename(columns=pre_rename)
        report_lines.append(f"Applied manual column mapping: {len(pre_rename)} column(s) renamed")

    # Rename columns to canonical names
    rename_map = {}
    unrecognized = []
    for col in df.columns:
        norm = _normalize_col(col)
        canonical = _COL_MAP.get(norm)
        if canonical:
            rename_map[col] = canonical
        else:
            unrecognized.append(col)

    df = df.rename(columns=rename_map)

    if unrecognized:
        report_lines.append(f"Unrecognized columns (kept as-is): {', '.join(unrecognized)}")

    # Determine join key priority: campaign > source_medium > channel_group
    join_key = None
    for candidate in ["campaign", "source_medium", "channel_group"]:
        if candidate in df.columns:
            join_key = candidate
            break

    if not join_key:
        raise ValueError(
            "GA4 CSV must contain a campaign name, source/medium, or channel group column to join with ad data."
        )

    report_lines.append(f"Join key: '{join_key}'")

    # Clean numeric columns
    numeric_cols = ["sessions", "users", "new_users", "engaged_sessions",
                    "engagement_rate", "bounce_rate", "avg_session_duration",
                    "pages_per_session", "conversions", "transactions", "revenue"]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = (
                df[col].astype(str)
                .str.replace(r"[%,₹$€£\s]", "", regex=True)
                .str.replace(r"[^0-9.\-]", "", regex=True)
                .replace("", "0")
                .astype(float)
            )

    # Normalize any date columns
    from src.cleaner import normalize_date_column
    date_candidates = [c for c in df.columns if any(kw in c.lower() for kw in ["date", "day", "week", "month", "period"])]
    for col in date_candidates:
        df, fixed = normalize_date_column(df, col)
        if fixed:
            report_lines.append(f"Normalized {fixed} date(s) in '{col}' to YYYY-MM-DD")

    # Clean campaign name
    df[join_key] = df[join_key].astype(str).str.strip()

    rows_before = len(df)
    df = df[df[join_key].str.lower() != "nan"].copy()
    if len(df) < rows_before:
        report_lines.append(f"Dropped {rows_before - len(df)} rows with missing {join_key}")

    report_lines.append(f"Loaded {len(df)} GA4 rows with columns: {[c for c in df.columns if c in _COL_MAP.values()]}")

    return {
        "df": df,
        "join_key": join_key,
        "has_campaign": join_key == "campaign",
        "cleaning_report": "\n".join(report_lines),
    }


def merge_ga4_into_campaigns(campaigns: list, ga_result: dict) -> list:
    """
    Merge GA4 metrics into the campaigns list from calculator.py.
    Joins on campaign name using case-insensitive fuzzy match.
    Unmatched campaigns get None for all GA fields.
    """
    if not ga_result or ga_result["df"].empty:
        return campaigns

    df = ga_result["df"]
    join_key = ga_result["join_key"]

    # Build lookup: lowercase campaign name -> row dict
    ga_lookup = {}
    for _, row in df.iterrows():
        key = str(row[join_key]).lower().strip()
        ga_lookup[key] = row.to_dict()

    def _find_match(name: str) -> dict:
        name_lower = name.lower().strip()
        # Exact match first
        if name_lower in ga_lookup:
            return ga_lookup[name_lower]
        # Substring match only when the shorter string is at least 10 chars
        # (prevents "Brand" matching "Brand Performance" AND "Performance Brand")
        for k, v in ga_lookup.items():
            short, long = (k, name_lower) if len(k) <= len(name_lower) else (name_lower, k)
            if len(short) >= 10 and short in long:
                return v
        return {}

    enriched = []
    for c in campaigns:
        match = _find_match(c["name"])
        ga = {
            "sessions": int(match["sessions"]) if "sessions" in match else None,
            "engaged_sessions": int(match["engaged_sessions"]) if "engaged_sessions" in match else None,
            "bounce_rate": round(float(match["bounce_rate"]), 1) if "bounce_rate" in match else None,
            "avg_session_duration": round(float(match["avg_session_duration"]), 1) if "avg_session_duration" in match else None,
            "ga_conversions": int(match["conversions"]) if "conversions" in match else None,
            "ga_revenue": round(float(match["revenue"]), 2) if "revenue" in match else None,
            "pages_per_session": round(float(match["pages_per_session"]), 2) if "pages_per_session" in match else None,
        }
        # Cost per session
        spend = c.get("spend") or 0
        if ga["sessions"] and spend:
            ga["cost_per_session"] = round(spend / ga["sessions"], 2)
        else:
            ga["cost_per_session"] = None

        enriched.append({**c, "ga": ga})

    return enriched


def build_ga_summary(campaigns: list) -> dict:
    """Build aggregate GA4 metrics across all campaigns."""
    sessions = [c["ga"]["sessions"] for c in campaigns if c.get("ga") and c["ga"].get("sessions")]
    ga_convs = [c["ga"]["ga_conversions"] for c in campaigns if c.get("ga") and c["ga"].get("ga_conversions")]
    cps_vals = [c["ga"]["cost_per_session"] for c in campaigns if c.get("ga") and c["ga"].get("cost_per_session")]

    if not sessions:
        return {}

    # Bounce rate weighted by sessions to avoid small-campaign bias
    weighted_bounce_num = 0.0
    weighted_bounce_den = 0.0
    for c in campaigns:
        ga = c.get("ga") or {}
        br = ga.get("bounce_rate")
        sess = ga.get("sessions") or 0
        if br is not None and sess:
            weighted_bounce_num += br * sess
            weighted_bounce_den += sess
    avg_bounce = round(weighted_bounce_num / weighted_bounce_den, 1) if weighted_bounce_den else None

    return {
        "total_sessions": sum(sessions),
        "avg_bounce_rate": avg_bounce,
        "total_ga_conversions": sum(ga_convs) if ga_convs else None,
        "avg_cost_per_session": round(sum(cps_vals) / len(cps_vals), 2) if cps_vals else None,
        "campaigns_matched": len(sessions),
    }
