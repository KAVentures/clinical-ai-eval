# Independent patient-world validity review

Patient transcript review assesses observable product behaviour. This
separate workflow evaluates the benchmark itself: the clinical plausibility
of declared patient worlds, reference disposition, available facts and
disclosure, and whether each applicable stress condition is a meaningful
clinical probe.

## Before candidate testing

Stage an approved patient-world case pack. An administrator opens
**Patient-world validity review**, selects the pack ID (including the
builtin demonstration pack where appropriate) and predeclares two to five
independent physician reviewer accounts. Verify expertise, credentials,
conflicts and independence outside the application. Do this before
candidate testing where possible; the application does not establish that
a physician has never seen a product output elsewhere.

Every applicable case/world/stress combination is included, including
baseline. The packet shows the opening, declared alternatives, original
and transformed clinical world, and applicable trajectory, but no
candidate response or judge score. A complete inventory of at most 200
items and 2 MiB is required; larger sources are refused, not silently
sampled. The frozen binding includes pack content, exact staged file
hashes, scientific definition hashes, item inventory and roster.

Each physician gives explicit yes/no/cannot-judge judgments for clinical
plausibility, reference appropriateness, realistic disclosure and stress
validity, with optional notes. Responses are immutable, independently
submitted and separately attested by an administrator. The report requires
all predeclared reviews. A negative vote is flagged even if other votes
agree; uncertainty remains indeterminate. Neither is treated as a pass.

The administrator may link the report to a completed verified patient
conversation job. The pack descriptor, file hashes and relevant scientific
definitions must match exactly. If a transcript-review roster exists,
overlapping reviewer accounts are refused for the linked report. In addition,
assignment in either direction is refused when an account was previously
assigned to the other review type for the same pack ID or content hash,
even if that reviewer never submitted answers. This conservatively protects
blinding across versions with the same pack ID. When no
transcript roster exists, the report explicitly records that separation
has not been established. A changed pack or definition invalidates the
current review, requiring a fresh version and review.

This workflow does not modify the canonical case pack, sign its metadata,
adjudicate product outputs against hidden facts, promote evidence claims,
or authorize use in patient care. A flagged benchmark needs investigation
and documented correction before it can support a stronger scientific
claim. Real clinical calibration and validation remain required.
