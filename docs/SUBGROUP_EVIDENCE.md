# Experimental prospective subgroup evidence

Local standalone descriptive assessment of a fixed cohort with baseline and
candidate adverse binary outcomes, stratified by mutually exclusive groups.
Useful for examining whether aggregate gains mask group-specific regressions.
Not a fairness certification, causal estimate, confidence interval, clinical
decision, or validated safety gate.

## Freeze before collecting or inspecting outcomes

Choose a single endpoint, eligible unit, baseline and candidate versions,
group definitions, group assignment rules, and a justified minimum planned
group size in the institution's existing study protocol. Use separately
approved reference adjudication. This module freezes the unit inventory and
group membership only; it does not bind endpoint or target versions itself.

Input inventory JSON:

    {"groups":["site-a","site-b"],"units":[
      {"unit_id":"local-001","group":"site-a"},
      {"unit_id":"local-002","group":"site-b"},
      {"unit_id":"local-003","group":null}
    ]}

Use pseudonymous identifiers, not names or raw clinical details. Null means
unknown group and remains an explicit separate bucket. Declare empty groups
as well. The tool rejects duplicated or undeclared units and groups.

    python -m caeval.subgroup_evidence_cli freeze --inventory inventory.json --minimum-group 20 --output frozen.json

Record the printed SHA-256 digest in the independently controlled prospective
protocol or audit log before outcomes are known. A digest alone does not
establish when a plan was created or who approved it.

## Outcomes and restricted analysis

Supply one row per known unit, with boolean adverse-event values or null for
unknown, failed, unreviewed or otherwise indeterminate outcomes:

    [{"unit_id":"local-001","baseline":false,"candidate":true},
     {"unit_id":"local-002","baseline":null,"candidate":false}]

An omitted row means both outcomes unknown. False requires determinate
evidence of no adverse event; it is not the default for absent evidence.

    python -m caeval.subgroup_evidence_cli analyze --plan frozen.json --expected-digest DIGEST --outcomes outcomes.json --output report.json

Each sufficiently large planned group reports both arm-specific observed rates
and sharp finite-cohort rate ranges. If n is the fixed group size, k observed
events, and m unknown outcomes, the range is [k/n, (k+m)/n].
Complete-pair delta is observed worsening minus improvement divided by the
number of complete pairs, or null when no pairs are complete. The separate
full-cohort delta range considers every possible completion of unknown
binary outcomes and is attainable. Positive candidate-minus-baseline means
more adverse events, not improved performance.

## Contrasts

    python -m caeval.subgroup_evidence_cli contrast --plan frozen.json --expected-digest DIGEST --outcomes outcomes.json --group-a site-a --group-b site-b --arm paired_delta --output contrast.json

Supported contrasts are baseline, candidate and paired_delta. With disjoint
groups, the sharp difference range is [lower_A-upper_B, upper_A-lower_B].
The paired-delta contrast can range from -2 to +2. This is a difference of
changes, not a causal difference-in-differences estimator.

Groups below the prespecified minimum (default 20 planned units) are flagged
INSUFFICIENT_UNITS and receive no endpoint metrics. This is not a statistical
power guarantee or privacy suppression. Missing membership is never
silently assigned to another group. Unknown outcomes can leave a sufficiently
large group with completely uninformative bounds.

## Restrictions and integration

Reports are marked decision_grade=false and publication_safe=false. Group
labels, counts, digests and pseudonymous unit inventories may be sensitive,
even with no raw notes. Keep the files local and apply access controls.
The CLI creates new files exclusively with owner-only POSIX permissions;
on Windows use a restricted directory and ACL. Do not pass real patient
identifiers as CLI arguments. The Python API assumes trusted local use and
contrast() must receive a freshly computed analyze() result, not a supplied
unverified report.

No overlapping strata, population inference, multiplicity correction,
intersectional subgroup guarantee, adjustment for confounding, or
differential privacy are implemented. The exact ranges describe only the
fixed eligible cohort. Repeated units can have clinical/statistical
dependence; a larger planned group does not remove that dependence.
A future institutional integration must bind endpoint, model version, plan
digest and authorized membership source into the existing prospective study
audit trail; no central router or shared schema edits are included here.

Run targeted tests with:
    python -m pytest -q tests_unit/test_subgroup_evidence.py

All test data are synthetic. One test exhaustively enumerates every
three-pair partial outcome pattern and all compatible binary completions.
