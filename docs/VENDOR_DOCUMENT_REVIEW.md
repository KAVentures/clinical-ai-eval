# Vendor document source review and declared freshness

From Candidate intake and assessment setup, open a registered document's
**Review original source and freshness** link. Active administrators and
operators can inspect the exact content-addressed original if it is supported
UTF-8 text up to 512 KiB, or explicitly [IT-staged PDF page text](PDF_SOURCE_REVIEW.md). No external service, model or parser is called in browser requests. Unstaged PDFs, Office, scans and unsupported documents remain registered evidence without fabricated text.

Record one explicitly identified vendor statement, a source finding, rationale,
unique verbatim quotations, document version, and optional issue and expiry
dates. Support or contradiction requires a matching quotation. Offsets are
Unicode codepoint positions in the exact decoded source, not page coordinates.
Repeated quotations and browser newline normalization that change the source
are refused. Use a unique single-line excerpt. A finding about the wording
does not establish the truth of the vendor claim, clinical performance or
regulatory compliance. Dates are reviewer-entered, not verified validity.

Every submission is append-only and binds the original SHA-256, vendor and
evidence identifier, prior review digest and audited account. A concurrent
update requires reopening the document; no previous record is overwritten.
Expired or unknown dates stay visible. A changed source or broken ledger is
unavailable, not accepted evidence.

The committee snapshot includes the latest source finding, declared freshness,
digest and count, but not reviewer identity, claim, quotation or rationale.
The original document presence and legacy review status remain separate.
Older snapshots remain exportable and are identified as historical when
evidence changes. The portable dossier includes this metadata under its
existing snapshot digest. It does not embed the full source-review ledger.
Only approved administrators may select original document attachments.

This is an administrative evidence review, not a physician adjudication or
clinical deployment gate. The PDF workflow uses derived page-relative text offsets, not visual coordinates. Governed scans, Office formats, independent review assignment and separately verifiable review-ledger export remain future work. Actual security, retention and clinical validation
requirements remain outstanding.

Synthetic tests: `python -m unittest tests_unit.test_vendor_document_review`.
