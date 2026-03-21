"""
Anomaly detector - statistical detection on campaign metrics vs DuckDB history.

Compares latest upload against rolling 7/14/30-day baselines.
Requires at least 3 prior data points to establish a baseline.
"""

import numpy as np
from typing import Optional

CPL_SPIKE_THRESHOLD  = 0.30   # 30% above baseline
ROAS_DROP_THRESHOLD  = 0.20   # 20% below baseline
CTR_DROP_THRESHOLD   = 0.25   # 25% below baseline
PACING_THRESHOLD     = 0.15   # 15% off expected


def _pct_diff(current: float, baseline: float) -> float:
    if not baseline:
        return 0.0
    return (current - baseline) / baseline


def _assign_severity(pct_change: float, threshold: float) -> str:
    ratio = abs(pct_change) / threshold
    if ratio >= 2.0:
        return "critical"
    elif ratio >= 1.0:
        return "warning"
    return "info"


def _build_baseline(history: list, metric_key: str) -> Optional[dict]:
    values = [r[metric_key] for r in history if r.get(metric_key) is not None]
    if len(values) < 3:
        return None
    arr = np.array(values, dtype=float)
    n = len(arr)
    return {
        "mean_7d":  float(np.mean(arr[-7:]  if n >= 7  else arr)),
        "mean_14d": float(np.mean(arr[-14:] if n >= 14 else arr)),
        "mean_30d": float(np.mean(arr)),
        "std":      float(np.std(arr)) if n > 1 else 0.0,
        "count":    n,
    }


def _make_anomaly(client_id, upload_id, campaign_name, platform,
                  metric, current, baseline_val, pct_change, direction, severity, desc):
    return {
        "client_id":      client_id,
        "upload_id":      upload_id,
        "campaign_name":  campaign_name,
        "platform":       platform,
        "metric":         metric,
        "current_value":  round(current, 4),
        "baseline_value": round(baseline_val, 4),
        "pct_change":     round(pct_change * 100, 1),
        "direction":      direction,
        "severity":       severity,
        "description":    desc,
    }


def detect_anomalies(current_upload_id: int, client_id: int,
                     conn, lookback_days: int = 30) -> list:
    from src.db import get_campaigns_for_upload, get_campaign_history

    current_campaigns = get_campaigns_for_upload(conn, current_upload_id)
    if not current_campaigns:
        return []

    anomalies = []

    for c in current_campaigns:
        name     = c["campaign_name"]
        platform = c["platform"]

        history = get_campaign_history(conn, client_id, name, platform, lookback_days)
        # Exclude the current upload from history baseline
        history = [h for h in history if h["upload_id"] != current_upload_id]

        # --- CAC / CPL check ---
        if c.get("cac") and c["cac"] > 0:
            baseline = _build_baseline(history, "cac")
            if baseline:
                pct = _pct_diff(c["cac"], baseline["mean_7d"])
                if pct > CPL_SPIKE_THRESHOLD:
                    sev = _assign_severity(pct, CPL_SPIKE_THRESHOLD)
                    anomalies.append(_make_anomaly(
                        client_id, current_upload_id, name, platform,
                        "cpl",
                        c["cac"], baseline["mean_7d"], pct,
                        "spike", sev,
                        f"CPL spiked {pct*100:.0f}% above 7-day avg "
                        f"(Rs {c['cac']:,.0f} vs Rs {baseline['mean_7d']:,.0f})"
                    ))

        # --- ROAS check ---
        if c.get("roas") is not None:
            baseline = _build_baseline(history, "roas")
            if baseline:
                pct = _pct_diff(c["roas"], baseline["mean_7d"])
                if pct < -ROAS_DROP_THRESHOLD:
                    sev = _assign_severity(abs(pct), ROAS_DROP_THRESHOLD)
                    anomalies.append(_make_anomaly(
                        client_id, current_upload_id, name, platform,
                        "roas",
                        c["roas"], baseline["mean_7d"], pct,
                        "drop", sev,
                        f"ROAS dropped {abs(pct)*100:.0f}% below 7-day avg "
                        f"({c['roas']:.2f}x vs {baseline['mean_7d']:.2f}x)"
                    ))

        # --- CTR check ---
        if c.get("ctr") and c["ctr"] > 0:
            baseline = _build_baseline(history, "ctr")
            if baseline:
                pct = _pct_diff(c["ctr"], baseline["mean_7d"])
                if pct < -CTR_DROP_THRESHOLD:
                    sev = _assign_severity(abs(pct), CTR_DROP_THRESHOLD)
                    anomalies.append(_make_anomaly(
                        client_id, current_upload_id, name, platform,
                        "ctr",
                        c["ctr"], baseline["mean_7d"], pct,
                        "drop", sev,
                        f"CTR dropped {abs(pct)*100:.0f}% below 7-day avg "
                        f"({c['ctr']:.2f}% vs {baseline['mean_7d']:.2f}%)"
                    ))

        # --- Spend pacing check ---
        if c.get("spend") and c["spend"] > 0:
            baseline = _build_baseline(history, "spend")
            if baseline:
                pct = _pct_diff(c["spend"], baseline["mean_7d"])
                if abs(pct) > PACING_THRESHOLD:
                    direction = "spike" if pct > 0 else "drop"
                    sev = _assign_severity(abs(pct), PACING_THRESHOLD)
                    anomalies.append(_make_anomaly(
                        client_id, current_upload_id, name, platform,
                        "spend_pacing",
                        c["spend"], baseline["mean_7d"], pct,
                        direction, sev,
                        f"Spend {direction} {abs(pct)*100:.0f}% vs 7-day avg "
                        f"(Rs {c['spend']:,.0f} vs Rs {baseline['mean_7d']:,.0f})"
                    ))

    return anomalies


def format_morning_brief(anomalies: list) -> str:
    if not anomalies:
        return "No anomalies detected. All campaigns performing within normal range."

    critical = [a for a in anomalies if a["severity"] == "critical"]
    warning  = [a for a in anomalies if a["severity"] == "warning"]

    parts = [f"{len(anomalies)} anomaly{'s' if len(anomalies) != 1 else ''} detected."]

    for a in (critical + warning)[:5]:
        parts.append(a["description"] + ".")

    if len(anomalies) > 5:
        parts.append(f"...and {len(anomalies) - 5} more.")

    return " ".join(parts)
