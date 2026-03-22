"""
Bulk CSV splitter - splits a multi-client ad export into per-client DataFrames.

Supported input formats:
  1. Single CSV/XLSX with a client-identifier column (auto-detected or manually mapped)
  2. XLSX with multiple sheets, one sheet per client

Returns a list of split results, one per detected client.
"""

import io
import logging
import chardet
import pandas as pd
from pathlib import Path

logger = logging.getLogger("adlens")

# Column names that likely identify a client/account grouping column
_CLIENT_COLUMN_VARIANTS = [
    "client", "client name", "client_name",
    "account", "account name", "account_name",
    "advertiser", "advertiser name", "advertiser_name",
    "brand", "brand name", "brand_name",
    "mcc account", "mcc", "manager account",
    "company", "company name",
    "customer", "customer name",
    "organization", "org",
]


def detect_client_column(columns: list[str]) -> str | None:
    """
    Return the column name that most likely identifies the client/account,
    or None if no candidate found.
    """
    cols_lower = {c.strip().lower(): c for c in columns}
    for variant in _CLIENT_COLUMN_VARIANTS:
        if variant in cols_lower:
            return cols_lower[variant]
    return None


def _read_file(filepath: str) -> tuple[pd.DataFrame | None, list[str], str | None]:
    """
    Read a CSV or XLSX file.
    Returns (dataframe, sheet_names, error_message).
    For CSV: sheet_names is empty. For XLSX with multiple sheets: sheet_names lists them.
    """
    path = Path(filepath)
    suffix = path.suffix.lower()

    try:
        if suffix in (".xlsx", ".xls"):
            xl = pd.ExcelFile(filepath, engine="openpyxl")
            sheets = xl.sheet_names
            if len(sheets) > 1:
                # Multi-sheet: caller handles per-sheet splitting
                return None, sheets, None
            else:
                df = xl.parse(sheets[0])
                return df, [], None
        else:
            # CSV with encoding detection
            with open(filepath, "rb") as f:
                raw = f.read(32768)
            enc = chardet.detect(raw).get("encoding") or "utf-8"
            df = None
            for enc_try in [enc, "utf-8-sig", "utf-8", "latin-1"]:
                try:
                    df = pd.read_csv(filepath, encoding=enc_try)
                    break
                except Exception:
                    continue
            if df is None:
                return None, [], "Could not read file - encoding issue"
            return df, [], None
    except Exception as e:
        return None, [], str(e)


def split_by_client_column(df: pd.DataFrame, client_col: str) -> dict[str, pd.DataFrame]:
    """
    Group df rows by unique values in client_col.
    Returns {client_name: sub_dataframe} with the client column dropped.
    """
    groups = {}
    for client_name, group_df in df.groupby(client_col, sort=False):
        name = str(client_name).strip()
        if not name or name.lower() in ("nan", "none", ""):
            continue
        sub = group_df.drop(columns=[client_col]).reset_index(drop=True)
        groups[name] = sub
    return groups


def split_by_sheets(filepath: str, sheet_names: list[str]) -> dict[str, pd.DataFrame]:
    """
    Read each sheet as a separate client DataFrame.
    Sheet name = client name.
    """
    groups = {}
    for sheet in sheet_names:
        try:
            df = pd.read_excel(filepath, sheet_name=sheet, engine="openpyxl")
            if df.empty:
                continue
            groups[sheet.strip()] = df
        except Exception as e:
            logger.warning("Could not read sheet %s: %s", sheet, e)
    return groups


def split_bulk_file(
    filepath: str,
    client_col_override: str | None = None,
) -> dict:
    """
    Main entry point. Detects split strategy and returns structured result.

    Returns:
    {
        "strategy": "column" | "sheets" | "single",
        "client_col": str | None,        # column used to split (if strategy=column)
        "clients": {
            client_name: pd.DataFrame,   # one per detected client
        },
        "all_columns": list[str],        # original column names (for mapping UI)
        "error": str | None,
    }
    """
    df, sheet_names, read_err = _read_file(filepath)

    if read_err:
        return {"strategy": None, "client_col": None, "clients": {}, "all_columns": [], "error": read_err}

    # Multi-sheet XLSX
    if sheet_names:
        clients = split_by_sheets(filepath, sheet_names)
        return {
            "strategy": "sheets",
            "client_col": None,
            "clients": clients,
            "all_columns": [],
            "error": None,
        }

    if df is None or df.empty:
        return {"strategy": None, "client_col": None, "clients": {}, "all_columns": [], "error": "File is empty"}

    all_columns = list(df.columns)

    # Determine client column
    client_col = client_col_override
    if not client_col:
        client_col = detect_client_column(all_columns)

    if client_col and client_col in df.columns:
        unique_clients = df[client_col].dropna().unique()
        if len(unique_clients) <= 1:
            # Only one client in the file - treat as single
            clients = {str(unique_clients[0]).strip() if len(unique_clients) else "Unknown": df}
            return {
                "strategy": "single",
                "client_col": client_col,
                "clients": clients,
                "all_columns": all_columns,
                "error": None,
            }
        clients = split_by_client_column(df, client_col)
        return {
            "strategy": "column",
            "client_col": client_col,
            "clients": clients,
            "all_columns": all_columns,
            "error": None,
        }

    # No client column found - return whole file as single (caller decides what to do)
    return {
        "strategy": "none",
        "client_col": None,
        "clients": {},
        "all_columns": all_columns,
        "error": None,
    }


def df_to_tempfile(df: pd.DataFrame, suffix: str = ".csv") -> str:
    """
    Write a DataFrame to a temp CSV file and return its path.
    Used to feed per-client DataFrames into the existing load_*_with_report pipeline.
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="w",
                                      encoding="utf-8", newline="")
    df.to_csv(tmp, index=False)
    tmp.close()
    return tmp.name


def match_or_create_client(conn, client_name: str, currency: str = "INR") -> int:
    """
    Find existing client by exact or case-insensitive name match.
    Create a new client record if no match found.
    Returns client_id.
    """
    from src.db import get_clients, create_client

    clients = get_clients(conn)
    name_lower = client_name.strip().lower()

    # Exact match first
    for c in clients:
        if c["name"].strip().lower() == name_lower:
            return c["id"]

    # Partial match (client name is a substring or vice versa)
    for c in clients:
        existing_lower = c["name"].strip().lower()
        if name_lower in existing_lower or existing_lower in name_lower:
            return c["id"]

    # No match - create new client
    new_client = create_client(conn, client_name.strip(), currency)
    return new_client["id"]
