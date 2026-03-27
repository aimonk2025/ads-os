"""
Collect all experiment results, pick the winner, apply it to claude_client.py.

Usage:
    python autoresearch/collect.py
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from shared import MUTATIONS, EVALS, MAX_SCORE, RESULTS_DIR, PROMPT_SNAPSHOT_DIR, load_baseline_prompt, SRC_PROMPT_FILE

RESULTS_JSON = RESULTS_DIR / "results.json"
RESULTS_TSV = RESULTS_DIR / "results.tsv"
CHANGELOG = RESULTS_DIR / "changelog.md"

def collect():
    # Load baseline
    baseline_file = RESULTS_DIR / "0.json"
    if not baseline_file.exists():
        print("ERROR: baseline result (0.json) not found. Run: python experiment.py 0 first.")
        sys.exit(1)

    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    baseline_score = baseline["score"]
    baseline_pass_rate = baseline["pass_rate"]

    experiments = [baseline]
    best_score = baseline_score
    best_time = baseline.get("avg_time_s", 9999)
    best_id = 0

    # Load all mutation results
    for mutation in MUTATIONS:
        result_file = RESULTS_DIR / f"{mutation['id']}.json"
        if not result_file.exists():
            print(f"  WARNING: result for mutation {mutation['id']} not found, skipping")
            continue
        result = json.loads(result_file.read_text(encoding="utf-8"))
        if result.get("skipped"):
            result["status"] = "skipped"
        else:
            r_score = result["score"]
            r_time = result.get("avg_time_s", 9999)
            # Win if: higher score, OR same score but faster (speed tie-break)
            if r_score > best_score or (r_score == best_score and r_time < best_time):
                result["status"] = "keep"
                best_score = r_score
                best_time = r_time
                best_id = result["id"]
            else:
                result["status"] = "discard"
        experiments.append(result)

    best_pass_rate = round(best_score / MAX_SCORE * 100, 1) if MAX_SCORE else 0

    # Write results.tsv
    lines = ["experiment\tscore\tmax_score\tpass_rate\tavg_time_s\tstatus\tdescription"]
    for e in experiments:
        avg_t = e.get("avg_time_s", "n/a")
        lines.append(f"{e['id']}\t{e['score']}\t{e['max_score']}\t{e['pass_rate']}%\t{avg_t}s\t{e['status']}\t{e['description']}")
    RESULTS_TSV.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Write changelog
    changelog_lines = ["# Autoresearch Changelog\n"]
    for e in experiments:
        pct = e["pass_rate"]
        changelog_lines.append(f"## Experiment {e['id']} — {e['status'].upper()}\n")
        changelog_lines.append(f"**Score:** {e['score']}/{e['max_score']} ({pct}%)")
        changelog_lines.append(f"**Change:** {e['description']}")
        if e.get("hypothesis"):
            changelog_lines.append(f"**Reasoning:** {e['hypothesis']}")
        if e.get("eval_totals"):
            breakdown = " | ".join(
                f"{name}: {v['pass']}/{v['total']}"
                for name, v in e["eval_totals"].items()
            )
            changelog_lines.append(f"**Eval breakdown:** {breakdown}")
        changelog_lines.append("")
    CHANGELOG.write_text("\n".join(changelog_lines), encoding="utf-8")

    # Aggregate eval totals across all experiments for dashboard
    eval_totals_global = {e["name"]: {"pass": 0, "total": 0} for e in EVALS}
    for exp in experiments:
        for name, counts in (exp.get("eval_totals") or {}).items():
            if name in eval_totals_global:
                eval_totals_global[name]["pass"] += counts.get("pass", 0)
                eval_totals_global[name]["total"] += counts.get("total", 0)

    eval_breakdown = [
        {"name": name, "pass_count": v["pass"], "total": v["total"]}
        for name, v in eval_totals_global.items()
    ]

    # Write results.json for dashboard
    dashboard_data = {
        "skill_name": "ads-os audit prompt",
        "status": "complete",
        "current_experiment": len(experiments) - 1,
        "baseline_score": baseline_pass_rate,
        "best_score": best_pass_rate,
        "max_score": MAX_SCORE,
        "runs_per_experiment": 2,
        "test_count": 3,
        "eval_count": len(EVALS),
        "experiments": [
            {
                "id": e["id"],
                "score": e["score"],
                "max_score": e["max_score"],
                "pass_rate": e["pass_rate"],
                "status": e["status"],
                "description": e["description"],
            }
            for e in experiments
        ],
        "eval_breakdown": eval_breakdown,
        "last_updated": datetime.now().isoformat(),
    }
    RESULTS_JSON.write_text(json.dumps(dashboard_data, indent=2), encoding="utf-8")

    # Save best prompt
    best_prompt_snapshot = PROMPT_SNAPSHOT_DIR / f"prompt_v{best_id}.txt"
    best_prompt_text = best_prompt_snapshot.read_text(encoding="utf-8")
    best_path = RESULTS_DIR / "best_prompt.txt"
    best_path.write_text(best_prompt_text, encoding="utf-8")

    # Apply winning prompt to claude_client.py
    source = SRC_PROMPT_FILE.read_text(encoding="utf-8")
    new_source = re.sub(
        r'(SYSTEM_PROMPT\s*=\s*""").*?(""")',
        lambda m: m.group(1) + best_prompt_text + m.group(2),
        source,
        count=1,
        flags=re.DOTALL,
    )
    SRC_PROMPT_FILE.write_text(new_source, encoding="utf-8")

    # Print summary
    print("\n" + "=" * 60)
    print("AUTORESEARCH COMPLETE")
    print(f"Baseline:    {baseline_score}/{MAX_SCORE} ({baseline_pass_rate}%)")
    print(f"Best result: {best_score}/{MAX_SCORE} ({best_pass_rate}%) [experiment {best_id}]")
    print(f"Improvement: +{best_score - baseline_score} pts ({best_pass_rate - baseline_pass_rate:.1f}pp)")
    print(f"\nWinning prompt applied to: src/claude_client.py")
    print(f"Backup at:                 autoresearch/results/best_prompt.txt")
    print(f"Full log:                  autoresearch/results/changelog.md")
    print(f"Scores:                    autoresearch/results/results.tsv")
    print("=" * 60)

    print("\nExperiment results:")
    for e in experiments:
        marker = "<-- WINNER" if e["id"] == best_id else ""
        avg_t = e.get("avg_time_s", "n/a")
        print(f"  [{e['status']:8}] exp {e['id']}: {e['score']}/{e['max_score']} ({e['pass_rate']}%)  {avg_t}s avg  {e['description'][:55]} {marker}")

    print("\nEval breakdown (across all runs):")
    for name, v in eval_totals_global.items():
        pct = round(v["pass"] / v["total"] * 100) if v["total"] else 0
        bar = "#" * (pct // 10) + "." * (10 - pct // 10)
        print(f"  [{bar}] {pct:3}%  {name}")


if __name__ == "__main__":
    collect()
