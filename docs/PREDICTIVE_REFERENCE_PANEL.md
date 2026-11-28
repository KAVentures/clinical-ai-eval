# Independent predictive reference panel

This standalone offline workflow produces binary reference labels for the exact
prospectively frozen predictive research cohort. It is not the existing
safe/unsafe review of model outputs or imaging-specific reader protocol.
The labels represent the specified reference outcome, not a judgment of a
candidate's safety. No prediction files or model responses are read by this tool.

## Freeze and assign before exposure

Use the predictive study plan from PREDICTIVE_RESEARCH.md. Independently define
the reference outcome, horizon, evidence sources and physician instructions,
including what makes a reference unascertainable. The reference protocol digest
in the predictive plan must identify that approved protocol. Before exposing
readers to outcomes or predictions, prepare a JSON specification with four
distinct pseudonymous role tokens: coordinator, reader_a, reader_b, adjudicator;
and source_inventory, a mapping from every planned unit ID to the SHA-256 of
its approved reference evidence package. No source bytes or clinical narratives
are included. The coordinator must authenticate physicians independently and
provide the approved reference source and protocol securely outside this tool.

    python -m caeval.predictive_reference_panel freeze --plan plan.json --expected-plan PLAN_DIGEST --spec panel-spec.json --output panel.json

Retain the printed panel digest separately. It binds the exact study plan,
reference protocol, endpoint, horizon, full eligible roster, sources and roles.
At most 10,000 planned units. Freeze ordering and external non-exposure are
operator responsibilities; a digest alone cannot prove prospective chronology.

## Separate primary reviews

Each packet is generated with the same common arguments:

    --plan plan.json --expected-plan PLAN_DIGEST --panel panel.json --expected-panel PANEL_DIGEST

Run the packet command with these arguments, --role reader_a or reader_b,
and --output PACKET.json. Retain the packet digest and provide each physician
only their own packet and approved source evidence. Packets contain unit tokens,
source digests and protocol/endpoint/horizon, not model versions, predictions,
other reader identities or responses. Unit identifiers and source materials
must themselves be checked for information that would defeat blinding.

Each physician independently supplies JSON rows with exactly unit_id,
source_sha256 and label (true, false, or null). The digest must correspond to
the source the physician actually reviewed. Omitted rows or null labels remain
unknown. No default negative outcome. Seal with:

    python -m caeval.predictive_reference_panel seal --packet PACKET.json --expected-packet PACKET_DIGEST --reader READER_TOKEN --rows ratings.json --output submission.json

The coordinator must retain both submission digests independently and prevent
sharing between primary readers until both submissions are frozen. The seal
checks structure and declared reader identity, not a digital signature or
medical credential. Independent storage and authenticated transfer are required.

## Disagreement and finalization

Use the adjudication-packet command with the common plan/panel arguments and
--reader-a A.json --expected-reader-a A_DIGEST --reader-b B.json
--expected-reader-b B_DIGEST --output third.json. This packet includes only
units with two known conflicting primary labels and binds both exact submission
digests. It does not disclose which physician gave which answer. The third
physician independently reviews the approved source and returns rows using the
same seal command. They may leave disagreement unresolved. A single known
primary rating cannot be promoted by adjudication; incomplete primaries remain
unknown. The third reader cannot overwrite consensus or incomplete units.

Run finalize with the common plan/panel and primary submission arguments,
optionally --adjudication THIRD_SUBMISSION.json --expected-adjudication DIGEST,
and --output reference-package.json. The receipt reports primary paired counts,
observed agreement and exact rational Cohen's kappa before adjudication, where
defined, and counts of consensus, adjudicated, unresolved and incomplete units.
Agreement is descriptive on known pairs only; it is not clinical validity,
a confidence interval, evidence of independence or an acceptance criterion.
The native reference bundle contains all planned units, including null labels.
Use its bundle digest in predictive_research; the source receipt digest can
also be carried into the existing browser collection metadata and rows.
The verify API replays the full panel, submissions and package against the
retained receipt digest. Keep original packet, submission and source records.

## Boundaries

Use private approved storage, independent checkpoint custody and authenticated
transfer of the minimal pseudonymous materials. Hashes neither de-identify nor
attest sources, identity or external blinding. This module does not authenticate
reviewers, perform source extraction, collect raw clinical records, provide
browser accounts or make any clinical decision. All outputs are experimental
and restricted. No clinical validation, publication clearance or deployment
authorization is claimed. Existing review workflows are unchanged.

    python -m pytest -q tests_unit/test_predictive_reference_panel.py
