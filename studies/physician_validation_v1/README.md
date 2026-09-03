# Clinical AI Eval — Physician Validation Study v1

**Status:** pre-results study scaffold. This directory is staged on a study branch only. The intended canonical publication home is a dedicated repository (proposed: `KAVentures/clinical-ai-eval-physician-validation`) that pins a specific `clinical-ai-eval` commit as its measurement engine.

This study is **not** the BISV study. `clinical-branch-intersection-security` tests a narrow deterministic branch-intersection consistency property. This study validates the broader perturbation-based `clinical-ai-eval` measurement system against blinded physician judgments.

## Primary question

> When clinically important information is removed or contradicted, can a blinded automated evaluator detect unsafe over-commitment and related failures with a measured error profile relative to physicians, and how often do frontier clinical AI systems exhibit those failures?

The measurement-validity question is primary. Cross-model performance comparisons are secondary.

## Source cohorts

### Primary: HealthBench Professional

- Public source: `openai/healthbench-professional`
- License: MIT
- Released set: 525 examples
- Eligible source use case: `consult` only (236 examples)
- Prespecified final target: 150 physician-valid cases
- Stratum quotas preserve the released care-consult composition:
  - good-faith / typical: 53
  - good-faith / difficult: 38
  - red-teaming / difficult: 59

The benchmark authors request that examples not be reproduced publicly. Therefore **raw prompts, physician responses, rubrics, or transformed case text must never be committed to this repository**. Only source IDs, metadata, hashes, validation status, and derived aggregate results may be public.

### External replication: Real-POCQi

- Public source: `jjfenglab/Real-POCQi`
- License: CC BY 4.0
- 620 real physician point-of-care queries across 30 specialties
- Prespecified target: 50 patient-specific, decision-relevant cases after physician screening

Real-POCQi is secondary/external validation and must not alter the HealthBench Professional primary analysis.

## Physician team

- Reviewer A: investigator physician
- Reviewer B: independent physician
- Reviewer C: independent adjudicator for disagreements

Reviewer identities are frozen before unblinding. A and B review independently. C sees only cases requiring adjudication.

## Study workflow

1. `select_cases.py` retrieves the pinned public datasets and builds deterministic candidate queues.
2. Raw text is written only to a local/private vault; the public manifest contains IDs and hashes only.
3. `draft_perturbations.py` creates structured draft perturbations for review. Draft generation is **case construction, not measurement** and cannot confer validity.
4. Physicians A and B validate original-case coherence and each proposed perturbation. C adjudicates disagreements.
5. Cases are accepted sequentially by deterministic within-stratum priority until the prespecified quotas are filled. No case is selected because a target model succeeds or fails on it.
6. Freeze the case pack, perturbation pack, protocol, exact target-model IDs, judge-model IDs, prompts, sampling parameters, and `clinical-ai-eval` commit hash.
7. Run four provider-diverse target models on original + primary perturbation.
8. Score frozen responses using blinded automated judges. Rubric-aware/cued judging is secondary only.
9. Build the prespecified physician calibration sample independently of judge outputs.
10. Physicians A and B rate the blinded sample; C adjudicates disagreements.
11. Estimate judge operating characteristics against the physician reference and report model robustness outcomes separately.

## Public/private boundary

Public:

- study protocol and statistical analysis plan;
- source dataset versions and hashes;
- source IDs and non-content metadata;
- selection algorithm and randomization seed;
- perturbation type labels and content hashes;
- target/judge model IDs and inference settings;
- de-identified physician labels after permitted release;
- analysis code and aggregate results.

Private/gitignored:

- HealthBench Professional case text;
- HealthBench Professional physician answers and rubrics;
- transformed case text;
- reviewer packets containing case/response text;
- API keys;
- any source material whose terms or contamination guidance argue against republication.

## Start

```bash
python studies/physician_validation_v1/scripts/select_cases.py \
  --vault /secure/path/clinical_eval_validation_v1
```

This creates a public ID-only candidate manifest plus private reviewer/source files in the vault. Do **not** run target models until source/perturbation validation is complete and the protocol is locked.
