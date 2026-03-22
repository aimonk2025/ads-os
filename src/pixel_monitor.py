"""
Meta Pixel & CAPI Health Monitor.
Calls Meta Graph API directly to assess pixel health, event quality,
and CAPI deduplication. Scores 11 checks and returns a health report.
"""

import json
import requests
from datetime import datetime, timedelta
from .db import _q, _q1


GRAPH_BASE = "https://graph.facebook.com/v19.0"

# Check name -> weight for overall score
CHECK_WEIGHTS = {
    "Pixel Active":              2,
    "PageView Events":           2,
    "Purchase Events":           3,
    "CAPI Events Present":       3,
    "CAPI Data Freshness":       2,
    "Event Match Quality":       2,
    "Deduplication Ratio":       2,
    "AddToCart Events":          1,
    "ViewContent Events":        1,
    "InitiateCheckout Events":   1,
    "Domain Verification":       1,
}

STATUS_SCORE = {"good": 100, "warning": 50, "critical": 0}


def _graph_get(path: str, token: str, params: dict = None) -> dict:
    """Make a GET request to the Meta Graph API."""
    url = f"{GRAPH_BASE}/{path}"
    p = {"access_token": token}
    if params:
        p.update(params)
    try:
        r = requests.get(url, params=p, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        try:
            err = e.response.json()
            msg = err.get("error", {}).get("message", str(e))
        except Exception:
            msg = str(e)
        return {"error": msg}
    except Exception as e:
        return {"error": str(e)}


def _check(name, status, reason, detail=None):
    return {
        "check":  name,
        "status": status,
        "reason": reason,
        "detail": detail or "",
    }


def run_pixel_health(pixel_id: str, access_token: str) -> dict:
    """
    Run all 11 health checks against the given Meta Pixel ID.
    Returns a dict with checks list, overall score, and raw stats.
    """
    checks = []
    stats = {}

    # ---- 1. Pixel details ----
    pixel_data = _graph_get(pixel_id, access_token, {
        "fields": "id,name,creation_time,last_fired_time,is_unavailable"
    })

    if "error" in pixel_data:
        return {
            "error": f"Could not reach Pixel {pixel_id}: {pixel_data['error']}",
            "pixel_id": pixel_id,
        }

    is_active = not pixel_data.get("is_unavailable", True)
    last_fired = pixel_data.get("last_fired_time", "")
    stats["pixel_name"] = pixel_data.get("name", pixel_id)
    stats["creation_time"] = pixel_data.get("creation_time", "")
    stats["last_fired_time"] = last_fired

    # Check 1: Pixel Active
    if is_active and last_fired:
        try:
            fired_dt = datetime.strptime(last_fired[:19], "%Y-%m-%dT%H:%M:%S")
            hours_ago = (datetime.utcnow() - fired_dt).total_seconds() / 3600
            if hours_ago > 48:
                checks.append(_check("Pixel Active", "warning",
                    f"Pixel last fired {hours_ago:.0f}h ago - may be inactive.",
                    f"Last fired: {last_fired}"))
            else:
                checks.append(_check("Pixel Active", "good",
                    f"Pixel is active. Last fired {hours_ago:.1f}h ago."))
        except Exception:
            checks.append(_check("Pixel Active", "good", "Pixel is active."))
    elif not is_active:
        checks.append(_check("Pixel Active", "critical",
            "Pixel is marked unavailable or inactive.",
            "Check pixel installation and domain verification."))
    else:
        checks.append(_check("Pixel Active", "warning", "No last_fired_time available."))

    # ---- 2. Event stats (last 28 days) ----
    since = int((datetime.utcnow() - timedelta(days=28)).timestamp())
    until = int(datetime.utcnow().timestamp())

    stats_data = _graph_get(f"{pixel_id}/stats", access_token, {
        "start_time": since,
        "end_time": until,
        "aggregation": "event",
    })

    event_counts = {}
    if "data" in stats_data:
        for row in stats_data["data"]:
            event_counts[row.get("event_name", "")] = row.get("count", 0)
    stats["event_counts"] = event_counts

    # Check 2: PageView
    pv = event_counts.get("PageView", 0)
    if pv == 0:
        checks.append(_check("PageView Events", "critical",
            "No PageView events in last 28 days. Pixel may not be firing.",
            "Install base pixel code on all pages."))
    elif pv < 100:
        checks.append(_check("PageView Events", "warning",
            f"Only {pv} PageView events in 28 days - low traffic or partial install."))
    else:
        checks.append(_check("PageView Events", "good", f"{pv:,} PageView events in last 28 days."))

    # Check 3: Purchase
    purchase = event_counts.get("Purchase", 0)
    if purchase == 0:
        checks.append(_check("Purchase Events", "critical",
            "No Purchase events in last 28 days.",
            "Verify Purchase event fires on order confirmation page."))
    elif purchase < 10:
        checks.append(_check("Purchase Events", "warning",
            f"Only {purchase} Purchase events - low volume may affect optimization."))
    else:
        checks.append(_check("Purchase Events", "good", f"{purchase:,} Purchase events tracked."))

    # Check 4: AddToCart
    atc = event_counts.get("AddToCart", 0)
    if atc == 0:
        checks.append(_check("AddToCart Events", "warning",
            "No AddToCart events. Missing mid-funnel signal.",
            "Add AddToCart event to cart button."))
    else:
        checks.append(_check("AddToCart Events", "good", f"{atc:,} AddToCart events."))

    # Check 5: ViewContent
    vc = event_counts.get("ViewContent", 0)
    if vc == 0:
        checks.append(_check("ViewContent Events", "warning",
            "No ViewContent events. Missing product page signal.",
            "Fire ViewContent on product/category pages."))
    else:
        checks.append(_check("ViewContent Events", "good", f"{vc:,} ViewContent events."))

    # Check 6: InitiateCheckout
    ic = event_counts.get("InitiateCheckout", 0)
    if ic == 0:
        checks.append(_check("InitiateCheckout Events", "warning",
            "No InitiateCheckout events.",
            "Track checkout initiation to improve retargeting."))
    else:
        checks.append(_check("InitiateCheckout Events", "good", f"{ic:,} InitiateCheckout events."))

    # ---- 3. CAPI (Conversions API) stats ----
    capi_data = _graph_get(f"{pixel_id}/stats", access_token, {
        "start_time": since,
        "end_time": until,
        "aggregation": "event",
        "event_source": "SERVER",
    })

    capi_counts = {}
    if "data" in capi_data:
        for row in capi_data["data"]:
            capi_counts[row.get("event_name", "")] = row.get("count", 0)
    stats["capi_counts"] = capi_counts

    # Check 7: CAPI Events Present
    total_capi = sum(capi_counts.values())
    if total_capi == 0:
        checks.append(_check("CAPI Events Present", "critical",
            "No server-side (CAPI) events detected.",
            "Implement Conversions API to improve signal quality and iOS tracking."))
    elif total_capi < 100:
        checks.append(_check("CAPI Events Present", "warning",
            f"Only {total_capi} CAPI events. Consider sending more server events."))
    else:
        checks.append(_check("CAPI Events Present", "good",
            f"{total_capi:,} server-side events received via CAPI."))

    # Check 8: CAPI Data Freshness
    capi_fresh_data = _graph_get(f"{pixel_id}/stats", access_token, {
        "start_time": int((datetime.utcnow() - timedelta(hours=24)).timestamp()),
        "end_time": until,
        "aggregation": "event",
        "event_source": "SERVER",
    })
    capi_24h = sum(r.get("count", 0) for r in capi_fresh_data.get("data", []))
    stats["capi_24h"] = capi_24h
    if total_capi == 0:
        checks.append(_check("CAPI Data Freshness", "critical",
            "No CAPI events to assess freshness."))
    elif capi_24h == 0:
        checks.append(_check("CAPI Data Freshness", "warning",
            "No CAPI events in last 24h. May be a delay or outage.",
            "Check server-side event pipeline."))
    else:
        checks.append(_check("CAPI Data Freshness", "good",
            f"{capi_24h:,} CAPI events received in last 24 hours."))

    # ---- 4. Event Match Quality ----
    emq_data = _graph_get(f"{pixel_id}/match_quality", access_token, {
        "fields": "data",
    })

    emq_scores = {}
    if "data" in emq_data:
        for row in emq_data.get("data", []):
            emq_scores[row.get("event_name", "")] = row.get("event_match_quality_score", 0)
    stats["emq_scores"] = emq_scores

    if not emq_scores:
        checks.append(_check("Event Match Quality", "warning",
            "No EMQ data available. Ensure events include customer data (email, phone).",
            "Pass hashed email/phone in event parameters for better match rates."))
    else:
        avg_emq = sum(emq_scores.values()) / len(emq_scores)
        if avg_emq < 5:
            checks.append(_check("Event Match Quality", "critical",
                f"Average EMQ score {avg_emq:.1f}/10 - very low match rate.",
                "Include more customer identifiers: email, phone, first name, last name."))
        elif avg_emq < 7:
            checks.append(_check("Event Match Quality", "warning",
                f"Average EMQ score {avg_emq:.1f}/10 - moderate. Room to improve.",
                "Add more customer data parameters to events."))
        else:
            checks.append(_check("Event Match Quality", "good",
                f"Average EMQ score {avg_emq:.1f}/10 - good match quality."))

    # ---- 5. Deduplication Ratio ----
    browser_purchase = event_counts.get("Purchase", 0)
    server_purchase  = capi_counts.get("Purchase", 0)
    stats["browser_purchase"] = browser_purchase
    stats["server_purchase"]  = server_purchase

    if browser_purchase == 0 and server_purchase == 0:
        checks.append(_check("Deduplication Ratio", "warning",
            "No Purchase events to assess deduplication."))
    elif server_purchase == 0:
        checks.append(_check("Deduplication Ratio", "critical",
            "No server-side Purchase events. Deduplication not possible.",
            "Send Purchase events via CAPI with event_id for deduplication."))
    elif browser_purchase == 0:
        checks.append(_check("Deduplication Ratio", "warning",
            "Only server-side Purchase events. Browser pixel missing?"))
    else:
        ratio = server_purchase / browser_purchase
        if ratio < 0.5:
            checks.append(_check("Deduplication Ratio", "warning",
                f"Server events ({server_purchase:,}) far below browser ({browser_purchase:,}). CAPI underreporting.",
                "Check CAPI event_id matches browser event_id for deduplication."))
        elif ratio > 2.0:
            checks.append(_check("Deduplication Ratio", "warning",
                f"Server events ({server_purchase:,}) greatly exceed browser ({browser_purchase:,}). Possible double-counting.",
                "Ensure event_id deduplication is correctly implemented."))
        else:
            checks.append(_check("Deduplication Ratio", "good",
                f"Browser: {browser_purchase:,} | Server: {server_purchase:,} | Ratio: {ratio:.2f}x - deduplication looks healthy."))

    # ---- 6. Domain Verification ----
    domain_data = _graph_get(f"{pixel_id}", access_token, {
        "fields": "owner_ad_account"
    })
    if "owner_ad_account" in domain_data:
        checks.append(_check("Domain Verification", "good",
            "Pixel is associated with an ad account. Domain verification likely complete."))
    else:
        checks.append(_check("Domain Verification", "warning",
            "Could not confirm domain verification status.",
            "Verify domain in Business Manager > Brand Safety > Domains."))

    # ---- Overall Score ----
    total_weight = 0
    weighted_score = 0
    for ch in checks:
        w = CHECK_WEIGHTS.get(ch["check"], 1)
        total_weight += w
        weighted_score += STATUS_SCORE[ch["status"]] * w
    overall_score = round(weighted_score / total_weight) if total_weight else 0

    health_label = (
        "Excellent" if overall_score >= 85 else
        "Good"      if overall_score >= 70 else
        "Needs Work" if overall_score >= 50 else
        "Critical"
    )

    return {
        "pixel_id":      pixel_id,
        "pixel_name":    stats.get("pixel_name", pixel_id),
        "overall_score": overall_score,
        "health_label":  health_label,
        "checks":        checks,
        "stats":         stats,
        "good_count":    sum(1 for c in checks if c["status"] == "good"),
        "warning_count": sum(1 for c in checks if c["status"] == "warning"),
        "critical_count":sum(1 for c in checks if c["status"] == "critical"),
        "total_checks":  len(checks),
        "run_at":        datetime.utcnow().isoformat(),
    }


def save_pixel_health(conn, client_id: int, result: dict) -> None:
    """Save pixel health report to DB."""
    try:
        conn.execute("""
            INSERT INTO pixel_health_reports (
                client_id, pixel_id, pixel_name, overall_score, health_label,
                checks_json, stats_json, run_at
            ) VALUES (?,?,?,?,?,?,?,?)
        """, [
            client_id,
            result.get("pixel_id"),
            result.get("pixel_name"),
            result.get("overall_score"),
            result.get("health_label"),
            json.dumps(result.get("checks", [])),
            json.dumps(result.get("stats", {})),
            result.get("run_at"),
        ])
        conn.commit()
    except Exception:
        pass


def get_latest_pixel_health(conn, client_id: int) -> dict | None:
    """Fetch most recent pixel health report for a client."""
    row = _q1(conn, """
        SELECT * FROM pixel_health_reports
        WHERE client_id = ?
        ORDER BY run_at DESC LIMIT 1
    """, [client_id])
    if not row:
        return None
    row["checks"] = json.loads(row.get("checks_json") or "[]")
    row["stats"]  = json.loads(row.get("stats_json") or "{}")
    return row
