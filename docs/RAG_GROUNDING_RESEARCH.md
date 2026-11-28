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
identifiers. Keep it within the approved institution. There is no automated
vendor dispatch, arbitrary remote corpus ingestion, production retriever
connector, clinical credential verification, statistical inference, citation
entailment model, or clinical validation in this release.
