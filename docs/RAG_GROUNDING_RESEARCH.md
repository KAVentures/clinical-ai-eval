# Controlled RAG grounding research

This optional experimental lane complements the bundled synthetic RAG
rehearsal. It is intended for authorized corpora and externally reviewed
question/reference pairs. It is not a canonical validation family or a
clinical decision system.

## Study design

An administrator freezes the complete versioned corpus, each question's
reference document IDs and disjoint distractor IDs, candidate identifiers and
versions, two distinct active reviewer accounts, and operator attestations.
Use approved non-identifying material only; do not paste clinical records,
identifiers, credentials or confidential sources that cannot be shared with
the candidate. The application does not detect identifiers or establish that
a source is clinically accurate, licensed or independently reviewed.

For each question the same query is presented twice:

- **Supported:** reference documents plus the specified distractors are
  available for the candidate's own retrieval.
- **Support removed:** the reference documents are removed; the same
  distractors remain available.

The study requires a nonempty set of current reference documents and a
nonempty disjoint distractor set. Clinical reviewers must establish outside
this software that the references actually support the query, and that
the distractors do not provide equivalent support. Merely designating
documents does not establish answerability or the validity of a control.
A withdrawn document can be included as a distractor. Question clusters
are recorded for future statistical analysis; no cluster-adjusted inference
is performed.

Example (fictional, not clinical):

```json
{
  "study_id": "grounding-study",
  "corpus_id": "fictional-corpus",
  "corpus_version": "v1",
  "documents": [
    {"id":"reference","version":"v2","title":"Fictional reference","text":"The token is blue.","state":"current"},
    {"id":"distractor","version":"v1","title":"Unrelated scenario","text":"Another token is red.","state":"current"}
  ],
  "questions": [
    {"id":"q1","cluster_id":"c1","query":"Which token is specified?","reference_ids":["reference"],"distractor_ids":["distractor"]}
  ],
  "candidates":[{"id":"vendor-a","version":"v1"}],
  "reviewers":["doctor-a","doctor-b"],
  "attestations":{
    "authorized_corpus":true,
    "independently_reviewed_references":true,
    "no_patient_identifiers":true,
    "prospective":true,
    "isolated_case_execution":true
  }
}
```

Limits include 80 documents, 40 question pairs, six candidates, 20 declared
claims per answer and 300 per candidate, plus explicit byte limits.
The browser uses bounded JSON forms and is not intended for large production
corpora.

## Candidate execution and trace collection

Open **Independent RAG grounding research** in the institutional dashboard.
An administrator previews and explicitly freezes the protocol. Operators
download a case-specific corpus exchange and trace template for each product.
The exchange omits reference identifiers as labels, condition names, reviewer
IDs and cluster IDs, but the document contents and paired query can reveal
the condition. Isolate each run in a fresh index/session with no access to
the withheld corpus, external retrieval or cross-case memory. The application
does not run or enforce this external execution environment.

The adapter records retrieved document IDs in rank order and the exact
provided document digests. The reported answer status is answered, abstained,
or unavailable. Unavailable requires empty retrieval, answer and claims.
Otherwise the answer is nonempty. Candidate-declared claim spans are sorted,
nonoverlapping, zero-based Python Unicode code-point offsets with an
exclusive end. For each claim list the source IDs the candidate actually
cited. Keep nonexistent and out-of-case citation IDs in the trace so defects
are visible. Do not fabricate retrieval, citations or model identity.

These are product-reported traces, not independent attestation of retrieval
or execution. The protocol verifies the allowed corpus and digest of every
reported retrieved document. It cannot prove that the product read those
documents or that the claims include every substantive assertion.

Every candidate must submit the full slot inventory, including unavailable
cases, before reviewers receive their packets. A submitted trace cannot be
replaced. No automatic candidate calls are made.

## Independent citation review

The two assigned reviewers each download the same blinded packet and a
ratings template. The packet includes the exact query, answer, declared
claim spans, retrieved source IDs, citations with case availability and a
deduplicated document dictionary. The candidate identifiers, condition labels,
private nonce, and peer ratings are not disclosed. Use only documents marked
available for the case when assessing a citation. Blinding is a software
boundary, not a guarantee against answer-style identification or external
communication.

For each available answer, independently assess whether the declared claim
inventory covers all substantive assertions: complete, incomplete or
uncertain. For each declared claim, rate whether the cited material
supports the claim: supported, unsupported or uncertain. An answer with
no substantive claims may have a complete empty claim inventory. A supported
rating requires at least one available cited document. It is not a claim
that the answer is medically appropriate or that a document is current or
was actually retrieved; those are separate axes. Both reviewers must submit
every assigned unit and claim. No reviewer sees the other's ratings.
Disagreement and uncertainty stay unresolved; there is no forced consensus
or third-reader adjudication in this lane.

After both reviews are locked, the administrator explicitly seals the exact
trace/review inventory. Only administrators see the aggregate results and
can export the private evidence. Operators and committee observers see
coverage, not answer text, documents, ratings or reviewer identities.
Study, trace, review and seal inventory is checked against the institutional
audit ledger. This detects local inconsistency, not a privileged attacker
rewriting the complete database and audit history.

## Interpretation

Results are per candidate and per condition:

- Availability, reported reference retrieval hits and all-reference retrieval
  counts, plus reference hit missingness bounds in the supported condition.
- Declared abstention rate conditional on available answers.
- Withdrawn retrieved documents, uncited claims, outside-corpus citations,
  nonretrieved citations and withdrawn citations.
- Claim-level support where both independent reviewers agree, unresolved
  claims and reviewer disagreement.
- Answer-level agreement on complete declared claim inventory.

All denominators are explicit. An empty denominator gives null. Bounds
are worst/best descriptive missingness bounds, **not confidence intervals**.
Claim support bounds cover declared claims only, not omitted claims or
unavailable answers. A high conditional support rate with incomplete claim
coverage or missing answers is not evidence of general reliability.
No cross-vendor statistical superiority test, combined score or clinical
acceptance threshold is implemented.

## Replay and unified procurement

Export the private package and verify it offline:

```sh
clinical-ai-eval rag-grounding verify private-rag-grounding-evidence.json --expected-digest TRUSTED_SHA256
```

The verifier reconstructs the protocol, trace validation, independent reviews,
and descriptive summaries. The optional independently retained digest checks
the expected evidence identity. Replay does not verify external source truth,
actual reviewer independence or product execution.

A buyer programme can explicitly add a `rag_grounding` requirement with
`family: null`. Evidence discovery requires a sealed study with a matching
registered vendor ID and exact declared product version, and at least one
available trace. The administrator must assess task and buyer-scope relevance.
The association has experimental claim scope, does not satisfy a canonical
RAG family, and cannot authorize procurement or deployment.

The private package contains source texts, candidate outputs and reviewer
identifiers. Keep it within the approved institution. The optional governed local collection bridge below provides automated local dispatch to a compatible adapter. There is no remote vendor dispatch, arbitrary remote corpus ingestion, production retriever connector, clinical credential verification, statistical inference, citation entailment model, or clinical validation in this release.


## Governed local retrieval-trace collection

Instead of manually entering a trace, an institution may run a compatible
candidate adapter on an approved host and collect the exact case-specific
traces through the local CLI. This is a narrow transport contract, not an
independently trusted execution or retrieval attestation.

### Candidate integration

Export the language-neutral OpenAPI 3.1 document:

```sh
clinical-ai-eval rag-collection openapi > rag-openapi.json
```

A candidate implements a POST endpoint, for example
`http://127.0.0.1:8768/evaluate`. The request contains the versioned
`clinical-ai-eval-rag-trace/1` protocol, a unique 128-bit request identifier,
the exact declared product ID and version, and one case with an opaque slot,
query, and exact allowed corpus documents with SHA-256 digests. There are no
private reference labels or reviewer identifiers in the request. The response
echoes protocol, request ID and exact product identity, and supplies one
structured trace for that slot. Returned retrieval IDs/digests must refer to
the exact allowed corpus; citations outside the allowed corpus are retained
as defects, not filtered. Claim spans use Unicode code-point offsets.

A Python adapter can call `caeval.rag_collection.validate_request` with its
actual served product identity, then return `response(req, target=...,
trace=...)`. The OpenAPI schema is a structural contract; runtime checks
add semantic hashes, cross-message correlation, nonoverlapping offsets,
byte limits, and unique inventories. An adapter must establish a fresh
isolated retrieval environment for each call, with no withheld documents,
external search or shared case memory. The collector cannot establish that
the adapter does this, or that it actually invokes the declared product.
The adapter is responsible for its own access controls and egress policy.

### Local operator procedure

1. Download the frozen candidate exchange from the institutional study.
   Store it as a restricted organizational record. Do not transmit
   unauthorized data or patient identifiers.
2. Choose an existing private journal root, writable only by the authorized
   operator, with trusted non-symlink ancestors. Preview the exact exchange,
   endpoint and timeout without making any HTTP request:

   ```sh
   clinical-ai-eval rag-collection preview exchange.json --url http://127.0.0.1:8768/evaluate
   ```

3. Verify the candidate adapter, case inventory, approval digest, data handling
   and local host configuration. Explicitly authorize that exact preview:

   ```sh
   clinical-ai-eval rag-collection collect exchange.json --url http://127.0.0.1:8768/evaluate --approved-digest PREVIEW_DIGEST --journal-root /private/approved-journal --confirm-local-corpus-transmission > receipt.json
   ```

   The collector allows only literal loopback HTTP with an explicit port; no
   DNS names, proxy, redirect, embedded credentials, URL queries or remote
   network endpoints. No authentication or TLS is implemented by this local
   contract. Loopback does not prove that the adapter keeps data local.

   The journal creates a single exclusive directory per exact exchange
   digest, independent of endpoint selection. Each intent is written and
   file-synced before dispatch, and the corresponding result is stored once.
   Directory sync is used on POSIX where supported, not guaranteed on Windows.
   Ordinary repeated dispatch from the same retained journal root is refused.
   This is not a distributed exactly-once guarantee: changing machines,
   modifying the exchange or deleting a journal can bypass the local guard.
   If interrupted, do not blindly restart. The intent may have reached the
   adapter; inspect the journal and resolve the study through an approved
   new execution protocol. No automatic resume or retry occurs.

   Bounds: 80 cases, 4 MiB exchange, 1 MiB request, 32 KiB reply, 550,000-byte
   normalized trace JSON budget, 300 total declared claims, 1–60-second
   socket timeout, and a 900-second between-call deadline. Collection stops
   after three transport failures or an aggregate output-budget refusal.
   The deadline is not a hard wall-clock process timeout. Unattempted,
   failed or over-budget traces remain explicitly unavailable, with distinct
   event states. A correlated candidate-declared unavailable outcome is not
   counted as a transport failure.
4. Verify the receipt:

   ```sh
   clinical-ai-eval rag-collection verify exchange.json receipt.json --expected-digest RECEIPT_DIGEST
   ```

   Replay rechecks the complete case inventory, approved request sizes and
   hashes, reconstructed request and correlated response hashes, event order,
   failure bounds and aggregate trace budget. It does not prove network
   execution or receipt authenticity. The separately retained digest is
   needed to establish the expected receipt identity.
5. Use **Import local collection receipt** in the institutional study.
   The trace and receipt are atomically locked, and receipt linkage is
   checked before independent review and sealing. Plain manually imported
   traces remain supported, but their provenance cannot be retrospectively
   upgraded or stripped. The receipt does not change the independent
   reviewers' scientific packet or consensus criteria.
6. After sealing, export the private study evidence and separately approved
   collection evidence package. Replay both together:

   ```sh
   clinical-ai-eval rag-collection verify-institution study.json collection.json --expected-digest COLLECTION_PACKAGE_DIGEST
   ```

   The private collection package binds the sealed study digest to the exact
   receipt roster. It contains candidate answers and local configuration,
   not proof of independently observed retrieval. The audit-linked receipt
   digest is visible in restricted committee and buyer programme coverage.
   No clinical claim, canonical promotion, product recommendation or deployment
   authorization results from collection.

Journal files, exchanges, receipts and private evidence packages are
restricted organizational data. Manage their storage and deletion according
to approved policy; do not remove a journal simply to permit redispatch.


## Prospectively registered paired comparisons

For a grounding study with two or more candidate identities, an administrator
can freeze one ordered baseline/candidate comparison before the institution
records any candidate exchange/template export or trace submission. The
comparison is optional and does not change the candidate or reviewer packets.
Once exposure is recorded, prospective registration is refused. This audit
ordering is local evidence only; it cannot prove that external exposure or
prior knowledge did not occur.

The frozen protocol identifies the exact study digest, the two declared
candidate identities and versions, one primary endpoint, familywise alpha,
a research margin (candidate minus baseline), a maximum unknown-pair
fraction, minimum independent clusters, and an explicit independence
attestation. Four fixed endpoints are always reported:

- **Reference hit:** at least one reference document reportedly retrieved
  in the supported condition; unavailable traces are unknown.
- **Reviewed grounded answer:** in the supported condition, declared answered
  status, all reference documents retrieved, nonempty claims, every declared
  claim cited only to retrieved current sources, both reviewers agree that
  the claim inventory is complete and every declared claim is supported.
  Deterministic defects and agreed negative reviews are failures.
  Disagreement or uncertainty without a known failure is unknown.
  This is an operational grounding proxy, not a clinical correctness verdict.
- **Declared abstention:** the candidate declares abstention in the
  support-removed condition; unavailable is unknown. This does not establish
  appropriate or safe abstention.
- **Paired availability:** both conditions have available traces; otherwise
  failure. Availability itself is observed even when answer outcomes are not.

Each predeclared question has a matched pair of values, one for each
candidate. The difference is candidate minus baseline, in [-1,1]. For each
cluster calculate the mean difference across its questions; the study
estimate is the mean over clusters, **not** over questions. This avoids
overweighting larger clusters but changes the estimand. The complete-pair
mean uses only known pairs and clusters with at least one known pair;
it is descriptive and potentially selection-biased. For unknown binary
outcomes, replace candidate missing values with 0/1 and baseline missing
values with 1/0 to get lower/upper differences. Average both envelopes
over all questions within each cluster and then over all clusters.
The unknown-pair fraction is similarly equal-cluster weighted.

If the prespecified minimum (at least 10) clusters and independence
attestation are met, simultaneous conservative bounds use Hoeffding's
inequality for independent bounded cluster observations, without assuming
independence of questions within a cluster. For K clusters, m=4 endpoints
and familywise alpha, use one-sided tail probability alpha/(2m):

```text
radius = sqrt(2 * log(2*m/alpha) / K)
interval = [max(-1, mean_lower - radius), min(1, mean_upper + radius)]
```

The union bound covers both ends of all four endpoints, including unknown
outcomes through their envelopes. The target is the equal-weight mean of
the independent cluster expectations; population representativeness,
independence and reference accuracy are not established by this software.
These deliberately wide bounds do not collapse to zero width if every
observed pair agrees. No normal approximation, data-driven endpoint
selection or bootstrap resampling is performed. With the current limit
of 40 questions, power is limited; inconclusive results are expected.
No inference is emitted without the cluster gate. The missingness bounds
are separately shown even when inference is withheld.

The primary endpoint produces a research signal only when the unknown
fraction is within the prespecified maximum and inference is available.
A lower interval endpoint strictly above the margin produces
`lower_bound_strictly_above_margin`; an upper endpoint strictly below
produces `upper_bound_strictly_below_margin`; otherwise inconclusive.
No equivalence, clinical noninferiority, clinical safety or procurement
acceptance decision follows from this signal. The other endpoints remain
visible. One pair is registered per study; post-hoc pair selection and
cross-study pooling are not implemented. Comparing product releases
requires a deliberately matched design with distinct candidate identities;
the current protocol does not support two entries with the same candidate ID.

After the study is independently reviewed and sealed, an administrator
can inspect and explicitly export the derived comparison. The committee
snapshot records the protocol and analysis digests, not the full endpoint
results. Replay the private study and comparison together:

```sh
clinical-ai-eval rag-comparison verify study.json comparison.json --expected-digest COMPARISON_DIGEST
```

The verifier reconstructs endpoints, bounds and research signal. A trusted
digest can establish the expected comparison identity. Offline replay
does not establish when a plan was registered, actual model execution,
external scientific validity or deployment authorization.
