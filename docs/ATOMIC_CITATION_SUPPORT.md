# Atomic citation support audit — experimental, offline

A citation resolving to a document does **not** imply that it supports the
clinical claim. This standalone scorer makes the distinction explicit.

The existing `citation_verification` **family remains blocked** in the project
registry because the current product/RAG adapters do not produce an independently
reviewed, exhaustive set of atomic claims. This tool implements the missing
claim-source *adjudication and measurement component*; it does not silently
promote that family to validated or runnable.

## Inputs

One version-pinned JSON pack supplies:
- `sources`: unique `source_id`, nonempty `text`, `version`, and
  `status` (`current` or `superseded`).
- `claims`: unique `claim_id`, atomic claim `text`, and list of
  `cited_source_ids`. **Claim extraction is externally supplied**. You must
  ensure extraction has adequate coverage before using any result as evidence.

No medical ground truth is inferred from document overlap. Missing citations,
obsolete references and nonexistent source IDs are detected deterministically.
Actual semantic support requires independent human judgement.

## Workflow

```bash
clinical-citation-audit prepare \
  --claims casepacks/citation_support/public_dev/demo.json \
  --out out/citation_review_queue.jsonl
```

Each queue item contains a single atomic claim, an exact current source
document, and the content hash of the whole reference pack. It contains no
other reviewers' answers, supporting blind/independent review.

Write independent reviewer JSONL records, at least **two distinct clinicians**
per claim/current-source pair:

```json
{
  "claim_id": "claim-a",
  "source_id": "SYN-001",
  "reviewer_id": "reviewer-1",
  "reviewer_kind": "clinician",
  "verdict": "supports",
  "evidence_quote": "SYNTHETIC SOFTWARE FIXTURE: fictional action ALPHA is prohibited when fictional condition Z is present.",
  "pack_sha256": "<copy exact pack hash from review queue>"
}
```

Allowed verdicts: `supports`, `contradicts`, `does_not_support`. Support
and contradiction annotations require a verbatim quote present in the cited
source. **Quote matching establishes provenance of the quoted string, not
semantic entailment**. Two reviewers must independently agree on a verdict;
ties or absent reviews remain unassessed. Automated judge labels may be
included with `reviewer_kind=automated`, but **never count** toward the human
quorum. Reviewer identity is self-declared metadata and is not independently
authenticated.

```bash
clinical-citation-audit score \
  --claims casepacks/citation_support/public_dev/demo.json \
  --reviews out/citation_labels.jsonl \
  --out out/citation_report.json
```

The report includes:
- `unsupported_claim_rate_on_assessed` — denominator **only** claims whose
  support is independently adjudicated or whose citations are deterministically
  invalid. Never include unassessed citations as safe.
- counts of unassessed/conflicting claims, citation coverage and missing/obsolete
  source references;
- version-specific source and pack hashes;
- claim-by-claim evidence mapping and limitations.

Incomplete/conflicting reviews cause a **nonzero exit**, after the report is
written. No remote API calls, private keys, or claimed clinical certification.
The supplied demo corpus is **fictional**.

## Still required for the full clinical-RAG family

Integrate a vendor's **actual** retrieval trace, independently extract all
atomic factual/action claims from its final response, adjudicate claim-to-source
support using a calibrated process and locked source version, and measure
retrieval quality separately. This package only makes semantic citation-support
measurement honest and operational when those inputs are available.
