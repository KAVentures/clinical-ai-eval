# Institutional clinical research studio (experimental)

The four standalone research modules introduced in #55–#58 are available
through a common institutional browser workspace. The browser does not
reimplement their scoring or interpret one evidence format as another.
Canonical project execution and its claim/manifest contract remain separate.

## Roles and workflow

Open **Clinical research studio** from the institutional dashboard.

1. An administrator or operator chooses citation grounding, scribe
   documentation, rule-bound certifiability or paired robustness and
   uploads an approved specification JSON. The exact plan is frozen,
   including the current module definitions. Operators see only their own
   studies; administrators can coordinate all studies.
2. Download the frozen plan, candidate-facing exchange and response
   template. Only send the candidate-facing exchange to the candidate.
   The reference and response template can contain withheld material.
   Conduct collection using the approved candidate interface, preserving
   every actual response or failure. The studio does not perform model
   calls, automate collection or verify a vendor's identity/version.
3. Confirm the exact plan and import the complete responses. Changed plans,
   prompts, target metadata, evidence spans and missing outcomes are rejected
   by the underlying assessment. For citation studies the response template
   includes the expected controlled context, not independently measured
   live retrieval. Fill the actual observed outcome and claim spans.
4. For human-reviewed studies, an administrator confirms reference provenance,
   independent clinical credentials and conflicts externally, and assigns
   enabled reviewer accounts. Robustness requires a separate reference-validity
   roster. Choose the roster and protocol before looking at candidate results
   wherever possible; the studio enforces role separation at packet issuance
   but does not prove precollection roster preregistration.
5. Reviewers see only their own account-issued material. They can complete the
   submission JSON in a browser or download their packet and upload the
   completed document. The interface currently presents structured JSON
   rather than a bespoke per-question form. The submission is locked against
   the exact request and reviewer identity. It cannot be overwritten.
6. An administrator externally attests each exact locked submission by digest.
   Only then is it imported into the underlying signed review workflow.
   Offline imports cannot be counted as authenticated browser attestations.
7. When every independent review is attested, an administrator analyzes
   the study. Rule-bound certifiability uses the deterministic native JSON
   assessment and does not require reviewer packets; its reference remains
   clinically UNVERIFIED. The results are descriptive and cannot be decision
   grade. Operators and administrators can download verified full reports;
   observers see only aggregate summaries and state.

The source material may itself identify a patient or product. Only use
approved synthetic or non-identifiable records; the interface does not
de-identify them. Clinical credentials and independence must be verified
by the institution. The server uses the existing HTTPS origin, CSRF,
federated identity and role boundaries. Uploaded file names are ignored
for path construction. Maximum request size is the existing browser
limit of 8 MiB; the scientific modules impose additional size limits.

## Integrity and recovery

SQLite binds exact plan, collection, roster, pending review and report
digests. The original module validates its current definitions and issued
packets. Report access rechecks frozen inputs and the browser attestation
ledger; modified evidence is unavailable. The study registry and evidence
directory are inside the institution's existing stopped-service backup
boundary. The file and SQLite stores are not one atomic transaction. A
crash between publication and database commit can leave an orphan file;
the ledger fails closed on unexpected files. Investigate with trusted IT
and use a fresh study or verified recovery rather than manually rewriting
digests. No deletion or retention scheduler is included here.

## Scientific contracts and remaining integration

See [citation grounding](CONTROLLED_CITATION_ASSESSMENT.md),
[scribe assessment](SCRIBE_ASSESSMENT.md),
[decision certifiability](DECISION_CERTIFIABILITY_ASSESSMENT.md) and
[paired robustness](PAIRED_CLINICAL_ROBUSTNESS.md) for each schema, rubric,
denominators and limitations. All four have standalone CLI workflows as well.

The canonical executor registry and suite selection are intentionally
unchanged. The study reports are experimental derivatives, not canonical
family packages or procurement evidence. Do not count the six associated
declared suites as canonical-runnable. Automated candidate collection,
canonical executor/report/manifest integration, comparative research
analysis, richer browser forms and clinical calibration remain necessary.

## Supervised local candidate collection

The optional [supervised collection worker](RESEARCH_SUPERVISED_COLLECTION.md) supports the four research modules through approved staged loopback connectors. Browser users can queue and inspect a collection; a separately authorized OS worker executes it. Exact raw outcomes are bound to the scientific import. Clinical claim annotations and independent review remain separate. The canonical executor registry is unchanged.

## Matched cross-product research comparison

See [Matched clinical research comparisons](MATCHED_CLINICAL_RESEARCH_COMPARISONS.md). The browser compares analyzed studies with identical protocols, review rosters and collection modes; it preserves fixed opportunity denominators and missingness. Results remain exploratory and separate from procurement evidence.

## Independent evidence replay

Administrators can export complete analyzed experimental evidence for independent offline reproduction of the full scientific report. See [Experimental evidence replay](EXPERIMENTAL_EVIDENCE_REPLAY.md) for the CLI, approved-sharing boundary, and distinction between report replay and original identity/provenance verification. No canonical-family or procurement gate is upgraded.
