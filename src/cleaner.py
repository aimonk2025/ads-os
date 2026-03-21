"""
Data cleaner - auto-fixes messy real-world CSV exports before validation.

Produces a cleaning report: every change made, so the user can see exactly
what was normalized.
"""

import re
import chardet
import pandas as pd
from pathlib import Path
from typing import Optional


# Fuzzy column mappings: canonical name -> list of known variants
GOOGLE_COLUMN_VARIANTS = {
    "campaign":         ["campaign", "campaign name", "campaign_name", "ad campaign", "name", "campaign title"],
    "impressions":      ["impressions", "impr.", "impr", "impression", "total impressions", "imps"],
    "clicks":           ["clicks", "click", "total clicks", "link clicks"],
    "cost":             ["cost", "spend", "cost (inr)", "amount spent", "total cost", "spend (inr)",
                         "cost (rs)", "spend (rs)", "cost/currency", "total spend", "charges"],
    "conversions":      ["conversions", "conversion", "conv.", "conv", "total conversions",
                         "all conv.", "all conversions", "results", "leads"],
    "conversion value": ["conversion value", "conv. value", "revenue", "total conversion value",
                         "conv. value (inr)", "value", "total value", "purchase value"],
}

META_COLUMN_VARIANTS = {
    "campaign name":  ["campaign name", "campaign", "campaign_name", "name", "ad campaign"],
    "impressions":    ["impressions", "impr.", "impr", "reach", "total impressions"],
    "clicks":         ["clicks", "link clicks", "total clicks", "click", "outbound clicks"],
    "amount spent":   ["amount spent", "spend", "cost", "amount spent (inr)", "amount spent (rs)",
                       "total amount spent", "charges", "spend (inr)"],
    "results":        ["results", "conversions", "leads", "purchases", "actions", "outcomes"],
    "purchase roas":  ["purchase roas", "roas", "website purchase roas", "return on ad spend",
                       "purchase return on ad spend", "ad set roas"],
}

FUNNEL_COLUMN_VARIANTS = {
    "campaign":   ["campaign", "campaign name", "campaign_name", "name", "source", "ad campaign"],
    "leads":      ["leads", "lead", "total leads", "all leads", "form fills", "enquiries",
                   "inquiries", "submissions", "form submissions"],
    "mqls":       ["mql", "mqls", "marketing qualified leads", "marketing qualified",
                   "qualified leads", "marketing ql"],
    "sqls":       ["sql", "sqls", "sales qualified leads", "sales qualified",
                   "opportunities", "sales ql", "sales accepted leads", "sal"],
    "customers":  ["customers", "customer", "closed", "closed won", "new customers",
                   "sales", "deals won", "won", "purchases", "paying customers"],
}

# Values that should be treated as zero
NULL_VALUES = {"--", "-", "n/a", "na", "null", "none", "", "0.00", "0,00"}

# Shorthand number expansions
SHORTHAND_PATTERN = re.compile(r"^([\d,]+\.?\d*)\s*([kKmMlLcCbB]r?)$")


def _detect_encoding(filepath: str) -> str:
    with open(filepath, "rb") as f:
        raw = f.read(32768)
    result = chardet.detect(raw)
    return result.get("encoding") or "utf-8"


def _read_csv_robust(filepath: str) -> tuple[pd.DataFrame, list]:
    """Read CSV handling encoding issues. Returns (df, notes)."""
    notes = []
    path = Path(filepath)

    # Try to detect encoding
    try:
        encoding = _detect_encoding(filepath)
        if encoding.lower() not in ("utf-8", "ascii"):
            notes.append(f"Detected encoding: {encoding} - converting to UTF-8")
    except Exception:
        encoding = "utf-8"

    # Try reading with detected encoding, fallback to latin-1
    for enc in [encoding, "utf-8-sig", "utf-16", "latin-1"]:
        try:
            df = pd.read_csv(filepath, encoding=enc, skip_blank_lines=True)
            if enc != "utf-8" and enc != encoding:
                notes.append(f"Fallback encoding used: {enc}")
            return df, notes
        except (UnicodeDecodeError, Exception):
            continue

    raise ValueError(f"Could not read file with any supported encoding: {path.name}")


def _expand_shorthand(val: str) -> Optional[float]:
    """Expand 1.2K -> 1200, 2.5L -> 250000, 1Cr -> 10000000 etc."""
    val = val.strip().replace(",", "")
    m = SHORTHAND_PATTERN.match(val)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2).lower()
    multipliers = {
        "k": 1_000,
        "m": 1_000_000,
        "l": 100_000,
        "cr": 10_000_000,
        "b": 1_000_000_000,
    }
    return num * multipliers.get(suffix, 1)


def _clean_numeric_value(val) -> float:
    """Convert a single cell value to float, handling all messy formats."""
    if pd.isna(val):
        return 0.0
    s = str(val).strip()
    # Null-like values
    if s.lower() in NULL_VALUES:
        return 0.0
    # Strip currency symbols and spaces
    s = re.sub(r"[₹$€£\s]", "", s)
    # Remove percentage sign
    s = s.replace("%", "")
    # Try shorthand
    expanded = _expand_shorthand(s)
    if expanded is not None:
        return expanded
    # Remove commas (thousands separator)
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_numeric_column(df: pd.DataFrame, col: str) -> tuple[pd.DataFrame, int]:
    """Clean a numeric column in-place. Returns (df, count_of_fixed_cells)."""
    original = df[col].copy()
    df[col] = df[col].apply(_clean_numeric_value)
    fixed = int((original.astype(str) != df[col].astype(str)).sum())
    return df, fixed


def _fuzzy_match_columns(df_cols: list, variants_map: dict) -> tuple[dict, list]:
    """
    Try to match actual DataFrame columns to canonical names using variants.
    Returns (remap_dict, notes).
    remap_dict: {actual_col -> canonical_col}
    """
    remap = {}
    notes = []
    df_cols_lower = {c.lower().strip(): c for c in df_cols}

    for canonical, variants in variants_map.items():
        # Already present as canonical
        if canonical in df_cols_lower:
            continue
        # Try each variant
        for variant in variants:
            v_lower = variant.lower().strip()
            if v_lower in df_cols_lower:
                actual = df_cols_lower[v_lower]
                remap[actual] = canonical
                notes.append(f"Renamed column '{actual}' to '{canonical}'")
                break
        else:
            # Try partial match as last resort
            for actual_lower, actual in df_cols_lower.items():
                if canonical in actual_lower and actual not in remap:
                    remap[actual] = canonical
                    notes.append(f"Partial match: renamed '{actual}' to '{canonical}'")
                    break

    return remap, notes


def _drop_summary_rows(df: pd.DataFrame, name_col: str) -> tuple[pd.DataFrame, int]:
    """Drop total/summary/blank rows by campaign name column."""
    summary_patterns = re.compile(
        r"^(total|grand total|subtotal|all campaigns|summary|report total|aggregate|\s*)$",
        re.IGNORECASE
    )
    mask = df[name_col].astype(str).str.strip().apply(lambda x: bool(summary_patterns.match(x)))
    count = int(mask.sum())
    return df[~mask].reset_index(drop=True), count


def _drop_zero_rows(df: pd.DataFrame, spend_col: str) -> tuple[pd.DataFrame, int]:
    """Drop rows where spend is 0 (paused/inactive campaigns with no data)."""
    mask = df[spend_col].astype(float) == 0
    count = int(mask.sum())
    return df[~mask].reset_index(drop=True), count


def clean_google_ads(filepath: str) -> tuple[pd.DataFrame, list]:
    """
    Load and clean a Google Ads CSV.
    Returns (cleaned_df, cleaning_report_lines).
    """
    report = []
    report.append(f"File: {Path(filepath).name}")

    df, encoding_notes = _read_csv_robust(filepath)
    report.extend(encoding_notes)
    report.append(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    report.append(f"Raw columns: {list(df.columns)}")

    # Normalize column names
    df.columns = [col.strip().lower() for col in df.columns]

    # Fuzzy column matching
    remap, remap_notes = _fuzzy_match_columns(list(df.columns), GOOGLE_COLUMN_VARIANTS)
    if remap:
        df = df.rename(columns=remap)
        report.extend(remap_notes)

    # Drop summary rows
    if "campaign" in df.columns:
        df, dropped = _drop_summary_rows(df, "campaign")
        if dropped:
            report.append(f"Dropped {dropped} summary/total row(s)")

    # Drop rows with no campaign name
    before = len(df)
    df = df.dropna(subset=["campaign"] if "campaign" in df.columns else [])
    df = df[df["campaign"].astype(str).str.strip() != ""]
    blank_dropped = before - len(df)
    if blank_dropped:
        report.append(f"Dropped {blank_dropped} row(s) with blank campaign name")

    # Drop zero-spend rows
    if "cost" in df.columns:
        df, zero_dropped = _drop_zero_rows(df, "cost")
        if zero_dropped:
            report.append(f"Dropped {zero_dropped} inactive campaign(s) with zero spend")

    # Clean numeric columns
    for col in ["impressions", "clicks", "cost", "conversions", "conversion value"]:
        if col in df.columns:
            df, fixed = _clean_numeric_column(df, col)
            if fixed:
                report.append(f"Cleaned {fixed} cell(s) in '{col}' (stripped symbols/formatting)")

    report.append(f"Final: {len(df)} campaign rows ready for analysis")
    return df, report


def clean_meta_ads(filepath: str) -> tuple[pd.DataFrame, list]:
    """
    Load and clean a Meta Ads CSV.
    Returns (cleaned_df, cleaning_report_lines).
    """
    report = []
    report.append(f"File: {Path(filepath).name}")

    df, encoding_notes = _read_csv_robust(filepath)
    report.extend(encoding_notes)
    report.append(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    report.append(f"Raw columns: {list(df.columns)}")

    # Normalize column names
    df.columns = [col.strip().lower() for col in df.columns]

    # Fuzzy column matching
    remap, remap_notes = _fuzzy_match_columns(list(df.columns), META_COLUMN_VARIANTS)
    if remap:
        df = df.rename(columns=remap)
        report.extend(remap_notes)

    # Drop summary rows
    name_col = "campaign name" if "campaign name" in df.columns else None
    if name_col:
        df, dropped = _drop_summary_rows(df, name_col)
        if dropped:
            report.append(f"Dropped {dropped} summary/total row(s)")

    # Drop rows with no campaign name
    if name_col:
        before = len(df)
        df = df.dropna(subset=[name_col])
        df = df[df[name_col].astype(str).str.strip() != ""]
        blank_dropped = before - len(df)
        if blank_dropped:
            report.append(f"Dropped {blank_dropped} row(s) with blank campaign name")

    # Drop zero-spend rows
    if "amount spent" in df.columns:
        df, zero_dropped = _drop_zero_rows(df, "amount spent")
        if zero_dropped:
            report.append(f"Dropped {zero_dropped} inactive campaign(s) with zero spend")

    # Clean numeric columns
    for col in ["impressions", "clicks", "amount spent", "results", "purchase roas"]:
        if col in df.columns:
            df, fixed = _clean_numeric_column(df, col)
            if fixed:
                report.append(f"Cleaned {fixed} cell(s) in '{col}' (stripped symbols/formatting)")

    report.append(f"Final: {len(df)} campaign rows ready for analysis")
    return df, report


def clean_funnel(filepath: str) -> tuple[pd.DataFrame, list]:
    """
    Load and clean a funnel CSV.
    Returns (cleaned_df, cleaning_report_lines).
    """
    report = []
    report.append(f"File: {Path(filepath).name}")

    df, encoding_notes = _read_csv_robust(filepath)
    report.extend(encoding_notes)
    report.append(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    report.append(f"Raw columns: {list(df.columns)}")

    # Normalize column names
    df.columns = [col.strip().lower() for col in df.columns]

    # Fuzzy column matching
    remap, remap_notes = _fuzzy_match_columns(list(df.columns), FUNNEL_COLUMN_VARIANTS)
    if remap:
        df = df.rename(columns=remap)
        report.extend(remap_notes)

    # Drop summary rows
    name_col = next((c for c in ["campaign", "campaign name"] if c in df.columns), None)
    if name_col:
        df, dropped = _drop_summary_rows(df, name_col)
        if dropped:
            report.append(f"Dropped {dropped} summary/total row(s)")

    # Clean numeric columns for any detected funnel stage columns
    for col in ["leads", "mqls", "sqls", "customers"]:
        if col in df.columns:
            df, fixed = _clean_numeric_column(df, col)
            if fixed:
                report.append(f"Cleaned {fixed} cell(s) in '{col}'")

    report.append(f"Final: {len(df)} rows ready")
    return df, report


def format_cleaning_report(report_lines: list, source: str) -> dict:
    """Format cleaning report for API/UI consumption."""
    changes = [l for l in report_lines if any(
        word in l.lower() for word in ["renamed", "dropped", "cleaned", "converted", "fallback", "detected"]
    )]
    return {
        "source": source,
        "summary": report_lines[-1] if report_lines else "",
        "changes": changes,
        "all_lines": report_lines,
        "has_changes": len(changes) > 0,
    }
