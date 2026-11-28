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
population validation or deployment
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
access, PACS/DICOM transport,
study-specific operator assignment, and browser editing of the source cohort
are not implemented. All active administrators/operators in the single-
organisation workspace have the described role-level access; provision roles
accordingly.


## Independent clinician reference review

For prospective two-reader studies, administrators may use **Independent
imaging reference review** instead of importing already-established labels.
The clinician workflow is browser-only, while image inspection occurs in the
hospital's approved external viewing environment.

- Prepare the same specification as above, **without** the `reference_method`
  key, the `independent_reference` attestation or any case's `reference` key.
  This lane accepts 1–500 cases and reserves one immutable study identifier.
- Assign two distinct active reviewer accounts and a different adjudicator
  account. The administrator approves the exact normalized cohort, candidate
  roster, thresholds and assignments before reference collection.
- Primary readers see the task, modality and opaque case slots with asset
  hashes, not the vendor roster, candidate predictions, other readers' labels,
  subgroup tags or patient cluster tokens. Read each exact asset externally.
  Structured forms have no preselected outcome. Lock every case as positive,
  negative or unresolved.
- The adjudicator sees only disagreements and their unordered primary labels,
  after both primary reads are permanently locked. Agreement is retained,
  including agreement on unresolved. Disagreements require one explicit
  adjudication label for every disputed case; unresolved remains allowed.
  If there are no disagreements, no adjudication submission is accepted.
- The administrator reviews the private reference summary and explicitly
  finalizes the exact ratings. Finalization atomically creates the candidate
  imaging study with the derived reference labels and a fresh candidate slot
  nonce. A reserved study cannot be created using the imported-label route.
  All candidate operations recheck the independent reference provenance.
- Candidate collection then proceeds using the institutional workflow above.
  Keep the reference evidence separate from candidate-facing exchanges.
  The administrator can explicitly export the full private reference package,
  including reviewer account identifiers, source ratings and final plan.

The private reference package can be independently replayed offline:

```sh
clinical-ai-eval diagnostic-imaging verify-reference private-imaging-reference.json --expected-digest DIGEST_FROM_TRUSTED_RECORD
```

Compare the returned final plan digest to the plan digest in the separate
candidate evidence package. The reference verifier rederives all labels,
checks disagreement coverage and reconstructs the exact frozen candidate plan.
It does not prove authentic reviewer identities, credentials, clinical truth,
independent behavior, actual image receipt, or external non-exposure. All
clinical instructions and approved image assets must be available separately
to the reviewers; the protocol task identifier is not a clinical guideline.
The reference package and small-cohort summaries are sensitive. Do not send
them to vendors or unapproved services. Committee inventory contains reference
workflow status, not case ratings, reviewer identities or reference prevalence.

This is an experimental binary consensus-reference workflow, not an external
gold-standard determination, multireader multicase inferential analysis,
radiology workstation, clinical credentialing or regulatory validation. No
PACS/DICOM access, image bytes, external image identity verification or live
clinical decisions are supported. Incomplete reviews cannot be silently
discarded or finalized; a new study design requires a distinct identifier.


## Exact-asset local imaging bridge

The optional `imaging-transport` CLI connects an already frozen imaging
exchange to a vendor-neutral HTTP endpoint on the same approved host. This
works with custom hospital gateways and vendor adapters as well as a separately
approved PACS export process. It is not a PACS/DICOMweb client.

### Staging and security boundary

An authorized imaging team must export and de-identify each asset using its
approved workflow **before** staging. DICOM metadata, private tags and burned-in
pixel annotations can contain identifying information; a filename hash or
format signature does not establish de-identification. Do not use real patient
assets unless all applicable organizational and legal approvals are in place.
The CLI cannot verify those approvals. The staging directory and every parent
must be trusted, controlled by the local operator and not writable by other
users. Put each exact file in that directory under its lowercase SHA-256
digest, without an extension. No source asset paths or filenames are included
in the candidate request. The exchange must enumerate those same asset hashes.

The transport accepts DICOM Part 10-style signatures, PNG and JPEG signatures.
The DICOM signature check uses the DICM prefix after the 128-byte preamble
(see [PS3.10 §7](https://dicom.nema.org/medical/dicom/2025c/output/chtml/part10/chapter_7.html)).
This is only a format identification check, not a complete file parser,
transfer-syntax validator, DICOM conformance test or privacy scan. No pixels
are decoded. One exact asset per case is supported; series, volumes and
multi-instance studies are not yet supported as first-class inputs.

The approved local adapter must listen on literal loopback HTTP with an
explicit port. Proxies, redirects, remote hosts, URL query parameters and
embedded credentials are refused. The operator must verify the adapter's
identity, access controls, network egress and data-handling behavior separately.
Loopback does not prove that the adapter will keep data local. The transport
does not implement TLS or remote authentication; hospital IT must own any
separate forwarding gateway. Do not expose the example endpoint to a network.

### Workflow

1. Download the candidate exchange from the frozen institutional study.
2. Stage the authorized exact assets as above.
3. Export the language-neutral OpenAPI contract:
   `clinical-ai-eval imaging-transport openapi`.
4. Preview without transmitting any image:

   ```sh
   clinical-ai-eval imaging-transport preview exchange.json /approved/staging --url http://127.0.0.1:8768/evaluate
   ```

5. Review the exact endpoint, case inventory, media signatures, byte totals
   and preview digest. Explicitly approve this digest:

   ```sh
   clinical-ai-eval imaging-transport collect exchange.json /approved/staging --url http://127.0.0.1:8768/evaluate --approved-digest PREVIEW_DIGEST --confirm-local-asset-transmission > receipt.json
   ```

   Every asset is checked again just before dispatch. Limits are 64 cases per
   complete exchange, 8 MiB per asset, 128 MiB total and a 12 MiB JSON request
   limit. There is a 32 KiB reply limit, a configured 1–60 second socket
   timeout, a 900-second between-call stop budget and a stop after three
   transport failures. There are no automatic retries; the deadline is not
   a hard wall-clock process timeout. If a staged asset changes after calls
   have begun the invocation refuses rather than producing a partial receipt.
   Earlier requests may already have occurred; do not blindly rerun.
6. Verify receipt consistency:

   ```sh
   clinical-ai-eval imaging-transport verify exchange.json receipt.json --expected-digest RECEIPT_DIGEST
   ```

7. In the institutional browser study, use **Import local imaging collection
   receipt**. The operator locks the exact predictions and receipt atomically.
   Plain prediction submission is still supported, but provenance cannot be
   retroactively upgraded or removed. After sealing, an administrator may
   explicitly export the private study evidence and the separate transport
   package; both contain sensitive study metadata even though neither contains
   image bytes.

   ```sh
   clinical-ai-eval imaging-transport verify-institution transport.json study.json --expected-digest TRANSPORT_PACKAGE_DIGEST
   ```

### Vendor contract

The candidate receives only a unique request ID, exact product identifier
and version, task and modality identifiers, and base64-encoded exact asset
bytes with SHA-256 and media type. The response must echo request ID, product
identity/version and asset digest, with one of:

- `scored`: integer score in basis points from 0 to 10000;
- `abstained` or `unavailable`: null score.

A candidate-side implementation can use
`caeval.imaging_transport.validate_request(req, expected_target=...)` and
`response(req, target=..., status=..., score_bp=...)`. The candidate's own
model processing and clinical interpretation are outside the evaluator.
No reference labels, reviewer identities, threshold or other candidates'
predictions are sent in the HTTP request. Echoed identity and hash are not
independent evidence that the candidate actually processed the bytes.

A failed or unattempted transport is kept as unavailable, with a distinct
transport event in the receipt. Offline replay verifies structure, inventory,
binding and digest consistency, **not** authenticity, actual request
execution, or actual model identity. No cryptographic network attestation
or durable one-shot dispatch lock is implemented by this local CLI.
