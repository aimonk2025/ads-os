"""
Executive Dashboard data module.
Queries DuckDB to build KPI cards, trend data, and channel splits
from the campaigns, uploads, funnel_data tables.
"""

from .db import _q, _q1


def get_dashboard_data(conn, client_id: int, date_range: str = "30") -> dict:
    """
    Build the full dashboard payload for a client.
    date_range: "7", "30", "90", or "all"
    Returns a dict with kpis, trend, channel_split, platform_compare, funnel_summary.
    """
    # Build date filter
    if date_range == "7":
        date_filter = "AND COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) >= CURRENT_DATE - INTERVAL 7 DAY"
    elif date_range == "30":
        date_filter = "AND COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) >= CURRENT_DATE - INTERVAL 30 DAY"
    elif date_range == "90":
        date_filter = "AND COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) >= CURRENT_DATE - INTERVAL 90 DAY"
    else:
        date_filter = ""

    # --- KPI Summary ---
    kpi = _q1(conn, f"""
        SELECT
            SUM(c.spend)                                    AS total_spend,
            SUM(c.conversions)                              AS total_conversions,
            SUM(c.conversion_value)                         AS total_revenue,
            CASE WHEN SUM(c.spend) > 0
                 THEN SUM(c.conversion_value) / SUM(c.spend)
                 ELSE 0 END                                 AS blended_roas,
            CASE WHEN SUM(c.conversions) > 0
                 THEN SUM(c.spend) / SUM(c.conversions)
                 ELSE 0 END                                 AS cpa,
            AVG(c.ctr)                                      AS avg_ctr,
            AVG(c.cpc)                                      AS avg_cpc,
            COUNT(DISTINCT c.campaign_name)                 AS campaign_count,
            COUNT(DISTINCT u.id)                            AS upload_count
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          {date_filter}
    """, [client_id]) or {}

    # MER (marketing efficiency ratio) = revenue / total spend (same as blended ROAS here)
    total_spend = kpi.get("total_spend") or 0
    total_revenue = kpi.get("total_revenue") or 0
    kpi["mer"] = round(total_revenue / total_spend, 2) if total_spend else 0

    # --- Prior period KPIs for delta calculation ---
    if date_range in ("7", "30", "90"):
        days = int(date_range)
        prior_filter = f"""
            AND COALESCE(u.period_start, CAST(u.uploaded_at AS DATE))
                BETWEEN CURRENT_DATE - INTERVAL {days * 2} DAY
                    AND CURRENT_DATE - INTERVAL {days} DAY - INTERVAL 1 DAY
        """
        prior = _q1(conn, f"""
            SELECT
                SUM(c.spend)         AS total_spend,
                SUM(c.conversions)   AS total_conversions,
                SUM(c.conversion_value) AS total_revenue,
                CASE WHEN SUM(c.spend) > 0
                     THEN SUM(c.conversion_value) / SUM(c.spend)
                     ELSE 0 END      AS blended_roas,
                CASE WHEN SUM(c.conversions) > 0
                     THEN SUM(c.spend) / SUM(c.conversions)
                     ELSE 0 END      AS cpa
            FROM campaigns c
            JOIN uploads u ON c.upload_id = u.id
            WHERE c.client_id = ?
              {prior_filter}
        """, [client_id]) or {}
    else:
        prior = {}

    def pct_delta(current, prev):
        if prev and prev != 0:
            return round((current - prev) / abs(prev) * 100, 1)
        return None

    deltas = {
        "spend_delta":       pct_delta(kpi.get("total_spend") or 0, prior.get("total_spend")),
        "revenue_delta":     pct_delta(kpi.get("total_revenue") or 0, prior.get("total_revenue")),
        "conversions_delta": pct_delta(kpi.get("total_conversions") or 0, prior.get("total_conversions")),
        "roas_delta":        pct_delta(kpi.get("blended_roas") or 0, prior.get("blended_roas")),
        "cpa_delta":         pct_delta(kpi.get("cpa") or 0, prior.get("cpa")),
    }

    # --- Trend: spend and revenue per upload period ---
    trend = _q(conn, f"""
        SELECT
            COALESCE(u.period_label, CAST(COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS VARCHAR)) AS label,
            COALESCE(u.period_start, CAST(u.uploaded_at AS DATE))  AS period_date,
            SUM(c.spend)              AS spend,
            SUM(c.conversion_value)   AS revenue,
            SUM(c.conversions)        AS conversions,
            CASE WHEN SUM(c.spend) > 0
                 THEN SUM(c.conversion_value) / SUM(c.spend)
                 ELSE 0 END           AS roas
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          {date_filter}
        GROUP BY u.id, u.period_label, u.period_start, u.uploaded_at
        ORDER BY period_date ASC
    """, [client_id])

    # --- Channel split: spend and ROAS by platform ---
    channel_split = _q(conn, f"""
        SELECT
            c.platform,
            SUM(c.spend)            AS spend,
            SUM(c.conversions)      AS conversions,
            SUM(c.conversion_value) AS revenue,
            CASE WHEN SUM(c.spend) > 0
                 THEN SUM(c.conversion_value) / SUM(c.spend)
                 ELSE 0 END         AS roas,
            AVG(c.ctr)              AS avg_ctr,
            AVG(c.cpc)              AS avg_cpc
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          {date_filter}
        GROUP BY c.platform
        ORDER BY spend DESC
    """, [client_id])

    # --- Top campaigns by spend ---
    top_campaigns = _q(conn, f"""
        SELECT
            c.campaign_name,
            c.platform,
            SUM(c.spend)            AS spend,
            SUM(c.conversions)      AS conversions,
            SUM(c.conversion_value) AS revenue,
            CASE WHEN SUM(c.spend) > 0
                 THEN SUM(c.conversion_value) / SUM(c.spend)
                 ELSE 0 END         AS roas,
            CASE WHEN SUM(c.conversions) > 0
                 THEN SUM(c.spend) / SUM(c.conversions)
                 ELSE 0 END         AS cpa,
            AVG(c.ctr)              AS ctr
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
          {date_filter}
        GROUP BY c.campaign_name, c.platform
        ORDER BY spend DESC
        LIMIT 10
    """, [client_id])

    # --- Funnel summary ---
    funnel = _q1(conn, f"""
        SELECT
            SUM(fd.leads)                AS total_leads,
            SUM(fd.mqls)                 AS total_mqls,
            SUM(fd.sqls)                 AS total_sqls,
            SUM(fd.customers)            AS total_customers,
            AVG(fd.cost_per_lead)        AS avg_cpl,
            AVG(fd.cost_per_customer)    AS avg_cac
        FROM funnel_data fd
        JOIN uploads u ON fd.upload_id = u.id
        WHERE fd.client_id = ?
          {date_filter}
    """, [client_id]) or {}

    # --- P&L waterfall (best effort from available data) ---
    pl = {
        "total_spend":    round(kpi.get("total_spend") or 0, 2),
        "total_revenue":  round(kpi.get("total_revenue") or 0, 2),
        "gross_profit":   round((kpi.get("total_revenue") or 0) - (kpi.get("total_spend") or 0), 2),
    }
    if pl["total_revenue"] > 0:
        pl["gross_margin_pct"] = round(pl["gross_profit"] / pl["total_revenue"] * 100, 1)
    else:
        pl["gross_margin_pct"] = 0

    return {
        "kpi":            _clean(kpi),
        "deltas":         deltas,
        "trend":          [_clean(r) for r in trend],
        "channel_split":  [_clean(r) for r in channel_split],
        "top_campaigns":  [_clean(r) for r in top_campaigns],
        "funnel":         _clean(funnel),
        "pl":             pl,
        "date_range":     date_range,
    }


def _clean(d: dict) -> dict:
    """Round floats, replace None with 0 for numeric fields."""
    if not d:
        return {}
    out = {}
    for k, v in d.items():
        if isinstance(v, float):
            out[k] = round(v, 2)
        elif v is None:
            out[k] = 0
        else:
            out[k] = v
    return out
