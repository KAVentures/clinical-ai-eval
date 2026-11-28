# Hospital-specific referral policy

This optional institutional workflow governs whether a frozen buyer
programme can be referred for a separate external clinical assessment.
It never approves clinical use, deployment, purchasing, regulatory status,
or a product's safety and effectiveness. Its conditions are deliberately
stricter than the administrative source coverage view.

## Registration and workflow

1. Freeze the buyer programme and its exact product versions. Register one
   policy per vendor in the programme **before** any source association for
   that vendor. Native evaluations may already have been exposed, so this
   is not prospective clinical-trial registration.
2. Specify intended use, explicit local criteria, and at least two distinct
   active institutional reviewer accounts. The six mandatory domains are
   clinical safety, clinical effectiveness, privacy/security, regulatory
   quality, workflow/human factors, and monitoring/incident response.
   Additional local gates are allowed, up to 16 total. The software checks
   the required identifiers and assignments, not the substantive adequacy
   of locally written criteria.
3. Lock and verify native sources in the unified programme. An administrator
   previews and freezes one exact review snapshot per policy. Start only
   when ready to assess the evidence; freezing does not require complete
   coverage, so an incomplete dossier can be documented and held or stopped.
4. Each assigned reviewer sees the vendor-scoped evidence conditions and
   their own gate criteria, not other reviewers' ratings. They assess the
   authorized external artifacts separately and submit one immutable rating:
   attested met, not met, or unresolved. A met rating requires a SHA-256
   artifact digest and explicit rationale. The software does not obtain the
   artifact, check its digest against bytes, verify credentials or prove
   reviewer independence. Reviewers must not enter patient information.
5. An administrator reviews the frozen ratings and records a digest-approved,
   immutable disposition: stop, hold, or refer for external assessment.
   A stop/hold may document incomplete or stale evidence. Referral requires
   current evidence, all administrative evidence conditions and all gates
   attested met. Assigned accounts must still be active reviewers at referral.
   Any changed target-vendor source makes the snapshot stale. A new scope or
   reassessment requires a new programme and policy, not a replacement rating.
6. The committee inventory contains coverage, historical disposition and
   currentness only. Full rationale, criteria and ratings are restricted.
   An exported historical disposition is not a statement of current readiness.

## Example policy (fictional criteria)

```json
{
  "policy_id": "fictional-hospital-policy",
  "programme_id": "existing-frozen-programme",
  "vendor_id": "vendor-a",
  "intended_use": "Fictional intended-use description",
  "gates": [
    {"id":"clinical_safety","reviewer":"doctor-a","criterion":"Review external hazard analysis and unresolved safety issues."},
    {"id":"clinical_effectiveness","reviewer":"doctor-b","criterion":"Assess external evidence for exact intended population and version."},
    {"id":"privacy_security","reviewer":"doctor-a","criterion":"Assess approved security and data protection evidence."},
    {"id":"regulatory_quality","reviewer":"doctor-b","criterion":"Assess applicable regulatory and quality documentation."},
    {"id":"workflow_human_factors","reviewer":"doctor-a","criterion":"Assess workflow validation, human oversight and usability."},
    {"id":"monitoring_incident_response","reviewer":"doctor-b","criterion":"Assess documented monitoring, escalation and rollback plans."}
  ]
}
```

These are illustrative placeholders, not recommended clinical acceptance
criteria. The institution must define its own substantive criteria and assign
appropriately qualified reviewers.

## Mandatory referral conditions

Every selected vendor requirement must have a current verified source
association. Omitted required canonical suites remain explicit blockers.
For a requirement tied to a canonical family, only a native text or
conversation package with the exact buyer scope and both mode and claim
`internal_regression` can satisfy this narrow administrative condition.
The bundled synthetic RAG rehearsal cannot fulfill that condition.
This can make referral impossible for a programme with unsupported canonical
families. Do not remove a necessary family to bypass a blocker.

Noncanonical sources remain experimental or synthetic as stated in their
native claim scope. An incomplete imaging roster also blocks referral.
No available source is a clinical pass. The native internal-regression
claim is a limited evidence classification, not proof of clinical validity.
The gate review remains independently required; no combined score is computed.
A referral decision is only a request for a separate external assessment,
not automated procurement or clinical authorization.

## Evidence and replay

The restricted export contains the full frozen programme coverage snapshot
(including other vendors' administrative metadata), scoped policy,
gate ratings and rationales, and exact historical disposition. Keep it
within the approved institution. Reviewers see only their assigned
vendor-scoped snapshot. The audit inventory checks local record consistency
and ordering, not a privileged attacker rewriting the entire database.

```sh
clinical-ai-eval verify-hospital-referral evidence.json --expected-digest TRUSTED_PACKAGE_DIGEST
```

The offline verifier reconstructs the structural coverage, policy, ratings
and disposition and optionally checks a trusted digest. It does not verify
native evidence files, external artifacts, prospective timing, reviewer
identity, or currentness. The live institution rechecks current target
evidence before referral. Neither the live workflow nor offline replay
authorizes deployment.
