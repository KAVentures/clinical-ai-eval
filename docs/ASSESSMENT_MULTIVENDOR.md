# Matched multi-product assessment comparison

Use this local CLI/API to compare two to five prespecified candidate product
versions. It extends the aggregate research dossier, not the institutional
committee database. No rankings, combined scores or purchasing decisions.

## Freeze comparable conditions

Create a separate local assessment workspace and freeze its evidence policy for
each candidate before inspecting results. The comparison requires the same
buyer, exact predictive specification except candidate version, exact binary
specification except candidate version, normalized full dataset manifest,
subgroup plan, vendor documentary requirements and evidence-completeness
policy. Each version-specific plan and policy is independently checked against
its retained digest and the current procurement history. Distinct products
may have the same version token; the vendor/product/version tuple must be unique.
Each workbench can have its own private audit and site keys. A matching digest
is not proof that the original clinical data are authentic or that the study
was genuinely prospective.

Prepare a trusted local configuration mapping two to five product aliases to
records of the form below. Repeat for every declared product, using the same
aliases throughout the workflow. Example placeholders are not valid digests.

    {
      "product-a": {
        "workspace": "/private/workspaces/product-a",
        "expected_plan": "PLAN_DIGEST",
        "expected_head": "PROCUREMENT_HEAD",
        "expected_policy": "POLICY_DIGEST",
        "result": null
      },
      "product-b": { ... }
    }

The workspace must have the dossier policy file created by the dossier browser
(or an equivalent independently frozen policy at research-dossier-policy.json).
Use distinct owner-only workspace directories; only a trusted operator may
supply paths. Freeze before outcome collection, retaining the comparison digest
independently. The API does not independently establish chronology.

    python -m caeval.assessment_multivendor freeze --config config.json --output comparison.json

## Complete dossiers and compare

Generate each product's dossier using ASSESSMENT_RESEARCH_DOSSIER.md and store it
as research-dossier.json in its workspace. Use the same administrative as-of
date. The baseline and reference native bundles are necessarily scoped to
individual plans, so their bundle hashes may differ; their scope, exact
canonical rows (including explicit null versus omitted rows), and source
receipt digest must nevertheless match exactly. Source receipt digests must
be independently retained; they are pointers, not independent attestations.
For instance, independently sealed per-candidate reference panels may have
different receipt digests even if they agree on all labels: this implementation
refuses that combination rather than asserting common reference provenance.
Use a genuinely shared, independently governed reference export for this
comparison. Assisted-trial packages remain optional and individually replayed;
their findings are not contrasted across products.

Update each record's expected_head if its procurement history has progressed
and replace result:null with this object for a completed dossier:

    {"expected_report":"REPORT_DIGEST", "expected_dossier":"DOSSIER_DIGEST",
     "trial_package":null, "expected_trial":null}

If the original dossier included an assisted trial, provide the private package
path and its independently retained digest instead of both nulls. A supplied
result is replayed against the local assessment, policy and any trial package;
its exact saved dossier must match. A product without a supplied dossier stays
in the frozen roster with status not_supplied; the CLI refuses to omit a product
whose saved dossier file exists. Missing products have no calculated contrasts.

    python -m caeval.assessment_multivendor analyze --config config.json --comparison comparison.json --expected-comparison COMPARISON_DIGEST --output comparison-report.json

The report contains separate restricted aggregate dossiers and descriptive
pairwise comparisons for every replayed pair. The direction is explicitly
right minus left, using lexical aliases only to enumerate pairs. Brier loss
(lower is better) and net benefit (higher is better under the declared action
threshold) retain the full planned denominator and share the same unknown
reference outcome for each pair. The existing native matched engines calculate
complete-case differences and worst-case finite-cohort bounds directly from
candidate rows. These are not differences of unrelated marginal statistics,
confidence intervals, treatment-effect estimates, or superiority claims. No
ranking or cross-product binary/assisted-trial contrast is provided. Case
clustering, multiplicity, model selection and actual clinical utility require
separate analysis and clinical review.

Retain the comparison report digest independently. To replay use:

    python -m caeval.assessment_multivendor verify --config config.json --comparison comparison.json --expected-comparison COMPARISON_DIGEST --report comparison-report.json --expected-report REPORT_DIGEST

This requires the exact source workspaces and trial packages, not just the
aggregate report. The output excludes raw source records and case rows, but
small aggregates and product details may still be sensitive. Use restricted
storage, independently held checkpoints, approved ACLs and consistent backups.
The CLI writes new outputs exclusively. No institutional user accounts, browser
comparison view, automatic clinical validation or purchasing authorization is
provided. Full repository integration CI remains necessary.

    python -m pytest -q tests_unit/test_assessment_multivendor.py
