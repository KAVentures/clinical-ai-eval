# Local assisted-trial clinician decision collection

This module implements a private, source-bound browser for each declared
clinician in the randomized assisted trial. It is not a clinical care tool.
Use independently approved research protocols and pseudonymous data.

## Coordinator export

Freeze and allocate the study using PREDICTIVE_ASSISTED_TRIAL.md. Preserve
its independently retained design and allocation digests. The coordinator
must establish prospective enrollment, actual randomization and concealment,
appropriate decision-time evidence, intervention integrity and isolation.

In a private coordinator directory prepare four fixed-name files:

- decisions.json: mapping of every planned unit ID to its exact UTF-8
  decision-time source text.
- assistance.json: mapping of every planned unit ID to its exact UTF-8
  assistance material, corresponding to the frozen candidate version.
- decision-protocol.txt and assistance-protocol.txt: approved UTF-8 protocols.

These must match the frozen study's per-unit and protocol SHA-256 commitments.
Each text is limited to 64 KiB; each JSON file to 16 MiB. Do not include raw
patient records, outcome information, credentials or other readers' decisions.
This module does not generate assistance or prove clinical source validity.

Call `predictive_assisted_reader.export` with the six frozen-scope arguments
of the assisted-trial engine and keyword arguments `allocation`,
`allocation_digest`, `clinician`, `source_directory`, `output_root`.
The output root must be a separate private directory, not nested with the
coordinator directory. Each export creates an exclusive `reader-CLINICIAN`
folder with the approved packet, decision inputs, protocols and only the
assistance assigned to assisted cases. Control-case assistance is not copied.
Retain the packet digest independently and transfer only the individual
folder, never the shared parent or coordinator materials. Enforce access
using institutional ACLs. The same allocation applies to all declared
clinicians, as specified by the original trial engine.

## Clinician review

    python -m caeval.predictive_assisted_reader --directory /private/reader-clinician-a --expected-packet PACKET_DIGEST --clinician clinician-a

Authenticate at the printed loopback address with the session-only random
password and username `reader`. The browser shows one case at a time;
assistance and its protocol are displayed only in the assigned assisted arm.
Record the protocol action, no action, or unknown. After every assigned case
has an explicit decision, seal the submission and retain its digest.
The exclusive submission.json contains only decision tokens and commitments,
not source text. The coordinator can supply it directly to the existing
trial analyze/verify API with independently retained submission digests.
No external source receipt is asserted; the source pointer is a digest of
this declared local decision session. Unsubmitted in-memory decisions are
lost if the browser process stops; no automatic recovery is provided.

The reviewer browser rechecks the approved packet, protocol and exact text
inventory before every action. It refuses altered or extra control assistance,
stale forms, incomplete sealing and overwrite. The loopback, credential,
CSRF and private directory restrictions are the same trusted-local-workstation
boundary as the reference reader. Clinicians know their assigned arm, and
exposure to assisted cases may influence later control decisions; study design
and independent procedures must address carryover and interference. Neither
this software nor its digests establish actual blinding, professional identity,
clinical validity, causal patient benefit, publication clearance or deployment
authorization. Full repository integration CI is required.

    python -m pytest -q tests_unit/test_predictive_assisted_reader.py
