"""
Loader - thin wrapper over cleaner.py.
Returns (df, cleaning_report, gran_result) for each platform.
Validates required columns after cleaning.
"""

import pandas as pd
from pathlib import Path
from src.cleaner import clean_google_ads, clean_meta_ads, clean_funnel, format_cleaning_report
from src.granularity import process as gran_process


GOOGLE_REQUIRED_COLUMNS = [
    "campaign", "impressions", "clicks", "cost", "conversions", "conversion value"
]

META_REQUIRED_COLUMNS = [
    "campaign name", "impressions", "clicks", "amount spent", "results", "purchase roas"
]


def validate_columns(df: pd.DataFrame, required: list, source: str) -> None:
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            f"{source} CSV is missing required columns after cleaning: {missing}\n"
            f"Found columns: {list(df.columns)}\n"
            f"Tip: Your CSV may use different column names. The cleaner tried common variants.\n"
            f"Required (any variant): {required}"
        )


def load_google_ads(filepath: str) -> pd.DataFrame:
    """Load and clean Google Ads CSV. Raises on unrecoverable errors."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Google Ads file not found: {filepath}")

    df, _ = clean_google_ads(filepath)
    validate_columns(df, GOOGLE_REQUIRED_COLUMNS, "Google Ads")
    return df


def load_google_ads_with_report(filepath: str) -> tuple:
    """
    Load and clean Google Ads CSV.
    Returns (campaign_df, cleaning_report, gran_result) where:
      - campaign_df is aggregated to campaign level (safe for metrics)
      - gran_result is the full granularity dict with keyword_df, adgroup_df, etc.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Google Ads file not found: {filepath}")

    df, report_lines = clean_google_ads(filepath)
    validate_columns(df, GOOGLE_REQUIRED_COLUMNS, "Google Ads")
    cleaning_report = format_cleaning_report(report_lines, "Google Ads")

    gran = gran_process(df, platform="google")
    campaign_df = gran["campaign_df"] if gran["campaign_df"] is not None else df

    return campaign_df, cleaning_report, gran


def load_meta_ads(filepath: str) -> pd.DataFrame:
    """Load and clean Meta Ads CSV. Raises on unrecoverable errors."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Meta Ads file not found: {filepath}")

    df, _ = clean_meta_ads(filepath)
    validate_columns(df, META_REQUIRED_COLUMNS, "Meta Ads")
    return df


def load_meta_ads_with_report(filepath: str) -> tuple:
    """
    Load and clean Meta Ads CSV.
    Returns (campaign_df, cleaning_report, gran_result).
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Meta Ads file not found: {filepath}")

    df, report_lines = clean_meta_ads(filepath)
    validate_columns(df, META_REQUIRED_COLUMNS, "Meta Ads")
    cleaning_report = format_cleaning_report(report_lines, "Meta Ads")

    gran = gran_process(df, platform="meta")
    campaign_df = gran["campaign_df"] if gran["campaign_df"] is not None else df

    return campaign_df, cleaning_report, gran
