# End-to-end runbook

This study is staged inside `clinical-ai-eval` for development only. Its intended publication home is a dedicated study repository pinning the engine commit. Do not merge raw study outputs or benchmark text to a public branch.

Set a vault outside the repository:

```bash
export STUDY_VAULT=/secure/path/clinical-ai-eval-physician-validation-v1
mkdir -p "$STUDY_VAULT"
```

## Phase 0 — environment

```bash
python -m pip install -e .
python -m pip install pyyaml pandas numpy
```

Put provider keys in the existing gitignored `API_KEYS.local.md` format or set `MEDROBUST_KEYS_PATH`.

## Phase 1 — source queues

```bash
python studies/physician_validation_v1/scripts/select_cases.py \
  --vault "$STUDY_VAULT"
```

Expected:

- 236 HealthBench Professional care-consult source candidates in a public ID/hash-only queue;
- a broader Real-POCQi patient/decision candidate reservoir;
- raw source records only inside `$STUDY_VAULT/sources/`.

Stop if the pinned HealthBench Professional digest or expected source counts do not match.

## Phase 2 — perturbation drafts

Dry-run a handful first. The proposed authoring model is GPT-5.6 Luna and is **not** a primary judge.

```bash
python studies/physician_validation_v1/scripts/draft_perturbations.py \
  --sources "$STUDY_VAULT/sources/healthbench_professional_candidates.private.jsonl" \
  --vault "$STUDY_VAULT" \
  --provider openai \
  --model gpt-5.6-luna \
  --limit 5
```

Inspect those five manually. If the authoring contract is behaving correctly, rerun from a clean draft output for the full reservoir (or deliberately use `--resume`).

Drafts are not valid cases.

## Phase 3 — physician construct review

```bash
python studies/physician_validation_v1/scripts/make_construct_packets.py \
  --drafts "$STUDY_VAULT/drafts/perturbations.private.jsonl" \
  --out-dir "$STUDY_VAULT/review/construct"
```

Give `construct_review_A.csv` and `construct_review_B.csv` separately to physicians A and B together with `review/REVIEW_INSTRUCTIONS.md`.

If A/B disagree on any draft, prepare a C sheet containing only those discordant rows. Reviewer C adjudicates using the same six construct questions. Do not show C which reviewer gave which answer.

Then finalize the primary casepack:

```bash
python studies/physician_validation_v1/scripts/finalize_casepack.py \
  --drafts "$STUDY_VAULT/drafts/perturbations.private.jsonl" \
  --candidate-queue studies/physician_validation_v1/data/healthbench_professional_candidate_queue.csv \
  --review-a "$STUDY_VAULT/review/construct/construct_review_A.csv" \
  --review-b "$STUDY_VAULT/review/construct/construct_review_B.csv" \
  --adjudication-c "$STUDY_VAULT/review/construct/construct_review_C.csv" \
  --vault "$STUDY_VAULT" \
  --public-out studies/physician_validation_v1/data/primary_casepack_manifest.csv
```

Expected primary pack: exactly 150 cases with source quotas 53/38/59 and one locked primary perturbation per case.

## Phase 4 — freeze the model panel and study lock

Dry-run each provider/model ID on a non-study prompt first.

Review `configs/model_panel.yaml`. Verify exact API identifiers and request parameters against provider APIs. Then change only:

```yaml
frozen: true
```

Commit that change together with the locked protocol. Record the commit SHA before any primary target call.

Do not substitute a model after execution begins. A provider outage is a documented protocol deviation, not permission to silently change the panel.

## Phase 5 — target-model execution

```bash
python studies/physician_validation_v1/scripts/run_targets.py \
  --casepack "$STUDY_VAULT/casepack/primary_hbp_150.private.jsonl" \
  --models studies/physician_validation_v1/configs/model_panel.yaml \
  --vault "$STUDY_VAULT" \
  --public-manifest studies/physician_validation_v1/data/target_response_manifest.csv
```

Expected: 150 cases × 2 presentations × 4 targets = **1,200 frozen target responses** (including retained API/empty failures).

## Phase 6 — select physician calibration BEFORE judges

This ordering is mandatory.

```bash
python studies/physician_validation_v1/scripts/select_physician_calibration.py \
  --responses "$STUDY_VAULT/responses/target_responses.private.jsonl" \
  --vault "$STUDY_VAULT" \
  --public-manifest studies/physician_validation_v1/data/physician_calibration_selection.csv
```

Expected: 60 source cases per target × both presentations × 4 targets = **480 response cells**.

Create independent blinded A/B packets:

```bash
python studies/physician_validation_v1/scripts/make_response_packets.py \
  --units "$STUDY_VAULT/review/physician_calibration_units.private.jsonl" \
  --out-dir "$STUDY_VAULT/review/responses"
```

The packet must contain no target ID, provider, perturbation type, source stratum, or automated label.

## Phase 7 — automated judges

After the physician calibration sample is frozen, run the primary blinded judges:

```bash
python studies/physician_validation_v1/scripts/run_judges.py \
  --responses "$STUDY_VAULT/responses/target_responses.private.jsonl" \
  --casepack "$STUDY_VAULT/casepack/primary_hbp_150.private.jsonl" \
  --models studies/physician_validation_v1/configs/model_panel.yaml \
  --vault "$STUDY_VAULT" \
  --public-manifest studies/physician_validation_v1/data/judge_manifest.csv
```

Run `--include-secondary` only as the prespecified sensitivity/cueing analysis. Never combine cued judges with the blinded primary vote.

## Phase 8 — physician response review and C adjudication

After A and B independently complete their 480-cell response packets:

```bash
python studies/physician_validation_v1/scripts/response_adjudication.py prepare \
  --units "$STUDY_VAULT/review/physician_calibration_units.private.jsonl" \
  --review-a "$STUDY_VAULT/review/responses/response_review_A.csv" \
  --review-b "$STUDY_VAULT/review/responses/response_review_B.csv" \
  --out "$STUDY_VAULT/review/responses/response_adjudication_C.csv"
```

Give only that generated sheet to C. Then:

```bash
python studies/physician_validation_v1/scripts/response_adjudication.py finalize \
  --units "$STUDY_VAULT/review/physician_calibration_units.private.jsonl" \
  --review-a "$STUDY_VAULT/review/responses/response_review_A.csv" \
  --review-b "$STUDY_VAULT/review/responses/response_review_B.csv" \
  --review-c "$STUDY_VAULT/review/responses/response_adjudication_C.csv" \
  --out "$STUDY_VAULT/review/physician_reference.private.csv"
```

## Phase 9 — primary validation analysis

```bash
python studies/physician_validation_v1/analysis/analyze_judge_validation.py \
  --physician-reference "$STUDY_VAULT/review/physician_reference.private.csv" \
  --judge-scores "$STUDY_VAULT/judges/judge_scores.private.jsonl" \
  --models studies/physician_validation_v1/configs/model_panel.yaml \
  --out studies/physician_validation_v1/results/judge_validation.csv \
  --bootstrap 10000 \
  --seed 20260903
```

Only after this point interpret automated judge performance against physicians.

## Phase 10 — Real-POCQi replication

Repeat the construct-validation pipeline on the deterministic Real-POCQi candidate queue until 50 physician-valid patient-specific cases are obtained. Freeze that pack separately. Do not use it to modify thresholds or choices made from the primary HealthBench Professional-derived cohort.

## Non-negotiable integrity checks

- no raw HealthBench Professional text in Git;
- no target execution before construct-valid casepack lock;
- calibration sample selected before judge results are inspected;
- target responses frozen before judging;
- physician response packets contain opaque IDs only;
- A/B work independently;
- C sees only discordances;
- no cued judge in a blinded quorum;
- no collapsed safety/helpfulness score;
- every model ID and provider parameter frozen and reported;
- every deviation logged before analysis.
