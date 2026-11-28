# Offline vendor evidence requests and submissions

This standalone buyer/vendor workflow complements the existing institutional
document-review and procurement systems. It verifies the submitted bytes
against a prospective request, not the truth of vendor claims, safety,
regulatory conformity, or suitability for clinical deployment.

## Buyer: define the required evidence before submission

Create a unique request ID and identify the exact buyer, vendor, product and
product version. List mandatory and optional documentary requirements. For
example, save the following as spec.json:

    {
      "request_id": "request-2026-001",
      "buyer": "hospital-a",
      "vendor": "vendor-a",
      "product": "clinical-assistant",
      "version": "v1.2",
      "requirements": [
        {"id":"intended-use","mandatory":true,"description":"Version-specific intended use and limitations"},
        {"id":"validation","mandatory":true,"description":"Independent evaluation report"},
        {"id":"monitoring","mandatory":true,"description":"Post-deployment monitoring plan"},
        {"id":"usability","mandatory":false,"description":"Usability study"}
      ]
    }

    python -m caeval.vendor_submission freeze --spec spec.json --output request.json

Retain the printed SHA-256 request digest in a separately controlled buyer
record, then transfer the request via an approved channel. The digest does
not prove authorship, authorization or creation time. Use non-identifying
tokens. Do not put credentials, patient records or other sensitive material
into the request. This module does not supply a mandatory regulatory checklist;
the buyer determines requirements in consultation with relevant experts.

## Vendor: create a bounded, deterministic package

Use the Python API in a restricted environment. Load the request and compare
its digest against the independently communicated buyer digest. Respond to
every requirement: supplied, unavailable, or not_applicable_claimed.
Missing or claimed-inapplicable items require an explanation. A vendor claim
of inapplicability does not waive a mandatory item.

    import json
    from pathlib import Path
    from caeval.vendor_submission import build

    request = json.loads(Path("request.json").read_text())
    responses = {
        "intended-use": {"status":"supplied","explanation":""},
        "validation": {"status":"supplied","explanation":""},
        "monitoring": {"status":"unavailable","explanation":"Awaiting review"},
        "usability": {"status":"not_applicable_claimed","explanation":"Vendor explanation"}
    }
    blobs = {
        "intended-use": Path("intended-use.pdf").read_bytes(),
        "validation": Path("validation.pdf").read_bytes()
    }
    archive = build(request, independently_communicated_digest, responses, blobs)
    with open("submission.zip", "xb") as handle:
        handle.write(archive)

Use a private working directory and restricted permissions. The archive
uses uncompressed ZIP members with canonical names and exact byte hashes.
One artifact is permitted per supplied item. Limits: 100 requirements,
8 MiB per artifact, 32 MiB total artifacts and 33 MiB archive. Do not put
raw patient records, secrets, or executable content into submissions.

## Buyer: verify an offline package

    python -m caeval.vendor_submission verify --request request.json --expected-digest DIGEST --archive submission.zip --output receipt.json

The verifier checks request and version scope, the exact response inventory,
canonical manifest, artifact sizes and hashes, allowed member names, and
mandatory coverage. It rejects duplicate or unexpected members, path traversal
names, symlinks, encrypted or compressed members, malformed JSON and altered
bytes when they disagree with the manifest. It never extracts the archive
or interprets document contents. Outputs are created exclusively, with
owner-only POSIX permissions; on Windows use a restricted directory and ACL.

Exit status 0 means mandatory files supplied and internally consistent.
Exit 1 means a verified package has missing mandatory files (receipt written).
Exit 2 means invalid input, archive or output operation (no valid receipt).

## Trust and review boundaries

VERIFIED_BYTES_ONLY indicates that the package is internally consistent and
bound to the expected request, not that it was sent by an authentic vendor.
A vendor can replace bytes and regenerate a consistent manifest. Authenticate
the sender through a separate approved channel and retain the full archive
digest independently to detect later changes. A receipt does not attest
clinical content, legal compliance, malware absence, privacy or completeness
of the underlying evidence. Never open untrusted files on a clinical system.
Use approved quarantine, scanning, document review and source verification.

The receipt lists missing mandatory requirements and artifact hashes but
does not reproduce explanations or raw evidence. The report remains
restricted (decision_grade=false, publication_safe=false). Coverage is
not an acceptance decision. A mandatory artifact can be supplied and
still be irrelevant, false, obsolete, or insufficient. The buyer's
existing human review and institutional governance must decide sufficiency.

No network transport, persistent replay ledger, digital signatures, shared
router, database or central CLI modifications are included. A subsequent
institutional integration should preserve authorization and the independently
retained request and archive hashes when handing evidence to the existing
source-bound document review workflow.

Run synthetic tests:

    python -m pytest -q tests_unit/test_vendor_submission.py
