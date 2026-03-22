"""
Data cleaner - auto-fixes messy real-world CSV exports before validation.

Produces a cleaning report: every change made, so the user can see exactly
what was normalized.

Date handling:
  normalize_date(val) -> "YYYY-MM-DD" or None
  Handles all known real-world formats: DD/MM/YYYY, MM/DD/YYYY, DD-MM-YYYY,
  MM-DD-YYYY, YYYY-MM-DD, DD MMM YYYY, MMM DD YYYY, YYYYMMDD, etc.
  Ambiguous day/month combos (both <= 12) are resolved by:
    1. Scanning the whole column to find a value where one component > 12
    2. Falling back to context: Indian/UK data defaults DD/MM, US defaults MM/DD
"""

import re
import chardet
import pandas as pd
from pathlib import Path
from typing import Optional
from datetime import date as _date


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# ---------------------------------------------------------------------------
# Period label -> start date resolution
# ---------------------------------------------------------------------------

# ISO week: week 1 = first week with a Thursday (ISO 8601)
# Q1-Q4: calendar quarters Jan/Apr/Jul/Oct
# FY: financial year - India default is Apr 1 (fy_start_month configurable)
# H1/H2: half-years

_QUARTER_START_MONTH = {1: 1, 2: 4, 3: 7, 4: 10}
_HALF_START_MONTH    = {1: 1, 2: 7}

# Patterns for period labels
_PERIOD_PATTERNS = [
    # Q1 2026, Q1/2026, Q1-2026, 2026 Q1, 2026-Q1
    (re.compile(r"^[Qq]([1-4])[\s/\-](\d{4})$"),   "quarter_y"),
    (re.compile(r"^(\d{4})[\s/\-][Qq]([1-4])$"),    "y_quarter"),
    # H1 2026, H2/2026, 2026 H2
    (re.compile(r"^[Hh]([12])[\s/\-](\d{4})$"),     "half_y"),
    (re.compile(r"^(\d{4})[\s/\-][Hh]([12])$"),     "y_half"),
    # FY2026, FY 2026, FY26, FY-2026
    (re.compile(r"^[Ff][Yy][\s\-]?(\d{4})$"),       "fy_4y"),
    (re.compile(r"^[Ff][Yy][\s\-]?(\d{2})$"),       "fy_2y"),
    # FY2025-26, FY2025/26  (Indian straddling format)
    (re.compile(r"^[Ff][Yy](\d{4})[-/]\d{2,4}$"),   "fy_straddle"),
    # Week 3 2026, Week3/2026, W3 2026, Wk3-2026
    (re.compile(r"^[Ww](?:eek\s*|[Kk]\s*)?(\d{1,2})[\s/\-](\d{4})$"), "week_y"),
    # Week 3 (no year - use current year)
    (re.compile(r"^[Ww](?:eek\s*|[Kk]\s*)?(\d{1,2})$"),               "week_noy"),
    # Month name 2026: Jan 2026, January 2026, Jan-2026
    (re.compile(r"^([a-zA-Z]+)[\s\-/](\d{4})$"),    "month_y"),
    # 2026 Jan, 2026-Jan
    (re.compile(r"^(\d{4})[\s\-/]([a-zA-Z]+)$"),    "y_month"),
]


def resolve_period_label(val: str, fy_start_month: int = 4) -> Optional[str]:
    """
    Convert a period label string to the ISO start date of that period.

    Examples:
      "Q1 2026"   -> "2026-01-01"
      "Q2 2026"   -> "2026-04-01"
      "H1 2026"   -> "2026-01-01"
      "FY2026"    -> "2026-04-01"  (India: FY starts April 1)
      "FY2025-26" -> "2025-04-01"
      "Week 3 2026" -> "2026-01-12" (ISO week 3)
      "Week 3"    -> current-year ISO week 3 start
      "Jan 2026"  -> "2026-01-01"
      "January 2026" -> "2026-01-01"

    fy_start_month: 4 for India/UK (April), 1 for calendar year, 10 for US federal
    Returns None if not recognisable as a period label.
    """
    if not val:
        return None
    s = str(val).strip()

    for pattern, fmt in _PERIOD_PATTERNS:
        m = pattern.match(s)
        if not m:
            continue
        g = m.groups()

        if fmt == "quarter_y":
            q, y = int(g[0]), int(g[1])
            mo = _QUARTER_START_MONTH[q]
            return _to_date_str(y, mo, 1)

        if fmt == "y_quarter":
            y, q = int(g[0]), int(g[1])
            mo = _QUARTER_START_MONTH[q]
            return _to_date_str(y, mo, 1)

        if fmt == "half_y":
            h, y = int(g[0]), int(g[1])
            mo = _HALF_START_MONTH[h]
            return _to_date_str(y, mo, 1)

        if fmt == "y_half":
            y, h = int(g[0]), int(g[1])
            mo = _HALF_START_MONTH[h]
            return _to_date_str(y, mo, 1)

        if fmt == "fy_4y":
            y = int(g[0])
            return _to_date_str(y, fy_start_month, 1)

        if fmt == "fy_2y":
            y = _fix_2y(int(g[0]))
            return _to_date_str(y, fy_start_month, 1)

        if fmt == "fy_straddle":
            # FY2025-26 -> year 2025, FY starts fy_start_month
            y = int(g[0])
            return _to_date_str(y, fy_start_month, 1)

        if fmt in ("week_y", "week_noy"):
            week_num = int(g[0])
            if fmt == "week_y":
                year = int(g[1])
            else:
                from datetime import date as _d
                year = _d.today().year
            # ISO 8601: find the Monday of ISO week week_num
            try:
                from datetime import date as _d2
                # Python: date.fromisocalendar(year, week, 1) = Monday of that ISO week
                start = _d2.fromisocalendar(year, week_num, 1)
                return start.isoformat()
            except (ValueError, AttributeError):
                # fromisocalendar added in 3.8; fallback
                import datetime
                jan4 = datetime.date(year, 1, 4)  # Jan 4 is always in week 1
                week1_monday = jan4 - datetime.timedelta(days=jan4.weekday())
                start = week1_monday + datetime.timedelta(weeks=week_num - 1)
                return start.isoformat()

        if fmt == "month_y":
            mon_str, y = g[0].lower(), int(g[1])
            mo = _MONTH_MAP.get(mon_str)
            if mo:
                return _to_date_str(y, mo, 1)

        if fmt == "y_month":
            y, mon_str = int(g[0]), g[1].lower()
            mo = _MONTH_MAP.get(mon_str)
            if mo:
                return _to_date_str(y, mo, 1)

    return None


# Regex patterns tried in order (all capture groups: year, month, day OR day, month, year)
_DATE_PATTERNS = [
    # ISO: 2026-01-15  or  2026/01/15
    (re.compile(r"^(\d{4})[-/\.](\d{1,2})[-/\.](\d{1,2})$"), "ymd"),
    # YYYYMMDD compact: 20260115
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "ymd"),
    # DD/MM/YYYY or MM/DD/YYYY (ambiguous - resolved below)
    (re.compile(r"^(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})$"), "dmy_or_mdy"),
    # DD/MM/YY or MM/DD/YY
    (re.compile(r"^(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{2})$"), "dmy_or_mdy_2y"),
    # DD Mon YYYY  or  DD Mon YY  (e.g. 15-Jan-2026, 1-Jan-26, 15 Jan 2026)
    (re.compile(r"^(\d{1,2})[-/\.\s]([a-zA-Z]+)[-/\.\s](\d{2,4})$"), "dmy_text"),
    # Mon DD, YYYY  or  Month DD YYYY  (e.g. Jan 15, 2026)
    (re.compile(r"^([a-zA-Z]+)\s+(\d{1,2}),?\s+(\d{4})$"), "mdy_text"),
    # YYYY-Mon-DD  (e.g. 2026-Jan-15)
    (re.compile(r"^(\d{4})[-/\.]([a-zA-Z]+)[-/\.](\d{1,2})$"), "ymd_text"),
    # DD MM YYYY  (space-separated all-numeric, e.g. 15 01 2026)
    (re.compile(r"^(\d{1,2})\s+(\d{1,2})\s+(\d{4})$"), "dmy_or_mdy"),
    # Date range: extract start date  (e.g. "Jan 15, 2026 - Jan 31, 2026")
    (re.compile(r"^([a-zA-Z]+\s+\d{1,2},?\s+\d{4})\s*[-–]\s*.+$"), "range_start"),
    (re.compile(r"^(\d{1,2}[-/\.][a-zA-Z\d][-/\.]\d{2,4})\s*[-–]\s*.+$"), "range_start"),
]

# Excel epoch: 1900-01-01 (with Lotus 1-2-3 leap-year bug: serial 1=Jan1, 60=fake Feb29)
_EXCEL_EPOCH = _date(1899, 12, 30)


def _excel_serial_to_date(val: str) -> Optional[str]:
    """
    Convert Excel serial number to ISO date string.
    Only accepts serials in the range 2000-01-01 to 2040-12-31
    (serials 36526-51910) to avoid misinterpreting spend/metric values.
    """
    try:
        n = int(val)
        # 2000-01-01 = serial 36526, 2040-12-31 = serial 51911
        if not (36526 <= n <= 51911):
            return None
        from datetime import timedelta
        d = _EXCEL_EPOCH + timedelta(days=n)
        return d.isoformat()
    except (ValueError, OverflowError):
        return None


def _fix_2y(yr: int) -> int:
    """Expand 2-digit year: 00-49 -> 2000-2049, 50-99 -> 1950-1999."""
    return yr + 2000 if yr < 50 else yr + 1900


def _to_date_str(y: int, m: int, d: int) -> Optional[str]:
    try:
        return _date(y, m, d).isoformat()
    except ValueError:
        return None


def normalize_date(val, day_first: bool = True) -> Optional[str]:
    """
    Parse a single date value into ISO format YYYY-MM-DD.
    Returns None if unparseable (non-date text like "Q1 2026" or "Week 3").

    day_first=True  -> ambiguous DD/MM/YYYY (default, used in India/UK)
    day_first=False -> ambiguous MM/DD/YYYY (US format)

    Handles:
      ISO, YYYYMMDD, DD/MM/YYYY, MM/DD/YYYY, DD-Mon-YY(YY), Mon DD YYYY,
      2-digit years, space-separated, date ranges (extracts start), Excel serials.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in ("nan", "none", "null", "n/a", "--", "-", "date"):
        return None

    # Excel serial: pure integer in plausible range
    if re.match(r"^\d{5}$", s):
        result = _excel_serial_to_date(s)
        if result:
            return result

    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.match(s)
        if not m:
            continue
        g = m.groups()

        if fmt == "ymd":
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
            return _to_date_str(y, mo, d)

        if fmt in ("dmy_or_mdy", "dmy_or_mdy_2y"):
            a, b = int(g[0]), int(g[1])
            y = int(g[2])
            if fmt == "dmy_or_mdy_2y":
                y = _fix_2y(y)
            if a > 12:
                return _to_date_str(y, b, a)
            if b > 12:
                return _to_date_str(y, a, b)
            if day_first:
                return _to_date_str(y, b, a)   # a=day, b=month
            else:
                return _to_date_str(y, a, b)   # a=month, b=day

        if fmt == "mdy_text":
            mon_str, d, y = g[0].lower(), int(g[1]), int(g[2])
            mo = _MONTH_MAP.get(mon_str)
            if mo:
                return _to_date_str(y, mo, d)

        if fmt == "dmy_text":
            # Handles: 15-Jan-2026, 15-Jan-26, 15 Jan 2026, 1-Jan-26
            raw_d, mon_str, raw_y = g[0], g[1].lower(), g[2]
            mo = _MONTH_MAP.get(mon_str)
            if mo:
                d = int(raw_d)
                y = int(raw_y)
                if y < 100:
                    y = _fix_2y(y)
                return _to_date_str(y, mo, d)

        if fmt == "ymd_text":
            y, mon_str, d = int(g[0]), g[1].lower(), int(g[2])
            mo = _MONTH_MAP.get(mon_str)
            if mo:
                return _to_date_str(y, mo, d)

        if fmt == "range_start":
            # Recursively parse just the start portion of a date range
            start_str = g[0].strip()
            result = normalize_date(start_str, day_first=day_first)
            if result:
                return result

    # Last resort: try period label resolution (Q1 2026, FY2026, Week 3, Jan 2026...)
    period_result = resolve_period_label(s)
    if period_result:
        return period_result

    return None


def _detect_day_first(series: pd.Series) -> bool:
    """
    Scan a date column to determine if DD/MM or MM/DD is being used.
    Finds a row where one component > 12, making it unambiguous.
    Falls back to True (day-first, India/UK default).
    """
    sep_pat = re.compile(r"^(\d{1,2})[-/\.](\d{1,2})[-/\.]\d{2,4}$")
    for val in series.dropna():
        m = sep_pat.match(str(val).strip())
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12:
                return True   # first component is day -> day_first
            if b > 12:
                return False  # second component is day -> month_first
    return True  # default: day-first


def normalize_date_column(df: pd.DataFrame, col: str,
                          fy_start_month: int = 4) -> tuple[pd.DataFrame, int]:
    """
    Normalize a date column in-place to YYYY-MM-DD strings.
    Period labels (Q1 2026, FY2026, Week 3) are resolved to their start date.
    Returns (df, count_of_cells_changed).
    """
    day_first = _detect_day_first(df[col])

    def _norm(v):
        result = normalize_date(v, day_first=day_first)
        return result if result is not None else v

    original = df[col].copy()
    df[col] = df[col].apply(_norm)
    changed = int((original.astype(str) != df[col].astype(str)).sum())
    return df, changed


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
    "purchase value": ["purchase value", "conversion value", "conv. value", "revenue",
                       "total value", "website purchase value", "value"],
    "reach":          ["reach", "unique reach", "estimated reach", "people reached"],
    "frequency":      ["frequency", "avg. frequency", "average frequency", "ad frequency"],
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


def clean_google_ads(filepath: str, pre_rename: dict = None) -> tuple[pd.DataFrame, list]:
    """
    Load and clean a Google Ads CSV.
    Returns (cleaned_df, cleaning_report_lines).
    pre_rename: optional dict {original_col_name: canonical_col_name} applied before fuzzy matching.
    """
    report = []
    report.append(f"File: {Path(filepath).name}")

    df, encoding_notes = _read_csv_robust(filepath)
    report.extend(encoding_notes)
    report.append(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    report.append(f"Raw columns: {list(df.columns)}")

    # Apply manual column mapping if provided (before fuzzy matching)
    if pre_rename:
        df = df.rename(columns=pre_rename)
        report.append(f"Applied manual column mapping: {len(pre_rename)} column(s) renamed")

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

    # Normalize date columns
    date_candidates = [c for c in df.columns if any(kw in c for kw in ["date", "day", "week", "month", "period"])]
    for col in date_candidates:
        df, fixed = normalize_date_column(df, col)
        if fixed:
            report.append(f"Normalized {fixed} date(s) in '{col}' to YYYY-MM-DD")

    report.append(f"Final: {len(df)} campaign rows ready for analysis")
    return df, report


def clean_meta_ads(filepath: str, pre_rename: dict = None) -> tuple[pd.DataFrame, list]:
    """
    Load and clean a Meta Ads CSV.
    Returns (cleaned_df, cleaning_report_lines).
    pre_rename: optional dict {original_col_name: canonical_col_name} applied before fuzzy matching.
    """
    report = []
    report.append(f"File: {Path(filepath).name}")

    df, encoding_notes = _read_csv_robust(filepath)
    report.extend(encoding_notes)
    report.append(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    report.append(f"Raw columns: {list(df.columns)}")

    # Apply manual column mapping if provided (before fuzzy matching)
    if pre_rename:
        df = df.rename(columns=pre_rename)
        report.append(f"Applied manual column mapping: {len(pre_rename)} column(s) renamed")

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
    for col in ["impressions", "reach", "frequency", "clicks", "amount spent", "results", "purchase roas", "purchase value"]:
        if col in df.columns:
            df, fixed = _clean_numeric_column(df, col)
            if fixed:
                report.append(f"Cleaned {fixed} cell(s) in '{col}' (stripped symbols/formatting)")

    # Normalize date columns
    date_candidates = [c for c in df.columns if any(kw in c for kw in ["date", "day", "week", "month", "period"])]
    for col in date_candidates:
        df, fixed = normalize_date_column(df, col)
        if fixed:
            report.append(f"Normalized {fixed} date(s) in '{col}' to YYYY-MM-DD")

    report.append(f"Final: {len(df)} campaign rows ready for analysis")
    return df, report


def clean_funnel(filepath: str, pre_rename: dict = None) -> tuple[pd.DataFrame, list]:
    """
    Load and clean a funnel CSV.
    Returns (cleaned_df, cleaning_report_lines).
    pre_rename: optional dict {original_col_name: canonical_col_name} applied before fuzzy matching.
    """
    report = []
    report.append(f"File: {Path(filepath).name}")

    df, encoding_notes = _read_csv_robust(filepath)
    report.extend(encoding_notes)
    report.append(f"Loaded {len(df)} rows, {len(df.columns)} columns")
    report.append(f"Raw columns: {list(df.columns)}")

    # Apply manual column mapping if provided (before fuzzy matching)
    if pre_rename:
        df = df.rename(columns=pre_rename)
        report.append(f"Applied manual column mapping: {len(pre_rename)} column(s) renamed")

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

    # Normalize date columns
    date_candidates = [c for c in df.columns if any(kw in c for kw in ["date", "day", "week", "month", "period"])]
    for col in date_candidates:
        df, fixed = normalize_date_column(df, col)
        if fixed:
            report.append(f"Normalized {fixed} date(s) in '{col}' to YYYY-MM-DD")

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
