"""
Bulk Client Reporting.
Runs audit + narrative for every client with at least one upload, sequentially.
Generates a multi-client summary HTML report.
Progress is tracked in a module-level dict for SSE polling.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from .db import _q, _q1, get_connection, get_reports, get_anomaly_summary
from .narrator import generate_narrative
from .anomaly_detector import detect_anomalies, format_morning_brief

REPORTS_DIR = Path(__file__).parent.parent / "reports"

# ---- Progress state (per run) ----
_bulk_state: dict = {
    "running":   False,
    "total":     0,
    "current":   0,
    "current_client": "",
    "results":   [],
    "error":     None,
    "report_url": None,
    "started_at": None,
    "finished_at": None,
}


def get_bulk_status() -> dict:
    return dict(_bulk_state)


def _reset_state():
    _bulk_state.update({
        "running":   True,
        "total":     0,
        "current":   0,
        "current_client": "",
        "results":   [],
        "error":     None,
        "report_url": None,
        "started_at": datetime.now().isoformat(),
        "finished_at": None,
    })


def _get_clients_with_uploads(conn) -> list[dict]:
    """Return all clients that have at least one upload, with their latest upload."""
    return _q(conn, """
        SELECT
            c.id AS client_id,
            c.name AS client_name,
            c.currency,
            c.context,
            u.id AS upload_id,
            u.period_label,
            u.period_start,
            COALESCE(u.period_label, CAST(COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS VARCHAR)) AS period,
            SUM(camp.spend)            AS total_spend,
            SUM(camp.conversion_value) AS total_revenue,
            CASE WHEN SUM(camp.spend) > 0
                 THEN SUM(camp.conversion_value) / SUM(camp.spend) ELSE 0 END AS blended_roas,
            COUNT(DISTINCT camp.campaign_name) AS campaign_count
        FROM clients c
        JOIN uploads u ON u.client_id = c.id
        JOIN campaigns camp ON camp.upload_id = u.id
        WHERE u.id = (
            SELECT id FROM uploads u2
            WHERE u2.client_id = c.id
            ORDER BY COALESCE(u2.period_start, CAST(u2.uploaded_at AS DATE)) DESC
            LIMIT 1
        )
        GROUP BY c.id, c.name, c.currency, c.context, u.id, u.period_label, u.period_start, u.uploaded_at
        ORDER BY c.name
    """, [])


def _get_open_anomalies_count(conn, client_id: int) -> int:
    row = _q1(conn, """
        SELECT COUNT(*) AS cnt FROM anomalies
        WHERE client_id = ? AND status = 'open'
    """, [client_id])
    return row["cnt"] if row else 0


def _budget_health_label(conn, client_id: int, blended_roas: float) -> str:
    rules = _q1(conn, """
        SELECT google_min_roas, meta_min_roas FROM budget_rules WHERE client_id = ?
    """, [client_id]) or {}
    min_roas = min(
        filter(None, [rules.get("google_min_roas"), rules.get("meta_min_roas")])
    ) if rules else 2.0
    if blended_roas >= min_roas * 1.2:
        return "Good"
    if blended_roas >= min_roas * 0.8:
        return "Warning"
    return "Critical"


def _generate_summary_html(results: list[dict], agency_name: str = "") -> str:
    """Build the multi-client summary HTML report."""
    now_str = datetime.now().strftime("%d %b %Y %H:%M")
    health_color = {"Good": "#48bb78", "Warning": "#ecc94b", "Critical": "#e53e3e"}

    rows_html = ""
    for r in results:
        if r.get("status") == "error":
            rows_html += f"""
            <tr>
              <td>{r['client_name']}</td>
              <td colspan="6" style="color:#e53e3e;font-size:12px;">Error: {r.get('error','')}</td>
            </tr>"""
            continue
        h_color = health_color.get(r.get("budget_health", "Warning"), "#ecc94b")
        roas = r.get("blended_roas", 0)
        spend = r.get("total_spend", 0)
        cur = r.get("currency", "")
        anomalies = r.get("open_anomalies", 0)
        report_link = r.get("report_url", "")
        rows_html += f"""
        <tr>
          <td><strong>{r['client_name']}</strong></td>
          <td>{r.get('period','')}</td>
          <td>{roas:.2f}x</td>
          <td>{cur} {spend:,.0f}</td>
          <td style="color:{'#e53e3e' if anomalies > 0 else '#48bb78'};">{anomalies}</td>
          <td><span style="color:{h_color};font-weight:600;">{r.get('budget_health','')}</span></td>
          <td>{f'<a href="{report_link}" target="_blank">View Report</a>' if report_link else '-'}</td>
        </tr>"""

    good   = sum(1 for r in results if r.get("budget_health") == "Good")
    warn   = sum(1 for r in results if r.get("budget_health") == "Warning")
    crit   = sum(1 for r in results if r.get("budget_health") == "Critical")
    errors = sum(1 for r in results if r.get("status") == "error")

    header = agency_name or "Ads OS"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Multi-Client Summary Report - {now_str}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f8fafc; color: #1a202c; }}
  .header {{ background: linear-gradient(135deg,#667eea 0%,#764ba2 100%); color: #fff; padding: 32px 40px; }}
  .header h1 {{ font-size: 24px; font-weight: 700; }}
  .header p {{ font-size: 14px; opacity: .8; margin-top: 4px; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 32px 24px; }}
  .summary-pills {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 28px; }}
  .pill {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px 20px; text-align: center; min-width: 120px; }}
  .pill-val {{ font-size: 24px; font-weight: 700; }}
  .pill-lbl {{ font-size: 11px; color: #718096; margin-top: 2px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  th {{ background: #f7fafc; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #718096; padding: 12px 16px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
  td {{ padding: 12px 16px; font-size: 13px; border-bottom: 1px solid #f0f4f8; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f7fafc; }}
  a {{ color: #667eea; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .footer {{ text-align: center; color: #a0aec0; font-size: 12px; padding: 24px; }}
</style>
</head>
<body>
<div class="header">
  <h1>{header} - Multi-Client Summary</h1>
  <p>Generated {now_str} &bull; {len(results)} clients</p>
</div>
<div class="container">
  <div class="summary-pills">
    <div class="pill"><div class="pill-val">{len(results)}</div><div class="pill-lbl">Total Clients</div></div>
    <div class="pill"><div class="pill-val" style="color:#48bb78">{good}</div><div class="pill-lbl">Good Health</div></div>
    <div class="pill"><div class="pill-val" style="color:#ecc94b">{warn}</div><div class="pill-lbl">Warning</div></div>
    <div class="pill"><div class="pill-val" style="color:#e53e3e">{crit}</div><div class="pill-lbl">Critical</div></div>
    {f'<div class="pill"><div class="pill-val" style="color:#718096">{errors}</div><div class="pill-lbl">Errors</div></div>' if errors else ''}
  </div>
  <table>
    <thead>
      <tr>
        <th>Client</th><th>Period</th><th>Blended ROAS</th>
        <th>Total Spend</th><th>Open Anomalies</th>
        <th>Budget Health</th><th>Report</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
<div class="footer">Generated by {header} &bull; Powered by Ads OS &bull; {now_str}</div>
</body>
</html>"""


def run_bulk_report_sync(agency_name: str = "") -> None:
    """
    Runs sequentially in a background thread.
    Connects to DB independently (not using Flask g).
    """
    _reset_state()
    conn = None
    try:
        conn = get_connection()
        clients = _get_clients_with_uploads(conn)
        _bulk_state["total"] = len(clients)

        if not clients:
            _bulk_state["error"] = "No clients with uploads found."
            _bulk_state["running"] = False
            return

        results = []

        for i, client in enumerate(clients):
            _bulk_state["current"] = i + 1
            _bulk_state["current_client"] = client["client_name"]

            row = {
                "client_name":   client["client_name"],
                "client_id":     client["client_id"],
                "currency":      client.get("currency", "INR"),
                "period":        client.get("period", ""),
                "total_spend":   float(client.get("total_spend") or 0),
                "total_revenue": float(client.get("total_revenue") or 0),
                "blended_roas":  float(client.get("blended_roas") or 0),
                "campaign_count":int(client.get("campaign_count") or 0),
                "open_anomalies": _get_open_anomalies_count(conn, client["client_id"]),
                "budget_health": _budget_health_label(conn, client["client_id"],
                                                      float(client.get("blended_roas") or 0)),
                "report_url":    None,
                "status":        "ok",
            }

            # Try to get the latest report URL for this client
            try:
                latest_report = _q1(conn, """
                    SELECT html_path FROM reports
                    WHERE client_id = ? AND report_type = 'audit'
                    ORDER BY created_at DESC LIMIT 1
                """, [client["client_id"]])
                if latest_report and latest_report.get("html_path"):
                    p = Path(latest_report["html_path"])
                    row["report_url"] = f"/report/{p.name}"
            except Exception:
                pass

            results.append(row)

        _bulk_state["results"] = results

        # Build summary HTML
        html = _generate_summary_html(results, agency_name)
        filename = f"multi_client_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        out_path = REPORTS_DIR / filename
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")

        _bulk_state["report_url"] = f"/report/{filename}"
        _bulk_state["finished_at"] = datetime.now().isoformat()

    except Exception as e:
        _bulk_state["error"] = str(e)
    finally:
        _bulk_state["running"] = False
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def start_bulk_report(agency_name: str = "") -> None:
    """Start bulk report in background thread. Non-blocking."""
    if _bulk_state.get("running"):
        return
    t = threading.Thread(target=run_bulk_report_sync, args=(agency_name,), daemon=True)
    t.start()
