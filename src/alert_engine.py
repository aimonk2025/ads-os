"""
KPI Alert Engine.
Triggered after every CSV upload. Checks campaigns against client thresholds.
Stores alerts in kpi_alerts table. Severity: high/medium/low.
Alert types: roas_below, cpa_spike, cpl_spike, spend_overpace, spend_underpace,
             ctr_drop, cvr_drop, zero_conversions.
"""

import json
from datetime import datetime
from .db import _q, _q1


SEVERITY_MAP = {
    "roas_below":       lambda pct: "high" if pct < -30 else "medium",
    "cpa_spike":        lambda pct: "high" if pct > 50  else "medium",
    "cpl_spike":        lambda pct: "high" if pct > 50  else "medium",
    "spend_overpace":   lambda pct: "high" if pct > 30  else "medium",
    "spend_underpace":  lambda pct: "medium",
    "ctr_drop":         lambda pct: "high" if pct < -40 else "medium",
    "cvr_drop":         lambda pct: "high" if pct < -40 else "medium",
    "zero_conversions": lambda pct: "high",
}


def _pct_change(current, baseline):
    if not baseline:
        return 0.0
    return round((current - baseline) / abs(baseline) * 100, 1)


def _get_rules(conn, client_id: int) -> dict:
    row = _q1(conn, """
        SELECT google_min_roas, meta_min_roas, google_min_cpl, meta_min_cpl
        FROM budget_rules WHERE client_id = ?
    """, [client_id]) or {}
    return row


def _get_prior_upload(conn, client_id: int, current_upload_id: int):
    """Get the upload just before the current one for baseline comparison."""
    return _q1(conn, """
        SELECT id FROM uploads
        WHERE client_id = ? AND id != ?
        ORDER BY COALESCE(period_start, CAST(uploaded_at AS DATE)) DESC
        LIMIT 1
    """, [client_id, current_upload_id])


def _get_campaign_metrics(conn, upload_id: int, client_id: int) -> list[dict]:
    return _q(conn, """
        SELECT campaign_name, platform, spend, conversions, conversion_value,
               roas, cac, ctr, cpc, impressions, clicks
        FROM campaigns
        WHERE upload_id = ? AND client_id = ?
    """, [upload_id, client_id])


def run_alert_checks(conn, client_id: int, upload_id: int) -> list[dict]:
    """
    Run all alert checks for a new upload.
    Returns list of alert dicts (not yet persisted).
    """
    rules    = _get_rules(conn, client_id)
    current  = _get_campaign_metrics(conn, upload_id, client_id)
    prior_up = _get_prior_upload(conn, client_id, upload_id)
    prior    = _get_campaign_metrics(conn, prior_up["id"], client_id) if prior_up else []

    # Index prior by (campaign_name, platform)
    prior_map = {(r["campaign_name"], r["platform"]): r for r in prior}

    alerts = []

    for camp in current:
        name     = camp["campaign_name"]
        platform = (camp.get("platform") or "").lower()
        spend    = camp.get("spend") or 0
        conv     = camp.get("conversions") or 0
        roas     = camp.get("roas") or 0
        cac      = camp.get("cac") or 0
        ctr      = camp.get("ctr") or 0
        impr     = camp.get("impressions") or 0
        clicks   = camp.get("clicks") or 0

        prior_c  = prior_map.get((name, camp.get("platform")), {})
        p_spend  = prior_c.get("spend") or 0
        p_conv   = prior_c.get("conversions") or 0
        p_roas   = prior_c.get("roas") or 0
        p_ctr    = prior_c.get("ctr") or 0

        # Conversion rate (CVR = conv / clicks)
        cvr   = conv / clicks * 100 if clicks else 0
        p_cvr = (prior_c.get("conversions") or 0) / (prior_c.get("clicks") or 1) * 100

        # 1. ROAS below target
        min_roas = rules.get(f"{platform}_min_roas") or rules.get("google_min_roas") or 2.0
        if spend > 50 and roas > 0 and roas < min_roas:
            pct = _pct_change(roas, min_roas)
            alerts.append({
                "client_id":    client_id,
                "upload_id":    upload_id,
                "campaign":     name,
                "platform":     platform,
                "alert_type":   "roas_below",
                "threshold":    min_roas,
                "actual_value": round(roas, 2),
                "deviation_pct": pct,
                "severity":     SEVERITY_MAP["roas_below"](pct),
                "message":      f"ROAS {roas:.2f}x below target {min_roas}x",
            })

        # 2. Zero conversions with significant spend
        if spend > 100 and conv == 0:
            alerts.append({
                "client_id":    client_id,
                "upload_id":    upload_id,
                "campaign":     name,
                "platform":     platform,
                "alert_type":   "zero_conversions",
                "threshold":    1,
                "actual_value": 0,
                "deviation_pct": -100.0,
                "severity":     "high",
                "message":      f"Zero conversions with {spend:,.0f} spend",
            })

        # 3. CPA spike vs prior period
        min_cpl = rules.get(f"{platform}_min_cpl")
        if min_cpl and cac > 0 and cac > min_cpl * 1.3:
            pct = _pct_change(cac, min_cpl)
            alerts.append({
                "client_id":    client_id,
                "upload_id":    upload_id,
                "campaign":     name,
                "platform":     platform,
                "alert_type":   "cpa_spike",
                "threshold":    min_cpl,
                "actual_value": round(cac, 2),
                "deviation_pct": pct,
                "severity":     SEVERITY_MAP["cpa_spike"](pct),
                "message":      f"CPA {cac:,.0f} exceeds ceiling {min_cpl:,.0f}",
            })
        elif prior_c and cac > 0 and p_conv > 0:
            p_cac = prior_c.get("cac") or 0
            if p_cac > 0:
                pct = _pct_change(cac, p_cac)
                if pct > 30:
                    alerts.append({
                        "client_id":    client_id,
                        "upload_id":    upload_id,
                        "campaign":     name,
                        "platform":     platform,
                        "alert_type":   "cpa_spike",
                        "threshold":    round(p_cac, 2),
                        "actual_value": round(cac, 2),
                        "deviation_pct": pct,
                        "severity":     SEVERITY_MAP["cpa_spike"](pct),
                        "message":      f"CPA up {pct:.0f}% vs prior period ({cac:,.0f} vs {p_cac:,.0f})",
                    })

        # 4. CTR drop vs prior period
        if prior_c and ctr > 0 and p_ctr > 0:
            pct = _pct_change(ctr, p_ctr)
            if pct < -20:
                alerts.append({
                    "client_id":    client_id,
                    "upload_id":    upload_id,
                    "campaign":     name,
                    "platform":     platform,
                    "alert_type":   "ctr_drop",
                    "threshold":    round(p_ctr, 2),
                    "actual_value": round(ctr, 2),
                    "deviation_pct": pct,
                    "severity":     SEVERITY_MAP["ctr_drop"](pct),
                    "message":      f"CTR dropped {abs(pct):.0f}% ({ctr:.2f}% vs {p_ctr:.2f}%)",
                })

        # 5. CVR drop vs prior period
        if prior_c and cvr > 0 and p_cvr > 0:
            pct = _pct_change(cvr, p_cvr)
            if pct < -25:
                alerts.append({
                    "client_id":    client_id,
                    "upload_id":    upload_id,
                    "campaign":     name,
                    "platform":     platform,
                    "alert_type":   "cvr_drop",
                    "threshold":    round(p_cvr, 2),
                    "actual_value": round(cvr, 2),
                    "deviation_pct": pct,
                    "severity":     SEVERITY_MAP["cvr_drop"](pct),
                    "message":      f"CVR dropped {abs(pct):.0f}% ({cvr:.2f}% vs {p_cvr:.2f}%)",
                })

        # 6. Spend pacing vs prior period
        if prior_c and p_spend > 0:
            pct = _pct_change(spend, p_spend)
            if pct > 40:
                alerts.append({
                    "client_id":    client_id,
                    "upload_id":    upload_id,
                    "campaign":     name,
                    "platform":     platform,
                    "alert_type":   "spend_overpace",
                    "threshold":    round(p_spend, 2),
                    "actual_value": round(spend, 2),
                    "deviation_pct": pct,
                    "severity":     SEVERITY_MAP["spend_overpace"](pct),
                    "message":      f"Spend up {pct:.0f}% vs prior ({spend:,.0f} vs {p_spend:,.0f})",
                })
            elif pct < -40 and spend > 0:
                alerts.append({
                    "client_id":    client_id,
                    "upload_id":    upload_id,
                    "campaign":     name,
                    "platform":     platform,
                    "alert_type":   "spend_underpace",
                    "threshold":    round(p_spend, 2),
                    "actual_value": round(spend, 2),
                    "deviation_pct": pct,
                    "severity":     "medium",
                    "message":      f"Spend down {abs(pct):.0f}% vs prior ({spend:,.0f} vs {p_spend:,.0f})",
                })

    return alerts


def persist_alerts(conn, alerts: list[dict]) -> int:
    """Insert alerts into kpi_alerts table. Returns count inserted."""
    if not alerts:
        return 0
    count = 0
    for a in alerts:
        conn.execute("""
            INSERT INTO kpi_alerts (
                client_id, upload_id, campaign, platform, alert_type,
                threshold, actual_value, deviation_pct, severity, message, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, [
            a["client_id"], a["upload_id"], a["campaign"], a["platform"],
            a["alert_type"], a["threshold"], a["actual_value"],
            a["deviation_pct"], a["severity"], a["message"], "new",
        ])
        count += 1
    conn.commit()
    return count


def trigger_alerts_for_upload(conn, client_id: int, upload_id: int) -> int:
    """Full pipeline: run checks + persist. Returns alert count."""
    alerts = run_alert_checks(conn, client_id, upload_id)
    return persist_alerts(conn, alerts)


def get_alerts(conn, client_id: int = None, status: str = None,
               limit: int = 100) -> list[dict]:
    conditions = []
    params = []
    if client_id:
        conditions.append("client_id = ?")
        params.append(client_id)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    return _q(conn, f"""
        SELECT a.*, c.name AS client_name
        FROM kpi_alerts a
        LEFT JOIN clients c ON a.client_id = c.id
        {where}
        ORDER BY
            CASE a.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            a.created_at DESC
        LIMIT ?
    """, params)


def get_unread_count(conn) -> int:
    """Total new alerts across all clients."""
    row = _q1(conn, "SELECT COUNT(*) AS cnt FROM kpi_alerts WHERE status = 'new'", [])
    return row["cnt"] if row else 0


def update_alert_status(conn, alert_id: int, status: str) -> None:
    conn.execute("UPDATE kpi_alerts SET status = ? WHERE id = ?", [status, alert_id])
    conn.commit()


def dismiss_all_for_client(conn, client_id: int) -> None:
    conn.execute(
        "UPDATE kpi_alerts SET status = 'dismissed' WHERE client_id = ? AND status = 'new'",
        [client_id]
    )
    conn.commit()
