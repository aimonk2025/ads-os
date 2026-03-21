"""
Ad Audit Web UI - Complete Flask app
Run: python web/app.py
Opens at: http://localhost:5000
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import pandas as pd

from flask import Flask, render_template, request, jsonify, send_file, g

from src.db import (
    get_connection, create_client, get_clients, get_client, update_client, delete_client,
    create_upload, get_uploads, get_upload,
    insert_campaigns, get_campaigns_for_upload, get_platform_history,
    insert_funnel_data, get_funnel_for_upload,
    insert_granular_rows, get_granular_rows,
    insert_anomalies, get_anomalies, update_anomaly_status, get_anomaly_summary,
    save_report, get_reports, get_report, update_report_pdf_path, delete_report,
    get_budget_rules, save_budget_rules,
)
from src.granularity import build_granular_insights, granularity_to_claude_context
from src.loader import load_google_ads_with_report, load_meta_ads_with_report, load_google_ads, load_meta_ads
from src.ga_loader import load_ga4, merge_ga4_into_campaigns, build_ga_summary
from src.calculator import calculate_google_metrics, calculate_meta_metrics, build_summary
from src.funnel_loader import load_funnel
from src.claude_client import analyze
from src.renderer import render_report
from src.anomaly_detector import detect_anomalies, format_morning_brief
from src.narrator import generate_narrative
from src.budget_agent import run_budget_agent, format_reallocation_table

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
    update_client(get_db(), client_id, data["name"], data.get("currency", "INR"))
    return jsonify({"ok": True})


@app.delete("/api/clients/<int:client_id>")
def api_delete_client(client_id):
    delete_client(get_db(), client_id)
    return jsonify({"ok": True})


# ---- Upload ----

@app.post("/api/upload")
def api_upload():
    saved = {}
    try:
        use_sample = request.form.get("use_sample") == "true"
        client_id  = int(request.form.get("client_id", 0))
        compare    = request.form.get("compare") == "true"
        period_label = request.form.get("period_label", "").strip() or None

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
            google_df, g_rep, google_gran = load_google_ads_with_report(paths["google"])
            google_df = calculate_google_metrics(google_df)
            cleaning_reports.append(g_rep)
            platforms.append("google")

        if paths.get("google_prev") and compare:
            prev_google_df = load_google_ads(paths["google_prev"])

        if paths.get("meta"):
            meta_df, m_rep, meta_gran = load_meta_ads_with_report(paths["meta"])
            meta_df = calculate_meta_metrics(meta_df)
            cleaning_reports.append(m_rep)
            platforms.append("meta")

        if paths.get("meta_prev") and compare:
            prev_meta_df = load_meta_ads(paths["meta_prev"])

        if paths.get("funnel"):
            funnel_data = load_funnel(paths["funnel"])
            if funnel_data.get("cleaning_report"):
                cleaning_reports.append(funnel_data["cleaning_report"])

        ga_result = None
        if paths.get("ga"):
            try:
                ga_result = load_ga4(paths["ga"])
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
        upload_id = create_upload(db, client_id, platforms,
                                  bool(funnel_data), period_label, gran_level)

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

    claude_output, used_claude = analyze(analysis)

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
    client = get_client(db, client_id)
    client_name = (client or {}).get("name", "client")
    html_filename = f"audit_{client_name.lower().replace(' ', '_')}_{ts}.html"
    html_path = str(REPORTS_DIR / html_filename)

    render_report(analysis, claude_output, html_path)

    report_id = save_report(
        db, client_id, upload_id, "audit",
        f"Audit - {client_name} - {datetime.now().strftime('%d/%b/%Y')}",
        html_path=html_path,
    )

    # Auto-detect anomalies after audit
    anomalies_found = []
    try:
        anomalies_found = detect_anomalies(upload_id, client_id, db)
        if anomalies_found:
            insert_anomalies(db, anomalies_found)
    except Exception:
        pass

    return jsonify({
        "report_id":    report_id,
        "report_url":   f"/report/{html_filename}",
        "used_claude":  used_claude,
        "anomalies_detected": len(anomalies_found),
        "morning_brief": format_morning_brief(anomalies_found),
    })


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
    anomalies = detect_anomalies(upload_id, client_id, db)
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
    narrative_md, used_claude = generate_narrative(
        analysis, open_anomalies, tone, date_range, currency
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
<title>Performance Narrative - {client_name}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:860px;margin:40px auto;padding:0 24px;color:#0f172a;background:#f8fafc;}}
header{{background:#0f172a;color:#fff;padding:28px 32px;border-radius:12px;margin-bottom:32px;}}
header h1{{font-size:22px;font-weight:700;margin-bottom:4px;}}
header p{{color:#94a3b8;font-size:14px;}}
.badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;margin-left:8px;}}
.content{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:32px;}}
h2{{font-size:18px;font-weight:700;color:#0f172a;margin:28px 0 12px;padding-bottom:8px;border-bottom:2px solid #e2e8f0;}}
h2:first-child{{margin-top:0;}}
p{{color:#334155;line-height:1.7;margin-bottom:12px;}}
ul,ol{{padding-left:20px;margin-bottom:14px;}}
li{{color:#334155;margin-bottom:6px;line-height:1.6;}}
strong{{color:#0f172a;}}
footer{{text-align:center;color:#94a3b8;font-size:13px;margin-top:32px;}}
@media print{{body{{background:#fff;}}header{{background:#0f172a!important;-webkit-print-color-adjust:exact;print-color-adjust:exact;}}}}
</style>
</head>
<body>
<header>
  <h1>Performance Narrative <span class="badge">{tone.title()}</span></h1>
  <p>{client_name} &nbsp;|&nbsp; {date_range or datetime.now().strftime('%d/%b/%Y')}</p>
</header>
<div class="content">
{content_html}
</div>
<footer>Generated by Ad Audit &nbsp;|&nbsp; {datetime.now().strftime('%d/%b/%Y %H:%M')}</footer>
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

    recs, explanation_md, used_claude = run_budget_agent(
        analysis, rules, client_id, db, currency
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


def _write_budget_html(table_html: str, explanation_html: str,
                        recs: dict, client_name: str, currency: str, path: str) -> None:
    sym = "Rs" if currency == "INR" else currency
    freed = recs.get("total_freed_budget_pct", 0)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Budget Reallocation - {client_name}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:1000px;margin:40px auto;padding:0 24px;color:#0f172a;background:#f8fafc;}}
header{{background:#0f172a;color:#fff;padding:28px 32px;border-radius:12px;margin-bottom:32px;}}
header h1{{font-size:22px;font-weight:700;margin-bottom:4px;}}
header p{{color:#94a3b8;font-size:14px;}}
.section{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:24px;margin-bottom:20px;}}
.section-title{{font-size:16px;font-weight:600;margin-bottom:16px;}}
.data-table{{width:100%;border-collapse:collapse;font-size:14px;}}
.data-table th{{background:#1e293b;color:#e2e8f0;padding:10px 14px;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.5px;}}
.data-table td{{padding:10px 14px;border-bottom:1px solid #f1f5f9;}}
.data-table tr:nth-child(even){{background:#f8fafc;}}
.badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;}}
.badge-critical{{background:#fef2f2;color:#dc2626;border:1px solid #fca5a5;}}
.badge-warning{{background:#fffbeb;color:#d97706;border:1px solid #fcd34d;}}
.badge-ok{{background:#f0fdf4;color:#16a34a;border:1px solid #86efac;}}
h2{{font-size:17px;font-weight:600;margin:20px 0 10px;}}
p,li{{color:#334155;line-height:1.7;}}
ul{{padding-left:20px;}}
footer{{text-align:center;color:#94a3b8;font-size:13px;margin-top:32px;}}
@media print{{body{{background:#fff;}}header{{background:#0f172a!important;-webkit-print-color-adjust:exact;print-color-adjust:exact;}}}}
</style>
</head>
<body>
<header>
  <h1>Budget Reallocation Report</h1>
  <p>{client_name} &nbsp;|&nbsp; {datetime.now().strftime('%d/%b/%Y')} &nbsp;|&nbsp; Budget freed: {freed}%</p>
</header>
<div class="section">
  <div class="section-title">Reallocation Actions</div>
  {table_html}
</div>
<div class="section">
  {explanation_html}
</div>
<footer>Generated by Ad Audit &nbsp;|&nbsp; {datetime.now().strftime('%d/%b/%Y %H:%M')}</footer>
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


if __name__ == "__main__":
    print()
    print("  Ad Audit Web UI")
    print("  ----------------")
    print("  Running at: http://localhost:5000")
    print("  Press Ctrl+C to stop")
    print()
    app.run(debug=True, port=5000)
