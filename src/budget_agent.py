"""
Budget Reallocation Agent - rule-based + Claude reasoning layer.
Rules always enforced. Claude adds explanation and nuance.
"""

import json
import subprocess
from typing import Optional


BUDGET_AGENT_PROMPT = """You are a performance marketing budget strategist. You receive campaign metrics and client-defined rules.

Your job:
1. Identify campaigns violating minimum ROAS or CPL thresholds
2. Recommend specific budget shifts (increase / decrease / pause) in percentage terms
3. Never recommend shifting more than the max_shift_pct in a single cycle
4. Prioritize: stop bleeding first, then scale winners

Output EXACTLY this structure - a JSON block first, then markdown explanation:

```json
{
  "recommendations": [
    {
      "campaign": "Campaign Name",
      "platform": "google|meta",
      "action": "increase|decrease|pause",
      "shift_pct": 15,
      "confidence": 85,
      "reason": "One sentence reason"
    }
  ],
  "total_freed_budget_pct": 0,
  "reallocation_targets": [
    {"campaign": "Campaign Name", "platform": "google|meta", "increase_pct": 10}
  ]
}
```

Then follow with:
## Reallocation Summary
## Risk Flags
## Confidence Notes

Confidence score (0-100): based on data quality, history depth, and rule alignment.
Currency: use the currency symbol provided."""


def run_budget_agent(analysis_data: dict, rules: dict,
                     client_id: int, conn,
                     currency: str = "INR",
                     business_context: str = "") -> tuple[dict, str, bool]:
    rule_recs = _compute_rule_based(analysis_data, rules)
    payload = _build_payload(analysis_data, rules, rule_recs, currency)
    context_section = (
        f"\n\nBusiness Context (use this to interpret campaign intent and budget priorities):\n{business_context.strip()}"
        if business_context and business_context.strip() else ""
    )
    prompt = f"{BUDGET_AGENT_PROMPT}\n\nData:\n{json.dumps(payload, indent=2)}{context_section}"

    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True, text=True, timeout=90
        )
        if result.returncode == 0 and result.stdout.strip():
            parsed = _parse_claude_output(result.stdout.strip(), rule_recs)
            return parsed, result.stdout.strip(), True
    except Exception:
        pass

    explanation = _fallback_explanation(rule_recs, rules, currency)
    return rule_recs, explanation, False


def _build_payload(analysis_data: dict, rules: dict,
                   rule_recs: dict, currency: str) -> dict:
    payload: dict = {
        "currency": currency,
        "rules": {
            "google_min_roas": rules.get("google_min_roas", 2.0),
            "meta_min_roas": rules.get("meta_min_roas", 2.0),
            "google_min_cpl": rules.get("google_min_cpl"),
            "meta_min_cpl": rules.get("meta_min_cpl"),
            "max_shift_pct": rules.get("max_shift_pct", 20.0),
        },
        "pre_computed_violations": rule_recs.get("violations", []),
        "campaigns": {},
    }
    for platform in ["google", "meta"]:
        pdata = analysis_data.get(platform)
        if pdata:
            payload["campaigns"][platform] = [
                {
                    "name":      c["name"],
                    "spend":     c["spend"],
                    "roas":      c["roas"],
                    "cac":       c["cac"],
                    "ctr":       c["ctr"],
                    "severity":  c["severity"],
                    "wasted":    c["wasted"],
                }
                for c in pdata.get("campaigns", [])
            ]
    return payload


def _compute_rule_based(analysis_data: dict, rules: dict) -> dict:
    violations = []
    winners = []
    recommendations = []
    total_freed = 0.0
    max_shift = rules.get("max_shift_pct", 20.0)

    for platform in ["google", "meta"]:
        pdata = analysis_data.get(platform)
        if not pdata:
            continue
        min_roas = rules.get(f"{platform}_min_roas", 2.0)
        min_cpl  = rules.get(f"{platform}_min_cpl")

        for c in pdata.get("campaigns", []):
            roas_ok = c["roas"] >= min_roas
            cpl_ok  = (min_cpl is None) or (c["cac"] <= min_cpl) or (c["cac"] == 0)

            if not roas_ok or not cpl_ok:
                reason_parts = []
                if not roas_ok:
                    reason_parts.append(f"ROAS {c['roas']:.2f}x below min {min_roas}x")
                if not cpl_ok:
                    reason_parts.append(f"CPL {c['cac']:,.0f} above max {min_cpl:,.0f}")
                reason = "; ".join(reason_parts)

                action = "pause" if c["roas"] < 1.0 else "decrease"
                shift = min(max_shift, 50.0) if action == "pause" else min(max_shift, 25.0)

                violations.append({
                    "campaign": c["name"],
                    "platform": platform,
                    "roas": c["roas"],
                    "cac": c["cac"],
                    "reason": reason,
                })
                recommendations.append({
                    "campaign":   c["name"],
                    "platform":   platform,
                    "action":     action,
                    "shift_pct":  shift,
                    "confidence": 90 if c["roas"] < 1.0 else 75,
                    "reason":     reason,
                })
                total_freed += shift
            elif c["severity"] == "ok" and c["roas"] >= min_roas * 1.5:
                winners.append({"campaign": c["name"], "platform": platform, "roas": c["roas"]})

    # Cap total freed and compute reallocation targets
    reallocation_targets = []
    if winners and total_freed > 0:
        per_winner = min(max_shift, total_freed / len(winners))
        for w in winners[:3]:
            reallocation_targets.append({
                "campaign":    w["campaign"],
                "platform":    w["platform"],
                "increase_pct": round(per_winner, 1),
            })

    return {
        "recommendations":         recommendations,
        "violations":              violations,
        "total_freed_budget_pct":  round(total_freed, 1),
        "reallocation_targets":    reallocation_targets,
    }


def _parse_claude_output(output: str, fallback: dict) -> dict:
    import re
    match = re.search(r"```json\s*([\s\S]+?)\s*```", output)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return fallback


def _fallback_explanation(recs: dict, rules: dict, currency: str) -> str:
    sym = "Rs" if currency == "INR" else currency
    lines = []
    lines.append("## Reallocation Summary")

    if not recs["recommendations"]:
        lines.append("All campaigns are within acceptable thresholds. No reallocation needed this cycle.")
    else:
        lines.append(f"{len(recs['recommendations'])} campaign(s) flagged for budget changes.")
        for r in recs["recommendations"]:
            lines.append(f"- **{r['campaign']}** ({r['platform'].title()}): "
                        f"{r['action'].title()} by {r['shift_pct']}%. {r['reason']}")

    lines.append("")
    lines.append("## Risk Flags")
    pauses = [r for r in recs["recommendations"] if r["action"] == "pause"]
    if pauses:
        lines.append(f"- {len(pauses)} campaign(s) recommended for pause. "
                    "Verify these are not brand-critical before pausing.")
    else:
        lines.append("- No high-risk actions required.")

    lines.append("")
    lines.append("## Confidence Notes")
    lines.append(f"- Rules-based analysis only (Claude unavailable).")
    lines.append(f"- Thresholds used: Google min ROAS {rules.get('google_min_roas', 2.0)}x, "
                f"Meta min ROAS {rules.get('meta_min_roas', 2.0)}x.")
    lines.append(f"- Max single-cycle shift capped at {rules.get('max_shift_pct', 20)}%.")

    return "\n".join(lines)


def format_reallocation_table(recs: dict, currency: str = "INR") -> str:
    sym = "Rs" if currency == "INR" else currency
    if not recs.get("recommendations"):
        return "<p>No reallocation actions required this cycle.</p>"

    rows = []
    for r in recs["recommendations"]:
        action_class = {"pause": "critical", "decrease": "warning", "increase": "ok"}.get(r["action"], "")
        rows.append(f"""
        <tr>
          <td>{r['campaign']}</td>
          <td>{r['platform'].title()}</td>
          <td><span class="badge badge-{action_class}">{r['action'].title()}</span></td>
          <td>{r['shift_pct']}%</td>
          <td>{r.get('confidence', 75)}%</td>
          <td>{r['reason']}</td>
        </tr>""")

    return f"""
    <table class="data-table">
      <thead>
        <tr>
          <th>Campaign</th><th>Platform</th><th>Action</th>
          <th>Shift %</th><th>Confidence</th><th>Reason</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>"""
