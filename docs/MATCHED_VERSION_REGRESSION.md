# Matched longitudinal version research

This experimental workflow compares two separately frozen studies of the same
declared product on an exactly matched historical cohort. It complements the
unified programme successor coverage view, which does not itself compare
performance. No statistical noninferiority, procurement acceptance, clinical
validity or deployment authorization is established.

## Registration and execution

1. Complete and seal the baseline imaging or RAG grounding study. A complete
   candidate roster is required.
2. Freeze a new native study with a distinct study ID and opaque nonce, the
   same cohort and reference protocol, the same target candidate ID and a
   different declared version. Keep every other candidate identity and
   version unchanged. Do not export any successor candidate material or
   submit any trace before registration.
3. Open **Matched longitudinal research** from the institutional dashboard.
   Select the modality, baseline and successor IDs, target vendor ID, primary
   endpoint, descriptive margin and maximum unknown-pair fraction. Preview
   and explicitly approve the exact protocol. One registration per successor
   and modality is allowed.
4. Collect successor predictions or retrieval traces through the corresponding
   native workflow and complete its required independent review and seal.
   The registration does not execute any candidate. A sealed incomplete
   imaging successor is explicitly blocked.
5. Administrators can inspect and export the comparison. Operators and
   committee observers see restricted registration and comparison coverage.

The local audit ledger enforces that the baseline seal and successor study
freeze precede the registration and the registration precedes any recorded
successor candidate export, submission or seal. It cannot establish that
external exposure did not occur. The baseline results are already available:
this is not a fully prospective confirmatory trial.

## Exact matching

For imaging, every case ID, asset digest, reference label, cluster, stratum,
task, modality, reference method and attestation must match. The target
candidate's threshold must remain unchanged. Reference-method identifiers
and labels do not prove the same external adjudication process or clinical
reference truth.

For RAG, every corpus document, version, question, reference/distractor
inventory, cluster, reviewer identity and attestation must match. Independent
reviews are performed within each native study; agreement and reviewer
behavior can change between runs. Matching reviewer accounts does not
establish identical judgment or independence.

Changed cohorts, clinical thresholds, reference protocols, other candidates,
or task definitions require separately scoped research. A version identifier
is declared, not independently verified. Historical cohort reuse may cause
selection bias, memorization, leakage or temporal confounding.

## Endpoints and interpretation

For imaging, the four fixed endpoints are resolved-reference accuracy,
sensitivity, specificity, and score availability. Unresolved reference
cases are excluded from the first three; non-scored resolved cases are
unknown. Availability includes every case. For RAG, the four fixed endpoints
are the native paired research proxies: reported reference hit, reviewed
grounded answer, declared abstention with support removed, and paired
availability. See [RAG grounding research](RAG_GROUNDING_RESEARCH.md)
for exact definitions and limitations.

Each endpoint is matched by private case/question ID, never by order or
candidate-facing slot. The binary outcome contrast is successor minus
baseline. Within each applicable cluster average case differences, then
equally weight the applicable clusters. This is not a case-weighted estimate.
Unknown binary values are bounded by assigning the successor missing value
0/1 and baseline missing value 1/0 for the lower/upper envelopes. The
complete-pair estimate is conditional on known pairs and is descriptive.
A missing endpoint denominator produces null, not zero.

The unknown pair fraction is also cluster-weighted. The primary endpoint
produces a descriptive signal only if its unknown fraction is within the
specified limit. The whole observed difference envelope must be strictly
above or below the research margin for the corresponding signal. Rational
arithmetic determines this comparison before conversion to displayed floats.
Equality or overlap is inconclusive about crossing the margin.

These bounds describe the finite reused cohort, **not confidence intervals**.
No statistical significance, population inference, noninferiority or clinical
acceptance follows from them. There is no combined cross-modality score.
Clinical utility, calibration, temporal drift, workflow impact and safety
require separately designed validation.

## Replay

Export both private native evidence packages and the restricted comparison
from the institutional interface.

```sh
clinical-ai-eval verify-matched-regression baseline.json successor.json comparison.json --expected-digest TRUSTED_COMPARISON_DIGEST
```

The verifier reconstructs native evidence, exact matching, endpoint
applicability, missingness envelopes, protocol and result digest. Replay
does not verify the prospective audit timing independently, reviewer
independence or actual product identity/execution. Store all private evidence
and trusted digests in the approved institutional environment. Committee
snapshots record the registration and derived comparison identity, not
private outputs or individual ratings.
