# Ethics and governance checklist

This file is operational guidance, not a determination by an ethics board.

## Materials

The planned source cohorts are publicly released/de-identified benchmark materials. The study does not recruit patients, intervene in care, or use the outputs for patient management.

Physicians A/B/C participate as study reviewers/collaborators and therefore may still trigger local institutional requirements concerning research participation, consent, conflicts of interest, compensation, or data handling.

## Before physician data collection

Obtain and archive the appropriate local determination (for example ethics-review approval, exemption/non-human-subject determination, or documented statement that formal review is not required) from the institution/jurisdiction under which the study will be conducted. Do not infer the correct category from this repository.

Record:

- responsible institution/sponsor;
- ethics/IRB authority contacted;
- determination/reference number if applicable;
- date;
- whether physician reviewer consent is required and how obtained;
- compensation, if any;
- conflicts of interest;
- data-retention plan.

## Reviewer independence

- A and B complete construct and response reviews independently.
- C adjudicates only after A/B submissions are locked.
- C must not have authored the perturbation being adjudicated if that would compromise the claimed independence; if unavoidable, use another adjudicator for that item or disclose it and exclude it from the independence-sensitive analysis.
- Individual labels are preserved; consensus does not erase disagreement.

## Data minimization

The public repository stores only source identifiers, metadata, hashes, code, aggregate results, and de-identified labels permitted for release.

The private study vault stores source text, transformed text, target responses, and reviewer packets. It should be access-controlled and encrypted according to the team's institutional policy even though source cases are public/de-identified, because the vault also contains unpublished study materials and reviewer data.

## HealthBench Professional contamination request

The source maintainers request that examples not be reproduced online in plaintext or images. This study follows that request: raw or transformed HealthBench Professional case text, source physician responses, and rubrics are private and never committed to the public repository.

## Real-POCQi attribution

Real-POCQi is CC BY 4.0. Preserve the source citation/attribution in the manuscript and repository metadata. Any redistributed derived material must comply with that license and the study's privacy/contamination policy.

## Model-provider data policy

Before sending source-derived cases to commercial APIs, record the API product/endpoint and the provider's data-use/retention settings used for the study. Prefer API configurations in which submitted data are not used to train provider models when available. Do not assume consumer-chat privacy terms apply to API traffic.

## Conflicts and funding

Predeclare:

- relationships with AI/model vendors;
- ownership or commercial interest in `clinical-ai-eval` or evaluated products;
- study funding/credits;
- whether any vendor supplied API credits or reviewed the manuscript.

Vendor review must not include veto or unreported control over analyses.

## Clinical claim boundary

The study validates a measurement procedure within a stated task/source/model scope. It does not certify a model as safe for deployment, does not replace prospective clinical validation, and does not make a regulatory classification.
