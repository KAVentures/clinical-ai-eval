# Human-Anchored Validation of Automated Evaluation for Clinical AI Under Missing and Conflicting Evidence

**Status:** preregistration-era manuscript skeleton; no results inserted.

## Abstract

### Background
Clinical AI evaluations increasingly use LLMs as automated judges, but evaluator choice can materially affect measured performance and clinically important robustness failures may emerge when decision-relevant information is missing or contradictory.

### Objective
To measure the validity of blinded automated clinical-AI judges against independent physician reference labels and characterize model behavior under controlled information degradation.

### Methods
Prospective paired perturbation study using 150 physician-validated HealthBench Professional-derived care-consult cases, one locked primary perturbation per case, four provider-diverse target models, three provider-diverse blinded automated judges, and a shared 60-case physician calibration cohort spanning all four targets and both presentations (480 response cells). Two physicians rate independently; a third adjudicates primary-endpoint disagreements. External replication uses 50 physician-valid Real-POCQi cases.

### Results
[LOCKED PLACEHOLDER — populate only from reproducible analysis outputs.]

### Conclusions
[LOCKED PLACEHOLDER — must distinguish measurement validity from model performance and avoid deployment-safety claims.]

## Introduction

1. Clinical generative AI is increasingly evaluated using rubric- or judge-based methods.
2. Model-based graders can make large-scale evaluation practical but are themselves measurement instruments with error, bias, and cue sensitivity.
3. Static fully specified cases under-test a clinically important failure mode: committing after information becomes insufficient or internally conflicting.
4. A useful assurance framework therefore needs both controlled clinical perturbations and empirical validation of the evaluator against physicians.
5. Study objectives and hypotheses.

## Methods

### Study design
Describe preregistered prospective paired perturbation study and distinguish:

- platform measurement validation;
- secondary target-model robustness comparison.

### Source datasets

#### HealthBench Professional-derived primary cohort
State dataset version/digest, MIT license, care-consult reservoir, source strata, quotas, and the source maintainers' request not to reproduce examples online. Clarify that the study uses source material and a stable study rendering; it is **not an official HealthBench Professional score**.

#### Real-POCQi external cohort
State version/digest, CC BY 4.0 attribution, 620-query source reservoir, patient/decision screening, physician validation, and 50-case external target.

### Perturbation construction
Describe constrained draft authoring, missing-information and conflicting-evidence definitions, minimum-edit principle, and prohibition on treating author-model output as ground truth.

### Physician construct validation
A/B six-question independent validation and C adjudication. State acceptance criterion (all six YES after adjudication).

### Target models
Insert exact frozen API identifiers, dates, reasoning settings, prompt hash, tool-disabled policy, and provider data-use settings.

### Automated judges
Describe three-provider blinded primary panel, panel-ANY and panel-MAJORITY as distinct endpoints, secondary cued condition, and optional fourth-provider sensitivity judge.

### Physician response-reference cohort
Describe shared 60 cases (30 missing-information, 30 conflict), four targets × original/perturbed = 480 response cells, opaque reviewer IDs, independent A/B review, C adjudication.

### Outcomes
Primary: `unsafe_overconfident`.
Secondary: harmful treatment, information-problem recognition, useful/guideline-concordant next step, excessive abstention, clinical helpfulness, malformed output.

### Statistical analysis
Follow `protocol/STATISTICAL_ANALYSIS_PLAN.md`. Source-case cluster bootstrap; sensitivity/specificity/balanced accuracy/PPV/NPV/kappa; paired model risk differences; family and provider interaction analyses; cueing analysis.

### Ethics and governance
Insert institutional determination and conflicts/funding/data-policy details from `protocol/ETHICS_AND_GOVERNANCE.md`.

## Results

### Cohort construction
Figure/table placeholders:

- 236 source care-consult candidates -> construct drafting -> A/B review -> adjudication -> 150 locked primary cases;
- count/reason for exclusions;
- specialty and source-stratum distribution;
- primary-family distribution.

### Physician agreement
Report A/B agreement and adjudication frequency without hiding disagreement.

### Automated judge validity
Primary table:

| Endpoint | Sensitivity | Specificity | Balanced accuracy | PPV | NPV | Kappa |
|---|---:|---:|---:|---:|---:|---:|
| Judge 1 | | | | | | |
| Judge 2 | | | | | | |
| Judge 3 | | | | | | |
| Panel ANY | | | | | | |
| Panel majority | | | | | | |

All CIs source-case clustered.

### Target-model robustness on physician labels
For each model report original rate, perturbation rate, paired risk difference and CI on the shared 60-case cohort.

### Missing-information vs conflict
Report family-stratified effects and interaction estimates.

### Judge/provider bias
Target-provider × judge-provider error matrix; same-provider sensitivity analysis.

### Cueing
Blinded vs rubric-aware positive rate and operating-characteristic change.

### External replication
Real-POCQi estimates kept distinct from primary results.

## Discussion

### Principal findings
[RESULT-DEPENDENT]

### Interpretation
Keep three questions separate:

1. Does the perturbation construct work clinically?
2. Does the automated judge measure the endpoint accurately enough for its stated use?
3. How do target models behave under the perturbation?

Do not let evidence for one substitute for another.

### Comparison with prior work
Discuss HealthBench/HealthBench Professional, LLM-as-judge clinical evaluation literature, Real-POCQi, and robustness/sufficiency literature without claiming this protocol is a universal benchmark.

### Practical implications
Potential use as a screening/regression layer only to the degree supported by measured judge performance and defer-to-human policy.

### Limitations
Prespecified items:

- source cohorts are not representative of all clinical encounters;
- cases are text-only and source-derived;
- constructed perturbations may not mirror every real EHR omission/conflict mechanism;
- three physician reviewers cannot represent all clinical practice variation;
- model/API behavior is version-specific;
- HealthBench-derived examples may have contamination exposure;
- judge calibration is endpoint- and prevalence-dependent;
- external replication still uses a curated public dataset;
- study does not measure real-world patient outcomes.

## Conclusion

Use the narrowest result-supported wording. A successful study may justify: "Within the audited task scope, [judge/panel] achieved measured [operating characteristics] for detecting [defined failure] relative to physician adjudication." It may not justify: "The harness proves medical AI is safe."

## Reproducibility statement

Release protocol, analysis code, source IDs, content hashes, model/prompt versions, de-identified labels where permissible, and aggregate results. Do not release HealthBench Professional case text or transformations.
