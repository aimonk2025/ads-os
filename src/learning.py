"""
Learning module - runs after every upload.

Mines the client's own DuckDB history to produce two outputs:
1. client_benchmarks  - per-client, per-platform medians updated each upload
2. outcome_signals    - tracks whether acted-on recommendations improved metrics

Called from app.py immediately after all insert_* calls complete.
No Claude API calls. Pure SQL + arithmetic.
"""

import json
from typing import Optional
import duckdb


# ---- Schema ----------------------------------------------------------------

def _ensure_tables(conn: duckdb.DuckDBPyConnection) -> None:
    existing = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}

    if "client_benchmarks" not in existing:
        conn.execute("""
            CREATE TABLE client_benchmarks (
                client_id       INTEGER NOT NULL,
                platform        VARCHAR NOT NULL,
                median_roas     DOUBLE,
                median_cpc      DOUBLE,
                median_cac      DOUBLE,
                median_ctr      DOUBLE,
                median_spend    DOUBLE,
                uploads_seen    INTEGER NOT NULL DEFAULT 0,
                updated_at      TIMESTAMP NOT NULL DEFAULT now(),
                PRIMARY KEY (client_id, platform)
            )
        """)
        conn.commit()

    if "outcome_signals" not in existing:
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS outcome_signals_seq START 1;
            CREATE TABLE outcome_signals (
                id              INTEGER PRIMARY KEY DEFAULT nextval('outcome_signals_seq'),
                client_id       INTEGER NOT NULL,
                action_item_id  INTEGER NOT NULL,
                campaign        VARCHAR,
                platform        VARCHAR,
                metric          VARCHAR NOT NULL,
                value_before    DOUBLE NOT NULL,
                value_after     DOUBLE NOT NULL,
                improved        BOOLEAN NOT NULL,
                upload_id_before  INTEGER NOT NULL,
                upload_id_after   INTEGER NOT NULL,
                recorded_at     TIMESTAMP NOT NULL DEFAULT now()
            )
        """)
        conn.commit()


# ---- Benchmark update -------------------------------------------------------

def _update_benchmarks(conn: duckdb.DuckDBPyConnection,
                        client_id: int) -> None:
    """
    Recompute median ROAS, CPC, CAC, CTR, spend per platform
    from all campaigns ever uploaded for this client.
    Uses DuckDB's built-in median() aggregate.
    """
    rows = conn.execute("""
        SELECT
            platform,
            median(roas)   AS median_roas,
            median(cpc)    AS median_cpc,
            median(cac)    AS median_cac,
            median(ctr)    AS median_ctr,
            median(spend)  AS median_spend,
            COUNT(DISTINCT upload_id) AS uploads_seen
        FROM campaigns
        WHERE client_id = ?
          AND roas  IS NOT NULL
          AND spend > 0
        GROUP BY platform
    """, [client_id]).fetchall()

    for row in rows:
        platform, med_roas, med_cpc, med_cac, med_ctr, med_spend, uploads_seen = row

        existing = conn.execute(
            "SELECT 1 FROM client_benchmarks WHERE client_id = ? AND platform = ?",
            [client_id, platform]
        ).fetchone()

        if existing:
            conn.execute("""
                UPDATE client_benchmarks
                SET median_roas  = ?,
                    median_cpc   = ?,
                    median_cac   = ?,
                    median_ctr   = ?,
                    median_spend = ?,
                    uploads_seen = ?,
                    updated_at   = now()
                WHERE client_id = ? AND platform = ?
            """, [med_roas, med_cpc, med_cac, med_ctr, med_spend,
                  uploads_seen, client_id, platform])
        else:
            conn.execute("""
                INSERT INTO client_benchmarks
                    (client_id, platform, median_roas, median_cpc,
                     median_cac, median_ctr, median_spend, uploads_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [client_id, platform, med_roas, med_cpc,
                  med_cac, med_ctr, med_spend, uploads_seen])

    conn.commit()


# ---- Outcome tracking -------------------------------------------------------

def _track_outcomes(conn: duckdb.DuckDBPyConnection,
                    client_id: int,
                    upload_id: int) -> None:
    """
    For every action_item that:
    - belongs to this client
    - has been marked done
    - has a snapshot_upload_id (we know which upload it was recommended on)
    - has not already been evaluated (no existing outcome_signal for it)

    Find the same campaign in the new upload and compare the relevant metric.
    Record whether it improved.
    """
    done_items = conn.execute("""
        SELECT
            id, campaign, platform,
            snapshot_roas, snapshot_cac, snapshot_ctr, snapshot_spend,
            snapshot_upload_id
        FROM action_items
        WHERE client_id = ?
          AND status = 'done'
          AND campaign IS NOT NULL
          AND snapshot_upload_id IS NOT NULL
    """, [client_id]).fetchall()

    already_tracked = {
        r[0] for r in conn.execute(
            "SELECT action_item_id FROM outcome_signals WHERE client_id = ?",
            [client_id]
        ).fetchall()
    }

    for item in done_items:
        (item_id, campaign, platform,
         snap_roas, snap_cac, snap_ctr, snap_spend,
         snap_upload_id) = item

        if item_id in already_tracked:
            continue

        # Look for the same campaign in the new upload
        new_row = conn.execute("""
            SELECT roas, cac, ctr, spend
            FROM campaigns
            WHERE upload_id  = ?
              AND client_id  = ?
              AND platform   = ?
              AND campaign_name = ?
            LIMIT 1
        """, [upload_id, client_id, platform or "", campaign]).fetchone()

        if not new_row:
            continue

        new_roas, new_cac, new_ctr, new_spend = new_row

        # Evaluate each metric that has a snapshot value
        checks = [
            ("roas", snap_roas, new_roas, True),   # higher is better
            ("cac",  snap_cac,  new_cac,  False),  # lower is better
            ("ctr",  snap_ctr,  new_ctr,  True),   # higher is better
        ]

        for metric, before, after, higher_is_better in checks:
            if before is None or after is None:
                continue
            if higher_is_better:
                improved = after > before
            else:
                improved = after < before

            conn.execute("""
                INSERT INTO outcome_signals
                    (client_id, action_item_id, campaign, platform,
                     metric, value_before, value_after, improved,
                     upload_id_before, upload_id_after)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [client_id, item_id, campaign, platform,
                  metric, before, after, improved,
                  snap_upload_id, upload_id])

    conn.commit()


# ---- Public API -------------------------------------------------------------

def run_learning(conn: duckdb.DuckDBPyConnection,
                 client_id: int,
                 upload_id: int) -> None:
    """
    Entry point called from app.py after every upload.
    Silent -- never raises, logs errors without crashing the upload flow.
    """
    try:
        _ensure_tables(conn)
        _update_benchmarks(conn, client_id)
        _track_outcomes(conn, client_id, upload_id)
    except Exception as e:
        # Learning is non-critical. Log but never break the upload.
        import traceback
        print(f"[learning] non-fatal error for client {client_id}: {e}")
        traceback.print_exc()


def get_benchmarks(conn: duckdb.DuckDBPyConnection,
                   client_id: int) -> Optional[str]:
    """
    Returns a formatted context string for injection into the audit prompt.
    Returns None if fewer than 2 uploads exist (not enough history to be meaningful).
    """
    rows = conn.execute("""
        SELECT platform, median_roas, median_cpc, median_cac,
               median_ctr, median_spend, uploads_seen
        FROM client_benchmarks
        WHERE client_id = ?
          AND uploads_seen >= 2
        ORDER BY platform
    """, [client_id]).fetchall()

    if not rows:
        return None

    lines = [
        "Account history (current state — this client's own performance baseline):",
        "  These reflect where this account actually is. Industry benchmarks above show where it should be heading.",
        "  Flag campaigns that underperform vs this client's own norms, AND note the gap to industry targets.",
    ]
    for row in rows:
        platform, med_roas, med_cpc, med_cac, med_ctr, med_spend, uploads_seen = row
        parts = [f"  {platform.upper()} ({uploads_seen} uploads):"]
        if med_roas  is not None: parts.append(f"median ROAS {med_roas:.2f}x")
        if med_cpc   is not None: parts.append(f"median CPC {med_cpc:.0f}")
        if med_cac   is not None: parts.append(f"median CAC {med_cac:.0f}")
        if med_ctr   is not None: parts.append(f"median CTR {med_ctr:.2f}%")
        lines.append(", ".join(parts))
    return "\n".join(lines)
