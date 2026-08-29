# BISV source strategy

## Decision

The Branch-Intersection Safety (BISV) pilot and confirmatory study should preferentially use clinically grounded source material rather than wholly synthetic vignettes. The BISV branch construction remains study-authored, but each clinical backbone should carry auditable provenance.

## Preferred source hierarchy

### Tier 1 — primary PMC Open Access case reports, indexed via MedR-Bench

MedR-Bench is useful as an index because its structured cases were derived from PMC Open Access case reports. For BISV, do not automatically copy MedR-Bench text into the public casepack. Instead:

1. select a clinically suitable MedR-Bench case;
2. recover the underlying PMCID/DOI where available;
3. verify the source article's reuse license;
4. restrict public derivative casepacks to permissively licensed source articles (prefer CC BY / CC0 or equivalent);
5. reconstruct a concise clinical backbone from the primary source;
6. create the two resolved-source branches and two unresolved-order variants as BISV-specific transformations;
7. preserve source identifiers and transformation notes.

This provides real-world clinical provenance while avoiding unnecessary dependence on MedR-Bench's CC BY-NC-SA redistribution terms.

### Tier 2 — Real-POCQi physician point-of-care queries

Real-POCQi contains real clinical decision-support queries submitted by practicing physicians and is CC BY 4.0. Use only patient-specific, action-relevant queries that can support two plausible source-resolution branches without inventing a wholly different clinical problem. Factual literature questions and non-patient-specific queries are ineligible.

These cases are valuable as a separate stratum because they establish that BISV behavior is not limited to case-report prose.

### Tier 3 — HealthBench / HealthBench Professional as external robustness material

HealthBench provides highly physician-informed realistic conversations, and HealthBench Professional contains physician-originated clinical tasks with intensive physician rubric adjudication. However, HealthBench explicitly asks researchers not to reveal benchmark examples publicly. Therefore:

- do not place raw HealthBench examples in a public BISV casepack;
- use it only in a private/non-redistributed robustness analysis if licensing and benchmark-integrity requirements are satisfied;
- report aggregate results and source IDs rather than reproducing prompts.

HealthBench should not be the primary public confirmatory source.

### Tier 4 — MedEinst as a mechanistic external-validation set

MedEinst contains counterfactual paired clinical narratives and is conceptually close to branch-sensitive reasoning. It can be used as a secondary external-validity experiment, not as the main BISV case source, because its diagnostic counterfactual pairs test a related but different construct (Einstellung/counterfactual diagnosis rather than unresolved source conflict).

## Recommended 120-case confirmatory composition

Primary confirmatory analysis:

- 80 PMC-primary cases selected through the MedR-Bench sampling frame and reconstructed from permissively licensed source articles.
- 40 Real-POCQi patient-specific physician decision queries.

Prespecify source stratum and report BISV separately by stratum as well as pooled with case-clustered uncertainty.

This 80/40 split prioritizes richly structured cases while retaining a substantial real-physician-query stratum.

Optional external robustness analyses should not enter the primary 120-case denominator:

- private HealthBench/HealthBench Professional subset, if permitted;
- MedEinst-derived counterfactual compatibility analysis.

## Pilot sampling

The 20-case development pilot should exercise both primary source pipelines before confirmatory execution:

- 12 PMC-primary / MedR-Bench-indexed cases;
- 8 Real-POCQi patient-specific queries.

Pilot cases are development data and never enter the confirmatory 120-case headline analysis.

## Required provenance fields per case

Every BISV case must record at minimum:

```yaml
source_provenance:
  source_stratum: pmc_primary | real_pocqi | healthbench_private | medeinst_external
  upstream_dataset: MedR-Bench | Real-POCQi | HealthBench | MedEinst | none
  upstream_case_id: ""
  primary_source_id: ""   # PMCID / DOI when applicable
  primary_source_url: ""
  source_license: ""
  source_access_date: ""
  backbone_derivation: "" # concise description of what clinical material was retained
  bisv_transformation: "" # exactly what was changed to create A/B/unresolved branches
  verbatim_source_text_used: false
```

A confirmatory case cannot be locked unless its provenance is complete and the source license has been checked.

## Construction rule

Source-derived does **not** mean that contradictions may be inserted arbitrarily. A case is eligible only when a clinically realistic source conflict can be constructed around an action-relevant variable such as:

- laboratory result / specimen validity;
- imaging interpretation or addendum;
- medication/allergy reconciliation;
- renal/hepatic function relevant to dosing;
- microbiology/susceptibility result;
- pathology result;
- pregnancy status;
- anticoagulation/bleeding-risk history;
- device/implant status;
- prior diagnostic result or outside-record discrepancy.

The unresolved condition must preserve both resolutions as genuinely plausible at the same decision timepoint. If the branch is clinically contrived, the case is excluded even if the source backbone is authentic.

## Why this strengthens the study

This design separates two questions:

1. **Clinical grounding:** the backbone comes from authentic physician queries or real published cases with traceable provenance.
2. **BISV construct:** the unresolved-vs-resolved branch manipulation is controlled and study-specific.

The resulting claim is stronger than either a wholly synthetic benchmark or a direct re-use of an existing benchmark: BISV is tested on source-grounded cases while retaining experimental control over the exact conflict structure.

## Contamination and overlap

Record whether each source case has appeared in prior KAVentures studies. Prior use is not an exclusion by itself because BISV is a different endpoint and prompt format, but report overlap transparently and include a sensitivity analysis restricted to source cases not previously used by the investigator where feasible.

Do not use pilot model performance to select the confirmatory source cases. Source eligibility, sampling strata, and clinical inclusion/exclusion rules must be fixed before inspecting confirmatory model outputs.
