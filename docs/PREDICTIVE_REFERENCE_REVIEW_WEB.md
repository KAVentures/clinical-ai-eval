# Source-bound independent reference review browser

This standalone browser is for a single declared reference reader, separate
from the operator's predictive assessment browser. It consumes the native
packets from PREDICTIVE_REFERENCE_PANEL.md and emits the same sealed submission
format. It does not expose any prediction data, other reader ratings or model
versions from the study. Independence, professional identity and external
blinding remain institutional responsibilities.

## Prepare a reader-specific private directory

The coordinator must first approve the exact clinical reference protocol and
source inventory, independently screen source material for identifiers, model
outputs and unintended labels, and freeze the predictive plan and reference
panel. The reference protocol digest is SHA-256 of exact UTF-8 protocol bytes;
each source digest is SHA-256 of its exact UTF-8 text bytes. Use pseudonymous
approved material only, no raw patient charts, secrets or unnecessary personal
data. This tool is not a de-identification pipeline or a source extractor.

Provide a separate owner-only directory (0700 on POSIX, restricted ACL on
Windows) for each reader, using these fixed filenames and owner-only files:

- `packet.json`: the role-specific primary or adjudication packet from the
  panel engine. Retain the packet digest independently of the files.
- `protocol.txt`: exact approved UTF-8 reference instructions.
- `sources.json`: JSON object mapping only the packet's unit tokens to the
  exact approved source text strings. The UTF-8 bytes of each decoded string
  must match the corresponding packet digest. No extra or missing unit keys.

For an adjudicator, use the disagreement-only packet and provide only its
source texts. The packet does not contain either primary reader's rating.
Do not distribute other readers' directories or prediction bundles. Each input
file is bounded to 16 MiB (protocol and individual text at most 64 KiB), with
at most 10,000 packet units. This version accepts text, not PDF or imaging.

## Review and seal

    python -m caeval.predictive_reference_review_web --directory /private/reader-a --expected-packet PACKET_DIGEST --reader doctor-a

Open the printed loopback address, use username `reviewer` and the session
password printed in the private terminal. Do not share the password or forward
the port. The interface verifies the packet, role, source digests and protocol
before starting and before every read/write review action. It displays the
protocol and one source at a time. Choose outcome present, outcome absent, or
insufficient evidence/unknown. Every unit requires an explicit rating, even
if unknown. The progress counter is not a clinical metric. Stale forms are
refused. Ratings are session-local and not editable; if a correction is needed
before sealing, stop the process and start a fresh review session. A crash or
restart before sealing loses unsealed ratings; no draft recovery is provided.

At the end, confirm the rating-count summary and seal. The browser exclusively
creates `submission.json`, containing source digests and labels but no source
text. Existing output is never overwritten. Retain the displayed submission
digest independently and transfer the sealed file via the institution's approved
restricted channel. The coordinator uses the existing panel adjudication and
finalization API/CLI, retaining both primary submission digests. The resulting
reference bundle can be consumed by predictive research without further model
calls. The browser does not automatically transfer records to the coordinator.

## Boundaries

The loopback service uses a fresh session password, exact host and peer checks,
origin checks, CSRF, bounded forms, escaped text, no-store responses and a
restrictive content policy. No arbitrary paths, upload endpoints, external
resources or patient systems are accessed by HTTP. A trusted local operator
must control files and credentials. Hashes demonstrate exact byte consistency,
not source authenticity, sufficient clinical evidence or professional identity.
Screen text before distribution; the software cannot detect hidden labels or
identifying information in source material. No external authentication, secure
remote collaboration, clinical validation, publication clearance or deployment
authorization is claimed. Full repository integration CI remains required.

    python -m pytest -q tests_unit/test_predictive_reference_review_web.py
