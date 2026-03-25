"""
Revenue Forecasting Engine.
Uses weighted moving average over historical upload periods.
Applies seasonality adjustment when prior-year data exists.
"""

import json
import subprocess
from datetime import date
from .db import _q, _q1
from .claude_client import stream_prompt


# Weights for WMA: most recent period gets highest weight
WMA_WEIGHTS = [1, 2, 3, 4, 5, 6]  # oldest to newest, trimmed to available periods


def _wma(values: list[float], weights: list[int]) -> float:
    """Weighted moving average. Values and weights must be same length."""
    if not values:
        return 0.0
    w = weights[-len(values):]
    total_w = sum(w)
    if total_w == 0:
        return 0.0
    return sum(v * wt for v, wt in zip(values, w)) / total_w


def _trend_slope(values: list[float]) -> float:
    """Simple linear trend: average change per period."""
    if len(values) < 2:
        return 0.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    return sum(changes) / len(changes)


def _seasonality_factor(conn, client_id: int, target_month: int) -> float:
    """
    Compare same calendar month from prior year vs its surrounding months.
    Returns a multiplier (e.g. 1.2 = 20% above baseline in that month).
    Returns 1.0 if not enough data.
    """
    rows = _q(conn, """
        SELECT
            MONTH(COALESCE(u.period_start, CAST(u.uploaded_at AS DATE))) AS month,
            YEAR(COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)))  AS year,
            SUM(c.spend) AS spend,
            CASE WHEN SUM(c.spend) > 0
                 THEN SUM(c.conversion_value) / SUM(c.spend) ELSE 0 END AS roas
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
        GROUP BY month, year
        ORDER BY year, month
    """, [client_id])

    if len(rows) < 6:
        return 1.0  # not enough history

    # Find rows matching target month (prior year)
    same_month = [r for r in rows if r["month"] == target_month]
    if not same_month:
        return 1.0

    # Compare that month's spend vs annual average spend
    total_spends = [r["spend"] for r in rows if r["spend"] and r["spend"] > 0]
    if not total_spends:
        return 1.0
    avg_spend = sum(total_spends) / len(total_spends)
    month_spend = same_month[-1]["spend"] or 0
    if avg_spend == 0:
        return 1.0

    factor = month_spend / avg_spend
    # Clamp to reasonable range
    return max(0.5, min(2.0, factor))


def run_forecast(conn, client_id: int, horizon_days: int = 30) -> dict:
    """
    Generate forward projections for all campaigns.
    horizon_days: 7, 30, or 60.
    Returns full forecast payload.
    """
    # Get historical periods ordered oldest to newest (up to 6)
    periods = _q(conn, """
        SELECT
            u.id AS upload_id,
            u.period_label,
            u.period_start,
            u.period_end,
            COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS sort_date,
            SUM(c.spend)            AS total_spend,
            SUM(c.conversions)      AS total_conversions,
            SUM(c.conversion_value) AS total_revenue,
            CASE WHEN SUM(c.spend) > 0
                 THEN SUM(c.conversion_value) / SUM(c.spend) ELSE 0 END AS blended_roas,
            CASE WHEN SUM(c.conversions) > 0
                 THEN SUM(c.spend) / SUM(c.conversions) ELSE 0 END AS avg_cpa
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
        GROUP BY u.id, u.period_label, u.period_start, u.period_end, u.uploaded_at
        ORDER BY sort_date ASC
        LIMIT 6
    """, [client_id])

    if not periods:
        return {"error": "No historical data found. Upload campaign data first."}

    # Campaign-level history
    campaigns_hist = _q(conn, """
        SELECT
            c.campaign_name,
            c.platform,
            u.id AS upload_id,
            COALESCE(u.period_start, CAST(u.uploaded_at AS DATE)) AS sort_date,
            c.spend, c.conversions, c.conversion_value,
            c.roas, c.cac, c.ctr
        FROM campaigns c
        JOIN uploads u ON c.upload_id = u.id
        WHERE c.client_id = ?
        ORDER BY c.campaign_name, sort_date ASC
    """, [client_id])

    # Group by campaign
    camp_map: dict[str, list] = {}
    for row in campaigns_hist:
        key = f"{row['campaign_name']}|||{row['platform']}"
        camp_map.setdefault(key, []).append(row)

    # Seasonality: target month is today + half horizon
    target_month = date.today().month
    season_factor = _seasonality_factor(conn, client_id, target_month)

    # Account-level WMA forecast
    spends   = [p["total_spend"] or 0 for p in periods]
    revenues = [p["total_revenue"] or 0 for p in periods]
    roas_vals = [p["blended_roas"] or 0 for p in periods]
    conv_vals = [p["total_conversions"] or 0 for p in periods]

    base_spend   = _wma(spends, WMA_WEIGHTS)
    base_revenue = _wma(revenues, WMA_WEIGHTS)
    base_roas    = _wma(roas_vals, WMA_WEIGHTS)
    base_conv    = _wma(conv_vals, WMA_WEIGHTS)

    slope_spend   = _trend_slope(spends)
    slope_revenue = _trend_slope(revenues)

    # Scale to horizon (assume each period ~ 30 days)
    scale = horizon_days / 30.0
    proj_spend   = max(0, (base_spend + slope_spend) * scale * season_factor)
    proj_revenue = max(0, (base_revenue + slope_revenue) * scale * season_factor)
    proj_roas    = round(proj_revenue / proj_spend, 2) if proj_spend else base_roas
    proj_conv    = max(0, base_conv * scale * season_factor)

    # Trend direction
    def trend_dir(vals):
        if len(vals) < 2:
            return "flat"
        avg_change = _trend_slope(vals)
        pct = avg_change / (vals[-1] or 1) * 100
        if pct > 3:
            return "up"
        elif pct < -3:
            return "down"
        return "flat"

    account_forecast = {
        "horizon_days":    horizon_days,
        "proj_spend":      round(proj_spend, 0),
        "proj_revenue":    round(proj_revenue, 0),
        "proj_roas":       proj_roas,
        "proj_conversions": round(proj_conv, 0),
        "spend_trend":     trend_dir(spends),
        "roas_trend":      trend_dir(roas_vals),
        "season_factor":   round(season_factor, 2),
        "periods_used":    len(periods),
    }

    # Campaign-level forecasts
    campaign_forecasts = []
    for key, rows in camp_map.items():
        name, platform = key.split("|||", 1)
        c_spends = [r["spend"] or 0 for r in rows]
        c_roas   = [r["roas"] or 0 for r in rows]
        c_cpa    = [r["cac"] or 0 for r in rows]

        c_base_spend = _wma(c_spends, WMA_WEIGHTS)
        c_slope      = _trend_slope(c_spends)
        c_base_roas  = _wma(c_roas, WMA_WEIGHTS)
        c_base_cpa   = _wma(c_cpa, WMA_WEIGHTS)

        c_proj_spend = max(0, (c_base_spend + c_slope) * scale * season_factor)
        c_proj_roas  = round(c_base_roas, 2)

        campaign_forecasts.append({
            "campaign_name": name,
            "platform":      platform,
            "proj_spend":    round(c_proj_spend, 0),
            "proj_roas":     c_proj_roas,
            "proj_cpa":      round(c_base_cpa, 0),
            "trend":         trend_dir(c_spends),
            "periods_used":  len(rows),
        })

    campaign_forecasts.sort(key=lambda x: x["proj_spend"], reverse=True)

    # Historical trend for chart
    history_chart = [
        {
            "label":   p.get("period_label") or str(p.get("period_start") or ""),
            "spend":   round(p["total_spend"] or 0, 0),
            "revenue": round(p["total_revenue"] or 0, 0),
            "roas":    round(p["blended_roas"] or 0, 2),
        }
        for p in periods
    ]

    return {
        "account":    account_forecast,
        "campaigns":  campaign_forecasts,
        "history":    history_chart,
        "horizon_days": horizon_days,
    }


def _build_forecast_prompt(forecast: dict, client_name: str, currency: str, business_context: str = "") -> tuple[str, dict, str]:
    """Returns (prompt, acc, sym) for reuse by both blocking and streaming variants."""
    acc = forecast["account"]
    camps = forecast["campaigns"][:5]
    sym = {"INR": "Rs", "USD": "$", "GBP": "£", "EUR": "€", "AED": "AED"}.get(currency, currency)
    payload = (
        f"Client: {client_name}\n"
        f"Forecast horizon: {acc['horizon_days']} days\n"
        f"Projected spend: {sym} {acc['proj_spend']:,.0f}\n"
        f"Projected revenue: {sym} {acc['proj_revenue']:,.0f}\n"
        f"Projected blended ROAS: {acc['proj_roas']}x\n"
        f"Projected conversions: {acc['proj_conversions']:,.0f}\n"
        f"Spend trend: {acc['spend_trend']}\n"
        f"ROAS trend: {acc['roas_trend']}\n"
        f"Seasonality factor applied: {acc['season_factor']}x\n"
        f"Periods used for model: {acc['periods_used']}\n\n"
        f"Top campaigns by projected spend:\n"
        + "\n".join(
            f"- {c['campaign_name']} ({c['platform']}): "
            f"{sym} {c['proj_spend']:,.0f} spend, {c['proj_roas']}x ROAS, trend={c['trend']}"
            for c in camps
        )
    )
    context_block = f"\n\n{business_context}" if business_context else ""
    prompt = (
        "You are a senior performance marketing strategist. "
        "Based on the following forecast data, write a concise 'What to Expect Next' narrative "
        "(2-3 short paragraphs, clean markdown). "
        "Highlight the projected spend and ROAS, flag any trend concerns, "
        "and give 1-2 concrete actions the team should take before the period starts. "
        "Be direct. No preamble.\n\n"
        + payload + context_block
    )
    return prompt, acc, sym


def _forecast_fallback(acc: dict, sym: str) -> str:
    trend_label = {"up": "improving", "down": "declining", "flat": "stable"}.get(acc["spend_trend"], "stable")
    return (
        f"**{acc['horizon_days']}-Day Forecast**\n\n"
        f"Based on {acc['periods_used']} historical periods, projected spend is "
        f"**{sym} {acc['proj_spend']:,.0f}** with a blended ROAS of **{acc['proj_roas']}x** "
        f"and an estimated **{acc['proj_conversions']:,.0f} conversions**.\n\n"
        f"Spend trend is **{trend_label}**. "
        f"A seasonality factor of **{acc['season_factor']}x** has been applied based on historical monthly patterns.\n\n"
        f"Review top campaigns before the period begins and adjust budgets to align with these projections."
    )


def stream_forecast_narrative(forecast: dict, client_name: str, currency: str, business_context: str = ""):
    """Yields ('chunk', text), ('done', full_text), or ('fallback', fallback_text)."""
    if not forecast or "error" in forecast:
        yield ('fallback', "")
        return
    prompt, acc, sym = _build_forecast_prompt(forecast, client_name, currency, business_context)
    for event_type, text in stream_prompt(prompt):
        if event_type == 'chunk':
            yield ('chunk', text)
        elif event_type == 'done':
            import re as _re
            cleaned = _re.sub(r"^#{1,4}\s+.+\n?", "", text, flags=_re.MULTILINE).strip()
            yield ('done', cleaned)
            return
        elif event_type == 'fallback':
            yield ('fallback', _forecast_fallback(acc, sym))
            return


def generate_forecast_narrative(forecast: dict, client_name: str, currency: str, business_context: str = "") -> str:
    """Ask Claude to narrate the forecast. Returns markdown string."""
    if not forecast or "error" in forecast:
        return ""
    prompt, acc, sym = _build_forecast_prompt(forecast, client_name, currency, business_context)
    try:
        result = subprocess.run(
            ["claude", "--print", "--output-format", "text", prompt],
            capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            import re as _re
            return _re.sub(r"^#{1,4}\s+.+\n?", "", result.stdout.strip(), flags=_re.MULTILINE).strip()
    except Exception:
        pass
    return _forecast_fallback(acc, sym)
