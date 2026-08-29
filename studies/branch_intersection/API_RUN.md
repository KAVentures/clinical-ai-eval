# BISV API execution

After `prepare`, use the resumable runner. It reuses `caeval/providers.py` and the existing git-ignored `API_KEYS.local.md` convention.

Example key file (never commit it):

```text
OPENAI_API_KEY = ...
ANTHROPIC_API_KEY = ...
GOOGLE_API_KEY = ...
XAI_API_KEY = ...
```

Run a **cost-capped smoke test first**:

```bash
python3 -m caeval.branch_intersection_run \
  --requests out/bisv-pilot/baseline_requests.jsonl \
  --provider openai \
  --model YOUR_EXACT_MODEL_ID \
  --out out/bisv-pilot/openai_MODEL_baseline_responses.jsonl \
  --max-calls 4
```

If those four calls are correct, resume by running the same command without `--max-calls`. Existing request IDs are skipped, so interrupted runs resume without paying twice for completed cells.

For models where deliberate reasoning is part of the intended product configuration, add `--high`. The flag is recorded in every response row. Do not mix reasoning settings inside a model's primary run.

```bash
python3 -m caeval.branch_intersection_run \
  --requests out/bisv-pilot/baseline_requests.jsonl \
  --provider anthropic \
  --model YOUR_EXACT_MODEL_ID \
  --high \
  --out out/bisv-pilot/anthropic_MODEL_baseline_responses.jsonl
```

Supported providers through the current repository layer: `openai`, `anthropic`, `google`, `xai`.

Then score:

```bash
python3 -m caeval.branch_intersection_study score \
  --cases out/bisv-pilot/validated_cases.yaml \
  --responses out/bisv-pilot/openai_MODEL_baseline_responses.jsonl \
  --out out/bisv-pilot/openai_MODEL_baseline_analysis.json
```

Use the provider's **exact API model identifier**, not a marketing nickname. Record the run date and do not silently substitute a model after the confirmatory lock.

## Cost control

The runner writes provider usage metadata when the upstream API returns it. `--max-calls` is a hard operational stop for smoke tests. Start with four calls/model, inspect output formatting, then continue the same output file.

The study intentionally avoids LLM judges for the primary BISV endpoint, so model-generation cost is the main API expense rather than generation plus an equally large judging bill.
