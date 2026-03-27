# Ads OS Autoresearch

Autonomous optimizer for the audit report system prompt in `src/claude_client.py`.

Runs the prompt against fixed test payloads, scores each output against binary evals,
tries one mutation at a time, and keeps changes that improve the score.

Result outputs (scores, logs, prompt snapshots) are local only - not committed to the repo.

---

## How to run

```bash
cd E:/PROJECTS/GrowthX/ads-os
python autoresearch/runner.py
```

Open `autoresearch/dashboard.html` in your browser to watch live progress.

---

## What it tests

3 test scenarios (fixed inputs, never change between experiments):
- **b2b_saas_full** - Google + Meta + funnel + keyword-level data
- **ecommerce_google_only** - Google only, no funnel
- **leadgen_meta_only** - Meta only, lead gen client

5 binary evals scored on every output:
1. **Specific entity names** - every recommendation names a campaign/keyword/ad set
2. **Concrete actions** - imperative verbs with measurable changes, no hedging
3. **Confidence scores present** - every rec has `[Confidence: High/Medium/Low]`
4. **No filler language** - no "leverage", "it is important to", etc.
5. **Budget Reallocation has numbers** - every budget action has a % or Rs figure

---

## Architecture: two separate systems

Autoresearch is one of two systems that improve recommendation quality over time.
They serve different purposes and run at different times.

### System 1: Prompt optimization (this file - developer tool)

- Run manually by the developer
- Improves the system prompt in `src/claude_client.py` using synthetic test cases
- Expensive (many Claude API calls) - never runs on user machines
- When a better prompt is found, the developer commits it to the repo
- All users get the improvement on next pull

### System 2: Local learning from real data (`src/learning.py` - runs per user)

- Runs automatically after every upload, triggered from `web/app.py`
- Mines each user's own DuckDB history (no cross-user data)
- No Claude API calls - pure SQL + arithmetic
- Produces two outputs:
  - `client_benchmarks` table: per-client, per-platform medians (ROAS, CPC, CAC, CTR) updated each upload
  - `outcome_signals` table: tracks whether acted-on recommendations improved metrics
- Output is injected into the audit prompt as account history context alongside industry benchmarks

The two systems complement each other: autoresearch improves the prompt centrally,
learning.py calibrates recommendations to each user's real account history locally.

---

## What it produces (autoresearch/results/ - local only, not committed)

| File | What it is |
|---|---|
| `results.json` | Live data powering the dashboard |
| `results.tsv` | Score log for every experiment |
| `changelog.md` | Full mutation log with reasoning |
| `best_prompt.txt` | The winning prompt at end of run |
| `prompt_snapshots/` | Every prompt variant tried |

---

## Applying the winning prompt

When the run finishes, `results/best_prompt.txt` contains the improved prompt.

To apply it back to the product:
1. Open `src/claude_client.py`
2. Replace the contents of `SYSTEM_PROMPT = """..."""` with the contents of `best_prompt.txt`
3. Test with a real upload to confirm the improvement holds

The original prompt is never auto-modified. You review and apply manually.

---

## Adding more mutations

Edit the `MUTATIONS` list in `runner.py`. Each mutation needs:

```python
{
    "id": 8,
    "description": "One-line description of the change",
    "hypothesis": "Why this is expected to help",
    "find": "exact text to find in the current best prompt",
    "replace": "replacement text",
}
```

Keep mutations targeted - one change per entry. Do not rewrite large sections at once.

---

## Adding more evals

Edit the `EVALS` list in `runner.py`. Every eval must be binary:

```python
{
    "name": "Short name",
    "question": "Yes/no question about the output",
    "pass": "What 'yes' looks like - specific",
    "fail": "What triggers 'no' - specific",
}
```
