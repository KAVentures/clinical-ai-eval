# Unified institutional lifecycle cockpit

Use **Clinical AI lifecycle cockpit** in the institutional workspace to
follow exact product versions from saved buyer programmes through evaluation
source association, hospital referral reviews, version-only successor
lineage and operational monitoring. Administrators, operators and observers
can access the cockpit; each action opens its native authorized workflow.

The cockpit is a read-only administrative projection, not a new clinical
evaluation engine or clinical acceptance decision. Each source inventory is
independently verified by its native service. These reads are not an atomic
cross-service snapshot. If a source inventory fails, the cockpit marks it
unavailable, not empty. Unmatched records are counted separately. Exact
programme ID, vendor ID and declared product target must agree before a
policy or monitor is joined; different versions do not inherit evidence.

## Programme workflow

Filter by programme, vendor and high/critical attention. Each scope shows
the planned requirement inventory, currently available source count,
uncovered required families, native evidence claim classification, local
policy review progress, operational monitor status, and version lineage.
It does not calculate a combined clinical score or rank vendors.

Next actions distinguish missing evidence, stale scope, changed or
unavailable evidence, uncovered canonical suites, pending local gate
reviews, monitoring concerns and reported version changes. Critical version
change notices come first. A missing optional monitor is not evidence of
deployment or a safety failure. A policy may only be newly registered before
that vendor's first source association. Once the evidence is associated,
a new prospective policy requires a separate programme.

From an eligible current vendor scope, administrators can open guided forms
for the six mandatory local policy gates and the optional operational
monitor. The forms select active reviewer accounts and accept institution-
defined criteria; they do not supply clinical thresholds or approved
criteria. The monitor supports up to twelve complete metric rows, fixed
UTC reporting windows, and an explicit external authorization attestation.
Both forms submit to the existing native preview and digest-approved freeze
workflow. The advanced JSON builders remain available for extra policy gates
or alternative programmatic setup.

## Independent reviewer workspace

The separate **My independent reviews** page is reviewer-only. It aggregates
standard blinded reviews, formal safety/validity reviews, prospective
validation cohorts, diagnostic imaging reference assignments, RAG citation
review and local hospital gate assignments. It shows only the authenticated
reviewer's own assignments and completion state available from the native
service. Some source inventories cannot distinguish a presently openable
packet from a linked or conditional assignment; those are labeled explicitly.
Imaging adjudicators receive a conditional link without peer progress or
disagreement details. The native review route determines whether the packet
can be opened.

The page does not download packets, expose candidate outputs or peer ratings,
or submit reviews. It does not verify reviewer credentials or independence.
An unavailable source does not imply there is no remaining assigned work.
The existing native review and lock workflows remain authoritative.

## Operational use

The cockpit is a navigation and administrative triage aid. Reopen and
reverify native source records before action, and use the existing committee
snapshot and export workflow for evidence decisions. Neither the cockpit nor
the reviewer inbox authorizes deployment or establishes clinical safety.


## Reported version-change linkage

The cockpit now shows the number of reported version changes, linked
successor programmes and currently scoped successor links. Administrators
can open the native handoff workflow for unlinked changes or stale
successors. A linked successor is not a completed reassessment and does
not clear the original critical alert. Handoff and monitoring inventories
are read independently; mismatched monitor IDs or reported-change presence
are marked inconsistent and withheld rather than presented as complete.
No incident-level records or external artifact digests enter the cockpit.
