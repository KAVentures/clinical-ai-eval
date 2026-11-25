# Offline diagnostic imaging research

The existing synthetic image-text readiness assay checks whether a vendor can read
a fictional display. This separate experimental study lane evaluates structured
binary diagnostic predictions against a buyer-defined reference cohort. It does
not extend the canonical procurement executors or promote their maturity.

## Scope and governance

- Run on an approved institution-controlled host; there is no network access,
  image ingestion, automatic candidate invocation or EHR write-back in this lane.
- The institution selects the exact task, imaging modality, reference method,
  reference labels, candidate versions and operating thresholds before exchange.
  Use independently established references and a prospectively approved design.
  The tool records attestations; it cannot establish that those statements are true.
- Use only authorized synthetic or appropriately de-identified data under the
  institution's governance. Do not put patient names, record identifiers, dates,
  locations, clinical narratives or raw images in the metadata. A hash is not
  anonymization. Study identifiers, cluster membership, hashes and labels may
  still be sensitive and must be treated as restricted research data.
- An asset hash refers to the exact bytes selected externally. The tool does
  not open the image, verify its bytes or prove which image a candidate saw.
  The institution must perform those checks in its approved imaging environment.
- Use one immutable image asset per case. Multi-view, longitudinal, segmentation,
  localization, free-text radiology, calibration and clinical utility evaluations
  are outside this contract. Cluster IDs can represent dependent cases from the
  same pseudonymous patient; no independent-case confidence interval is claimed.
- Use private local files with restrictive permissions. The private plan contains
  reference labels and an unpredictable nonce; never share it with candidates.
  The full evidence package also contains that private material and predictions.
  Candidate exchanges omit the nonce, references, cluster IDs and strata, but
  still contain asset hashes and should be released only to authorized recipients.
  The CLI writes to stdout, so use secure redirection and avoid shared logs.

## Input format

A specification is an exact JSON object with:
 
- `study_id`, `task_id`, `modality`, `reference_method`: bounded non-identifying
  protocol tokens. These point to an institution-owned protocol, not free text.
- `attestations`: all four required true values in the example.
- `candidates`: one to eight distinct candidate IDs, exact versions and
  predeclared integer thresholds (0–10000 basis points). Scores at or above a
  threshold are classified positive. Thresholds may differ across candidates;
  comparisons are between the declared operating points, not equal thresholds.
- `strata`: up to twenty predeclared non-identifying tags. Tags may overlap.
  No data-driven subgroup discovery or multiple-testing claim is made.
- `cases`: one to 2000 case records, with unique case IDs and unique SHA-256
  asset hashes, a cluster token, positive/negative/unresolved reference and
  zero or more predeclared stratum tags.

For a fully artificial metadata-only example see
`examples/diagnostic-imaging-study.json`. Its hashes are fabricated fixture
identifiers, not hashes of actual clinical images.

## Workflow

In a private directory on an approved host (shell redirection is an example for
POSIX shells):

```sh
umask 077
clinical-ai-eval diagnostic-imaging freeze spec.json --confirm-prospective > private-plan.json
clinical-ai-eval diagnostic-imaging exchange private-plan.json vendor-a > vendor-a-exchange.json
clinical-ai-eval diagnostic-imaging template private-plan.json vendor-a > vendor-a-predictions.json
clinical-ai-eval diagnostic-imaging exchange private-plan.json vendor-b > vendor-b-exchange.json
clinical-ai-eval diagnostic-imaging template private-plan.json vendor-b > vendor-b-predictions.json
```

Freeze once, before candidate access; retain the private plan and its digest in
the approved local study record. The exchange identifies cases by opaque slots
and asset hashes. Deliver actual image assets only via the institution's approved
external workflow. A candidate or operator fills the matching prediction
template; retain every slot and its declared identity and threshold:

- `status: "scored"`: integer `score_bp` from 0 to 10000.
- `status: "abstained"` or `"unavailable"`: `score_bp: null`.

Never encode a missing outcome as a negative prediction. Missing slots, duplicate
slots, unknown candidates, version/threshold changes and altered plan digests are
refused. An entirely missing candidate remains explicitly not submitted. Do not
silently re-freeze the study after looking at results.

```sh
clinical-ai-eval diagnostic-imaging assess private-plan.json vendor-a-predictions.json vendor-b-predictions.json > private-evidence.json
clinical-ai-eval diagnostic-imaging verify private-evidence.json --expected-digest DIGEST_FROM_TRUSTED_RECORD
```

The evidence includes the frozen private plan, submitted documents and regenerated
report. Verification checks deterministic consistency, not signatures or
authenticity. A separately communicated expected digest can identify an original
package; the digest must be taken from a trusted record, not merely from the
package being checked. The CLI refuses invalid input with a nonzero exit code.

## Analysis

For each submitted candidate, report overall and predeclared subgroup:
 
- Confusion counts among scored cases with resolved references, separate
  counts for abstention, unavailability and unresolved references, and total
  score/reference coverage.
- Sensitivity and specificity among scored, reference-resolved cases.
- Worst/best-case bounds over **all reference-resolved cases** of the relevant
  class: count missing predictions as all incorrect or all correct, respectively.
  These bounds are not confidence intervals and do not address unresolved
  reference labels.
- AUROC on scored, reference-resolved positives and negatives only. Pairwise
  score wins count one and ties count one half; denominators are reported.
  No AUC is reported without both classes.
- Once the entire roster is submitted, descriptive paired correct-decision
  differences and worst/best-case bounds over the same reference-resolved cases.
  No unpaired comparison, p-values, ranking, superiority or equivalence claims.
  A submitted all-unavailable document is explicit; it does not establish coverage.

Empty denominators produce null, not zero or a passing result. Every report and
replay explicitly remains non-decision-grade. The standalone offline module has no API/MCP execution, DICOM/PACS transport,
independent reference-review interface, population validation or deployment
authorization. Those remain separate integration and validation milestones.


## Institution-controlled browser workflow

The existing authenticated institutional application now exposes **Diagnostic
imaging research** from its dashboard. No workstation installation is needed.
The application must run on the approved hospital-controlled host, not on a
public endpoint. It stores bounded structured metadata in the existing private
institution access database; it does not store or transmit image bytes.

1. An administrator pastes and previews the private study specification. The
   confirmation binds its normalized content. The study ID is reserved once,
   with a fresh private nonce, and cannot be replaced.
2. Administrators and operators download candidate-specific exchanges and
   response templates. These omit reference labels, strata, cluster IDs and
   the private nonce. Operators must arrange external delivery of the exact
   authorized image assets. Candidate identity is still self-declared.
3. An operator pastes a complete structured prediction document for each
   candidate and explicitly locks it. Exact retransmission is idempotent;
   altered submissions are refused. Interim reference-derived metrics are not
   released to any browser role.
4. An administrator seals the exact current submission inventory. If a
   candidate is absent, an additional explicit confirmation is required;
   comparisons remain suppressed. A stale seal preview fails closed.
5. Only administrators can view overall reference-derived metrics after the
   seal or export the full private replayable package, using a separate
   confirmation bound to the exact evidence digest. Operators and committee
   observers see only study coverage and candidate submission status. Small
   cohorts can reveal sensitive information in aggregate, so do not share the
   restricted package or administrator view outside the authorized team.

Study freezes, candidate exports, locked submissions, seals and private
exports are recorded in the institutional audit store. Study, submission and
seal inventories are checked against that audit store; replay also checks
content digests and audit ordering. This is local consistency, not tamper-proof
storage or independent attestation. Committee snapshots contain study coverage
only and become stale when that inventory changes. Candidate-facing account
access, independent physician reference adjudication, PACS/DICOM transport,
study-specific operator assignment, and browser editing of the source cohort
are not implemented. All active administrators/operators in the single-
organisation workspace have the described role-level access; provision roles
accordingly.
