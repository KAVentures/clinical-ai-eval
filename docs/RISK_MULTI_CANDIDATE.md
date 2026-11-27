# Shared risk conditions and institutional handoff

On a current buyer risk protocol, select one or more additional saved candidate
plans in the activation preview. All candidates must have distinct vendor IDs
and target name/version identities, exactly matching clinical intake, target
profile, selection rules, required suites, case pack and generated prompt
inventory. The primary buyer-authored hazard criteria apply to every candidate.
The preview digest commits to the roster; no candidate can be silently added
after activation. Existing single-candidate receipts remain compatible.

The active risk study accepts complete collected submissions for any frozen
candidate plan. Prepare and queue jobs in the usual workflow; only approved
operators execute them. The active study shows completed eligible jobs and their
manifest references. An administrator explicitly approves internal handoff.
The server revalidates the prepared project, job and each family package,
copies only manifest-bound files into the private institutional package store,
re-verifies the staged evidence, and publishes all of a candidate's results
together. No model calls or arbitrary source paths are permitted. Existing
results cannot be replaced; an identical verified handoff is idempotent.

An incomplete candidate roster is explicitly incomparable. Measured rates are
descriptive until the missing candidate results are available and the existing
comparability checks pass. Clinical evidence remains exploratory and neither
clinical validation nor deployment permission is inferred.

The handoff serializes supported browser writers using the institutional
database lock and atomically replaces the study JSON. Trusted CLI/OS writers
must be quiesced. A crash between staging and publication can leave an orphan
directory; IT must investigate before recovery, never blindly delete or reuse
it. Staging is limited to the existing bounded research-package byte budget
across the job. Source and staged packages remain private. Back up and retain
both according to institutional policy.

Run synthetic tests with `python -m pytest tests_unit/test_risk_multi_candidate.py`.
These tests do not establish clinical validation or production readiness.

Incomplete risk-bound comparisons include explicit NO_EVIDENCE rows for every absent candidate at each hazard, with the frozen planned-cell denominator. Threshold conclusions are withheld from the comparative summary until the roster is complete; standalone estimates remain descriptive.
