# Offline PDF source review

The institution may optionally provision a patched Poppler `pdftotext`
executable in an approved, isolated POSIX maintenance environment. Do not run
untrusted PDF parsers with privileged accounts, production credentials or
unnecessary network access. The browser never parses PDFs. Stop the gateway,
workers and other CLI writers before staging a registered original document:

    clinical-ai-eval institution pdf-stage /approved/workspace --evidence-id EVIDENCE_ID --confirm-services-stopped --confirm-pdf-extraction

The operator must approve the PDF source and the isolated parser environment.
The staging tool checks the original SHA-256, runs the local executable with
bounded CPU, address-space, file-size and elapsed time limits, and refuses
warnings, malformed/encrypted documents, over 50 pages, over 512 KiB of
extracted text, and documents with no extractable text. Resource limits are
not a security sandbox. No OCR or external service is invoked. The extraction
stores the exact parser version, configuration, page text digests, original
document hash and audited extraction digest. Blank or scanned pages in a mixed
document may have no extractable text and must not be assumed empty visually.

After restarting the approved browser service, an operator can open the source
review, select a page number and quote a unique exact passage from that derived
page text. Compare with the original visual document in an approved viewer
before attesting. Tables, charts, hidden text, reading order and font encodings
may not be faithfully represented. The page-relative Unicode offsets are not
PDF coordinates or proof of semantic accuracy. Historical text reviews retain
their original schema. PDF review history binds the extraction digest and
page-specific source hash. The committee receives only the existing restricted
review metadata, not raw text or reviewer notes.

To independently repeat the extraction in the same controlled environment:

    clinical-ai-eval institution pdf-replay /approved/workspace --evidence-id EVIDENCE_ID --confirm-services-stopped --confirm-pdf-extraction

Replay refuses a changed parser version or extracted page text. A staged
derivative cannot be silently replaced; a changed document needs its own
content-addressed source. No claim of clinical or regulatory validity follows.
This is local IT-attested extraction, not a cryptographic signature or full
PDF conformance validator. Scans, Office files, layout verification, OCR and
review-ledger exchange remain separate work.

    python -m unittest tests_unit.test_pdf_source_review

The real-parser smoke test skips when Poppler is absent; mocked staging tests
exercise browser and ledger behavior without a parser dependency. IT must
validate the actual deployed parser separately.
