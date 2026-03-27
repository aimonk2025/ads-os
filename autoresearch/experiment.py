"""
Run a single experiment and write its result to results/<id>.json.

Usage:
    python autoresearch/experiment.py <mutation_id>
    python autoresearch/experiment.py 0   # baseline (no mutation)
"""

import json
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent))
from shared import (
    MUTATIONS, TEST_INPUTS, EVALS, RUNS_PER_EXPERIMENT,
    MAX_SCORE, RESULTS_DIR, PROMPT_SNAPSHOT_DIR,
    load_baseline_prompt, run_claude, score_output,
)

def run_experiment(mutation_id: int):
    baseline_prompt = load_baseline_prompt()

    if mutation_id == 0:
        prompt = baseline_prompt
        description = "baseline - original prompt, no changes"
        status = "baseline"
        mutation_info = {}
    else:
        mutation = next((m for m in MUTATIONS if m["id"] == mutation_id), None)
        if mutation is None:
            print(f"ERROR: mutation id {mutation_id} not found")
            sys.exit(1)
        if mutation["find"] not in baseline_prompt:
            print(f"SKIP: target text for mutation {mutation_id} not found in prompt")
            result = {
                "id": mutation_id,
                "score": -1,
                "max_score": MAX_SCORE,
                "pass_rate": -1,
                "status": "skipped",
                "description": mutation["description"],
                "hypothesis": mutation["hypothesis"],
                "eval_totals": {},
                "skipped": True,
            }
            out = RESULTS_DIR / f"{mutation_id}.json"
            out.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result

        prompt = baseline_prompt.replace(mutation["find"], mutation["replace"], 1)

        # Optional second mutation for combination experiments
        if mutation.get("find2") and mutation.get("replace2"):
            if mutation["find2"] in prompt:
                prompt = prompt.replace(mutation["find2"], mutation["replace2"], 1)
            else:
                print(f"  WARNING: find2 not found for mutation {mutation_id}, applying only first mutation")
        description = mutation["description"]
        status = "pending"
        mutation_info = {"hypothesis": mutation["hypothesis"]}

    # Save prompt snapshot
    snap = PROMPT_SNAPSHOT_DIR / f"prompt_v{mutation_id}.txt"
    snap.write_text(prompt, encoding="utf-8")

    # Run all test inputs x RUNS_PER_EXPERIMENT
    eval_totals = {e["name"]: {"pass": 0, "total": 0} for e in EVALS}
    total_score = 0
    all_times = []

    for test in TEST_INPUTS:
        print(f"  [exp {mutation_id}] test={test['name']}")
        for run in range(RUNS_PER_EXPERIMENT):
            print(f"    run {run+1}/{RUNS_PER_EXPERIMENT}...", end=" ", flush=True)
            output, elapsed = run_claude(prompt, test["payload"], test["business_context"])
            all_times.append(elapsed)
            score, eval_results = score_output(output, test["payload"])
            total_score += score
            print(f"score {score}/5  {elapsed}s")
            for r in eval_results:
                eval_totals[r["name"]]["total"] += 1
                if r["passed"]:
                    eval_totals[r["name"]]["pass"] += 1

    pass_rate = round(total_score / MAX_SCORE * 100, 1) if MAX_SCORE else 0
    avg_time = round(sum(all_times) / len(all_times), 1) if all_times else 0
    min_time = round(min(all_times), 1) if all_times else 0
    max_time = round(max(all_times), 1) if all_times else 0

    result = {
        "id": mutation_id,
        "score": total_score,
        "max_score": MAX_SCORE,
        "pass_rate": pass_rate,
        "avg_time_s": avg_time,
        "min_time_s": min_time,
        "max_time_s": max_time,
        "status": status,
        "description": description,
        "eval_totals": eval_totals,
        **mutation_info,
    }

    out = RESULTS_DIR / f"{mutation_id}.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"  [exp {mutation_id}] DONE - score {total_score}/{MAX_SCORE} ({pass_rate}%) -> {out}")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python experiment.py <mutation_id>")
        sys.exit(1)
    run_experiment(int(sys.argv[1]))
