# Aggregate assessment research dossier

Generate a restricted procurement supplement from the coordinated assessment
workbench and, optionally, the sealed clinician-plus-AI research package. This
is not an extension of the existing institutional committee database or a
portable independently replayable export. It does not rank vendors, establish
clinical benefit or authorize purchasing or deployment.

## Freeze administrative coverage requirements

Use the existing private workspace with its frozen assessment plan. Prepare a
policy JSON with exactly these fields (illustrative values, not clinical advice):

    {"minimum_coverage_bp":{"baseline":9000,"candidate":9000,"reference":9000},
     "require_assisted_trial":true,"minimum_trial_coverage_bp":9000}

Coverage thresholds are integer basis points from 0 to 10000. They concern
completeness of evidence, not clinical performance. Set and independently
approve them before evidence exposure. Actual chronology is not proved by
this module. Freeze the policy separately from the clinical research design:

    python -m caeval.assessment_research_dossier freeze --workspace /private/workspace --expected-plan PLAN_DIGEST --policy policy.json --output frozen-policy.json

Retain the printed policy digest independently. Complete and verify the
assessment using ASSESSMENT_RESUME_WEB.md. Retain the report and procurement
head separately. An optional assisted-trial package must be sealed through
PREDICTIVE_ASSISTED_COORDINATOR.md and independently checkpointed.

## Generate and replay

    python -m caeval.assessment_research_dossier build --workspace /private/workspace --expected-plan PLAN_DIGEST --policy frozen-policy.json --expected-policy POLICY_DIGEST --expected-head VENDOR_HEAD --expected-report REPORT_DIGEST --output dossier.json

To include the assisted trial, add both `--trial-package PACKAGE_PATH` and
`--expected-trial PACKAGE_DIGEST`. The package must replay and its predictive
plan and entire native reference bundle must exactly match the assessment.
A reference receipt pointer by itself is insufficient. A mismatched supplied
package is refused, not silently omitted. If a trial is absent, its status
is explicitly not_supplied even when the policy does not require one.

The dossier includes documentary gaps and clarification counts, prediction
coverage, paired Brier and net-benefit bounds, pooled adverse outcome bounds,
population-shift status counts, and optionally assisted-versus-unassisted
correctness bounds, reference panel resolution and the conditional sharp-null
randomization test. These endpoints remain distinct. The dossier uses a fixed
field allowlist rather than embedding unit-level rows, clinical source text,
reviewer identities, keys, individual site results or vendor documents. Small
aggregate cells can still disclose sensitive information; this is not a
privacy-safe public release and must remain in approved restricted storage.

Administrative evidence gaps identify missing mandatory documentation,
unresolved clarifications, insufficient specified coverage and a missing
required assisted trial. No gap is equivalent to clinical failure, and no
absence of gaps is a clinical or purchasing pass. Documentary evidence is
byte-verified, not independently validated claims. Clinical performance,
reference validity, external blinding, actual randomization and clinical
utility remain subject to independent review.

Use the same arguments with `verify` instead of `build`, replacing `--output`
with `--dossier dossier.json --expected-dossier DOSSIER_DIGEST`. Include the
same optional trial arguments. Verification replays the underlying local
assessment at its retained head, replays the supplied trial and recomputes
the entire aggregate projection. It is not offline verification from the
dossier alone; retain the exact approved workspace, underlying trial package
and checkpoints under independent custody. The CLI writes files exclusively
and owner-only on POSIX. Approved ACLs are required on other platforms.

No automatically imported committee evidence, browser interface, independent
source-record validation, institutional identity verification or regulatory
clearance is supplied. Full repository integration CI remains required.

    python -m pytest -q tests_unit/test_assessment_research_dossier.py
