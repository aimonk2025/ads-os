"""
DuckDB database layer.
Single file: data/adaudit.duckdb
"""

import json
import hashlib
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
            context     VARCHAR,
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
            period_start      DATE,
            period_end        DATE,
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
            reach            DOUBLE,
            frequency        DOUBLE,
            new_users        DOUBLE,
            returning_users  DOUBLE,
            pct_new_users    DOUBLE,
            cost_per_new_user DOUBLE,
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
            type_overrides      VARCHAR,
            updated_at          TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS kpi_alerts_seq START 1;
        CREATE TABLE IF NOT EXISTS kpi_alerts (
            id            INTEGER PRIMARY KEY DEFAULT nextval('kpi_alerts_seq'),
            client_id     INTEGER NOT NULL,
            upload_id     INTEGER,
            campaign      VARCHAR,
            platform      VARCHAR,
            alert_type    VARCHAR NOT NULL,
            threshold     DOUBLE,
            actual_value  DOUBLE,
            deviation_pct DOUBLE,
            severity      VARCHAR NOT NULL,
            message       VARCHAR NOT NULL,
            status        VARCHAR NOT NULL DEFAULT 'new',
            created_at    TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS competitors_seq START 1;
        CREATE TABLE IF NOT EXISTS competitors (
            id               INTEGER PRIMARY KEY DEFAULT nextval('competitors_seq'),
            client_id        INTEGER NOT NULL,
            brand_name       VARCHAR NOT NULL,
            ad_library_url   VARCHAR,
            last_scraped_at  VARCHAR,
            created_at       TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS competitor_ads_seq START 1;
        CREATE TABLE IF NOT EXISTS competitor_ads (
            id                   INTEGER PRIMARY KEY DEFAULT nextval('competitor_ads_seq'),
            competitor_id        INTEGER NOT NULL,
            ad_text              VARCHAR,
            cta                  VARCHAR,
            days_running         INTEGER,
            is_active            BOOLEAN,
            media_type           VARCHAR,
            angle                VARCHAR,
            offer                VARCHAR,
            psychology_triggers  VARCHAR,
            funnel_stage         VARCHAR,
            persona              VARCHAR,
            insight              VARCHAR,
            scraped_at           VARCHAR
        );

        CREATE SEQUENCE IF NOT EXISTS structured_audits_seq START 1;
        CREATE TABLE IF NOT EXISTS structured_audits (
            id                   INTEGER PRIMARY KEY DEFAULT nextval('structured_audits_seq'),
            client_id            INTEGER NOT NULL,
            upload_id            INTEGER,
            overall_score        INTEGER,
            category_scores      VARCHAR,
            checks_json          VARCHAR,
            recommendations_json VARCHAR,
            created_at           TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS forecasts_seq START 1;
        CREATE TABLE IF NOT EXISTS forecasts (
            id               INTEGER PRIMARY KEY DEFAULT nextval('forecasts_seq'),
            client_id        INTEGER NOT NULL,
            horizon_days     INTEGER NOT NULL,
            proj_spend       DOUBLE,
            proj_revenue     DOUBLE,
            proj_roas        DOUBLE,
            proj_conversions DOUBLE,
            spend_trend      VARCHAR,
            roas_trend       VARCHAR,
            season_factor    DOUBLE,
            periods_used     INTEGER,
            campaign_data    VARCHAR,
            created_at       TIMESTAMP NOT NULL DEFAULT now()
        );

        CREATE SEQUENCE IF NOT EXISTS action_items_seq START 1;
        CREATE TABLE IF NOT EXISTS action_items (
            id               INTEGER PRIMARY KEY DEFAULT nextval('action_items_seq'),
            client_id        INTEGER NOT NULL,
            text_hash        VARCHAR NOT NULL,
            text             VARCHAR NOT NULL,
            source           VARCHAR NOT NULL,
            priority         VARCHAR NOT NULL,
            campaign         VARCHAR,
            platform         VARCHAR,
            status           VARCHAR NOT NULL DEFAULT 'todo',
            done_at          TIMESTAMP,
            notes            VARCHAR,
            snapshot_roas    DOUBLE,
            snapshot_cac     DOUBLE,
            snapshot_ctr     DOUBLE,
            snapshot_spend   DOUBLE,
            snapshot_upload_id INTEGER,
            created_at       TIMESTAMP NOT NULL DEFAULT now(),
            updated_at       TIMESTAMP NOT NULL DEFAULT now(),
            UNIQUE(client_id, text_hash)
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
    if "period_start" not in existing_upload_cols:
        conn.execute("ALTER TABLE uploads ADD COLUMN period_start DATE")
        conn.commit()
    if "period_end" not in existing_upload_cols:
        conn.execute("ALTER TABLE uploads ADD COLUMN period_end DATE")
        conn.commit()
    if "period_notes" not in existing_upload_cols:
        conn.execute("ALTER TABLE uploads ADD COLUMN period_notes VARCHAR")
        conn.commit()

    existing_client_cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='clients'"
    ).fetchall()}
    if "context" not in existing_client_cols:
        conn.execute("ALTER TABLE clients ADD COLUMN context VARCHAR")
        conn.commit()

    existing_rules_cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='budget_rules'"
    ).fetchall()}
    if "type_overrides" not in existing_rules_cols:
        conn.execute("ALTER TABLE budget_rules ADD COLUMN type_overrides VARCHAR")
        conn.commit()

    # kpi_alerts migration
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if 'kpi_alerts' not in existing_tables:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS kpi_alerts_seq START 1;
            CREATE TABLE kpi_alerts (
                id            INTEGER PRIMARY KEY DEFAULT nextval('kpi_alerts_seq'),
                client_id     INTEGER NOT NULL,
                upload_id     INTEGER,
                campaign      VARCHAR,
                platform      VARCHAR,
                alert_type    VARCHAR NOT NULL,
                threshold     DOUBLE,
                actual_value  DOUBLE,
                deviation_pct DOUBLE,
                severity      VARCHAR NOT NULL,
                message       VARCHAR NOT NULL,
                status        VARCHAR NOT NULL DEFAULT 'new',
                created_at    TIMESTAMP NOT NULL DEFAULT now()
            )
        """)
        conn.commit()

    # competitors migration
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if 'competitors' not in existing_tables:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS competitors_seq START 1;
            CREATE TABLE competitors (
                id               INTEGER PRIMARY KEY DEFAULT nextval('competitors_seq'),
                client_id        INTEGER NOT NULL,
                brand_name       VARCHAR NOT NULL,
                ad_library_url   VARCHAR,
                last_scraped_at  VARCHAR,
                created_at       TIMESTAMP NOT NULL DEFAULT now()
            )
        """)
        conn.commit()
    if 'competitor_ads' not in existing_tables:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS competitor_ads_seq START 1;
            CREATE TABLE competitor_ads (
                id                   INTEGER PRIMARY KEY DEFAULT nextval('competitor_ads_seq'),
                competitor_id        INTEGER NOT NULL,
                ad_text              VARCHAR,
                cta                  VARCHAR,
                days_running         INTEGER,
                is_active            BOOLEAN,
                media_type           VARCHAR,
                angle                VARCHAR,
                offer                VARCHAR,
                psychology_triggers  VARCHAR,
                funnel_stage         VARCHAR,
                persona              VARCHAR,
                insight              VARCHAR,
                scraped_at           VARCHAR
            )
        """)
        conn.commit()

    # structured_audits migration
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if 'structured_audits' not in existing_tables:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS structured_audits_seq START 1;
            CREATE TABLE structured_audits (
                id                   INTEGER PRIMARY KEY DEFAULT nextval('structured_audits_seq'),
                client_id            INTEGER NOT NULL,
                upload_id            INTEGER,
                overall_score        INTEGER,
                category_scores      VARCHAR,
                checks_json          VARCHAR,
                recommendations_json VARCHAR,
                created_at           TIMESTAMP NOT NULL DEFAULT now()
            )
        """)
        conn.commit()

    # forecasts migration
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if 'forecasts' not in existing_tables:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS forecasts_seq START 1;
            CREATE TABLE forecasts (
                id               INTEGER PRIMARY KEY DEFAULT nextval('forecasts_seq'),
                client_id        INTEGER NOT NULL,
                horizon_days     INTEGER NOT NULL,
                proj_spend       DOUBLE,
                proj_revenue     DOUBLE,
                proj_roas        DOUBLE,
                proj_conversions DOUBLE,
                spend_trend      VARCHAR,
                roas_trend       VARCHAR,
                season_factor    DOUBLE,
                periods_used     INTEGER,
                campaign_data    VARCHAR,
                created_at       TIMESTAMP NOT NULL DEFAULT now()
            )
        """)
        conn.commit()

    # action_items migration
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if 'action_items' not in existing_tables:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS action_items_seq START 1;
            CREATE TABLE action_items (
                id               INTEGER PRIMARY KEY DEFAULT nextval('action_items_seq'),
                client_id        INTEGER NOT NULL,
                text_hash        VARCHAR NOT NULL,
                text             VARCHAR NOT NULL,
                source           VARCHAR NOT NULL,
                priority         VARCHAR NOT NULL,
                campaign         VARCHAR,
                platform         VARCHAR,
                status           VARCHAR NOT NULL DEFAULT 'todo',
                done_at          TIMESTAMP,
                notes            VARCHAR,
                snapshot_roas    DOUBLE,
                snapshot_cac     DOUBLE,
                snapshot_ctr     DOUBLE,
                snapshot_spend   DOUBLE,
                snapshot_upload_id INTEGER,
                created_at       TIMESTAMP NOT NULL DEFAULT now(),
                updated_at       TIMESTAMP NOT NULL DEFAULT now(),
                UNIQUE(client_id, text_hash)
            )
        """)
        conn.commit()

    # Column migrations for campaigns table
    existing_cols = {row[0] for row in conn.execute("DESCRIBE campaigns").fetchall()}
    for col, dtype in [
        ("reach", "DOUBLE"), ("frequency", "DOUBLE"),
        ("new_users", "DOUBLE"), ("returning_users", "DOUBLE"),
        ("pct_new_users", "DOUBLE"), ("cost_per_new_user", "DOUBLE"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE campaigns ADD COLUMN {col} {dtype}")
    conn.commit()

    # onboarding_status migration
    existing_tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
    if 'onboarding_status' not in existing_tables:
        conn.execute("""
            CREATE TABLE onboarding_status (
                client_id        INTEGER PRIMARY KEY,
                steps_completed  VARCHAR NOT NULL DEFAULT '[]',
                completed_at     VARCHAR
            )
        """)
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


def update_client(conn, client_id: int, name: str, currency: str, context: str = None) -> None:
    conn.execute(
        "UPDATE clients SET name = ?, currency = ?, context = ? WHERE id = ?",
        [name, currency, context, client_id]
    )
    conn.commit()


def delete_client(conn, client_id: int) -> None:
    conn.execute("DELETE FROM clients WHERE id = ?", [client_id])
    conn.commit()


# ---- Uploads ----

def check_duplicate_upload(conn, client_id: int, period_start: str,
                            period_end: str, platforms: list) -> Optional[dict]:
    """
    Check if an upload with the same client, period dates, and platforms already exists.
    Returns the existing upload row if found, else None.
    """
    if not period_start or not period_end:
        return None
    platform_json = json.dumps(sorted(platforms))
    row = _q1(conn, """
        SELECT id, period_label, period_start, period_end, uploaded_at
        FROM uploads
        WHERE client_id = ?
          AND period_start = ?
          AND period_end = ?
        ORDER BY uploaded_at DESC
        LIMIT 1
    """, [client_id, period_start, period_end])
    return row


def delete_upload(conn, upload_id: int) -> None:
    """Delete an upload and all associated data (campaigns, funnel, granular rows, anomalies)."""
    for table in ["granular_rows", "campaigns", "funnel_data", "anomalies"]:
        conn.execute(f"DELETE FROM {table} WHERE upload_id = ?", [upload_id])
    conn.execute("DELETE FROM uploads WHERE id = ?", [upload_id])
    conn.commit()


def create_upload(conn, client_id: int, platforms: list,
                  has_funnel: bool, period_label: str = None,
                  granularity_level: str = None,
                  period_start: str = None, period_end: str = None,
                  period_notes: str = None) -> int:
    conn.execute(
        "INSERT INTO uploads (client_id, platforms, has_funnel, period_label, granularity_level, period_start, period_end, period_notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [client_id, json.dumps(platforms), has_funnel, period_label, granularity_level, period_start, period_end, period_notes]
    )
    conn.commit()
    row = _q1(conn, "SELECT id FROM uploads WHERE client_id = ? ORDER BY uploaded_at DESC LIMIT 1", [client_id])
    return row["id"]


def get_upload_timeline(conn, client_id: int) -> list:
    """
    Return all uploads for a client ordered by period_start (or uploaded_at),
    with per-upload aggregated metrics for trend charting.
    """
    return _q(conn, """
        SELECT
            u.id            AS upload_id,
            u.period_label,
            u.period_start,
            u.period_end,
            u.uploaded_at,
            u.platforms,
            u.granularity_level,
            SUM(c.spend)              AS total_spend,
            AVG(c.roas)               AS avg_roas,
            SUM(c.conversions)        AS total_conversions,
            AVG(c.ctr)                AS avg_ctr,
            SUM(c.wasted_spend)       AS total_wasted,
            COUNT(DISTINCT c.campaign_name) AS campaign_count
        FROM uploads u
        JOIN campaigns c ON c.upload_id = u.id
        WHERE u.client_id = ?
        GROUP BY u.id, u.period_label, u.period_start, u.period_end,
                 u.uploaded_at, u.platforms, u.granularity_level
        ORDER BY COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) ASC
    """, [client_id])


def get_campaign_timeline(conn, client_id: int, campaign_name: str,
                          platform: str) -> list:
    """
    Return the full history of a single campaign across all uploads,
    ordered by period_start for trend analysis.
    """
    return _q(conn, """
        SELECT
            c.*,
            u.period_label,
            u.period_start,
            u.period_end,
            u.uploaded_at
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          AND c.campaign_name = ?
          AND c.platform = ?
        ORDER BY COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) ASC
    """, [client_id, campaign_name, platform])


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
        ga = c.get("ga") or {}
        rows.append([
            upload_id, client_id, platform,
            c.get("name", ""),
            c.get("impressions"), c.get("clicks"),
            c.get("spend"), c.get("conversions"),
            c.get("revenue"),
            c.get("roas"), c.get("cac"), c.get("ctr"),
            c.get("cpc"), c.get("severity"),
            c.get("wasted", 0),
            c.get("reach"), c.get("frequency"),
            ga.get("new_users"), ga.get("returning_users"),
            ga.get("pct_new_users"), ga.get("cost_per_new_user"),
        ])
    conn.executemany("""
        INSERT INTO campaigns (
            upload_id, client_id, platform, campaign_name,
            impressions, clicks, spend, conversions, conversion_value,
            roas, cac, ctr, cpc, severity, wasted_spend,
            reach, frequency, new_users, returning_users,
            pct_new_users, cost_per_new_user
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    conn.commit()


def get_campaigns_for_upload(conn, upload_id: int) -> list:
    return _q(conn,
        "SELECT * FROM campaigns WHERE upload_id = ? ORDER BY roas ASC",
        [upload_id]
    )


def get_campaign_history(conn, client_id: int, campaign_name: str,
                         platform: str, periods: int = 6,
                         days: int = None) -> list:
    """
    Return the last N periods of campaign history ordered by period_start ASC.

    Uses period count (not wall-clock days) so historical imports -- where all
    uploads land on the same uploaded_at -- are handled correctly.

    The legacy `days` param is accepted but ignored; callers should migrate to `periods`.
    """
    return _q(conn, """
        SELECT c.*,
               COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS period_date,
               u.period_label,
               u.period_start,
               u.period_end
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          AND c.campaign_name = ?
          AND c.platform = ?
          AND u.id IN (
              SELECT id FROM uploads
              WHERE client_id = ?
              ORDER BY COALESCE(period_start, CAST(uploaded_at AS DATE)) DESC
              LIMIT ?
          )
        ORDER BY COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) ASC
    """, [client_id, campaign_name, platform, client_id, periods])


def get_platform_history(conn, client_id: int, platform: str,
                         periods: int = 6,
                         days: int = None) -> list:
    """
    Return the last N periods of platform-level history ordered by period_start ASC.

    Uses period count not wall-clock days. Legacy `days` param accepted but ignored.
    """
    return _q(conn, """
        SELECT c.*,
               COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS period_date,
               u.period_label,
               u.period_start,
               u.period_end,
               u.uploaded_at AS upload_at
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ? AND c.platform = ?
          AND u.id IN (
              SELECT id FROM uploads
              WHERE client_id = ?
              ORDER BY COALESCE(period_start, CAST(uploaded_at AS DATE)) DESC
              LIMIT ?
          )
        ORDER BY COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) ASC
    """, [client_id, platform, client_id, periods])


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
    "type_overrides": [],  # [{type, keyword, min_roas, min_cpl}]
}


def get_budget_rules(conn, client_id: int) -> dict:
    row = _q1(conn, "SELECT * FROM budget_rules WHERE client_id = ?", [client_id])
    if not row:
        return {**DEFAULT_RULES, "client_id": client_id}
    row["priority_channels"] = json.loads(row.get("priority_channels") or '["google","meta"]')
    row["type_overrides"] = json.loads(row.get("type_overrides") or "[]")
    return row


def save_budget_rules(conn, client_id: int, rules: dict) -> None:
    existing = _q1(conn, "SELECT id FROM budget_rules WHERE client_id = ?", [client_id])
    channels = json.dumps(rules.get("priority_channels", ["google", "meta"]))
    overrides = json.dumps(rules.get("type_overrides", []))
    if existing:
        conn.execute("""
            UPDATE budget_rules SET
                google_min_roas = ?, meta_min_roas = ?,
                google_min_cpl = ?, meta_min_cpl = ?,
                max_shift_pct = ?, priority_channels = ?,
                type_overrides = ?,
                updated_at = now()
            WHERE client_id = ?
        """, [
            rules.get("google_min_roas", 2.0),
            rules.get("meta_min_roas", 2.0),
            rules.get("google_min_cpl"),
            rules.get("meta_min_cpl"),
            rules.get("max_shift_pct", 20.0),
            channels,
            overrides,
            client_id,
        ])
    else:
        conn.execute("""
            INSERT INTO budget_rules (
                client_id, google_min_roas, meta_min_roas,
                google_min_cpl, meta_min_cpl, max_shift_pct, priority_channels, type_overrides
            ) VALUES (?,?,?,?,?,?,?,?)
        """, [
            client_id,
            rules.get("google_min_roas", 2.0),
            rules.get("meta_min_roas", 2.0),
            rules.get("google_min_cpl"),
            rules.get("meta_min_cpl"),
            rules.get("max_shift_pct", 20.0),
            channels,
            overrides,
        ])
    conn.commit()


# ---- Action Items ----

def _action_hash(client_id: int, text: str) -> str:
    return hashlib.md5(f"{client_id}:{text[:200]}".encode()).hexdigest()


def upsert_action_items(conn, client_id: int, actions: list) -> None:
    """
    Insert new action items. Existing ones (same client+text_hash) are NOT overwritten
    so status/notes/done_at are preserved.
    """
    for a in actions:
        h = _action_hash(client_id, a['text'])
        existing = conn.execute(
            "SELECT id FROM action_items WHERE client_id=? AND text_hash=?", [client_id, h]
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT INTO action_items (client_id, text_hash, text, source, priority, campaign, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [client_id, h, a['text'], a.get('source', 'audit'), a.get('priority', 'low'),
                  a.get('campaign'), a.get('platform')])
    conn.commit()


def get_action_items(conn, client_id: int) -> list:
    result = conn.execute("""
        SELECT id, client_id, text, source, priority, campaign, platform,
               status, done_at, notes,
               snapshot_roas, snapshot_cac, snapshot_ctr, snapshot_spend,
               snapshot_upload_id, created_at, updated_at
        FROM action_items
        WHERE client_id=?
        ORDER BY
            CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            created_at ASC
    """, [client_id])
    return _rows_to_dicts(result.fetchall(), result.description)


def update_action_item(conn, item_id: int, client_id: int, status: str = None,
                       notes: str = None, done_at=None) -> bool:
    item = conn.execute(
        "SELECT id FROM action_items WHERE id=? AND client_id=?", [item_id, client_id]
    ).fetchone()
    if not item:
        return False
    if status is not None:
        conn.execute("UPDATE action_items SET status=?, updated_at=now() WHERE id=?", [status, item_id])
    if notes is not None:
        conn.execute("UPDATE action_items SET notes=?, updated_at=now() WHERE id=?", [notes, item_id])
    if done_at is not None:
        conn.execute("UPDATE action_items SET done_at=?, updated_at=now() WHERE id=?", [done_at, item_id])
    conn.commit()
    return True


def set_action_snapshot(conn, item_id: int, roas, cac, ctr, spend, upload_id: int) -> None:
    conn.execute("""
        UPDATE action_items
        SET snapshot_roas=?, snapshot_cac=?, snapshot_ctr=?, snapshot_spend=?,
            snapshot_upload_id=?, updated_at=now()
        WHERE id=?
    """, [roas, cac, ctr, spend, upload_id, item_id])
    conn.commit()
