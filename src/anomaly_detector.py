"""
Anomaly detector - statistical detection on campaign metrics vs DuckDB history.

Compares latest upload against rolling 7/14/30-day baselines.
Requires at least 3 prior data points to establish a baseline.
Supports per-client sensitivity settings and per-campaign target goals.
"""

import numpy as np
from typing import Optional

# Default thresholds (medium sensitivity)
_THRESHOLDS = {
    "low":    {"cpl": 0.50, "roas": 0.35, "ctr": 0.40, "pacing": 0.30},
    "medium": {"cpl": 0.30, "roas": 0.20, "ctr": 0.25, "pacing": 0.15},
    "high":   {"cpl": 0.15, "roas": 0.10, "ctr": 0.12, "pacing": 0.10},
}

# Expose defaults for backward compatibility
CPL_SPIKE_THRESHOLD  = _THRESHOLDS["medium"]["cpl"]
ROAS_DROP_THRESHOLD  = _THRESHOLDS["medium"]["roas"]
CTR_DROP_THRESHOLD   = _THRESHOLDS["medium"]["ctr"]
PACING_THRESHOLD     = _THRESHOLDS["medium"]["pacing"]


def _get_thresholds(sensitivity: str) -> dict:
    return _THRESHOLDS.get(sensitivity, _THRESHOLDS["medium"])


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
    # Use median instead of mean - single outlier won't skew the baseline
    return {
        "mean_7d":  float(np.median(arr[-7:]  if n >= 7  else arr)),
        "mean_14d": float(np.median(arr[-14:] if n >= 14 else arr)),
        "mean_30d": float(np.median(arr)),
        "std":      float(np.std(arr)) if n > 1 else 0.0,
        "count":    n,
    }


def _is_seasonal_pattern(current_value: float, history: list,
                          metric_key: str, current_period_month: Optional[int]) -> bool:
    """
    Seasonal smoothing: if the same calendar month last year had a similar deviation,
    suppress the anomaly - it's a known seasonal pattern, not a real issue.

    Returns True if the deviation is consistent with prior-year same-month behavior.
    Requires at least one prior-year same-month data point.
    """
    if current_period_month is None:
        return False

    # Find history records from the same month in prior years
    prior_year_same_month = []
    for h in history:
        pd_date = h.get("period_start") or h.get("period_date")
        if pd_date is None:
            continue
        try:
            if hasattr(pd_date, "month"):
                month = pd_date.month
            else:
                from datetime import datetime as _dt
                month = _dt.fromisoformat(str(pd_date)[:10]).month
        except (ValueError, TypeError):
            continue
        if month == current_period_month:
            val = h.get(metric_key)
            if val is not None:
                prior_year_same_month.append(float(val))

    if not prior_year_same_month:
        return False

    # Build a "typical same-month" value
    typical = float(np.median(prior_year_same_month))
    if typical == 0:
        return False

    # If the current value is within 25% of the typical same-month value, it's seasonal
    deviation = abs(_pct_diff(current_value, typical))
    return deviation <= 0.25


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


def _check_against_target(client_id, upload_id, campaign_name, platform,
                           current_roas, current_cac, target: dict) -> list:
    """
    Compare current campaign metrics against user-defined targets.
    Returns anomaly dicts for any target violations.
    """
    anomalies = []
    target_roas = target.get("target_roas")
    target_cpl  = target.get("target_cpl")
    target_cpa  = target.get("target_cpa")

    if target_roas and current_roas is not None:
        if current_roas < target_roas:
            pct = _pct_diff(current_roas, target_roas)
            sev = "critical" if abs(pct) >= 0.4 else "warning"
            anomalies.append(_make_anomaly(
                client_id, upload_id, campaign_name, platform,
                "roas_target",
                current_roas, target_roas, pct,
                "miss", sev,
                f"ROAS {current_roas:.2f}x misses target {target_roas:.2f}x "
                f"({abs(pct)*100:.0f}% below goal)"
            ))

    if target_cpl and current_cac and current_cac > 0:
        if current_cac > target_cpl:
            pct = _pct_diff(current_cac, target_cpl)
            sev = "critical" if pct >= 0.5 else "warning"
            anomalies.append(_make_anomaly(
                client_id, upload_id, campaign_name, platform,
                "cpl_target",
                current_cac, target_cpl, pct,
                "miss", sev,
                f"CPL {current_cac:,.0f} exceeds target {target_cpl:,.0f} "
                f"({pct*100:.0f}% over goal)"
            ))

    if target_cpa and current_cac and current_cac > 0:
        if current_cac > target_cpa:
            pct = _pct_diff(current_cac, target_cpa)
            sev = "critical" if pct >= 0.5 else "warning"
            anomalies.append(_make_anomaly(
                client_id, upload_id, campaign_name, platform,
                "cpa_target",
                current_cac, target_cpa, pct,
                "miss", sev,
                f"CPA {current_cac:,.0f} exceeds target {target_cpa:,.0f} "
                f"({pct*100:.0f}% over goal)"
            ))

    return anomalies


def _check_creative_fatigue(client_id, upload_id, campaign_name, platform,
                             history: list) -> Optional[dict]:
    """
    Detect creative fatigue: CTR declining for 3+ consecutive uploads
    without a single-period threshold breach.
    Requires at least 4 data points (3 history + current).
    """
    ctr_vals = [h.get("ctr") for h in history if h.get("ctr") is not None]
    if len(ctr_vals) < 3:
        return None

    # Check last 3 values for monotonic decline
    recent = ctr_vals[-3:]
    if recent[0] > recent[1] > recent[2]:
        total_drop = _pct_diff(recent[2], recent[0])
        # Only flag as fatigue if each individual drop was below threshold (so it wasn't caught already)
        individual_max = max(
            abs(_pct_diff(recent[1], recent[0])),
            abs(_pct_diff(recent[2], recent[1])),
        )
        if individual_max < CTR_DROP_THRESHOLD and abs(total_drop) >= 0.10:
            return _make_anomaly(
                client_id, upload_id, campaign_name, platform,
                "creative_fatigue",
                recent[2], recent[0], total_drop,
                "drop", "warning",
                f"Creative fatigue: CTR declined {abs(total_drop)*100:.0f}% over last 3 uploads "
                f"({recent[0]:.2f}% to {recent[2]:.2f}%) - consider refreshing creative"
            )
    return None


def detect_anomalies(current_upload_id: int, client_id: int,
                     conn, lookback_days: int = 30,
                     client_context: dict = None) -> list:
    from src.db import get_campaigns_for_upload, get_campaign_history, get_upload

    current_campaigns = get_campaigns_for_upload(conn, current_upload_id)
    if not current_campaigns:
        return []

    # Read sensitivity from client context
    ctx = client_context or {}
    sensitivity = ctx.get("anomaly_sensitivity", "medium")
    thresholds = _get_thresholds(sensitivity)

    # Determine current upload's month for seasonal smoothing
    current_period_month = None
    upload_row = get_upload(conn, current_upload_id)
    if upload_row:
        ps = upload_row.get("period_start")
        if ps is not None:
            try:
                if hasattr(ps, "month"):
                    current_period_month = ps.month
                else:
                    from datetime import datetime as _dt
                    current_period_month = _dt.fromisoformat(str(ps)[:10]).month
            except (ValueError, TypeError):
                pass

    # Build campaign targets lookup {campaign_name_lower: target_dict}
    campaign_targets = {}
    for t in ctx.get("campaign_targets", []):
        name = (t.get("name") or "").strip().lower()
        if name:
            campaign_targets[name] = t

    # Build campaign tags lookup {campaign_name_lower: tag}
    campaign_tags = {
        k.lower().strip(): v
        for k, v in ctx.get("campaign_tags", {}).items()
    }

    anomalies = []

    for c in current_campaigns:
        name     = c["campaign_name"]
        platform = c["platform"]
        name_lower = name.lower().strip()

        # Skip anomaly detection for campaigns tagged as "test" or "brand"
        tag = campaign_tags.get(name_lower, "")
        if tag in ("test", "brand"):
            continue

        history = get_campaign_history(conn, client_id, name, platform, lookback_days)
        # Exclude the current upload from history baseline
        history = [h for h in history if h["upload_id"] != current_upload_id]

        # Extended history (up to 400 days) used only for seasonal pattern detection
        seasonal_history = get_campaign_history(conn, client_id, name, platform, 400)
        seasonal_history = [h for h in seasonal_history if h["upload_id"] != current_upload_id]

        # --- Target goal checks (fire against user-defined targets first) ---
        target = campaign_targets.get(name_lower)
        if target:
            target_anomalies = _check_against_target(
                client_id, current_upload_id, name, platform,
                c.get("roas"), c.get("cac"), target
            )
            anomalies.extend(target_anomalies)

        # --- CAC / CPL check (vs baseline) ---
        if c.get("cac") and c["cac"] > 0:
            baseline = _build_baseline(history, "cac")
            if baseline:
                pct = _pct_diff(c["cac"], baseline["mean_7d"])
                if pct > thresholds["cpl"]:
                    if not _is_seasonal_pattern(c["cac"], seasonal_history, "cac", current_period_month):
                        sev = _assign_severity(pct, thresholds["cpl"])
                        anomalies.append(_make_anomaly(
                            client_id, current_upload_id, name, platform,
                            "cpl",
                            c["cac"], baseline["mean_7d"], pct,
                            "spike", sev,
                            f"CPL spiked {pct*100:.0f}% above 7-day avg "
                            f"({c['cac']:,.0f} vs {baseline['mean_7d']:,.0f})"
                        ))

        # --- ROAS check (vs baseline) ---
        if c.get("roas") is not None:
            baseline = _build_baseline(history, "roas")
            if baseline:
                pct = _pct_diff(c["roas"], baseline["mean_7d"])
                if pct < -thresholds["roas"]:
                    if not _is_seasonal_pattern(c["roas"], seasonal_history, "roas", current_period_month):
                        sev = _assign_severity(abs(pct), thresholds["roas"])
                        anomalies.append(_make_anomaly(
                            client_id, current_upload_id, name, platform,
                            "roas",
                            c["roas"], baseline["mean_7d"], pct,
                            "drop", sev,
                            f"ROAS dropped {abs(pct)*100:.0f}% below 7-day avg "
                            f"({c['roas']:.2f}x vs {baseline['mean_7d']:.2f}x)"
                        ))

        # --- CTR check (vs baseline) ---
        if c.get("ctr") and c["ctr"] > 0:
            baseline = _build_baseline(history, "ctr")
            if baseline:
                pct = _pct_diff(c["ctr"], baseline["mean_7d"])
                if pct < -thresholds["ctr"]:
                    if not _is_seasonal_pattern(c["ctr"], seasonal_history, "ctr", current_period_month):
                        sev = _assign_severity(abs(pct), thresholds["ctr"])
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
                if abs(pct) > thresholds["pacing"]:
                    if not _is_seasonal_pattern(c["spend"], seasonal_history, "spend", current_period_month):
                        direction = "spike" if pct > 0 else "drop"
                        sev = _assign_severity(abs(pct), thresholds["pacing"])
                        anomalies.append(_make_anomaly(
                            client_id, current_upload_id, name, platform,
                            "spend_pacing",
                            c["spend"], baseline["mean_7d"], pct,
                            direction, sev,
                            f"Spend {direction} {abs(pct)*100:.0f}% vs 7-day avg "
                            f"({c['spend']:,.0f} vs {baseline['mean_7d']:,.0f})"
                        ))

        # --- Creative fatigue check ---
        fatigue = _check_creative_fatigue(
            client_id, current_upload_id, name, platform, history
        )
        if fatigue:
            anomalies.append(fatigue)

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
