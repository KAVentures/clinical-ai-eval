# Common reference and baseline evidence across candidates

This local research API/CLI addresses the exact shared-evidence requirement in
ASSESSMENT_MULTIVENDOR.md. It replays one independently collected reference
panel and one retained baseline prediction bundle, then issues native,
candidate-plan-specific bundles carrying the same source receipt pointers and
canonical rows. It never copies or generates candidate predictions.

## Freeze the roster before collecting evidence

Prepare a JSON roster with exactly two fields: plans, a mapping from two to
five product aliases to native predictive plan objects, and expected_plans,
the matching mapping of independently retained predictive plan digests.
Choose one anchor alias. Every plan must match in every normalized predictive
specification field except candidate_version, including study, cohort,
eligibility, reference protocol, baseline version, action, horizon and
thresholds. Product identities and documentary/binary compatibility remain
the responsibility of the existing multi-product assessment engine.

    python -m caeval.predictive_shared_evidence freeze --plans roster.json --anchor a --output design.json

Retain the printed design digest independently. Freeze before any reference
or comparator exposure. A digest does not prove actual chronology.

## Replay the anchor panel

Use the approved independent reference panel on the anchor plan, with two
primary readers and optional third-reader adjudication, as documented in
PREDICTIVE_REFERENCE_PANEL.md. Independently validate the original source
records, reviewer identity, external blinding and baseline model provenance.
Prepare evidence JSON containing exactly these fields:

- panel and expected_panel: the anchor reference panel and retained digest.
- primary and expected_primary: both primary submissions and retained digests.
- adjudication and expected_adjudication: the third submission and digest, or
  both null.
- expected_reference: the retained final reference receipt digest.
- baseline and expected_baseline: the anchor native baseline bundle and its
  independently retained digest.

    python -m caeval.predictive_shared_evidence build --plans roster.json --design design.json --expected-design DESIGN_DIGEST --evidence evidence.json --output shared-package.json

The reference is recomputed from native submissions, including explicit unknown
labels; no missing outcome is converted to negative. The baseline bundle is
validated in its original plan scope. Every target bundle is newly sealed
under its exact target plan digest, preserving the original reference receipt
and baseline source pointer. No synthetic source receipt or relabeling is
introduced. The shared package contains restricted labels and predictions,
not raw source text. Retain the package digest independently.

## Verify and export

    python -m caeval.predictive_shared_evidence verify --plans roster.json --design design.json --expected-design DESIGN_DIGEST --evidence evidence.json --package shared-package.json --expected-package PACKAGE_DIGEST

Use export with the same arguments plus --product ALIAS --output pair.json
to produce the selected native baseline/reference pair. Each export replays
the exact design, panel, baseline and package. Use the resulting rows and
original source receipt pointers in the target workspace collection and
retain native bundle digests. The receiving workspace should reconstruct
the exact native bundles. Supply candidate predictions separately under each
target plan and follow the existing dossier and multi-product workflow.

The adapter deliberately does not pretend the anchor panel was independently
collected again for each target product. Candidate-specific assisted-trial
dossiers still require exact native reference bundles from their own panel
replay; they may be incompatible with a shared anchor receipt. This module
does not bypass that restriction or introduce a cross-product assisted-trial
contrast. It does not write into a workspace or alter existing assessments.
Private storage and trusted operators are required. No independent source-byte
authentication, clinical validity, actual chronology, publication or deployment
approval is established. Full repository integration CI is required.

    python -m pytest -q tests_unit/test_predictive_shared_evidence.py
