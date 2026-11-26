# Independently replayable experimental research evidence

For an analyzed institutional research studio study, an administrator can
explicitly authorize **Export independently replayable evidence**. The
export contains the complete frozen scientific plan, responses, normalized
review binding and submitted ratings (for human-reviewed modules), and the
full scientific report. It supports citation, scribe, decision certifiability
and paired robustness.

The institution validates the original report, collection and reviewer ledger
before generating the package and then replays it before allowing export.
No database, original HMAC signing secret, credentials, session tokens or
local file paths are included by the exporter. Clinical text, proprietary
candidate answers, reviewer IDs and reference material ARE included; only
share with approved recipients and use approved non-identifiable inputs.
Export is administrator-only, explicitly confirmed, CSRF-protected, audited
and limited to the existing 8 MiB portable exchange size. Larger studies
remain available in the governed original workspace, not silently truncated.

A researcher or buyer can independently run:

    clinical-ai-eval verify-experimental-evidence experimental-evidence.json
    clinical-ai-eval verify-experimental-evidence experimental-evidence.json --expected-digest SHA256_FROM_TRUSTED_CHANNEL

The JSON result reports REPLAY_VERIFIED, the package and report digests, and
whether the separately supplied expected digest matched. Failure is a nonzero
exit and a JSON error. No model calls, Internet access or original institution
secret is needed. The verifier rebuilds the plan with the installed scientific
definitions, reconstructs the review binding and exact independent submission
records in a fresh private temporary directory, reruns the existing scorer, and
requires exact agreement with the full supplied report. Temporary signing keys
are generated locally for replay only and removed with the temporary directory.
They do not establish any original reviewer's identity. Native decision studies
are replayed directly against the frozen rules and exact candidate responses.

A fully replaced but internally consistent package can pass replay. Verify a
digest communicated independently by the trusted exporting institution when
you need to identify the original package; this still does not prove external
candidate identity, reviewer qualifications or reference correctness. The
receipt is not a canonical project assessment manifest and does not satisfy
canonical family coverage, clinical validation, procurement acceptance or
deployment gates. The existing verify-package command is unchanged.

The export does not transport the institutional access audit, original signed
review packets, prospective protocol ledger or supervised collection logs.
Those provenance records remain governed locally; numerical replay is separate
from authentication. Definition changes can invalidate replay; use the matching
version of the software. The package is unencrypted JSON.

Synthetic tests: python -m unittest tests_unit.test_experimental_evidence
