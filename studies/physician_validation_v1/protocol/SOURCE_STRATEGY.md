# Source and case-selection strategy

Version: draft v0.1, 2026-09-03

## Scientific principle

Cases are selected for **construct suitability before any target-model output is observed**. A source case is valuable here only if a clinically meaningful decision is present and a controlled perturbation can change the evidentiary state without changing the patient, task, or decision timepoint.

No source case may enter or leave the confirmatory cohort because a target model performed well or badly on it.

## Primary source: HealthBench Professional

Dataset: `openai/healthbench-professional`.

Pinned retrieval information is defined in `scripts/select_cases.py` and must be recorded in the frozen study manifest. The script verifies the expected file digest before parsing.

### Eligible source pool

Only `use_case == consult` examples are eligible for the primary cohort.

The released care-consult pool contains 236 examples in three source strata used here:

| source stratum | reservoir | final validated quota |
|---|---:|---:|
| good_faith / typical | 84 | 53 |
| good_faith / difficult | 59 | 38 |
| red_teaming / difficult | 93 | 59 |
| total | 236 | 150 |

Within each stratum, every source ID receives a deterministic SHA-256 priority based on the locked study seed. Physicians review in that order until the validated quota is filled.

This means failed construct-validation cases are replaced only by the next prespecified candidate in the same stratum, never by investigator preference after model results are seen.

### Clinical suitability screen

A source case proceeds to perturbation drafting only if the study team can identify:

1. a concrete clinical decision or recommendation;
2. the decision timepoint represented by the prompt;
3. at least one piece of information whose status could plausibly change what a safe response should do;
4. a transformation that leaves the patient and clinical question materially unchanged.

Examples that are essentially literature-search, prevalence, mechanism, trial-identification, or general educational questions are excluded even if metadata labels them as consultation.

### Public-data rule

HealthBench Professional examples, physician responses, rubrics, and derived transformed text are not copied into the public repository. Public artifacts contain source IDs, source metadata, source-file revision/digest, content hashes, validation status, and aggregate results only.

## Secondary source: Real-POCQi

Dataset: `jjfenglab/Real-POCQi`.

The study uses Real-POCQi only as an external replication cohort. The source contains real physician point-of-care questions, but many questions are factual/research queries rather than patient-specific decisions.

`scripts/select_cases.py` therefore applies a **pre-model, reproducible lexical screen** to produce a broad candidate reservoir requiring both:

- patient/context signals; and
- decision/management signals.

Obvious prevalence/trial/research-style question openings are excluded by the lexical screen.

The lexical screen is deliberately permissive. It is not a clinical classifier and cannot confer eligibility. Physicians apply the same construct-validity criteria as for HealthBench Professional. The first 50 physician-valid cases in deterministic priority order form the external cohort.

## Perturbation drafting

For each eligible source case, the drafting stage may propose:

- one `missing_information` variant;
- one `conflicting_evidence` variant.

A draft generator may be an LLM-assisted authoring tool, but its output has **zero evidentiary status**. Every retained perturbation requires independent physician validation.

To reduce shared-model circularity, the model used to author perturbation drafts must not be one of the primary automated judges. Preferably it is also not one of the primary target models. If unavoidable, this overlap is declared and a sensitivity analysis excludes that model family where relevant.

## Missing-information drafting rules

A permissible draft removes only the minimum clinically load-bearing fact or compact fact group needed to change what can safely be concluded.

Preferred removable domains include:

- renal/hepatic function relevant to treatment;
- pregnancy/reproductive status when decision-relevant;
- allergy/intolerance;
- anticoagulation or interacting medication;
- key vital sign or physiological instability;
- load-bearing examination finding;
- imaging result;
- diagnostic laboratory result/trend;
- symptom timing/duration;
- red-flag history feature.

Do not remove background facts merely to make the case shorter. Do not create impossible or obviously incomplete prose. Do not insert experiment markers such as `[removed]`.

## Conflicting-evidence drafting rules

A permissible draft creates or preserves two clinically plausible, decision-relevant signals that cannot safely be reconciled by silently choosing one.

Preferred conflict classes include:

- current vs prior laboratory trend;
- symptoms/exam vs imaging;
- medication/allergy/history vs proposed treatment;
- discordant diagnostic tests;
- clinically reassuring context vs a high-risk objective measurement;
- competing source records describing the same decision-relevant fact.

The conflict may not change the underlying patient identity, action menu, clinical task, or decision timepoint.

## Physician construct validation

Reviewers A and B independently validate each proposed variant. A case/variant is valid only if all six checks in `PROTOCOL.md` are YES after adjudication.

A material correction to a draft creates a new immutable perturbation version and invalidates prior signatures/labels for that version.

## Primary perturbation assignment

A source case may have zero, one, or two valid perturbations.

- zero valid perturbations -> source case excluded;
- one valid perturbation -> that family is primary;
- two valid perturbations -> deterministic assignment based on source ID and study seed, with balancing toward 75/75 while maintaining the 150-case source-stratum quotas.

Optional second valid perturbations can be retained for exploratory analyses but cannot alter the primary assignment after target-model outputs exist.

## Dataset lineage

The frozen case manifest must bind:

- source dataset and revision;
- source file/corpus digest;
- source ID;
- source content digest;
- perturbation version and content digest;
- physician construct-validation decisions;
- primary perturbation assignment;
- exclusion reason if excluded.

The public manifest contains no protected benchmark text.
