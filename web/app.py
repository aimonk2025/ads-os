"""
Ad Audit Web UI - Complete Flask app
Run: python web/app.py
Opens at: http://localhost:5000
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import logging
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ads-os")

import pandas as pd

from flask import Flask, render_template, request, jsonify, send_file, g, Response

from src.db import (
    get_connection, create_client, get_clients, get_client, update_client, delete_client,
    create_upload, get_uploads, get_upload, check_duplicate_upload, delete_upload,
    insert_campaigns, get_campaigns_for_upload, get_latest_upload_campaigns, get_platform_history,
    get_upload_timeline, get_campaign_timeline,
    insert_funnel_data, get_funnel_for_upload,
    insert_granular_rows, get_granular_rows,
    insert_anomalies, get_anomalies, update_anomaly_status, get_anomaly_summary,
    save_report, get_reports, get_report, update_report_pdf_path, delete_report,
    get_budget_rules, save_budget_rules,
    upsert_action_items, get_action_items, update_action_item, set_action_snapshot,
)
from src.detector import detect_platform_from_bytes
from src.cleaner import _fuzzy_match_columns, GOOGLE_COLUMN_VARIANTS, META_COLUMN_VARIANTS, FUNNEL_COLUMN_VARIANTS
from src.granularity import build_granular_insights, granularity_to_claude_context
from src.loader import load_google_ads_with_report, load_meta_ads_with_report, load_google_ads, load_meta_ads
from src.ga_loader import load_ga4, merge_ga4_into_campaigns, build_ga_summary
from src.context import format_client_context, parse_context_from_json, BUSINESS_TYPE_PROFILES
from src.calculator import calculate_google_metrics, calculate_meta_metrics, build_summary
from src.funnel_loader import load_funnel
from src.claude_client import analyze, analyze_stream, stream_prompt
from src.renderer import render_report
from src.anomaly_detector import detect_anomalies, format_morning_brief
from src.narrator import generate_narrative, stream_narrative
from src.budget_agent import run_budget_agent, stream_budget_agent, format_reallocation_table
from src.learning import run_learning, get_benchmarks
from src.bulk_splitter import split_bulk_file, df_to_tempfile, detect_client_column, match_or_create_client
from src.dashboard import get_dashboard_data
from src.copilot import ask_copilot
from src.forecaster import run_forecast, generate_forecast_narrative, stream_forecast_narrative
from src.structured_audit import run_structured_audit, generate_audit_summary, stream_audit_summary
from src.bulk_reporter import start_bulk_report, get_bulk_status
from src.alert_engine import (
    trigger_alerts_for_upload, get_alerts, get_unread_count,
    update_alert_status, dismiss_all_for_client,
)
from src.onboarding import (
    get_onboarding_status, save_step_data, generate_client_brief, STEPS,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
REPORTS_DIR = Path(__file__).parent.parent / "reports"
PDFS_DIR = Path(__file__).parent.parent / "reports" / "pdfs"
SAMPLE_DIR = Path(__file__).parent.parent / "sample_data"

for d in [UPLOAD_DIR, REPORTS_DIR, PDFS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ---- DB connection per request ----

def get_db():
    if "db" not in g:
        g.db = get_connection()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---- Helpers ----

def _save_upload_file(file, prefix: str) -> Path:
    suffix = Path(file.filename).suffix or ".csv"
    p = UPLOAD_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}{suffix}"
    file.save(str(p))
    return p


def _fmt_date(dt) -> str:
    if not dt:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.strftime("%d/%b/%Y %H:%M")


def _jsonify_dates(obj):
    if isinstance(obj, list):
        return [_jsonify_dates(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _fmt_date(v) if isinstance(v, datetime) else _jsonify_dates(v)
                for k, v in obj.items()}
    return obj


# ---- Page ----

@app.route("/")
def index():
    return render_template("app.html")


@app.route("/deck")
def deck():
    return render_template("carousel.html")


# ---- Clients ----

@app.post("/api/clients")
def api_create_client():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    try:
        client = create_client(get_db(), data["name"].strip(), data.get("currency", "INR"))
        return jsonify(_jsonify_dates(client))
    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": f"Client '{data['name']}' already exists"}), 409
        return jsonify({"error": str(e)}), 500


@app.get("/api/clients")
def api_list_clients():
    return jsonify(_jsonify_dates(get_clients(get_db())))


@app.patch("/api/clients/<int:client_id>")
def api_update_client(client_id):
    data = request.get_json()
    update_client(get_db(), client_id, data["name"], data.get("currency", "INR"), data.get("context"))
    return jsonify({"ok": True})


@app.delete("/api/clients/<int:client_id>")
def api_delete_client(client_id):
    delete_client(get_db(), client_id)
    return jsonify({"ok": True})


# ---- Dashboard ----

@app.get("/api/dashboard/<int:client_id>")
def api_dashboard(client_id):
    date_range = request.args.get("range", "30")
    if date_range not in ("7", "30", "90", "all"):
        date_range = "30"
    try:
        data = get_dashboard_data(get_db(), client_id, date_range)
        return jsonify(data)
    except Exception as e:
        logger.exception("Dashboard error")
        return jsonify({"error": str(e)}), 500


# ---- Copilot ----

@app.post("/api/copilot/chat")
def api_copilot_chat():
    data = request.get_json()
    client_id = data.get("client_id")
    question = (data.get("question") or "").strip()
    history = data.get("history") or []

    if not client_id:
        return jsonify({"error": "client_id is required"}), 400
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        result = ask_copilot(get_db(), int(client_id), question, history)
        return jsonify(result)
    except Exception as e:
        logger.exception("Copilot error")
        return jsonify({"error": str(e)}), 500


# ---- Forecast ----

@app.get("/api/forecast/<int:client_id>")
def api_forecast(client_id):
    horizon = int(request.args.get("horizon", 30))
    if horizon not in (7, 30, 60):
        horizon = 30
    try:
        conn = get_db()
        forecast = run_forecast(conn, client_id, horizon)
        if "error" in forecast:
            return jsonify(forecast), 400

        # Save to forecasts table
        conn.execute("""
            INSERT INTO forecasts (
                client_id, horizon_days, proj_spend, proj_revenue, proj_roas,
                proj_conversions, spend_trend, roas_trend, season_factor,
                periods_used, campaign_data
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, [
            client_id,
            forecast["account"]["horizon_days"],
            forecast["account"]["proj_spend"],
            forecast["account"]["proj_revenue"],
            forecast["account"]["proj_roas"],
            forecast["account"]["proj_conversions"],
            forecast["account"]["spend_trend"],
            forecast["account"]["roas_trend"],
            forecast["account"]["season_factor"],
            forecast["account"]["periods_used"],
            json.dumps(forecast["campaigns"]),
        ])
        conn.commit()

        # Get client info for narrative
        client = get_client(conn, client_id) or {}
        raw_context = (client.get("context") or "")
        business_context = format_client_context(parse_context_from_json(raw_context))
        narrative = generate_forecast_narrative(
            forecast, client.get("name", "Client"), client.get("currency", "INR"),
            business_context=business_context
        )
        forecast["narrative"] = narrative
        return jsonify(forecast)
    except Exception as e:
        logger.exception("Forecast error")
        return jsonify({"error": str(e)}), 500


@app.get("/api/forecast/stream/<int:client_id>")
def api_forecast_stream(client_id):
    horizon = int(request.args.get("horizon", 30))
    if horizon not in (7, 30, 60):
        horizon = 30

    # Do all DB work inside the request context before the generator starts
    try:
        conn = get_db()
        forecast = run_forecast(conn, client_id, horizon)
        if "error" in forecast:
            return jsonify(forecast), 400

        conn.execute("""
            INSERT INTO forecasts (
                client_id, horizon_days, proj_spend, proj_revenue, proj_roas,
                proj_conversions, spend_trend, roas_trend, season_factor,
                periods_used, campaign_data
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, [
            client_id,
            forecast["account"]["horizon_days"],
            forecast["account"]["proj_spend"],
            forecast["account"]["proj_revenue"],
            forecast["account"]["proj_roas"],
            forecast["account"]["proj_conversions"],
            forecast["account"]["spend_trend"],
            forecast["account"]["roas_trend"],
            forecast["account"]["season_factor"],
            forecast["account"]["periods_used"],
            json.dumps(forecast["campaigns"]),
        ])
        conn.commit()

        client = get_client(conn, client_id) or {}
        raw_context = (client.get("context") or "")
        business_context = format_client_context(parse_context_from_json(raw_context))
        client_name = client.get("name", "Client")
        currency = client.get("currency", "INR")
    except Exception as e:
        logger.exception("Forecast stream setup error")
        return jsonify({"error": str(e)}), 500

    def generate():
        try:
            # Send forecast data immediately so UI can render charts
            yield f"data: {json.dumps({'type': 'forecast', 'data': forecast})}\n\n"

            # Stream the narrative
            for event_type, text in stream_forecast_narrative(forecast, client_name, currency, business_context):
                if event_type == 'chunk':
                    yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
                elif event_type in ('done', 'fallback'):
                    yield f"data: {json.dumps({'type': 'done', 'narrative': text})}\n\n"
                    return

        except Exception as e:
            logger.exception("Forecast stream error")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/structured-audit/stream")
def api_structured_audit_stream():
    data      = request.get_json()
    client_id = data.get("client_id")
    upload_id = data.get("upload_id")
    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    # Do all DB work inside the request context before the generator starts
    try:
        conn = get_db()
        result = run_structured_audit(conn, int(client_id), int(upload_id) if upload_id else None)
        if "error" in result:
            return jsonify(result), 400
        client_row = get_client(conn, int(client_id)) or {}
        raw_context = client_row.get("context", "") or ""
        business_context = format_client_context(parse_context_from_json(raw_context))
    except Exception as e:
        logger.exception("Structured audit stream setup error")
        return jsonify({"error": str(e)}), 500

    def generate():
        try:
            # Send audit data immediately so UI can render checks/scores
            yield f"data: {json.dumps({'type': 'audit', 'data': result})}\n\n"

            # Stream the summary
            for event_type, text in stream_audit_summary(result, business_context):
                if event_type == 'chunk':
                    yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
                elif event_type in ('done', 'fallback'):
                    yield f"data: {json.dumps({'type': 'done', 'summary': text})}\n\n"
                    return

        except Exception as e:
            logger.exception("Structured audit stream error")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- Bulk Report ----

@app.post("/api/bulk-report/run")
def api_bulk_report_run():
    data = request.get_json() or {}
    agency_name = (data.get("agency_name") or "").strip()
    if get_bulk_status().get("running"):
        return jsonify({"error": "Bulk report already running."}), 409
    start_bulk_report(agency_name)
    return jsonify({"ok": True, "message": "Bulk report started."})


@app.get("/api/bulk-report/status")
def api_bulk_report_status():
    return jsonify(get_bulk_status())


# ---- Structured Audit ----

@app.post("/api/structured-audit/run")
def api_structured_audit_run():
    data = request.get_json()
    client_id = data.get("client_id")
    upload_id = data.get("upload_id")
    if not client_id:
        return jsonify({"error": "client_id required"}), 400
    try:
        conn = get_db()
        result = run_structured_audit(conn, int(client_id), int(upload_id) if upload_id else None)
        if "error" in result:
            return jsonify(result), 400
        client_row = get_client(conn, int(client_id)) or {}
        raw_context = client_row.get("context", "") or ""
        business_context = format_client_context(parse_context_from_json(raw_context))
        summary = generate_audit_summary(result, business_context=business_context)
        result["summary"] = summary
        return jsonify(result)
    except Exception as e:
        logger.exception("Structured audit error")
        return jsonify({"error": str(e)}), 500


@app.get("/api/structured-audit/latest/<int:client_id>")
def api_structured_audit_latest(client_id):
    try:
        conn = get_db()
        row = _q1_local(conn, """
            SELECT id, overall_score, category_scores, checks_json,
                   recommendations_json, created_at, upload_id
            FROM structured_audits
            WHERE client_id = ?
            ORDER BY created_at DESC LIMIT 1
        """, [client_id])
        if not row:
            return jsonify({"error": "No structured audit found."}), 404
        row["category_scores"]  = json.loads(row["category_scores"] or "{}")
        row["checks"]           = json.loads(row["checks_json"] or "[]")
        row["recommendations"]  = json.loads(row["recommendations_json"] or "[]")
        return jsonify(row)
    except Exception as e:
        logger.exception("Structured audit latest error")
        return jsonify({"error": str(e)}), 500



# ---- KPI Alerts ----

@app.get("/api/alerts")
def api_alerts_list():
    client_id = request.args.get("client_id", type=int)
    status    = request.args.get("status")
    limit     = request.args.get("limit", 100, type=int)
    try:
        alerts = get_alerts(get_db(), client_id=client_id, status=status, limit=limit)
        unread = get_unread_count(get_db())
        return jsonify({"alerts": alerts, "unread_count": unread})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/alerts/unread-count")
def api_alerts_unread():
    try:
        return jsonify({"unread_count": get_unread_count(get_db())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/alerts/<int:alert_id>/status")
def api_alert_update_status(alert_id):
    data   = request.get_json()
    status = data.get("status")
    if status not in ("seen", "dismissed"):
        return jsonify({"error": "status must be 'seen' or 'dismissed'"}), 400
    try:
        update_alert_status(get_db(), alert_id, status)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/alerts/dismiss-all")
def api_alerts_dismiss_all():
    data      = request.get_json()
    client_id = data.get("client_id")
    if not client_id:
        return jsonify({"error": "client_id required"}), 400
    try:
        dismiss_all_for_client(get_db(), int(client_id))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---- Context ----

@app.get("/api/context/<int:client_id>")
def api_get_context(client_id):
    db = get_db()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    raw = client.get("context") or ""
    ctx = parse_context_from_json(raw)
    return jsonify({
        "context": ctx,
        "business_type_profiles": {k: {"label": v["label"], "show_fields": v["show_fields"]}
                                    for k, v in BUSINESS_TYPE_PROFILES.items()},
    })


@app.post("/api/branding/<int:client_id>")
def api_save_branding(client_id):
    """Save agency name and optional logo (base64) into the client context JSON."""
    import base64
    db = get_db()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404

    agency_name = request.form.get("agency_name", "").strip()
    logo_file = request.files.get("logo")

    raw = client.get("context") or ""
    ctx = parse_context_from_json(raw)
    ctx["agency_name"] = agency_name

    if logo_file and logo_file.filename:
        logo_bytes = logo_file.read()
        mime = logo_file.content_type or "image/png"
        ctx["agency_logo"] = f"data:{mime};base64,{base64.b64encode(logo_bytes).decode()}"
    elif request.form.get("clear_logo") == "1":
        ctx.pop("agency_logo", None)

    update_client(db, client_id, client["name"], client["currency"], json.dumps(ctx))
    return jsonify({"ok": True})


@app.post("/api/context/<int:client_id>")
def api_save_context(client_id):
    data = request.get_json()
    db = get_db()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404
    context_json = json.dumps(data) if data else None
    update_client(db, client_id, client["name"], client["currency"], context_json)
    return jsonify({"ok": True})


# ---- Platform Detection ----

@app.post("/api/detect-platform")
def api_detect_platform():
    """
    Peek at a CSV's header and return the detected platform.
    Accepts multipart/form-data with a 'file' field.
    Reads only the first 2KB - fast, no disk write needed.
    """
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400
    # Read only first 2KB for detection
    chunk = f.read(2048)
    result = detect_platform_from_bytes(chunk, f.filename or "")
    return jsonify(result)


# Canonical fields per platform for peek-columns endpoint
_PLATFORM_CANONICAL_FIELDS = {
    "google":  GOOGLE_COLUMN_VARIANTS,
    "meta":    META_COLUMN_VARIANTS,
    "funnel":  FUNNEL_COLUMN_VARIANTS,
    "ga4": {
        "campaign":            ["session campaign name", "session campaign", "campaign", "campaign name", "utm_campaign"],
        "sessions":            ["sessions"],
        "engaged_sessions":    ["engaged sessions"],
        "bounce_rate":         ["bounce rate"],
        "conversions":         ["conversions", "key events", "goal completions"],
        "revenue":             ["purchase revenue", "total revenue", "revenue", "ecommerce revenue"],
    },
}


@app.post("/api/peek-columns")
def api_peek_columns():
    """
    Read the first 5 rows of an uploaded file and return column info with auto-mapping.
    Accepts multipart/form-data with 'file' and optional 'platform' fields.
    """
    import chardet as _chardet

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file provided"}), 400

    platform = request.form.get("platform", "").lower().strip()

    # Save temp file
    suffix = Path(f.filename or "upload").suffix.lower() or ".csv"
    tmp_path = UPLOAD_DIR / f"peek_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}{suffix}"
    try:
        f.save(str(tmp_path))

        # Read first 5 rows
        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(str(tmp_path), nrows=5, engine="openpyxl")
        else:
            # Detect encoding
            try:
                with open(str(tmp_path), "rb") as fh:
                    raw = fh.read(32768)
                enc_result = _chardet.detect(raw)
                enc = enc_result.get("encoding") or "utf-8"
            except Exception:
                enc = "utf-8"

            df = None
            for enc_try in [enc, "utf-8-sig", "utf-8", "latin-1"]:
                try:
                    df = pd.read_csv(str(tmp_path), nrows=5, encoding=enc_try)
                    break
                except Exception:
                    continue
            if df is None:
                return jsonify({"error": "Could not read file"}), 400

        columns = list(df.columns)
        # Convert sample rows to JSON-safe dicts
        sample_rows = []
        for _, row in df.iterrows():
            sample_rows.append({
                col: ("" if pd.isna(v) else str(v))
                for col, v in row.items()
            })

        # Run auto-mapping using fuzzy matching
        auto_mapping = {}
        unmapped_canonical = []
        variants_map = _PLATFORM_CANONICAL_FIELDS.get(platform, {})

        if variants_map:
            # _fuzzy_match_columns returns {actual_col -> canonical}; we want {canonical -> actual_col} for auto_mapping
            remap, _ = _fuzzy_match_columns(
                [c.strip().lower() for c in columns],
                variants_map,
            )
            # remap keys are lowercased column names; map back to original case
            cols_lower_to_orig = {c.strip().lower(): c for c in columns}
            for lowered_actual, canonical in remap.items():
                orig = cols_lower_to_orig.get(lowered_actual, lowered_actual)
                auto_mapping[canonical] = orig

            # Also catch columns already matching canonical names exactly
            cols_lower_set = {c.strip().lower() for c in columns}
            for canonical in variants_map:
                if canonical not in auto_mapping and canonical in cols_lower_set:
                    orig = cols_lower_to_orig[canonical]
                    auto_mapping[canonical] = orig

            # Find unmapped canonical fields
            unmapped_canonical = [c for c in variants_map if c not in auto_mapping]

        return jsonify({
            "columns": columns,
            "sample_rows": sample_rows,
            "platform": platform,
            "auto_mapping": auto_mapping,
            "unmapped_canonical": unmapped_canonical,
        })

    except Exception as e:
        logger.exception("peek-columns error")
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---- Funnel Reconciliation ----

@app.post("/api/funnel-preview")
def api_funnel_preview():
    """
    Preview funnel CSV match against campaign names from the most recent upload.
    Returns: matched rows, unmatched funnel rows, unmatched campaigns.
    """
    client_id = request.form.get("client_id", type=int)
    upload_id = request.form.get("upload_id", type=int)
    f = request.files.get("funnel")
    if not f or not f.filename:
        return jsonify({"error": "No funnel file provided"}), 400
    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    suffix = Path(f.filename).suffix.lower() or ".csv"
    tmp_path = UPLOAD_DIR / f"funnel_preview_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}{suffix}"
    try:
        f.save(str(tmp_path))
        funnel_data = load_funnel(str(tmp_path))
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    db = get_db()
    if upload_id:
        campaigns = get_campaigns_for_upload(db, upload_id)
    else:
        campaigns = get_latest_upload_campaigns(db, client_id)

    campaign_names = list({c["campaign_name"] for c in campaigns})
    funnel_campaigns = list(funnel_data.get("campaign_data", {}).keys())

    # Match funnel rows to campaign names (same logic as merge_funnel_into_campaigns)
    matched = []
    unmatched_funnel = []
    matched_campaign_names = set()

    for fname in funnel_campaigns:
        fname_lower = fname.lower().strip()
        best = None
        for cname in campaign_names:
            cname_lower = cname.lower().strip()
            if fname_lower == cname_lower or fname_lower in cname_lower or cname_lower in fname_lower:
                best = cname
                break
        if best:
            matched.append({"funnel_name": fname, "campaign_name": best})
            matched_campaign_names.add(best)
        else:
            unmatched_funnel.append(fname)

    unmatched_campaigns = [c for c in campaign_names if c not in matched_campaign_names]

    return jsonify({
        "join_level": funnel_data["join_level"],
        "stages_available": funnel_data["stages_available"],
        "matched": matched,
        "unmatched_funnel": unmatched_funnel,
        "unmatched_campaigns": unmatched_campaigns,
        "campaign_names": campaign_names,
        "total_funnel_rows": len(funnel_campaigns),
        "total_campaigns": len(campaign_names),
    })


# ---- Upload ----

@app.post("/api/upload")
def api_upload():
    saved = {}
    try:
        use_sample   = request.form.get("use_sample") == "true"
        client_id    = int(request.form.get("client_id", 0))
        compare      = request.form.get("compare") == "true"
        period_label = request.form.get("period_label", "").strip() or None
        period_start = request.form.get("period_start", "").strip() or None
        period_end   = request.form.get("period_end", "").strip() or None
        period_notes = request.form.get("period_notes", "").strip() or None

        # Parse optional column_mappings JSON
        # Format: {"google": {"Original Col": "canonical"}, "meta": {...}, ...}
        column_mappings = {}
        col_map_raw = request.form.get("column_mappings", "").strip()
        if col_map_raw:
            try:
                column_mappings = json.loads(col_map_raw)
            except Exception:
                logger.warning("Could not parse column_mappings JSON - ignoring")

        if not client_id:
            return jsonify({"error": "client_id is required"}), 400

        if use_sample:
            paths = {
                "google":      str(SAMPLE_DIR / "google_ads_sample.csv"),
                "meta":        str(SAMPLE_DIR / "meta_ads_sample.csv"),
                "google_prev": str(SAMPLE_DIR / "google_ads_prev.csv"),
                "meta_prev":   str(SAMPLE_DIR / "meta_ads_prev.csv"),
                "funnel":      str(SAMPLE_DIR / "funnel_sample.csv"),
                "ga":          str(SAMPLE_DIR / "ga4_sample.csv"),
            }
            compare = True
        else:
            paths = {}
            for key in ["google", "meta", "google_prev", "meta_prev", "funnel", "ga"]:
                f = request.files.get(key)
                if f and f.filename:
                    p = _save_upload_file(f, key)
                    paths[key] = str(p)
                    saved[key] = p

        if not paths.get("google") and not paths.get("meta"):
            return jsonify({"error": "Upload at least one ads CSV (Google or Meta)."}), 400

        # Load + clean
        google_df = meta_df = prev_google_df = prev_meta_df = funnel_data = None
        google_gran = meta_gran = None
        cleaning_reports = []
        platforms = []

        if paths.get("google"):
            g_pre_rename = column_mappings.get("google") or None
            google_df, g_rep, google_gran = load_google_ads_with_report(
                paths["google"], pre_rename=g_pre_rename
            )
            google_df = calculate_google_metrics(google_df)
            cleaning_reports.append(g_rep)
            platforms.append("google")

        if paths.get("google_prev") and compare:
            prev_google_df = load_google_ads(paths["google_prev"])

        if paths.get("meta"):
            m_pre_rename = column_mappings.get("meta") or None
            meta_df, m_rep, meta_gran = load_meta_ads_with_report(
                paths["meta"], pre_rename=m_pre_rename
            )
            meta_df = calculate_meta_metrics(meta_df)
            cleaning_reports.append(m_rep)
            platforms.append("meta")

        if paths.get("meta_prev") and compare:
            prev_meta_df = load_meta_ads(paths["meta_prev"])

        if paths.get("funnel"):
            f_pre_rename = column_mappings.get("funnel") or None
            funnel_data = load_funnel(paths["funnel"], pre_rename=f_pre_rename)
            if funnel_data.get("cleaning_report"):
                cleaning_reports.append(funnel_data["cleaning_report"])

        ga_result = None
        if paths.get("ga"):
            try:
                ga_pre_rename = column_mappings.get("ga4") or column_mappings.get("ga") or None
                ga_result = load_ga4(paths["ga"], pre_rename=ga_pre_rename)
                if ga_result.get("cleaning_report"):
                    cleaning_reports.append(ga_result["cleaning_report"])
            except (ValueError, FileNotFoundError) as e:
                cleaning_reports.append(f"GA4 warning: {e}")
                ga_result = None

        # Build analysis
        analysis = build_summary(
            google_df=google_df, meta_df=meta_df,
            compare_mode=compare,
            prev_google_df=prev_google_df, prev_meta_df=prev_meta_df,
            funnel=funnel_data,
        )

        # Enrich campaigns with GA4 data
        if ga_result:
            for platform in ["google", "meta"]:
                pdata = analysis.get(platform)
                if pdata:
                    pdata["campaigns"] = merge_ga4_into_campaigns(pdata["campaigns"], ga_result)
                    pdata["ga_summary"] = build_ga_summary(pdata["campaigns"])
            analysis["has_ga"] = True
        else:
            analysis["has_ga"] = False

        # Determine overall granularity level for this upload
        gran_level = None
        if google_gran:
            gran_level = google_gran.get("level")
        elif meta_gran:
            gran_level = meta_gran.get("level")

        # Build granular insights for audit context
        google_gran_insights = build_granular_insights(google_gran) if google_gran else None
        meta_gran_insights = build_granular_insights(meta_gran) if meta_gran else None

        # Store in DuckDB
        db = get_db()

        # Check for duplicate upload (same client + period dates)
        overwrite = request.form.get("overwrite") == "true"
        if not use_sample and period_start and period_end:
            existing = check_duplicate_upload(db, client_id, period_start, period_end, platforms)
            if existing and not overwrite:
                return jsonify({
                    "duplicate": True,
                    "existing_upload_id": existing["id"],
                    "existing_period": existing.get("period_label") or f"{period_start} - {period_end}",
                    "existing_uploaded_at": _fmt_date(existing.get("uploaded_at")),
                    "message": f"An upload for this period ({period_start} to {period_end}) already exists (uploaded {_fmt_date(existing.get('uploaded_at'))}). Overwrite it?",
                }), 409
            if existing and overwrite:
                delete_upload(db, existing["id"])

        upload_id = create_upload(db, client_id, platforms,
                                  bool(funnel_data), period_label, gran_level,
                                  period_start=period_start, period_end=period_end,
                                  period_notes=period_notes)

        if google_df is not None:
            insert_campaigns(db, upload_id, client_id, "google",
                             analysis["google"]["campaigns"])
            if funnel_data and analysis["google"]["campaigns"]:
                camps_with_funnel = [c for c in analysis["google"]["campaigns"] if c.get("funnel")]
                if camps_with_funnel:
                    insert_funnel_data(db, upload_id, client_id, camps_with_funnel)
            # Store granular rows if available
            if google_gran:
                for level_key, db_level in [("adgroup_df", "adgroup"), ("keyword_df", "keyword"),
                                             ("ad_df", "ad"), ("placement_df", "placement")]:
                    gdf = google_gran.get(level_key)
                    if gdf is not None and len(gdf):
                        insert_granular_rows(db, upload_id, client_id, "google", db_level, gdf)

        if meta_df is not None:
            insert_campaigns(db, upload_id, client_id, "meta",
                             analysis["meta"]["campaigns"])
            if funnel_data and analysis["meta"]["campaigns"]:
                camps_with_funnel = [c for c in analysis["meta"]["campaigns"] if c.get("funnel")]
                if camps_with_funnel:
                    insert_funnel_data(db, upload_id, client_id, camps_with_funnel)
            # Store granular rows if available
            if meta_gran:
                for level_key, db_level in [("adgroup_df", "adgroup"), ("keyword_df", "keyword"),
                                             ("ad_df", "ad"), ("placement_df", "placement")]:
                    gdf = meta_gran.get(level_key)
                    if gdf is not None and len(gdf):
                        insert_granular_rows(db, upload_id, client_id, "meta", db_level, gdf)

        # Run learning (benchmarks + outcome tracking) after all data is committed
        run_learning(db, client_id, upload_id)

        # Quick stats
        total_spend = total_wasted = 0.0
        critical = warning = 0
        for p in ["google", "meta"]:
            pd_data = analysis.get(p)
            if pd_data:
                total_spend  += pd_data.get("total_spend", 0)
                total_wasted += pd_data.get("wasted_spend", 0)
                critical     += pd_data.get("critical_count", 0)
                warning      += pd_data.get("warning_count", 0)

        # Build granularity note for UI
        gran_note = None
        if google_gran:
            gran_note = google_gran.get("granularity_note")
        elif meta_gran:
            gran_note = meta_gran.get("granularity_note")

        return jsonify({
            "upload_id":        upload_id,
            "platforms":        platforms,
            "compare_mode":     compare,
            "has_funnel":       bool(funnel_data),
            "has_ga":           bool(ga_result),
            "cleaning_reports": cleaning_reports,
            "granularity_level": gran_level,
            "granularity_note":  gran_note,
            "stats": {
                "total_spend":   total_spend,
                "total_wasted":  total_wasted,
                "critical_count": critical,
                "warning_count":  warning,
                "campaign_count": (analysis.get("google", {}) or {}).get("campaign_count", 0) +
                                  (analysis.get("meta", {}) or {}).get("campaign_count", 0),
            },
            "funnel_summary": analysis.get("funnel_summary"),
        })

    except (FileNotFoundError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {e}"}), 500
    finally:
        for p in saved.values():
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


# ---- Bulk Upload ----

@app.post("/api/bulk-preview")
def api_bulk_preview():
    """
    Step 1 of bulk upload: read the file, detect (or accept) the client column,
    and return the list of detected clients + all column names for the mapping UI.
    Does NOT write anything to the database.
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided"}), 400

    platform  = request.form.get("platform", "google").lower().strip()
    client_col_override = request.form.get("client_col", "").strip() or None

    suffix = Path(f.filename).suffix.lower() or ".csv"
    tmp_path = UPLOAD_DIR / f"bulk_preview_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}{suffix}"
    try:
        f.save(str(tmp_path))
        result = split_bulk_file(str(tmp_path), client_col_override=client_col_override)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    if result.get("error"):
        return jsonify({"error": result["error"]}), 400

    strategy = result["strategy"]
    clients_detected = list(result["clients"].keys())

    # Check which detected client names match existing DB records
    db = get_db()
    existing_clients = {c["name"].lower().strip(): c for c in get_clients(db)}
    client_matches = []
    for name in clients_detected:
        name_lower = name.lower().strip()
        matched = existing_clients.get(name_lower)
        # Also try partial match
        if not matched:
            for ek, ev in existing_clients.items():
                if name_lower in ek or ek in name_lower:
                    matched = ev
                    break
        client_matches.append({
            "detected_name": name,
            "matched_client": matched["name"] if matched else None,
            "matched_client_id": matched["id"] if matched else None,
            "will_create": matched is None,
        })

    return jsonify({
        "strategy": strategy,
        "client_col": result.get("client_col"),
        "all_columns": result.get("all_columns", []),
        "clients": client_matches,
        "total_clients": len(clients_detected),
        "needs_mapping": strategy == "none",
    })


@app.post("/api/bulk-upload")
def api_bulk_upload():
    """
    Step 2 of bulk upload: split the file by client, run the upload + audit
    pipeline for each client sequentially, return per-client results.

    Form fields:
      file            - the bulk CSV/XLSX
      platform        - "google" or "meta"
      client_col      - (optional) column to use as client identifier
      period_label    - applied to all clients
      period_start    - applied to all clients
      period_end      - applied to all clients
      currency        - default currency for auto-created clients
      client_name_map - JSON: {detected_name: existing_client_id} for manual remapping
      run_audit       - "true" to auto-run Claude audit after each upload (default: true)
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file provided"}), 400

    platform         = request.form.get("platform", "google").lower().strip()
    client_col       = request.form.get("client_col", "").strip() or None
    period_label     = request.form.get("period_label", "").strip() or None
    period_start     = request.form.get("period_start", "").strip() or None
    period_end       = request.form.get("period_end", "").strip() or None
    period_notes     = request.form.get("period_notes", "").strip() or None
    currency         = request.form.get("currency", "INR").strip()
    run_audit_flag   = request.form.get("run_audit", "true").lower() != "false"

    client_name_map = {}
    raw_map = request.form.get("client_name_map", "").strip()
    if raw_map:
        try:
            client_name_map = json.loads(raw_map)
        except Exception:
            logger.warning("Could not parse client_name_map JSON - ignoring")

    suffix = Path(f.filename).suffix.lower() or ".csv"
    tmp_path = UPLOAD_DIR / f"bulk_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}{suffix}"
    results = []

    try:
        f.save(str(tmp_path))
        split_result = split_bulk_file(str(tmp_path), client_col_override=client_col)

        if split_result.get("error"):
            return jsonify({"error": split_result["error"]}), 400

        if not split_result["clients"]:
            return jsonify({"error": "No clients detected in file. Map the client identifier column first."}), 400

        db = get_db()

        for detected_name, client_df in split_result["clients"].items():
            client_result = {
                "detected_name": detected_name,
                "client_id": None,
                "client_name": None,
                "upload_id": None,
                "report_url": None,
                "status": "pending",
                "error": None,
                "campaign_count": 0,
                "anomalies_detected": 0,
            }

            try:
                # Resolve client_id - from manual map, or auto match/create
                if detected_name in client_name_map:
                    client_id = int(client_name_map[detected_name])
                    client = get_client(db, client_id)
                    client_result["client_name"] = client["name"] if client else detected_name
                else:
                    client_id = match_or_create_client(db, detected_name, currency=currency)
                    client = get_client(db, client_id)
                    client_result["client_name"] = client["name"] if client else detected_name

                client_result["client_id"] = client_id

                # Write client's rows to a temp file for the existing loader pipeline
                tmp_client_path = df_to_tempfile(client_df, suffix=".csv")

                try:
                    if platform == "google":
                        google_df, g_rep, google_gran = load_google_ads_with_report(tmp_client_path)
                        google_df = calculate_google_metrics(google_df)
                        meta_df = meta_gran = None
                        platforms_list = ["google"]
                        cleaning_report = g_rep
                    else:
                        meta_df, m_rep, meta_gran = load_meta_ads_with_report(tmp_client_path)
                        meta_df = calculate_meta_metrics(meta_df)
                        google_df = google_gran = None
                        platforms_list = ["meta"]
                        cleaning_report = m_rep

                    analysis = build_summary(
                        google_df=google_df,
                        meta_df=meta_df,
                        compare_mode=False,
                    )
                    analysis["has_ga"] = False

                    gran_level = None
                    if platform == "google" and google_gran:
                        gran_level = google_gran.get("level")
                    elif platform == "meta" and meta_gran:
                        gran_level = meta_gran.get("level")

                    upload_id = create_upload(
                        db, client_id, platforms_list,
                        False, period_label, gran_level,
                        period_start=period_start, period_end=period_end,
                        period_notes=period_notes,
                    )

                    if platform == "google" and google_df is not None:
                        insert_campaigns(db, upload_id, client_id, "google",
                                         analysis["google"]["campaigns"])
                        if google_gran:
                            for level_key, db_level in [("adgroup_df", "adgroup"), ("keyword_df", "keyword"),
                                                         ("ad_df", "ad"), ("placement_df", "placement")]:
                                gdf = google_gran.get(level_key)
                                if gdf is not None and len(gdf):
                                    insert_granular_rows(db, upload_id, client_id, "google", db_level, gdf)

                    elif platform == "meta" and meta_df is not None:
                        insert_campaigns(db, upload_id, client_id, "meta",
                                         analysis["meta"]["campaigns"])
                        if meta_gran:
                            for level_key, db_level in [("adgroup_df", "adgroup"), ("keyword_df", "keyword"),
                                                         ("ad_df", "ad"), ("placement_df", "placement")]:
                                gdf = meta_gran.get(level_key)
                                if gdf is not None and len(gdf):
                                    insert_granular_rows(db, upload_id, client_id, "meta", db_level, gdf)

                    run_learning(db, client_id, upload_id)

                    client_result["upload_id"] = upload_id
                    pdata = analysis.get(platform) or {}
                    client_result["campaign_count"] = pdata.get("campaign_count", 0)

                    # Auto-run audit
                    if run_audit_flag:
                        raw_context = (client or {}).get("context", "") or ""
                        client_context = format_client_context(parse_context_from_json(raw_context))
                        benchmarks_ctx = get_benchmarks(db, client_id) or ""
                        claude_output, used_claude = analyze(analysis, business_context=client_context,
                                                             benchmarks_context=benchmarks_ctx)

                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        safe_name = (client_result["client_name"] or detected_name).lower().replace(" ", "_")
                        html_filename = f"audit_{safe_name}_{ts}.html"
                        html_path = str(REPORTS_DIR / html_filename)

                        raw_ctx_dict = parse_context_from_json(raw_context)
                        branding = {
                            "agency_name": raw_ctx_dict.get("agency_name", ""),
                            "agency_logo": raw_ctx_dict.get("agency_logo", ""),
                            "client_name": client_result["client_name"],
                        }
                        render_report(analysis, claude_output, html_path, branding=branding)
                        save_report(db, client_id, upload_id, "audit",
                                    f"Audit - {client_result['client_name']} - {datetime.now().strftime('%d/%b/%Y')}",
                                    html_path=html_path)
                        client_result["report_url"] = f"/report/{html_filename}"

                        # Anomaly detection
                        try:
                            anomalies = detect_anomalies(upload_id, client_id, db,
                                                          client_context=parse_context_from_json(raw_context))
                            if anomalies:
                                insert_anomalies(db, anomalies)
                            client_result["anomalies_detected"] = len(anomalies)
                        except Exception as ae:
                            logger.warning("Anomaly detection failed for %s: %s", detected_name, ae)

                        # KPI alerts
                        try:
                            trigger_alerts_for_upload(db, client_id, upload_id)
                        except Exception as ae:
                            logger.warning("Alert engine failed for %s: %s", detected_name, ae)

                    client_result["status"] = "ok"

                finally:
                    try:
                        Path(tmp_client_path).unlink(missing_ok=True)
                    except Exception:
                        pass

            except Exception as e:
                logger.exception("Bulk upload failed for client %s", detected_name)
                client_result["status"] = "error"
                client_result["error"] = str(e)

            results.append(client_result)

    except Exception as e:
        return jsonify({"error": f"Failed to process bulk file: {e}"}), 500
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    total_ok = sum(1 for r in results if r["status"] == "ok")
    total_err = sum(1 for r in results if r["status"] == "error")

    return jsonify({
        "total_clients": len(results),
        "total_ok": total_ok,
        "total_errors": total_err,
        "results": results,
    })


# ---- Audit ----

@app.post("/api/audit")
def api_audit():
    data = request.get_json()
    upload_id = data.get("upload_id")
    client_id = data.get("client_id")
    currency  = data.get("currency", "INR")

    if not upload_id or not client_id:
        return jsonify({"error": "upload_id and client_id required"}), 400

    db = get_db()
    upload = get_upload(db, upload_id)
    if not upload:
        return jsonify({"error": "Upload not found"}), 404

    client = get_client(db, client_id)

    # Rebuild analysis from DuckDB
    campaigns = get_campaigns_for_upload(db, upload_id)
    funnel_rows = get_funnel_for_upload(db, upload_id)
    platforms = json.loads(upload.get("platforms") or "[]")

    # Reconstruct analysis dict from stored campaigns
    analysis = _reconstruct_analysis(campaigns, funnel_rows, platforms, upload_id)

    # Fetch granular rows stored during upload and build context for Claude
    gran_context_parts = []
    gran_level = upload.get("granularity_level") or "campaign"
    for platform in platforms:
        for row_level in ["adgroup", "keyword", "ad", "placement"]:
            grows = get_granular_rows(db, upload_id, row_level)
            plat_rows = [r for r in grows if r.get("platform") == platform]
            if not plat_rows:
                continue
            # Build a mini granularity insights dict from DB rows
            top_by_spend = sorted(plat_rows, key=lambda x: -(x.get("spend") or 0))[:10]
            worst_roas = sorted(top_by_spend, key=lambda x: (x.get("roas") or 0))[:3]
            best_roas  = sorted(top_by_spend, key=lambda x: -(x.get("roas") or 0))[:3]
            level_label = {"adgroup": "Ad Group", "keyword": "Keyword",
                           "ad": "Ad", "placement": "Placement"}.get(row_level, row_level)
            name_field = {"adgroup": "adgroup_name", "keyword": "keyword_name",
                          "ad": "ad_name", "placement": "placement_name"}.get(row_level, "adgroup_name")
            if worst_roas:
                gran_context_parts.append(
                    f"{platform.title()} {level_label} level - worst performers: " +
                    ", ".join(
                        f"{r.get(name_field) or '?'} "
                        f"(ROAS {r.get('roas', 0):.2f}x, spend {r.get('spend', 0):,.0f})"
                        for r in worst_roas
                    )
                )
            if best_roas:
                gran_context_parts.append(
                    f"{platform.title()} {level_label} level - top performers: " +
                    ", ".join(
                        f"{r.get(name_field) or '?'} "
                        f"(ROAS {r.get('roas', 0):.2f}x)"
                        for r in best_roas
                    )
                )

    if gran_context_parts:
        analysis["granularity_level"] = gran_level
        analysis["granular_context"] = (
            f"Data granularity: {gran_level} level.\n" + "\n".join(gran_context_parts)
        )

    raw_context = (client or {}).get("context", "") or ""
    client_context = format_client_context(parse_context_from_json(raw_context))
    benchmarks_ctx = get_benchmarks(db, client_id) or ""
    upload_period_notes = (upload or {}).get("period_notes") or ""
    claude_output, used_claude = analyze(analysis, business_context=client_context,
                                         benchmarks_context=benchmarks_ctx,
                                         period_notes=upload_period_notes)

    # Build granular insights from stored rows for report template
    all_gran_rows = get_granular_rows(db, upload_id)
    if all_gran_rows:
        level_rows = defaultdict(list)
        for r in all_gran_rows:
            level_rows[r["row_level"]].append(r)

        # Build a synthetic granularity dict for build_granular_insights
        def _rows_to_df(rows):
            if not rows:
                return None
            return pd.DataFrame(rows)

        adgroup_rows = level_rows.get("adgroup", [])
        keyword_rows = level_rows.get("keyword", [])
        ad_rows      = level_rows.get("ad", [])

        # Rename DB columns to match what build_granular_insights expects
        # Maps row_level to the right DB column for the primary name
        _name_field = {"adgroup": "adgroup_name", "keyword": "keyword_name",
                       "ad": "ad_name", "placement": "placement_name"}

        def _remap(rows, name_col, level):
            remapped = []
            src_field = _name_field.get(level, "adgroup_name")
            for r in rows:
                remapped.append({
                    name_col:           r.get(src_field, ""),
                    "campaign":         r.get("campaign_name", ""),
                    "cost":             r.get("spend", 0),
                    "conversions":      r.get("conversions", 0),
                    "conversion value": r.get("conversion_value", 0),
                    "roas":             r.get("roas", 0),
                    "ctr":              r.get("ctr", 0),
                    "cpc":              r.get("cpc", 0),
                    "quality score":    r.get("quality_score", 0),
                    "match type":       r.get("match_type", ""),
                    "ad type":          r.get("ad_type", ""),
                    "ad group":         r.get("adgroup_name", ""),
                })
            return remapped

        fake_gran = {
            "level":        upload.get("granularity_level") or "campaign",
            "spend_col":    "cost",
            "campaign_col": "campaign",
            "adgroup_col":  "ad group" if adgroup_rows else None,
            "keyword_col":  "keyword"  if keyword_rows else None,
            "ad_col":       "ad"       if ad_rows      else None,
            "adgroup_df":   pd.DataFrame(_remap(adgroup_rows, "ad group", "adgroup")) if adgroup_rows else None,
            "keyword_df":   pd.DataFrame(_remap(keyword_rows, "keyword",  "keyword")) if keyword_rows else None,
            "ad_df":        pd.DataFrame(_remap(ad_rows,      "ad",       "ad"))      if ad_rows      else None,
            "placement_df": None,
            "granularity_note": upload.get("granularity_level", "campaign") + " level",
        }
        gran_insights = build_granular_insights(fake_gran)
        analysis["granular_insights"] = gran_insights
        analysis["granularity_level"] = fake_gran["level"]
        analysis["granularity_note"]  = upload.get("granularity_level", "campaign") + " level data"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    client_name = (client or {}).get("name", "client")
    html_filename = f"audit_{client_name.lower().replace(' ', '_')}_{ts}.html"
    html_path = str(REPORTS_DIR / html_filename)

    raw_ctx_dict = parse_context_from_json(raw_context)
    branding = {
        "agency_name": raw_ctx_dict.get("agency_name", ""),
        "agency_logo": raw_ctx_dict.get("agency_logo", ""),
        "client_name": client_name,
    }
    render_report(analysis, claude_output, html_path, branding=branding)

    report_id = save_report(
        db, client_id, upload_id, "audit",
        f"Audit - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
        html_path=html_path,
    )

    # Auto-detect anomalies after audit
    anomalies_found = []
    try:
        anomalies_found = detect_anomalies(upload_id, client_id, db,
                                           client_context=parse_context_from_json(raw_context))
        if anomalies_found:
            insert_anomalies(db, anomalies_found)
    except Exception as e:
        logger.warning("Anomaly detection failed for upload %s: %s", upload_id, e)

    # KPI alert checks
    try:
        trigger_alerts_for_upload(db, client_id, upload_id)
    except Exception as e:
        logger.warning("Alert engine failed for upload %s: %s", upload_id, e)

    return jsonify({
        "report_id":    report_id,
        "report_url":   f"/report/{html_filename}",
        "used_claude":  used_claude,
        "anomalies_detected": len(anomalies_found),
        "morning_brief": format_morning_brief(anomalies_found),
    })


@app.post("/api/audit/stream")
def api_audit_stream():
    """
    Streaming version of /api/audit using Server-Sent Events.
    Sends Claude output chunks as they arrive, then finalizes the report.
    """
    data = request.get_json()
    upload_id = data.get("upload_id")
    client_id = data.get("client_id")
    currency  = data.get("currency", "INR")

    if not upload_id or not client_id:
        return jsonify({"error": "upload_id and client_id required"}), 400

    db = get_db()
    upload = get_upload(db, upload_id)
    if not upload:
        return jsonify({"error": "Upload not found"}), 404

    client = get_client(db, client_id)

    campaigns = get_campaigns_for_upload(db, upload_id)
    funnel_rows = get_funnel_for_upload(db, upload_id)
    platforms = json.loads(upload.get("platforms") or "[]")
    analysis = _reconstruct_analysis(campaigns, funnel_rows, platforms, upload_id)

    gran_context_parts = []
    gran_level = upload.get("granularity_level") or "campaign"
    for platform in platforms:
        for row_level in ["adgroup", "keyword", "ad", "placement"]:
            grows = get_granular_rows(db, upload_id, row_level)
            plat_rows = [r for r in grows if r.get("platform") == platform]
            if not plat_rows:
                continue
            top_by_spend = sorted(plat_rows, key=lambda x: -(x.get("spend") or 0))[:10]
            worst_roas = sorted(top_by_spend, key=lambda x: (x.get("roas") or 0))[:3]
            best_roas  = sorted(top_by_spend, key=lambda x: -(x.get("roas") or 0))[:3]
            level_label = {"adgroup": "Ad Group", "keyword": "Keyword",
                           "ad": "Ad", "placement": "Placement"}.get(row_level, row_level)
            name_field = {"adgroup": "adgroup_name", "keyword": "keyword_name",
                          "ad": "ad_name", "placement": "placement_name"}.get(row_level, "adgroup_name")
            if worst_roas:
                gran_context_parts.append(
                    f"{platform.title()} {level_label} level - worst performers: " +
                    ", ".join(f"{r.get(name_field) or '?'} (ROAS {r.get('roas', 0):.2f}x, spend {r.get('spend', 0):,.0f})"
                              for r in worst_roas)
                )
            if best_roas:
                gran_context_parts.append(
                    f"{platform.title()} {level_label} level - top performers: " +
                    ", ".join(f"{r.get(name_field) or '?'} (ROAS {r.get('roas', 0):.2f}x)"
                              for r in best_roas)
                )

    if gran_context_parts:
        analysis["granularity_level"] = gran_level
        analysis["granular_context"] = f"Data granularity: {gran_level} level.\n" + "\n".join(gran_context_parts)

    raw_context = (client or {}).get("context", "") or ""
    client_context = format_client_context(parse_context_from_json(raw_context))
    benchmarks_ctx = get_benchmarks(db, client_id) or ""
    upload_period_notes = (upload or {}).get("period_notes") or ""

    def generate():
        claude_output = []
        used_claude = False

        for event_type, text in analyze_stream(analysis, business_context=client_context,
                                               benchmarks_context=benchmarks_ctx,
                                               period_notes=upload_period_notes):
            if event_type == 'chunk':
                used_claude = True
                claude_output.append(text)
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            elif event_type in ('done', 'fallback'):
                used_claude = (event_type == 'done')
                if not claude_output:
                    claude_output.append(text)

        full_output = "".join(claude_output).strip()

        # Build granular insights for report template
        all_gran_rows = get_granular_rows(db, upload_id)
        if all_gran_rows:
            level_rows = defaultdict(list)
            for r in all_gran_rows:
                level_rows[r["row_level"]].append(r)

            def _rows_to_df(rows): return pd.DataFrame(rows) if rows else None
            adgroup_rows = level_rows.get("adgroup", [])
            keyword_rows = level_rows.get("keyword", [])
            ad_rows      = level_rows.get("ad", [])
            _name_field  = {"adgroup": "adgroup_name", "keyword": "keyword_name",
                            "ad": "ad_name", "placement": "placement_name"}

            def _remap(rows, name_col, level):
                src_field = _name_field.get(level, "adgroup_name")
                return [{
                    name_col: r.get(src_field, ""), "campaign": r.get("campaign_name", ""),
                    "cost": r.get("spend", 0), "conversions": r.get("conversions", 0),
                    "conversion value": r.get("conversion_value", 0), "roas": r.get("roas", 0),
                    "ctr": r.get("ctr", 0), "cpc": r.get("cpc", 0),
                    "quality score": r.get("quality_score", 0), "match type": r.get("match_type", ""),
                    "ad type": r.get("ad_type", ""), "ad group": r.get("adgroup_name", ""),
                } for r in rows]

            fake_gran = {
                "level": upload.get("granularity_level") or "campaign", "spend_col": "cost",
                "campaign_col": "campaign",
                "adgroup_col": "ad group" if adgroup_rows else None,
                "keyword_col": "keyword"  if keyword_rows else None,
                "ad_col":      "ad"       if ad_rows      else None,
                "adgroup_df":  pd.DataFrame(_remap(adgroup_rows, "ad group", "adgroup")) if adgroup_rows else None,
                "keyword_df":  pd.DataFrame(_remap(keyword_rows, "keyword",  "keyword")) if keyword_rows else None,
                "ad_df":       pd.DataFrame(_remap(ad_rows,      "ad",       "ad"))      if ad_rows      else None,
                "placement_df": None,
                "granularity_note": upload.get("granularity_level", "campaign") + " level",
            }
            gran_insights = build_granular_insights(fake_gran)
            analysis["granular_insights"] = gran_insights
            analysis["granularity_level"] = fake_gran["level"]
            analysis["granularity_note"]  = upload.get("granularity_level", "campaign") + " level data"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        client_name = (client or {}).get("name", "client")
        html_filename = f"audit_{client_name.lower().replace(' ', '_')}_{ts}.html"
        html_path = str(REPORTS_DIR / html_filename)

        raw_ctx_dict = parse_context_from_json(raw_context)
        branding = {
            "agency_name": raw_ctx_dict.get("agency_name", ""),
            "agency_logo": raw_ctx_dict.get("agency_logo", ""),
            "client_name": client_name,
        }
        render_report(analysis, full_output, html_path, branding=branding)

        report_id = save_report(
            db, client_id, upload_id, "audit",
            f"Audit - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
            html_path=html_path,
        )

        anomalies_found = []
        try:
            anomalies_found = detect_anomalies(upload_id, client_id, db,
                                               client_context=parse_context_from_json(raw_context))
            if anomalies_found:
                insert_anomalies(db, anomalies_found)
        except Exception as e:
            logger.warning("Anomaly detection failed for upload %s: %s", upload_id, e)

        try:
            trigger_alerts_for_upload(db, client_id, upload_id)
        except Exception as e:
            logger.warning("Alert engine failed for upload %s: %s", upload_id, e)

        yield f"data: {json.dumps({'type': 'done', 'report_id': report_id, 'report_url': f'/report/{html_filename}', 'used_claude': used_claude, 'anomalies_detected': len(anomalies_found), 'morning_brief': format_morning_brief(anomalies_found)})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _reconstruct_analysis(campaigns: list, funnel_rows: list,
                           platforms: list, upload_id: int) -> dict:
    from datetime import datetime as dt
    result: dict = {
        "generated_at": dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": platforms,
        "compare_mode": False,
        "google": None, "meta": None,
        "cross_platform": None, "comparison": None, "funnel_summary": None,
    }

    funnel_by_campaign = {r["campaign_name"]: r for r in funnel_rows}

    for platform in platforms:
        pcamps = [c for c in campaigns if c["platform"] == platform]
        if not pcamps:
            continue

        total_spend = sum(c.get("spend") or 0 for c in pcamps)
        total_rev   = sum(c.get("conversion_value") or 0 for c in pcamps)
        total_conv  = sum(c.get("conversions") or 0 for c in pcamps)
        wasted      = sum(c.get("wasted_spend") or 0 for c in pcamps)

        from src.calculator import safe_divide

        camp_list = []
        for c in pcamps:
            fd = funnel_by_campaign.get(c["campaign_name"], {})
            camp_list.append({
                "name":             c["campaign_name"],
                "spend":            c.get("spend") or 0,
                "revenue":          c.get("conversion_value"),
                "roas":             c.get("roas") or 0,
                "cac":              c.get("cac") or 0,
                "ctr":              c.get("ctr") or 0,
                "cpc":              c.get("cpc") or 0,
                "efficiency":       safe_divide(c.get("conversions") or 0, c.get("spend") or 0) * 1000,
                "conversions":      int(c.get("conversions") or 0),
                "severity":         c.get("severity") or "ok",
                "wasted":           c.get("wasted_spend") or 0,
                "funnel": {
                    "leads":     fd.get("leads"),
                    "mqls":      fd.get("mqls"),
                    "sqls":      fd.get("sqls"),
                    "customers": fd.get("customers"),
                } if fd else {},
                "cost_per_lead":     fd.get("cost_per_lead"),
                "cost_per_mql":      fd.get("cost_per_mql"),
                "cost_per_sql":      fd.get("cost_per_sql"),
                "cost_per_customer": fd.get("cost_per_customer"),
            })

        camp_list.sort(key=lambda x: x["roas"])
        spend_key = "cost" if platform == "google" else "amount spent"
        overall_roas = round(safe_divide(total_rev, total_spend), 2) if platform == "google" else \
                       round(sum(c.get("roas") or 0 for c in pcamps) / len(pcamps), 2)

        result[platform] = {
            "total_spend":      round(total_spend, 2),
            "total_revenue":    round(total_rev, 2) if platform == "google" else None,
            "overall_roas":     overall_roas,
            "total_conversions": round(total_conv, 0),
            "overall_cac":      round(safe_divide(total_spend, total_conv), 2),
            "overall_ctr":      round(sum(c.get("ctr") or 0 for c in pcamps) / len(pcamps), 2),
            "wasted_spend":     round(wasted, 2),
            "campaign_count":   len(pcamps),
            "critical_count":   sum(1 for c in pcamps if c.get("severity") == "critical"),
            "warning_count":    sum(1 for c in pcamps if c.get("severity") == "warning"),
            "ok_count":         sum(1 for c in pcamps if c.get("severity") == "ok"),
            "campaigns":        camp_list,
        }

    if "google" in platforms and "meta" in platforms and result["google"] and result["meta"]:
        g, m = result["google"], result["meta"]
        result["cross_platform"] = {
            "google_spend":    g["total_spend"],
            "meta_spend":      m["total_spend"],
            "google_roas":     g["overall_roas"],
            "meta_roas":       m["overall_roas"],
            "google_cac":      g["overall_cac"],
            "meta_cac":        m["overall_cac"],
            "google_wasted":   g["wasted_spend"],
            "meta_wasted":     m["wasted_spend"],
            "better_platform": "google" if g["overall_roas"] >= m["overall_roas"] else "meta",
            "total_spend":     round(g["total_spend"] + m["total_spend"], 2),
            "total_wasted":    round(g["wasted_spend"] + m["wasted_spend"], 2),
        }

    if funnel_rows:
        from src.funnel_loader import build_funnel_summary, calculate_funnel_metrics
        all_camps = []
        for platform in platforms:
            pdata = result.get(platform)
            if pdata:
                all_camps.extend(pdata["campaigns"])
        result["funnel_summary"] = build_funnel_summary(all_camps)

    return result


# ---- Anomalies ----

@app.get("/api/anomalies/<int:client_id>")
def api_get_anomalies(client_id):
    status = request.args.get("status")
    limit  = int(request.args.get("limit", 100))
    db = get_db()
    anomalies = get_anomalies(db, client_id, status, limit)
    summary   = get_anomaly_summary(db, client_id)
    brief     = format_morning_brief([a for a in anomalies if a.get("status") == "open"])
    return jsonify({
        "anomalies":     _jsonify_dates(anomalies),
        "summary":       summary,
        "morning_brief": brief,
    })


@app.post("/api/anomalies/detect")
def api_detect_anomalies():
    data = request.get_json()
    upload_id = data.get("upload_id")
    client_id = data.get("client_id")
    if not upload_id or not client_id:
        return jsonify({"error": "upload_id and client_id required"}), 400

    db = get_db()
    client = get_client(db, client_id)
    raw_ctx = (client or {}).get("context", "") or ""
    anomalies = detect_anomalies(upload_id, client_id, db,
                                  client_context=parse_context_from_json(raw_ctx))
    if anomalies:
        insert_anomalies(db, anomalies)

    return jsonify({
        "anomalies_found": len(anomalies),
        "anomalies":       _jsonify_dates(anomalies),
        "morning_brief":   format_morning_brief(anomalies),
        "summary":         get_anomaly_summary(db, client_id),
    })


@app.patch("/api/anomalies/<int:anomaly_id>")
def api_update_anomaly(anomaly_id):
    data = request.get_json()
    status = data.get("status")
    if status not in ("open", "acknowledged", "resolved"):
        return jsonify({"error": "Invalid status"}), 400
    update_anomaly_status(get_db(), anomaly_id, status)
    return jsonify({"ok": True})


@app.post("/api/morning-brief/<int:client_id>")
def api_morning_brief(client_id):
    """Stream a Claude-powered morning brief for this client's open anomalies via SSE."""
    db = get_db()
    client = get_client(db, client_id)
    if not client:
        return jsonify({"error": "Client not found"}), 404

    anomalies = get_anomalies(db, client_id, status="open", limit=20)
    summary = get_anomaly_summary(db, client_id)

    crit_count = summary.get("critical", 0)
    warn_count = summary.get("warning", 0)
    severity = "critical" if crit_count > 0 else "warning" if warn_count > 0 else "ok"

    payload = {
        "client_name": client.get("name", f"Client {client_id}"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary": summary,
        "anomalies": [
            {
                "campaign": a["campaign_name"],
                "platform": a["platform"],
                "metric": a["metric"],
                "description": a["description"],
                "severity": a["severity"],
                "pct_change": a.get("pct_change"),
            }
            for a in anomalies[:10]
        ],
    }

    BRIEF_PROMPT = """You are an ad performance analyst delivering a morning brief.

You receive JSON with anomaly data for a single client.

Write a concise morning brief covering:
1. A one-line headline with the most critical issue (or "All Clear" if none)
2. Breakdown of the key issues - name the campaign, the metric, the number
3. Top 3 recommended actions for today, ranked by urgency

Rules:
- Plain text only, no markdown headers or bullets
- Be direct and specific
- If no anomalies exist, say so and suggest running a fresh audit
- Max 200 words

Data:
"""

    def _fallback_brief():
        lines = []
        if not anomalies:
            lines.append(f"All Clear - no open anomalies for {client.get('name')}.")
            lines.append("Recommendation: Run a fresh audit to check latest data.")
        else:
            lines.append(f"{crit_count} critical, {warn_count} warnings detected for {client.get('name')}.")
            for a in anomalies[:5]:
                lines.append(f"[{a['severity'].upper()}] {a['campaign_name']} ({a['platform']}): {a['description']}")
            lines.append("Recommended: Review critical campaigns first, then warnings.")
        return "\n".join(lines)

    def generate():
        prompt = BRIEF_PROMPT + json.dumps(payload, indent=2)
        full_text = []
        used_claude = False

        for event_type, text in stream_prompt(prompt):
            if event_type == 'chunk':
                used_claude = True
                full_text.append(text)
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            elif event_type == 'done':
                brief = text or "".join(full_text)
                yield f"data: {json.dumps({'type': 'done', 'brief': brief, 'used_claude': True, 'severity': severity, 'summary': summary})}\n\n"
                return
            elif event_type == 'fallback':
                break

        # Fallback if Claude unavailable or returned nothing
        yield f"data: {json.dumps({'type': 'done', 'brief': _fallback_brief(), 'used_claude': False, 'severity': severity, 'summary': summary})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- Narrator ----

@app.post("/api/narrator")
def api_narrator():
    data = request.get_json()
    upload_id = data.get("upload_id")
    client_id = data.get("client_id")
    tone      = data.get("tone", "executive")
    currency  = data.get("currency", "INR")
    date_range = data.get("date_range", "")

    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    db = get_db()
    client = get_client(db, client_id)
    currency = currency or (client or {}).get("currency", "INR")

    analysis = {}
    if upload_id:
        campaigns  = get_campaigns_for_upload(db, upload_id)
        funnel_rows = get_funnel_for_upload(db, upload_id)
        upload     = get_upload(db, upload_id)
        platforms  = json.loads(upload.get("platforms") or "[]") if upload else []
        analysis   = _reconstruct_analysis(campaigns, funnel_rows, platforms, upload_id)

    open_anomalies = get_anomalies(db, client_id, status="open", limit=20)
    raw_context = (client or {}).get("context", "") or ""
    client_context = format_client_context(parse_context_from_json(raw_context))
    narrative_md, used_claude = generate_narrative(
        analysis, open_anomalies, tone, date_range, currency,
        business_context=client_context
    )

    from src.renderer import markdown_to_html
    narrative_html = markdown_to_html(narrative_md)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    client_name = (client or {}).get("name", "client")
    html_filename = f"narrative_{client_name.lower().replace(' ', '_')}_{ts}.html"
    html_path = str(REPORTS_DIR / html_filename)

    _write_narrative_html(narrative_html, tone, date_range, client_name, html_path)

    report_id = save_report(
        db, client_id, upload_id, "narrative",
        f"Weekly Narrative - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
        html_path=html_path, tone=tone,
    )

    return jsonify({
        "report_id":     report_id,
        "report_url":    f"/report/{html_filename}",
        "narrative_html": narrative_html,
        "used_claude":   used_claude,
        "tone":          tone,
    })


@app.post("/api/narrator/stream")
def api_narrator_stream():
    data = request.get_json()
    upload_id  = data.get("upload_id")
    client_id  = data.get("client_id")
    tone       = data.get("tone", "executive")
    currency   = data.get("currency", "INR")
    date_range = data.get("date_range", "")

    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    db = get_db()
    client   = get_client(db, client_id)
    currency = currency or (client or {}).get("currency", "INR")

    analysis = {}
    if upload_id:
        campaigns   = get_campaigns_for_upload(db, upload_id)
        funnel_rows = get_funnel_for_upload(db, upload_id)
        upload      = get_upload(db, upload_id)
        platforms   = json.loads(upload.get("platforms") or "[]") if upload else []
        analysis    = _reconstruct_analysis(campaigns, funnel_rows, platforms, upload_id)

    open_anomalies = get_anomalies(db, client_id, status="open", limit=20)
    raw_context    = (client or {}).get("context", "") or ""
    client_context = format_client_context(parse_context_from_json(raw_context))
    client_name    = (client or {}).get("name", "client")

    def generate():
        full_text = []

        for event_type, text in stream_narrative(analysis, open_anomalies, tone, date_range, currency, client_context):
            if event_type == 'chunk':
                full_text.append(text)
                yield f"data: {json.dumps({'type': 'chunk', 'text': text})}\n\n"
            elif event_type in ('done', 'fallback'):
                narrative_md = text or "".join(full_text)
                from src.renderer import markdown_to_html
                narrative_html = markdown_to_html(narrative_md)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                html_filename = f"narrative_{client_name.lower().replace(' ', '_')}_{ts}.html"
                html_path = str(REPORTS_DIR / html_filename)
                _write_narrative_html(narrative_html, tone, date_range, client_name, html_path)
                with app.app_context():
                    conn2 = get_connection()
                    report_id = save_report(
                        conn2, client_id, upload_id, "narrative",
                        f"Weekly Narrative - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
                        html_path=html_path, tone=tone,
                    )
                    conn2.close()
                yield f"data: {json.dumps({'type': 'done', 'report_id': report_id, 'report_url': f'/report/{html_filename}', 'narrative_html': narrative_html, 'used_claude': event_type == 'done', 'tone': tone})}\n\n"
                return

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _write_narrative_html(content_html: str, tone: str, date_range: str,
                           client_name: str, path: str) -> None:
    from src.renderer import TEMPLATE_DIR
    from jinja2 import Environment, FileSystemLoader
    report_css_path = TEMPLATE_DIR / "report.html"
    # Use a simple standalone HTML for narrative
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Performance Narrative - {client_name}</title>
<link rel="stylesheet" href="/static/report.css">
<style>
body{{max-width:860px;margin:40px auto;padding:0 24px 60px;}}
.tone-badge{{display:inline-block;padding:4px 12px;border-radius:12px;font-size:14px;font-weight:600;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;margin-left:10px;vertical-align:middle;}}
</style>
</head>
<body>
<header class="standalone">
  <h1>Performance Narrative <span class="tone-badge">{tone.title()}</span></h1>
  <p>{client_name} &nbsp;|&nbsp; {date_range or datetime.now().strftime('%d/%b/%Y')}</p>
</header>
<div class="section-card">
{content_html}
</div>
<footer class="inline">Generated by Ad Audit &nbsp;|&nbsp; {datetime.now().strftime('%d/%b/%Y %H:%M')}</footer>
</body>
</html>"""
    Path(path).write_text(html, encoding="utf-8")


# ---- Budget Agent ----

@app.get("/api/budget-rules/<int:client_id>")
def api_get_budget_rules(client_id):
    return jsonify(get_budget_rules(get_db(), client_id))


@app.post("/api/budget-rules/<int:client_id>")
def api_save_budget_rules(client_id):
    rules = request.get_json()
    save_budget_rules(get_db(), client_id, rules)
    return jsonify({"ok": True})


@app.post("/api/budget-agent")
def api_budget_agent():
    data = request.get_json()
    upload_id = data.get("upload_id")
    client_id = data.get("client_id")
    currency  = data.get("currency", "INR")

    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    db = get_db()
    client   = get_client(db, client_id)
    currency = currency or (client or {}).get("currency", "INR")
    rules    = get_budget_rules(db, client_id)

    analysis = {}
    if upload_id:
        campaigns   = get_campaigns_for_upload(db, upload_id)
        funnel_rows = get_funnel_for_upload(db, upload_id)
        upload      = get_upload(db, upload_id)
        platforms   = json.loads(upload.get("platforms") or "[]") if upload else []
        analysis    = _reconstruct_analysis(campaigns, funnel_rows, platforms, upload_id)

    raw_context = (client or {}).get("context", "") or ""
    ctx_dict = parse_context_from_json(raw_context)
    client_context = format_client_context(ctx_dict)
    campaign_tags = ctx_dict.get("campaign_tags", {})
    recs, explanation_md, used_claude = run_budget_agent(
        analysis, rules, client_id, db, currency,
        business_context=client_context,
        campaign_tags=campaign_tags,
    )

    from src.renderer import markdown_to_html
    explanation_html = markdown_to_html(explanation_md)
    table_html = format_reallocation_table(recs, currency)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    client_name = (client or {}).get("name", "client")
    html_filename = f"budget_{client_name.lower().replace(' ', '_')}_{ts}.html"
    html_path = str(REPORTS_DIR / html_filename)

    _write_budget_html(table_html, explanation_html, recs, client_name, currency, html_path)

    report_id = save_report(
        db, client_id, upload_id, "budget",
        f"Budget Reallocation - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
        html_path=html_path,
    )

    return jsonify({
        "report_id":        report_id,
        "report_url":       f"/report/{html_filename}",
        "recommendations":  recs,
        "explanation_html": explanation_html,
        "table_html":       table_html,
        "used_claude":      used_claude,
    })


@app.post("/api/budget-agent/stream")
def api_budget_agent_stream():
    data      = request.get_json()
    upload_id = data.get("upload_id")
    client_id = data.get("client_id")
    currency  = data.get("currency", "INR")

    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    db       = get_db()
    client   = get_client(db, client_id)
    currency = currency or (client or {}).get("currency", "INR")
    rules    = get_budget_rules(db, client_id)

    analysis = {}
    if upload_id:
        campaigns   = get_campaigns_for_upload(db, upload_id)
        funnel_rows = get_funnel_for_upload(db, upload_id)
        upload      = get_upload(db, upload_id)
        platforms   = json.loads(upload.get("platforms") or "[]") if upload else []
        analysis    = _reconstruct_analysis(campaigns, funnel_rows, platforms, upload_id)

    raw_context   = (client or {}).get("context", "") or ""
    ctx_dict      = parse_context_from_json(raw_context)
    client_context = format_client_context(ctx_dict)
    campaign_tags  = ctx_dict.get("campaign_tags", {})
    client_name    = (client or {}).get("name", "client")

    def generate():
        for event_type, payload in stream_budget_agent(
            analysis, rules, client_id, db, currency,
            business_context=client_context, campaign_tags=campaign_tags
        ):
            if event_type == 'chunk':
                yield f"data: {json.dumps({'type': 'chunk', 'text': payload})}\n\n"
            elif event_type in ('done', 'fallback'):
                recs, explanation_md = payload
                from src.renderer import markdown_to_html
                explanation_html = markdown_to_html(explanation_md)
                table_html = format_reallocation_table(recs, currency)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                html_filename = f"budget_{client_name.lower().replace(' ', '_')}_{ts}.html"
                html_path = str(REPORTS_DIR / html_filename)
                _write_budget_html(table_html, explanation_html, recs, client_name, currency, html_path)
                with app.app_context():
                    conn2 = get_connection()
                    report_id = save_report(
                        conn2, client_id, upload_id, "budget",
                        f"Budget Reallocation - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
                        html_path=html_path,
                    )
                    conn2.close()
                yield f"data: {json.dumps({'type': 'done', 'report_id': report_id, 'report_url': f'/report/{html_filename}', 'recommendations': recs, 'explanation_html': explanation_html, 'table_html': table_html, 'used_claude': event_type == 'done'})}\n\n"
                return

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _write_budget_html(table_html: str, explanation_html: str,
                        recs: dict, client_name: str, currency: str, path: str) -> None:
    sym = "Rs" if currency == "INR" else currency
    freed = recs.get("total_freed_budget_pct", 0)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Budget Reallocation - {client_name}</title>
<link rel="stylesheet" href="/static/report.css">
<style>
body{{max-width:1000px;margin:40px auto;padding:0 24px 60px;}}
</style>
</head>
<body>
<header class="standalone">
  <h1>Budget Reallocation Report</h1>
  <p>{client_name} &nbsp;|&nbsp; {datetime.now().strftime('%d/%b/%Y')} &nbsp;|&nbsp; Budget freed: {freed}%</p>
</header>
<div class="section-card">
  <div class="section-label">Reallocation Actions</div>
  <div class="table-wrap">{table_html}</div>
</div>
<div class="section-card">
  {explanation_html}
</div>
<footer class="inline">Generated by Ad Audit &nbsp;|&nbsp; {datetime.now().strftime('%d/%b/%Y %H:%M')}</footer>
</body>
</html>"""
    Path(path).write_text(html, encoding="utf-8")


# ---- History ----

@app.get("/api/history/<int:client_id>")
def api_history(client_id):
    report_type = request.args.get("type")
    limit  = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    db = get_db()
    reports = get_reports(db, client_id, report_type, limit + offset)
    reports = reports[offset:offset + limit]
    for r in reports:
        if r.get("html_path"):
            # Use replace+split to handle Windows backslash paths safely
            r["report_url"] = "/report/" + r["html_path"].replace("\\", "/").split("/")[-1]
    return jsonify({"reports": _jsonify_dates(reports), "total": len(reports)})


@app.delete("/api/report/<int:report_id>")
def api_delete_report(report_id):
    db = get_db()
    paths = delete_report(db, report_id)
    # Delete HTML file if it exists
    for key in ("html_path", "pdf_path"):
        p = paths.get(key)
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
    return jsonify({"ok": True})


@app.delete("/api/uploads/<int:upload_id>")
def api_delete_upload(upload_id):
    db = get_db()
    row = get_upload(db, upload_id)
    if not row:
        return jsonify({"error": "Upload not found"}), 404
    client_id = row["client_id"]
    delete_upload(db, upload_id)
    try:
        run_learning(db, client_id, None)
    except Exception:
        pass
    return jsonify({"ok": True})


# ---- Report serving ----

@app.get("/report/<filename>")
def serve_report(filename):
    path = REPORTS_DIR / filename
    if not path.exists():
        return "Report not found", 404
    return send_file(str(path))


@app.get("/pdf/<int:report_id>")
def generate_pdf(report_id):
    db = get_db()
    report = get_report(db, report_id)
    if not report:
        return jsonify({"error": "Report not found"}), 404

    pdf_path = report.get("pdf_path")
    if pdf_path and Path(pdf_path).exists():
        return send_file(pdf_path, as_attachment=True,
                        download_name=Path(pdf_path).name)

    html_path = report.get("html_path")
    if not html_path or not Path(html_path).exists():
        return jsonify({"error": "HTML source not found"}), 404

    try:
        from xhtml2pdf import pisa
        stem = Path(html_path).stem
        pdf_out = str(PDFS_DIR / f"{stem}.pdf")
        with open(html_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        with open(pdf_out, "wb") as f:
            result = pisa.CreatePDF(html_content, dest=f)
        if result.err:
            return jsonify({"error": "PDF generation failed: xhtml2pdf reported errors"}), 500
        update_report_pdf_path(db, report_id, pdf_out)
        return send_file(pdf_out, as_attachment=True,
                        download_name=f"{stem}.pdf")
    except Exception as e:
        return jsonify({"error": f"PDF generation failed: {e}"}), 500


@app.get("/api/timeline/<int:client_id>")
def api_timeline(client_id):
    """
    Return the full upload timeline for a client - all periods with aggregated metrics.
    Used for the historical analysis view and trend charts.
    """
    db = get_db()
    rows = get_upload_timeline(db, client_id)
    points = []
    for r in rows:
        # Determine the display date label
        if r.get("period_start"):
            ps = r["period_start"]
            pe = r.get("period_end")
            if isinstance(ps, str):
                label = ps[:7]  # YYYY-MM
            else:
                label = ps.strftime("%Y-%m") if hasattr(ps, "strftime") else str(ps)[:7]
            if pe:
                if isinstance(pe, str):
                    end_label = pe[:7]
                else:
                    end_label = pe.strftime("%Y-%m") if hasattr(pe, "strftime") else str(pe)[:7]
                if end_label != label:
                    label = f"{label} - {end_label}"
        elif r.get("period_label"):
            label = r["period_label"]
        else:
            dt = r.get("uploaded_at")
            label = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]

        points.append({
            "upload_id":       r["upload_id"],
            "label":           label,
            "period_label":    r.get("period_label"),
            "period_start":    str(r["period_start"]) if r.get("period_start") else None,
            "period_end":      str(r["period_end"])   if r.get("period_end")   else None,
            "uploaded_at":     _fmt_date(r.get("uploaded_at")),
            "platforms":       json.loads(r.get("platforms") or "[]"),
            "total_spend":     round(r.get("total_spend") or 0, 2),
            "avg_roas":        round(r.get("avg_roas") or 0, 2),
            "total_conversions": round(r.get("total_conversions") or 0, 0),
            "avg_ctr":         round(r.get("avg_ctr") or 0, 2),
            "total_wasted":    round(r.get("total_wasted") or 0, 2),
            "campaign_count":  r.get("campaign_count") or 0,
        })
    return jsonify({"timeline": points})


@app.get("/api/campaign-timeline/<int:client_id>")
def api_campaign_timeline(client_id):
    """Return the full history of a single campaign for drill-down trend view."""
    campaign_name = request.args.get("campaign")
    platform      = request.args.get("platform")
    if not campaign_name or not platform:
        return jsonify({"error": "campaign and platform required"}), 400
    db = get_db()
    rows = get_campaign_timeline(db, client_id, campaign_name, platform)
    points = []
    for r in rows:
        ps = r.get("period_start")
        label = str(ps)[:7] if ps else _fmt_date(r.get("uploaded_at"))
        points.append({
            "upload_id":   r["upload_id"],
            "label":       label,
            "period_label": r.get("period_label"),
            "spend":       round(r.get("spend") or 0, 2),
            "roas":        round(r.get("roas") or 0, 2),
            "cac":         round(r.get("cac") or 0, 2),
            "ctr":         round(r.get("ctr") or 0, 2),
            "conversions": round(r.get("conversions") or 0, 0),
            "severity":    r.get("severity"),
        })
    return jsonify({"points": points, "campaign": campaign_name, "platform": platform})


@app.get("/api/trends/<int:client_id>")
def api_trends(client_id):
    """Return per-upload aggregated metrics for sparkline charts."""
    db = get_db()
    days = int(request.args.get("days", 365))  # default to 1 year for historical view
    rows = []
    for platform in ["google", "meta"]:
        prows = get_platform_history(db, client_id, platform, days)
        rows.extend(prows)

    if not rows:
        return jsonify({"points": [], "campaigns": []})

    by_upload: dict = defaultdict(lambda: {
        "spend": 0, "roas_sum": 0, "roas_n": 0,
        "conversions": 0, "ctr_sum": 0, "ctr_n": 0,
        "cac_sum": 0, "cac_n": 0,
        "date": None, "period_label": None, "period_start": None,
    })
    for r in rows:
        uid = r["upload_id"]
        by_upload[uid]["spend"] += r.get("spend") or 0
        roas = r.get("roas") or 0
        if roas:
            by_upload[uid]["roas_sum"] += roas
            by_upload[uid]["roas_n"] += 1
        ctr = r.get("ctr") or 0
        if ctr:
            by_upload[uid]["ctr_sum"] += ctr
            by_upload[uid]["ctr_n"] += 1
        cac = r.get("cac") or 0
        if cac:
            by_upload[uid]["cac_sum"] += cac
            by_upload[uid]["cac_n"] += 1
        by_upload[uid]["conversions"] += r.get("conversions") or 0
        if not by_upload[uid]["date"]:
            by_upload[uid]["date"] = r.get("upload_at") or r.get("period_date")
        if not by_upload[uid]["period_start"]:
            by_upload[uid]["period_start"] = r.get("period_start")
        if not by_upload[uid]["period_label"]:
            by_upload[uid]["period_label"] = r.get("period_label")

    # Also aggregate funnel CPL per upload
    upload_ids = list(by_upload.keys())
    cpl_by_upload: dict = {}
    if upload_ids:
        for uid in upload_ids:
            frows = get_funnel_for_upload(db, uid)
            if frows:
                cpls = [r.get("cost_per_lead") for r in frows if r.get("cost_per_lead")]
                cpl_by_upload[uid] = round(sum(cpls) / len(cpls), 2) if cpls else None

    points = []
    for uid, d in sorted(by_upload.items()):
        # Use period_start for label if available - gives chronological correctness for historical data
        ps = d.get("period_start")
        if ps:
            dt_label = str(ps)[:7]  # YYYY-MM
        elif d.get("period_label"):
            dt_label = d["period_label"]
        else:
            dt = d["date"]
            if isinstance(dt, str):
                dt_label = dt[:10]
            elif dt:
                dt_label = dt.strftime("%Y-%m-%d")
            else:
                dt_label = str(uid)

        points.append({
            "upload_id":    uid,
            "date":         dt_label,
            "period_label": d.get("period_label"),
            "spend":        round(d["spend"], 2),
            "roas":         round(d["roas_sum"] / d["roas_n"], 2) if d["roas_n"] else 0,
            "conversions":  round(d["conversions"], 0),
            "ctr":          round(d["ctr_sum"] / d["ctr_n"], 2) if d["ctr_n"] else 0,
            "cac":          round(d["cac_sum"] / d["cac_n"], 2) if d["cac_n"] else 0,
            "cpl":          cpl_by_upload.get(uid),
        })

    # Top campaigns by total spend for drill-down
    campaign_spend: dict = defaultdict(float)
    for r in rows:
        campaign_spend[r["campaign_name"]] += r.get("spend") or 0
    top_campaigns = sorted(campaign_spend.items(), key=lambda x: -x[1])[:8]

    return jsonify({"points": points, "campaigns": [c[0] for c in top_campaigns]})


@app.get("/api/reports/list")
def api_list_recent_reports():
    reports = sorted(REPORTS_DIR.glob("*.html"), reverse=True)
    return jsonify([
        {
            "filename": r.name,
            "url":      f"/report/{r.name}",
            "created":  datetime.fromtimestamp(r.stat().st_mtime).strftime("%d/%b/%Y %H:%M"),
        }
        for r in reports[:30]
    ])


@app.get("/api/export-csv/<int:upload_id>")
def api_export_csv(upload_id):
    """Export cleaned campaign-level data as CSV for a given upload."""
    import io
    db = get_db()
    campaigns = get_campaigns_for_upload(db, upload_id)
    if not campaigns:
        return jsonify({"error": "No campaigns found for this upload"}), 404

    funnel_rows = get_funnel_for_upload(db, upload_id)
    funnel_by_campaign = {r["campaign_name"]: r for r in funnel_rows}

    rows = []
    for c in campaigns:
        f = funnel_by_campaign.get(c["campaign_name"], {})
        row = {
            "platform": c.get("platform", ""),
            "campaign": c.get("campaign_name", ""),
            "spend": round(c.get("spend") or 0, 2),
            "impressions": int(c.get("impressions") or 0),
            "clicks": int(c.get("clicks") or 0),
            "conversions": round(c.get("conversions") or 0, 1),
            "conversion_value": round(c.get("conversion_value") or 0, 2),
            "roas": round(c.get("roas") or 0, 2),
            "cac": round(c.get("cac") or 0, 2),
            "ctr": round(c.get("ctr") or 0, 2),
            "cpc": round(c.get("cpc") or 0, 2),
            "severity": c.get("severity", ""),
            "wasted_spend": round(c.get("wasted_spend") or 0, 2),
        }
        if f:
            row["leads"] = f.get("leads", "")
            row["mqls"] = f.get("mqls", "")
            row["sqls"] = f.get("sqls", "")
            row["customers"] = f.get("customers", "")
            row["cost_per_lead"] = f.get("cost_per_lead", "")
            row["cost_per_mql"] = f.get("cost_per_mql", "")
            row["cost_per_customer"] = f.get("cost_per_customer", "")
        rows.append(row)

    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)

    upload = get_upload(db, upload_id)
    period = (upload or {}).get("period_label") or f"upload_{upload_id}"
    filename = f"campaigns_{period.lower().replace(' ', '_')}.csv"

    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/api/upload-campaigns/<int:upload_id>")
def api_upload_campaigns(upload_id):
    db = get_db()
    rows = get_campaigns_for_upload(db, upload_id)
    campaigns = []
    for r in rows:
        campaigns.append({
            "campaign_name": r.get("campaign_name", ""),
            "platform":      r.get("platform", ""),
            "spend":         round(r.get("spend") or 0, 2),
            "roas":          round(r.get("roas") or 0, 2),
            "conversions":   round(r.get("conversions") or 0, 0),
            "ctr":           round(r.get("ctr") or 0, 2),
        })
    return jsonify({"campaigns": campaigns})


@app.get("/api/report-actions/<int:report_id>")
def api_report_actions(report_id):
    db = get_db()
    report = get_report(db, report_id)
    if not report:
        return jsonify({"error": "Report not found"}), 404
    html_path = report.get("html_path") or report.get("report_path")
    if not html_path or not Path(html_path).exists():
        return jsonify({"actions": []})
    try:
        html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return jsonify({"actions": []})
    # Find recommendations section and extract <li> items
    rec_match = re.search(
        r'(?i)(recommendation|action|priorit)[^<]*</[^>]+>(.*?)(</section|<h[123]|<div\s+class="section)',
        html,
        re.DOTALL,
    )
    search_text = rec_match.group(2) if rec_match else html
    items = re.findall(r'<li[^>]*>(.*?)</li>', search_text, re.DOTALL | re.IGNORECASE)
    # Strip HTML tags
    clean = []
    for item in items[:10]:
        text = re.sub(r'<[^>]+>', '', item).strip()
        text = re.sub(r'\s+', ' ', text)
        if text:
            clean.append(text)
    return jsonify({"actions": clean})


# ---- Multi-Period Comparison ----

@app.get("/api/compare/<int:client_id>")
def api_compare(client_id):
    """
    Return per-campaign metrics across up to N uploads, pivoted for comparison.
    Query params: upload_ids (comma-separated, up to 4)
    Returns: {periods: [...], campaigns: [{name, platform, periods: {upload_id: {roas, spend, cac, ctr}}}]}
    """
    raw = request.args.get("upload_ids", "")
    if not raw:
        return jsonify({"error": "upload_ids required"}), 400

    try:
        upload_ids = [int(x.strip()) for x in raw.split(",") if x.strip()][:4]
    except ValueError:
        return jsonify({"error": "upload_ids must be integers"}), 400

    if not upload_ids:
        return jsonify({"error": "at least one upload_id required"}), 400

    db = get_db()

    # Fetch period labels for each upload
    periods = []
    for uid in upload_ids:
        row = get_upload(db, uid)
        if row:
            label = row.get("period_label") or str(row.get("period_start") or uid)
            periods.append({"upload_id": uid, "label": label})

    # Fetch all campaigns across all uploads in one query
    placeholders = ",".join("?" * len(upload_ids))
    rows = db.execute(f"""
        SELECT upload_id, platform, campaign_name,
               spend, roas, cac, ctr, conversions, severity
        FROM campaigns
        WHERE upload_id IN ({placeholders}) AND client_id = ?
        ORDER BY campaign_name, platform, upload_id
    """, upload_ids + [client_id]).fetchall()

    cols = [d[0] for d in db.description]
    rows = [dict(zip(cols, r)) for r in rows]

    # Pivot: {(campaign_name, platform): {upload_id: metrics}}
    pivot: dict = {}
    for r in rows:
        key = (r["campaign_name"], r["platform"])
        if key not in pivot:
            pivot[key] = {}
        pivot[key][r["upload_id"]] = {
            "spend":       round(r.get("spend") or 0, 2),
            "roas":        round(r.get("roas") or 0, 2),
            "cac":         round(r.get("cac") or 0, 2),
            "ctr":         round(r.get("ctr") or 0, 2),
            "conversions": round(r.get("conversions") or 0, 0),
            "severity":    r.get("severity", "ok"),
        }

    campaigns = [
        {
            "name":     name,
            "platform": platform,
            "periods":  period_data,
        }
        for (name, platform), period_data in sorted(pivot.items())
    ]

    return jsonify({"periods": periods, "campaigns": campaigns})


# ---- Overview Dashboard ----

@app.get("/api/overview")
def api_overview():
    """
    Return a health summary row for every client:
    last_audit_date, last_roas, open_anomaly_count, status (healthy/warning/critical).
    """
    db = get_db()
    clients = get_clients(db)
    rows = []
    for client in clients:
        cid = client["id"]

        # Latest upload with campaign data
        latest_upload_row = _q1_local(db, """
            SELECT u.id AS upload_id, u.period_label, u.uploaded_at,
                   AVG(c.roas) AS avg_roas, SUM(c.spend) AS total_spend,
                   COUNT(DISTINCT c.campaign_name) AS campaign_count
            FROM uploads u
            JOIN campaigns c ON c.upload_id = u.id
            WHERE u.client_id = ?
            GROUP BY u.id, u.period_label, u.uploaded_at
            ORDER BY u.uploaded_at DESC LIMIT 1
        """, [cid])

        # Latest audit report
        latest_report = _q1_local(db, """
            SELECT created_at FROM reports
            WHERE client_id = ? AND report_type = 'audit'
            ORDER BY created_at DESC LIMIT 1
        """, [cid])

        # Open anomaly summary
        anomaly_summary = get_anomaly_summary(db, cid)

        # Determine status
        critical = anomaly_summary.get("critical", 0)
        warning  = anomaly_summary.get("warning", 0)
        if critical > 0:
            status = "critical"
        elif warning > 0:
            status = "warning"
        else:
            status = "healthy"

        rows.append({
            "id":              cid,
            "name":            client["name"],
            "currency":        client.get("currency", "INR"),
            "last_audit":      _fmt_date(latest_report["created_at"]) if latest_report else None,
            "last_period":     (latest_upload_row or {}).get("period_label"),
            "last_roas":       round(float((latest_upload_row or {}).get("avg_roas") or 0), 2),
            "total_spend":     round(float((latest_upload_row or {}).get("total_spend") or 0), 0),
            "campaign_count":  (latest_upload_row or {}).get("campaign_count", 0),
            "anomaly_critical": critical,
            "anomaly_warning":  warning,
            "anomaly_total":    anomaly_summary.get("total", 0),
            "status":           status,
        })

    return jsonify(rows)


def _q1_local(conn, sql: str, params: list):
    """Local helper - same as db._q1 but available in app.py scope."""
    result = conn.execute(sql, params)
    rows = result.fetchall()
    if not rows:
        return None
    cols = [d[0] for d in result.description]
    return dict(zip(cols, rows[0]))


# ---- Consolidated Action Plan ----

@app.get("/api/action-plan/<int:client_id>")
def api_action_plan(client_id):
    """
    Return a merged list of actions from the latest audit report + latest budget report.
    Each action has: source (audit|budget), text, priority (high|medium|low), campaign (optional).
    """
    db = get_db()

    # Latest audit report actions
    audit_report = _q1_local(db, """
        SELECT id, html_path FROM reports
        WHERE client_id = ? AND report_type = 'audit'
        ORDER BY created_at DESC LIMIT 1
    """, [client_id])

    audit_actions = []
    if audit_report and audit_report.get("html_path"):
        html_path = audit_report["html_path"]
        if Path(html_path).exists():
            try:
                html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
                items = re.findall(r'<li[^>]*>(.*?)</li>', html, re.DOTALL | re.IGNORECASE)
                for item in items[:15]:
                    text = re.sub(r'<[^>]+>', '', item).strip()
                    text = re.sub(r'\s+', ' ', text)
                    if len(text) > 15:
                        # Simple priority heuristic
                        low_text = text.lower()
                        if any(w in low_text for w in ['pause', 'stop', 'critical', 'immediately', 'urgent']):
                            priority = 'high'
                        elif any(w in low_text for w in ['reduce', 'decrease', 'review', 'monitor']):
                            priority = 'medium'
                        else:
                            priority = 'low'
                        audit_actions.append({
                            "source": "audit",
                            "text": text,
                            "priority": priority,
                        })
            except Exception:
                pass

    # Latest budget report - get recommendations from saved report
    budget_report = _q1_local(db, """
        SELECT id, html_path FROM reports
        WHERE client_id = ? AND report_type = 'budget'
        ORDER BY created_at DESC LIMIT 1
    """, [client_id])

    budget_actions = []
    if budget_report and budget_report.get("html_path"):
        html_path = budget_report["html_path"]
        if Path(html_path).exists():
            try:
                html = Path(html_path).read_text(encoding="utf-8", errors="ignore")
                # Extract from the table rows: campaign, action, reason
                rows_match = re.findall(
                    r'<tr[^>]*>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td>.*?badge[^>]*>(.*?)</span>.*?</td>\s*<td>[^<]*</td>\s*<td>[^<]*</td>\s*<td>(.*?)</td>',
                    html, re.DOTALL | re.IGNORECASE
                )
                for m in rows_match[:8]:
                    campaign = re.sub(r'<[^>]+>', '', m[0]).strip()
                    platform = re.sub(r'<[^>]+>', '', m[1]).strip()
                    action   = re.sub(r'<[^>]+>', '', m[2]).strip().lower()
                    reason   = re.sub(r'<[^>]+>', '', m[3]).strip()
                    if campaign and action:
                        text = f"{action.title()} {campaign} ({platform}): {reason}" if reason else f"{action.title()} {campaign} ({platform})"
                        priority = 'high' if action == 'pause' else 'medium' if action == 'decrease' else 'low'
                        budget_actions.append({
                            "source": "budget",
                            "text": text,
                            "priority": priority,
                            "campaign": campaign,
                        })
            except Exception:
                pass

    # Persist to DB (upsert - preserves existing status/notes)
    all_actions_to_upsert = audit_actions + budget_actions
    if all_actions_to_upsert:
        upsert_action_items(db, client_id, all_actions_to_upsert)

    # Return from DB so status/notes/done_at are included
    db_actions = get_action_items(db, client_id)

    # Serialize datetime fields to ISO strings
    def _serialize_action(a):
        out = dict(a)
        for field in ('done_at', 'created_at', 'updated_at'):
            val = out.get(field)
            if val is not None and hasattr(val, 'isoformat'):
                out[field] = val.isoformat()
        return out

    serialized = [_serialize_action(a) for a in db_actions]

    return jsonify({
        "actions": serialized,
        "audit_report_id": audit_report["id"] if audit_report else None,
        "budget_report_id": budget_report["id"] if budget_report else None,
    })


@app.patch("/api/action-items/<int:item_id>")
def api_update_action_item(item_id):
    db = get_db()
    client_id = request.json.get('client_id')
    if not client_id:
        return jsonify({"error": "client_id required"}), 400

    status  = request.json.get('status')
    notes   = request.json.get('notes')
    done_at = request.json.get('done_at')

    # When marking done (not not_needed), snapshot the campaign's latest metrics
    if status == 'done':
        campaign = request.json.get('campaign')
        platform = request.json.get('platform')
        if campaign:
            latest = _q1_local(db, """
                SELECT id FROM uploads WHERE client_id=? ORDER BY uploaded_at DESC LIMIT 1
            """, [client_id])
            if latest:
                if platform:
                    camp_row = _q1_local(db, """
                        SELECT roas, cac, ctr, spend FROM campaigns
                        WHERE upload_id=? AND LOWER(campaign_name)=LOWER(?) AND platform=?
                        LIMIT 1
                    """, [latest['id'], campaign, platform])
                else:
                    camp_row = _q1_local(db, """
                        SELECT roas, cac, ctr, spend FROM campaigns
                        WHERE upload_id=? AND LOWER(campaign_name)=LOWER(?)
                        LIMIT 1
                    """, [latest['id'], campaign])
                if camp_row:
                    set_action_snapshot(db, item_id,
                        camp_row.get('roas'), camp_row.get('cac'),
                        camp_row.get('ctr'), camp_row.get('spend'),
                        latest['id'])

    if done_at == '':
        done_at = None

    ok = update_action_item(db, item_id, client_id,
                            status=status, notes=notes, done_at=done_at)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.get("/api/action-items/<int:item_id>/impact")
def api_action_impact(item_id):
    """
    Compare campaign metrics after done_at vs snapshot at done time.
    Returns deltas for roas, cac, ctr, spend across uploads after done_at.
    """
    db = get_db()
    item = _q1_local(db, """
        SELECT id, client_id, campaign, platform, done_at,
               snapshot_roas, snapshot_cac, snapshot_ctr, snapshot_spend,
               snapshot_upload_id
        FROM action_items WHERE id=?
    """, [item_id])

    if not item or not item.get('done_at') or not item.get('campaign'):
        return jsonify({"available": False, "reason": "No campaign or not done yet"})

    if not item.get('snapshot_roas') and not item.get('snapshot_cac'):
        return jsonify({"available": False, "reason": "No baseline snapshot"})

    # Find uploads after done_at for this client
    uploads_after = db.execute("""
        SELECT id, period_label, uploaded_at FROM uploads
        WHERE client_id=? AND uploaded_at > ?
        ORDER BY uploaded_at ASC
        LIMIT 5
    """, [item['client_id'], item['done_at']]).fetchall()

    if not uploads_after:
        return jsonify({"available": False, "reason": "No uploads yet after action was completed"})

    # Get latest campaign metrics after done_at
    latest_upload_id = uploads_after[-1][0]
    camp = _q1_local(db, """
        SELECT roas, cac, ctr, spend FROM campaigns
        WHERE upload_id=? AND LOWER(campaign_name)=LOWER(?)
        LIMIT 1
    """, [latest_upload_id, item['campaign']])

    if not camp:
        return jsonify({"available": False, "reason": "Campaign not found in later uploads"})

    def pct(current, baseline):
        if baseline is None or baseline == 0:
            return None
        return round((current - baseline) / baseline * 100, 1)

    days_elapsed = None
    try:
        from datetime import datetime as _dt
        done_dt = item['done_at']
        if hasattr(done_dt, 'timestamp'):
            elapsed = datetime.utcnow() - done_dt
        else:
            elapsed = datetime.utcnow() - _dt.fromisoformat(str(done_dt))
        days_elapsed = elapsed.days
    except Exception:
        pass

    return jsonify({
        "available": True,
        "days_elapsed": days_elapsed,
        "uploads_after": len(uploads_after),
        "snapshot": {
            "roas":  item.get('snapshot_roas'),
            "cac":   item.get('snapshot_cac'),
            "ctr":   item.get('snapshot_ctr'),
            "spend": item.get('snapshot_spend'),
        },
        "current": {
            "roas":  camp.get('roas'),
            "cac":   camp.get('cac'),
            "ctr":   camp.get('ctr'),
            "spend": camp.get('spend'),
        },
        "delta": {
            "roas": pct(camp.get('roas'), item.get('snapshot_roas')),
            "cac":  pct(camp.get('cac'),  item.get('snapshot_cac')),
            "ctr":  pct(camp.get('ctr'),  item.get('snapshot_ctr')),
        }
    })


# ---- Onboarding Wizard ----

@app.get("/api/onboarding/<int:client_id>")
def api_onboarding_status(client_id):
    db = get_db()
    try:
        status = get_onboarding_status(db, client_id)
        status["steps"] = STEPS
        return jsonify(status)
    except Exception as e:
        logger.exception("Onboarding status error")
        return jsonify({"error": str(e)}), 500


@app.post("/api/onboarding/<int:client_id>/step")
def api_onboarding_step(client_id):
    db = get_db()
    data = request.get_json() or {}
    step = data.get("step")
    if not step or not isinstance(step, int) or step < 1 or step > 6:
        return jsonify({"error": "step must be an integer 1-6"}), 400
    try:
        result = save_step_data(db, client_id, step, data)
        return jsonify(result)
    except Exception as e:
        logger.exception("Onboarding step error")
        return jsonify({"error": str(e)}), 500


@app.post("/api/onboarding/<int:client_id>/generate-brief")
def api_onboarding_generate_brief(client_id):
    db = get_db()
    try:
        url = generate_client_brief(db, client_id)
        return jsonify({"ok": True, "report_url": url})
    except Exception as e:
        logger.exception("Onboarding brief error")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print()
    print("  Ad Audit Web UI")
    print("  ----------------")
    print("  Running at: http://localhost:5000")
    print("  Press Ctrl+C to stop")
    print()
    app.run(debug=True, port=5000)
