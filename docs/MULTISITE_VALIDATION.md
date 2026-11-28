# Experimental aggregate-only multisite validation

This standalone Python API supports prospective matched evaluation of two
model versions across several hospitals. It exchanges nine aggregate
paired outcome counts per site, not case-level observations. It is not
a secure aggregation protocol, privacy guarantee, independent clinical
validation or deployment decision.

## Prospective workflow

1. Agree on the endpoint (a binary adverse outcome), horizon, inclusion
   rules, case independence, model versions and reference protocol.
2. Each site creates an eligible local pseudonymous unit roster before
   outcomes are known. Establish distinct 32-byte cryptographically
   random shared secrets with an authorized coordinating institution,
   using an approved secret exchange channel.
3. Each site sends its roster commitment and planned size, not the roster.
   The coordinator freezes the common plan and distributes the digest.
4. Sites retain the digest independently in the approved audit record,
   obtain outcomes using the agreed protocol, and prepare one receipt
   each. The coordinator authenticates and combines them.

Example (illustrative local identifiers only):

    from caeval.multisite_validation import roster_commitment, freeze
    # In each site process:
    declaration = roster_commitment(site_id, eligible_ids, site_key)
    # At the coordinator:
    frozen = freeze({
        "study": "prospective-study-1",
        "endpoint": "adverse-event",
        "horizon": "day30",
        "reference_protocol": "reference-v1",
        "baseline_version": "v1",
        "candidate_version": "v2",
        "sites": declarations
    })

Protocol tokens and site identifiers must not contain patient identifiers.
Keep rosters, keys and outcome rows in restricted environments. Commitments
bind local roster strings, not clinical eligibility, independence or the
time a roster was created. Protect the prospective plan digest and keys
through separate institutional governance.

## Local site receipt

    from caeval.multisite_validation import site_receipt
    receipt = site_receipt(frozen, retained_digest, site_id, eligible_ids,
                           local_outcome_rows, site_key)

Each outcome row has exactly unit_id, baseline and candidate. The last two
fields are boolean adverse-event values or null when unknown. An omitted
unit means unknown for both versions; it is not a negative result. Do not
substitute failure to observe an outcome for verified absence of harm.

The receipt contains the plan digest, site token, fixed denominator and
nine paired counts (baseline/candidate each 0, 1 or unknown). HMAC-SHA256
uses separate roster and receipt domains. Keys and raw unit identifiers
are not in the receipt. Site receipts must still be reviewed before
transfer: aggregate counts, particularly small cells, can reveal
sensitive information. No suppression or differential privacy is applied.

## Coordinator aggregation

    from caeval.multisite_validation import aggregate
    report = aggregate(frozen, retained_digest, received_receipts, site_keys)

The coordinator checks the exact plan, site scope, count inventory and
denominator, shared-key authenticator and duplicate site receipts.
A missing hospital contributes its entire planned cohort as unknown in
both arms. A receipt with absent outcome rows also preserves the original
denominator. All site keys must be distinct and provisioned independently.

The report includes each site's finite-cohort adverse-event rate
difference bounds (candidate minus baseline), a unit-weighted pooled
bound and an equally weighted site-average bound. A positive delta means
more adverse outcomes. The bounds are sharp under unconstrained binary
completions of missing values and apply to the specified finite cohort,
not a new hospital or a population confidence interval.

The two weighting schemes answer different questions; neither is
necessarily representative of future deployment. Choose the estimand
prospectively. Do not discard unfavorable sites, change weighting after
seeing results, or treat a bound spanning zero as evidence of equivalence.

## Trust boundaries and limitations

Shared-key MACs authenticate relative to authorized key holders, not to
independent third parties; the coordinator can also construct a valid MAC.
They cannot establish that the site performed the actual evaluations or
reported honest counts. A plan must be independently time-recorded and
site receipts retained immutably by the operator. Duplicate detection is
within a submitted receipt set, not a persistent replay ledger. Cross-site
patient duplication is not detected; site governance must establish
disjoint eligibility or account for overlapping patients independently.
No transport layer, automated clinical inference, reference adjudication,
identity management, privacy certification, population inference or
heterogeneity significance testing is provided.

Outputs are restricted, with decision_grade=false and publication_safe=false.
This API introduces no new dependencies and changes no existing UI, CLI,
or database schemas. Institutional integration must bind approved local
evaluation receipts, study authorization and audit retention before use.

Run synthetic tests:

    python -m pytest -q tests_unit/test_multisite_validation.py
