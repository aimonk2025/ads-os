"""
DuckDB database layer.
Single file: data/adaudit.duckdb
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import duckdb

DB_PATH = Path(__file__).parent.parent / "data" / "adaudit.duckdb"


def get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    init_schema(conn)
    return conn


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.executemany("", [])  # no-op to ensure conn is live
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS clients_seq START 1;
        CREATE TABLE IF NOT EXISTS clients (
            id          INTEGER PRIMARY KEY DEFAULT nextval('clients_seq'),
            name        VARCHAR NOT NULL UNIQUE,
            currency    VARCHAR NOT NULL DEFAULT 'INR',
            created_at  TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS uploads_seq START 1;
        CREATE TABLE IF NOT EXISTS uploads (
            id                INTEGER PRIMARY KEY DEFAULT nextval('uploads_seq'),
            client_id         INTEGER NOT NULL,
            uploaded_at       TIMESTAMP NOT NULL DEFAULT now(),
            platforms         VARCHAR NOT NULL,
            has_funnel        BOOLEAN NOT NULL DEFAULT false,
            period_label      VARCHAR,
            notes             VARCHAR,
            granularity_level VARCHAR
        );

        CREATE SEQUENCE IF NOT EXISTS granular_rows_seq START 1;
        CREATE TABLE IF NOT EXISTS granular_rows (
            id            INTEGER PRIMARY KEY DEFAULT nextval('granular_rows_seq'),
            upload_id     INTEGER NOT NULL,
            client_id     INTEGER NOT NULL,
            platform      VARCHAR NOT NULL,
            row_level     VARCHAR NOT NULL,
            campaign_name VARCHAR,
            adgroup_name  VARCHAR,
            keyword_name  VARCHAR,
            ad_name       VARCHAR,
            placement_name VARCHAR,
            impressions   DOUBLE,
            clicks        DOUBLE,
            spend         DOUBLE,
            conversions   DOUBLE,
            conversion_value DOUBLE,
            roas          DOUBLE,
            ctr           DOUBLE,
            cpc           DOUBLE,
            quality_score INTEGER,
            match_type    VARCHAR,
            ad_type       VARCHAR,
            uploaded_at   TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS campaigns_seq START 1;
        CREATE TABLE IF NOT EXISTS campaigns (
            id               INTEGER PRIMARY KEY DEFAULT nextval('campaigns_seq'),
            upload_id        INTEGER NOT NULL,
            client_id        INTEGER NOT NULL,
            platform         VARCHAR NOT NULL,
            campaign_name    VARCHAR NOT NULL,
            impressions      DOUBLE,
            clicks           DOUBLE,
            spend            DOUBLE,
            conversions      DOUBLE,
            conversion_value DOUBLE,
            roas             DOUBLE,
            cac              DOUBLE,
            ctr              DOUBLE,
            cpc              DOUBLE,
            severity         VARCHAR,
            wasted_spend     DOUBLE,
            uploaded_at      TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS funnel_seq START 1;
        CREATE TABLE IF NOT EXISTS funnel_data (
            id                INTEGER PRIMARY KEY DEFAULT nextval('funnel_seq'),
            upload_id         INTEGER NOT NULL,
            client_id         INTEGER NOT NULL,
            campaign_name     VARCHAR NOT NULL,
            leads             DOUBLE,
            mqls              DOUBLE,
            sqls              DOUBLE,
            customers         DOUBLE,
            cost_per_lead     DOUBLE,
            cost_per_mql      DOUBLE,
            cost_per_sql      DOUBLE,
            cost_per_customer DOUBLE,
            mql_rate          DOUBLE,
            sql_rate          DOUBLE,
            close_rate        DOUBLE,
            uploaded_at       TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS anomalies_seq START 1;
        CREATE TABLE IF NOT EXISTS anomalies (
            id              INTEGER PRIMARY KEY DEFAULT nextval('anomalies_seq'),
            client_id       INTEGER NOT NULL,
            upload_id       INTEGER NOT NULL,
            campaign_name   VARCHAR NOT NULL,
            platform        VARCHAR NOT NULL,
            metric          VARCHAR NOT NULL,
            current_value   DOUBLE NOT NULL,
            baseline_value  DOUBLE NOT NULL,
            pct_change      DOUBLE NOT NULL,
            direction       VARCHAR NOT NULL,
            severity        VARCHAR NOT NULL,
            description     VARCHAR NOT NULL,
            status          VARCHAR NOT NULL DEFAULT 'open',
            detected_at     TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS reports_seq START 1;
        CREATE TABLE IF NOT EXISTS reports (
            id          INTEGER PRIMARY KEY DEFAULT nextval('reports_seq'),
            client_id   INTEGER NOT NULL,
            upload_id   INTEGER,
            report_type VARCHAR NOT NULL,
            title       VARCHAR NOT NULL,
            html_path   VARCHAR,
            pdf_path    VARCHAR,
            tone        VARCHAR,
            created_at  TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS budget_rules_seq START 1;
        CREATE TABLE IF NOT EXISTS budget_rules (
            id                  INTEGER PRIMARY KEY DEFAULT nextval('budget_rules_seq'),
            client_id           INTEGER NOT NULL UNIQUE,
            google_min_roas     DOUBLE NOT NULL DEFAULT 2.0,
            meta_min_roas       DOUBLE NOT NULL DEFAULT 2.0,
            google_min_cpl      DOUBLE,
            meta_min_cpl        DOUBLE,
            max_shift_pct       DOUBLE NOT NULL DEFAULT 20.0,
            priority_channels   VARCHAR NOT NULL DEFAULT '["google","meta"]',
            updated_at          TIMESTAMP NOT NULL DEFAULT now()
        );
    """)
    conn.commit()

    # Migrations: add columns to existing tables if not present
    existing_upload_cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='uploads'"
    ).fetchall()}
    if "granularity_level" not in existing_upload_cols:
        conn.execute("ALTER TABLE uploads ADD COLUMN granularity_level VARCHAR")
        conn.commit()


# ---- Helpers ----

def _row_to_dict(row, description) -> dict:
    return {description[i][0]: row[i] for i in range(len(description))}


def _rows_to_dicts(rows, description) -> list:
    return [_row_to_dict(r, description) for r in rows]


def _q(conn, sql: str, params=None) -> list[dict]:
    result = conn.execute(sql, params or [])
    rows = result.fetchall()
    desc = result.description
    return _rows_to_dicts(rows, desc)


def _q1(conn, sql: str, params=None) -> Optional[dict]:
    rows = _q(conn, sql, params)
    return rows[0] if rows else None


# ---- Clients ----

def create_client(conn, name: str, currency: str = "INR") -> dict:
    conn.execute(
        "INSERT INTO clients (name, currency) VALUES (?, ?)",
        [name, currency]
    )
    conn.commit()
    return _q1(conn, "SELECT * FROM clients WHERE name = ?", [name])


def get_clients(conn) -> list:
    return _q(conn, "SELECT * FROM clients ORDER BY name")


def get_client(conn, client_id: int) -> Optional[dict]:
    return _q1(conn, "SELECT * FROM clients WHERE id = ?", [client_id])


def update_client(conn, client_id: int, name: str, currency: str) -> None:
    conn.execute(
        "UPDATE clients SET name = ?, currency = ? WHERE id = ?",
        [name, currency, client_id]
    )
    conn.commit()


def delete_client(conn, client_id: int) -> None:
    conn.execute("DELETE FROM clients WHERE id = ?", [client_id])
    conn.commit()


# ---- Uploads ----

def create_upload(conn, client_id: int, platforms: list,
                  has_funnel: bool, period_label: str = None,
                  granularity_level: str = None) -> int:
    conn.execute(
        "INSERT INTO uploads (client_id, platforms, has_funnel, period_label, granularity_level) VALUES (?, ?, ?, ?, ?)",
        [client_id, json.dumps(platforms), has_funnel, period_label, granularity_level]
    )
    conn.commit()
    row = _q1(conn, "SELECT id FROM uploads WHERE client_id = ? ORDER BY uploaded_at DESC LIMIT 1", [client_id])
    return row["id"]


def get_uploads(conn, client_id: int, limit: int = 50) -> list:
    return _q(conn,
        "SELECT * FROM uploads WHERE client_id = ? ORDER BY uploaded_at DESC LIMIT ?",
        [client_id, limit]
    )


def get_upload(conn, upload_id: int) -> Optional[dict]:
    return _q1(conn, "SELECT * FROM uploads WHERE id = ?", [upload_id])


# ---- Campaigns ----

def insert_campaigns(conn, upload_id: int, client_id: int,
                     platform: str, campaigns: list) -> None:
    rows = []
    for c in campaigns:
        rows.append([
            upload_id, client_id, platform,
            c.get("name", ""),
            c.get("impressions"), c.get("clicks"),
            c.get("spend"), c.get("conversions"),
            c.get("revenue"),
            c.get("roas"), c.get("cac"), c.get("ctr"),
            c.get("cpc"), c.get("severity"),
            c.get("wasted", 0),
        ])
    conn.executemany("""
        INSERT INTO campaigns (
            upload_id, client_id, platform, campaign_name,
            impressions, clicks, spend, conversions, conversion_value,
            roas, cac, ctr, cpc, severity, wasted_spend
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def get_campaigns_for_upload(conn, upload_id: int) -> list:
    return _q(conn,
        "SELECT * FROM campaigns WHERE upload_id = ? ORDER BY roas ASC",
        [upload_id]
    )


def get_campaign_history(conn, client_id: int, campaign_name: str,
                         platform: str, days: int = 30) -> list:
    since = datetime.now() - timedelta(days=days)
    return _q(conn, """
        SELECT c.*, u.uploaded_at as period_date
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          AND c.campaign_name = ?
          AND c.platform = ?
          AND u.uploaded_at >= ?
        ORDER BY u.uploaded_at ASC
    """, [client_id, campaign_name, platform, since.isoformat()])


def get_platform_history(conn, client_id: int, platform: str,
                         days: int = 30) -> list:
    since = datetime.now() - timedelta(days=days)
    return _q(conn, """
        SELECT c.*, u.uploaded_at as period_date
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ? AND c.platform = ?
          AND u.uploaded_at >= ?
        ORDER BY u.uploaded_at ASC
    """, [client_id, platform, since.isoformat()])


def get_latest_upload_campaigns(conn, client_id: int) -> list:
    upload = _q1(conn,
        "SELECT id FROM uploads WHERE client_id = ? ORDER BY uploaded_at DESC LIMIT 1",
        [client_id]
    )
    if not upload:
        return []
    return get_campaigns_for_upload(conn, upload["id"])


# ---- Funnel Data ----

def insert_funnel_data(conn, upload_id: int, client_id: int,
                       funnel_campaigns: list) -> None:
    rows = []
    for c in funnel_campaigns:
        f = c.get("funnel", {}) or {}
        rows.append([
            upload_id, client_id, c.get("name", ""),
            f.get("leads"), f.get("mqls"), f.get("sqls"), f.get("customers"),
            c.get("cost_per_lead"), c.get("cost_per_mql"),
            c.get("cost_per_sql"), c.get("cost_per_customer"),
            c.get("mql_rate"), c.get("sql_rate"), c.get("close_rate"),
        ])
    conn.executemany("""
        INSERT INTO funnel_data (
            upload_id, client_id, campaign_name,
            leads, mqls, sqls, customers,
            cost_per_lead, cost_per_mql, cost_per_sql, cost_per_customer,
            mql_rate, sql_rate, close_rate
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def get_funnel_for_upload(conn, upload_id: int) -> list:
    return _q(conn,
        "SELECT * FROM funnel_data WHERE upload_id = ?",
        [upload_id]
    )


# ---- Granular Rows ----

def insert_granular_rows(conn, upload_id: int, client_id: int,
                         platform: str, row_level: str, rows_df) -> None:
    """
    Store granular (ad group / keyword / ad / placement) rows from a DataFrame.
    row_level: 'adgroup' | 'keyword' | 'ad' | 'placement'
    """
    import pandas as pd
    if rows_df is None or (hasattr(rows_df, "__len__") and len(rows_df) == 0):
        return

    spend_col = next((c for c in ["cost", "amount spent", "spend"] if c in rows_df.columns), None)
    rows = []
    for _, row in rows_df.iterrows():
        sp = float(row.get(spend_col, 0) or 0) if spend_col else 0.0
        clicks = float(row.get("clicks", 0) or 0)
        impr = float(row.get("impressions", 0) or 0)
        conv = float(row.get("conversions", row.get("results", 0)) or 0)
        rev = float(row.get("conversion value", 0) or 0)
        rows.append([
            upload_id, client_id, platform, row_level,
            str(row.get("campaign", row.get("campaign name", "")) or ""),
            str(row.get("ad group", row.get("ad set name", row.get("adgroup", ""))) or ""),
            str(row.get("keyword", row.get("keywords", "")) or ""),
            str(row.get("ad name", row.get("ad", "")) or ""),
            str(row.get("placement", row.get("site", "")) or ""),
            impr, clicks, sp, conv, rev,
            round(rev / sp, 4) if sp else 0.0,
            round(clicks / impr * 100, 4) if impr else 0.0,
            round(sp / clicks, 4) if clicks else 0.0,
            int(row.get("quality score", 0) or 0),
            str(row.get("match type", "") or ""),
            str(row.get("ad type", "") or ""),
        ])
    conn.executemany("""
        INSERT INTO granular_rows (
            upload_id, client_id, platform, row_level,
            campaign_name, adgroup_name, keyword_name, ad_name, placement_name,
            impressions, clicks, spend, conversions, conversion_value,
            roas, ctr, cpc, quality_score, match_type, ad_type
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def get_granular_rows(conn, upload_id: int, row_level: str = None) -> list:
    sql = "SELECT * FROM granular_rows WHERE upload_id = ?"
    params = [upload_id]
    if row_level:
        sql += " AND row_level = ?"
        params.append(row_level)
    sql += " ORDER BY spend DESC"
    return _q(conn, sql, params)


# ---- Anomalies ----

def insert_anomalies(conn, anomalies: list) -> None:
    rows = []
    for a in anomalies:
        rows.append([
            a["client_id"], a["upload_id"], a["campaign_name"],
            a["platform"], a["metric"], a["current_value"],
            a["baseline_value"], a["pct_change"], a["direction"],
            a["severity"], a["description"],
        ])
    conn.executemany("""
        INSERT INTO anomalies (
            client_id, upload_id, campaign_name, platform, metric,
            current_value, baseline_value, pct_change, direction,
            severity, description
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def get_anomalies(conn, client_id: int,
                  status: str = None, limit: int = 100) -> list:
    sql = "SELECT * FROM anomalies WHERE client_id = ?"
    params = [client_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY detected_at DESC LIMIT ?"
    params.append(limit)
    return _q(conn, sql, params)


def update_anomaly_status(conn, anomaly_id: int, status: str) -> None:
    conn.execute("UPDATE anomalies SET status = ? WHERE id = ?", [status, anomaly_id])
    conn.commit()


def get_anomaly_summary(conn, client_id: int) -> dict:
    rows = _q(conn, """
        SELECT severity, COUNT(*) as cnt
        FROM anomalies
        WHERE client_id = ? AND status = 'open'
        GROUP BY severity
    """, [client_id])
    summary = {"critical": 0, "warning": 0, "info": 0, "total": 0}
    for r in rows:
        sev = r["severity"]
        if sev in summary:
            summary[sev] = r["cnt"]
            summary["total"] += r["cnt"]
    return summary


# ---- Reports ----

def save_report(conn, client_id: int, upload_id: Optional[int],
                report_type: str, title: str,
                html_path: str = None, pdf_path: str = None,
                tone: str = None) -> int:
    conn.execute("""
        INSERT INTO reports (client_id, upload_id, report_type, title, html_path, pdf_path, tone)
        VALUES (?,?,?,?,?,?,?)
    """, [client_id, upload_id, report_type, title, html_path, pdf_path, tone])
    conn.commit()
    row = _q1(conn, """
        SELECT id FROM reports WHERE client_id = ? ORDER BY created_at DESC LIMIT 1
    """, [client_id])
    return row["id"]


def get_reports(conn, client_id: int,
                report_type: str = None, limit: int = 50) -> list:
    sql = "SELECT * FROM reports WHERE client_id = ?"
    params = [client_id]
    if report_type:
        sql += " AND report_type = ?"
        params.append(report_type)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    return _q(conn, sql, params)


def get_report(conn, report_id: int) -> Optional[dict]:
    return _q1(conn, "SELECT * FROM reports WHERE id = ?", [report_id])


def update_report_pdf_path(conn, report_id: int, pdf_path: str) -> None:
    conn.execute("UPDATE reports SET pdf_path = ? WHERE id = ?", [pdf_path, report_id])
    conn.commit()


def delete_report(conn, report_id: int) -> dict:
    """Delete a report record and return its file paths for cleanup."""
    row = _q1(conn, "SELECT html_path, pdf_path FROM reports WHERE id = ?", [report_id])
    conn.execute("DELETE FROM reports WHERE id = ?", [report_id])
    conn.commit()
    return row or {}


# ---- Budget Rules ----

DEFAULT_RULES = {
    "google_min_roas": 2.0,
    "meta_min_roas": 2.0,
    "google_min_cpl": None,
    "meta_min_cpl": None,
    "max_shift_pct": 20.0,
    "priority_channels": ["google", "meta"],
}


def get_budget_rules(conn, client_id: int) -> dict:
    row = _q1(conn, "SELECT * FROM budget_rules WHERE client_id = ?", [client_id])
    if not row:
        return {**DEFAULT_RULES, "client_id": client_id}
    row["priority_channels"] = json.loads(row.get("priority_channels") or '["google","meta"]')
    return row


def save_budget_rules(conn, client_id: int, rules: dict) -> None:
    existing = _q1(conn, "SELECT id FROM budget_rules WHERE client_id = ?", [client_id])
    channels = json.dumps(rules.get("priority_channels", ["google", "meta"]))
    if existing:
        conn.execute("""
            UPDATE budget_rules SET
                google_min_roas = ?, meta_min_roas = ?,
                google_min_cpl = ?, meta_min_cpl = ?,
                max_shift_pct = ?, priority_channels = ?,
                updated_at = now()
            WHERE client_id = ?
        """, [
            rules.get("google_min_roas", 2.0),
            rules.get("meta_min_roas", 2.0),
            rules.get("google_min_cpl"),
            rules.get("meta_min_cpl"),
            rules.get("max_shift_pct", 20.0),
            channels,
            client_id,
        ])
    else:
        conn.execute("""
            INSERT INTO budget_rules (
                client_id, google_min_roas, meta_min_roas,
                google_min_cpl, meta_min_cpl, max_shift_pct, priority_channels
            ) VALUES (?,?,?,?,?,?,?)
        """, [
            client_id,
            rules.get("google_min_roas", 2.0),
            rules.get("meta_min_roas", 2.0),
            rules.get("google_min_cpl"),
            rules.get("meta_min_cpl"),
            rules.get("max_shift_pct", 20.0),
            channels,
        ])
    conn.commit()
