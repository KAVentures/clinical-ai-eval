# Portable committee dossiers

The committee's frozen evidence page now offers a **Download portable dossier**
action. This is an offline JSON evidence record, not a clinical certificate or
an executable purchasing verdict. It retains the complete frozen committee
snapshot: intended use, vendor evidence and self-reported disclosures,
canonical comparison references, independent review coverage, experimental
programme coverage, implementation and monitoring state, missing evidence and
administrative dispositions.

## Export on the approved institutional server

1. Freeze the committee snapshot and inspect its current/historical status.
2. Open that snapshot and choose the portable dossier export. Administrators,
   operators and procurement observers may export the metadata record subject to
   the existing committee permissions and explicit sharing confirmation.
3. By default, no original document bytes or full experimental packages are
   attached. An administrator may separately select individual intact vendor
   documents and completed experimental replay packages bound to the exact
   frozen programme. These attachments may contain confidential information,
   clinical inputs, reviewer identities and ratings. Do not select them without
   approved disclosure. The original packet signing secrets are not included.
4. Confirm sharing. Exports are bounded to 8 MiB and at most 16 attachments.
   A changed, unavailable, or oversized selected attachment is refused rather
   than silently replaced or omitted. The metadata-only export remains
   available. The export is audited by content digest.
5. Communicate the dossier's top-level `digest` over a separate authenticated
   channel to identify the intended original. The digest alone is not a
   signature; a modified dossier could be resealed by someone else.

Historical snapshots can be exported. Their current status is observed at
export, not a guarantee of freshness at later verification. Disposition
rationales and author IDs are part of the committee record and must be shared
only with approved recipients.

## Verify independently (offline)

On an approved machine with the pinned Clinical AI Eval build:

    clinical-ai-eval verify-committee-dossier committee-portable-dossier.json --expected-digest EXPECTED_SHA256

The command emits JSON and exits nonzero on refusal. It re-derives the frozen
snapshot digest, disposition digests, exact document/report/manifest reference
inventory and envelope digest; independently checks embedded document bytes
and replays every embedded experimental evidence package. Unembedded references
remain explicit in the receipt. No original workspace, account, network or
provider credential is required.

Canonical scientific packages are **not automatically copied** into a portable
dossier. If the recipient has separately approved exact canonical package
directories, pass one or more explicit paths:

    clinical-ai-eval verify-committee-dossier committee-portable-dossier.json --expected-digest EXPECTED_SHA256 --canonical-package /approved/package

This uses the existing bounded research preflight and canonical manifest
verification, then checks that the family, target version and available target
name match the frozen reference. Package verification needs the compatible
scientific definition build. This is content and manifest verification, not
a second clinical review or new independent canonical scoring run. The dossier
does not contain or accept file paths for automatic reads.

The receipt distinguishes byte-verified vendor documents, replayed experimental
reports, verified canonical manifests, and references not independently checked.
None of these raises the clinical claim authority, fills a required-but-not-run
suite, or authorizes procurement/deployment.

Synthetic browser and offline regression tests:

    python -m unittest tests_unit.test_committee_portable

Actual hospital security review, governed data transfer, clinical validation and
independent procurement judgement remain required.
