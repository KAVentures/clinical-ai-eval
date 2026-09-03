# Canonical repository decision

## Use a new repository

Proposed canonical name:

`KAVentures/clinical-ai-eval-physician-validation`

Do **not** merge the study into `clinical-branch-intersection-security` (BISV). BISV tests a narrow deterministic branch-intersection property whose primary endpoint intentionally does not depend on an LLM judge.

Do **not** make the study dataset/manuscript a permanent part of the reusable `clinical-ai-eval` engine either. `clinical-ai-eval` should remain reusable measurement infrastructure.

## Relationship

```text
clinical-ai-eval
    reusable engine / test families / judge + review machinery
            ↑ pinned dependency
            │
clinical-ai-eval-physician-validation
    one preregistered validation study
    source manifests
    physician review schemas
    frozen model panel
    analysis
    manuscript

clinical-branch-intersection-security
    separate BISV scientific program
```

## Engine pin

Initial study development was based on:

`KAVentures/clinical-ai-eval@648ad23e8fb6b8a877217341a4bea9e4eb5bd9ca`

When the study is moved to its canonical repository, either:

1. install that exact commit as a VCS dependency; or
2. pin a later engine commit only **before** target execution, document the change, and freeze it in the preregistration manifest.

Never track `main` or an unpinned package version for the confirmatory run.

Example dependency:

```text
clinical-ai-eval @ git+https://github.com/KAVentures/clinical-ai-eval.git@648ad23e8fb6b8a877217341a4bea9e4eb5bd9ca
```

## What moves into the new repo

Move the contents of `studies/physician_validation_v1/` to repository root while preserving history if convenient:

```text
README.md
RUNBOOK.md
protocol/
configs/
prompts/
scripts/
analysis/
review/
data/       # public ID/hash-only manifests only
results/    # aggregate/de-identified outputs only
manuscript/
```

Add an Apache-2.0 license for study code unless another license is deliberately chosen. Dataset source licenses remain those of the original sources; the repo does not relicense source data.

## What never moves into Git

- HealthBench Professional case text;
- HealthBench Professional physician answers/rubrics;
- transformed HealthBench Professional text;
- raw target responses containing source case material;
- physician packets containing raw case/response text;
- API keys or provider credentials.

Those live in the external study vault and are linked to public artifacts by IDs and cryptographic hashes.

## Backport rule

If the study validates a reusable method (for example a calibrated judge policy or a validated perturbation family), the **generic implementation** may later be backported to `clinical-ai-eval` with its measured validity scope. The study cohort, manuscript, and analysis remain in the dedicated repository.
