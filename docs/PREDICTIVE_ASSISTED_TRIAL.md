# Cluster-randomized clinician-plus-AI decision study

This research API compares clinician decisions with and without AI assistance
on a frozen retrospective or simulated decision cohort. It is not a clinical
care system or a claim that AI improves patient outcomes. The reference panel
is separate from the decision clinicians; the binary endpoint is agreement
with the declared reference, not a measure of patient benefit.

## Freeze and allocate

Call `freeze(plan, expected, reference_panel, panel_digest, spec)` with the
existing predictive study and independent reference panel. The exact spec is:

- `clinicians`: 1–30 distinct fixed clinician tokens, none on the reference panel.
- `cluster_by_unit`: complete case-to-cluster mapping, e.g. linked patient IDs.
- `n_assisted`: number of clusters assigned to AI assistance (1 to K-1);
  the total number of clusters K must be between 2 and 200.
- `decision_protocol_sha256`, `assistance_protocol_sha256`: approved protocol
  digests specifying decision-time information, assistance and timing.
- `decision_input_sha256`, `assistance_input_sha256`: complete mappings of case
  tokens to approved input and version-specific assistance artifact digests.
- `randomization_test`: whether an exact two-sided sharp-null test is planned.

Freeze before assignment or exposure. Retain the design digest independently.
The candidate version is inherited from the predictive plan. Produce the
assistance artifacts under an independently verified protocol; no actual model
call or source-byte verification is done here. Reference outcomes and future
information must not appear in decision inputs or assistance. This must be
verified independently.

All downstream functions use the same six scope arguments: the four initial
scope arguments, frozen design and retained design digest. `draw` requires
`confirmed=True` and draws a uniform integer rank using the system randomness
source. `allocate` reconstructs an allocation from a retained integer rank.
The rank indexes the lexicographically ordered combinations of K clusters
choose n_assisted, so every allocation has exactly one rank. All encounters in
a cluster and all clinicians on that cluster receive the same arm. Retain the
first draw and its digest in an independently controlled audit record; never
redraw based on balance, anticipated results or convenience. This API cannot
prove that only one draw was made or that assignment was concealed.

## Collect decisions and replay

`packet` takes the six scope arguments and keyword allocation, allocation_digest
and clinician. The packet contains only that clinician's assigned case tokens,
arm and decision input digest. Assistance digests are present only for the
assisted arm; the assistance protocol digest is common to both arms. Deliver
approved source bytes separately under restricted access, without control-arm
assistance, reference labels or other clinicians' decisions. Readers know the
assigned arm, so this is not double-blinded. Randomized case-cluster assignment
does not prevent learning or carryover between cases; independently design and
justify the collection procedure, ordering and noninterference assumptions.

`seal` takes the same scope, allocation, allocation_digest, clinician,
source_receipt (digest pointer) and rows. Each row repeats the exact packet
item with `action` equal to true, false or null. Missing rows remain unknown.
Retain each submission digest independently. Every frozen clinician must have
a submission, which may be empty; do not exclude nonresponders or cases.

`analyze` takes the same six scope arguments, allocation and allocation_digest,
primary and expected_primary, adjudication and expected_adjudication (both
null if absent), expected_reference, submissions and expected_submissions.
It replays the reference panel and exact clinician inventory. Within each case,
the outcome is shared across all clinicians; bounds optimize its unknown value
jointly, rather than treating each comparison as a separate outcome. The
observed-arm mean is equal-weighted across clusters, with equal weighting of
all planned reader-case judgments within each cluster. The reported difference
bounds are sharp under unrestricted compatible completion, not causal bounds,
confidence intervals or estimates of the missing counterfactual outcomes.

If the sharp-null test was prespecified, all cases and reader decisions are
complete, and `randomization_assumptions_declared=True`, the exact two-sided
Fisher test enumerates every possible allocation (up to 100000). The statistic
is the absolute difference in equal-cluster mean correctness. Its null is no
assignment effect on any cluster outcome. Validity requires actual uniform
randomization, a fixed pre-exposure design, consistent measurement, no
interference and no post-allocation selection. The result is conditional on
these unverified assumptions; it is not a general test of average causal
effect, random-reader population inference, noninferiority, clinical utility
or deployment readiness. If data are incomplete or enumeration exceeds the
cap, no p-value is emitted. No approximate fallback or multiplicity adjustment.

`verify` recomputes the entire report and compares the independently retained
report digest. Store all source files and checkpoints privately. No browser,
CLI, authentication, clinical validation, publication clearance or deployment
authorization is provided. All tests are synthetic.

    python -m pytest -q tests_unit/test_predictive_assisted_trial.py
