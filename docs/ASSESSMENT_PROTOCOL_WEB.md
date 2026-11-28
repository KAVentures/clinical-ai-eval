# Native prospective protocol entry

Launch the local workspace selector with native protocol forms:

    python -m caeval.assessment_protocol_web --root /private/workspaces --staging-root /private/staging

Use distinct owner-only directories as described in ASSESSMENT_WORKSPACE_WEB.md.
Authenticate at the printed loopback address with the generated session password.
Open a new workspace, stage manifest.json and memberships.json in its named
staging directory using the documented pseudonymous formats, then return to the
workspace selector and choose Configure prospective protocol in browser.

The form uses one shared study identifier and baseline/candidate versions.
Enter buyer/product identifiers, distinct declared operator/reviewer/vendor roles,
protocol digests, prediction and adverse-event endpoints and horizons, action,
calibration bins, decision thresholds, holdout design, subgroup categories,
minimum group size, population coding version and feature warning limits.
Enter evidence requirements as one line per requirement, for example:

    safety | mandatory | Version-specific safety report
    usability | optional | Usability report

Enter population features as one line per feature, for example:

    setting | 1500 | inpatient,outpatient

These are syntax examples, not clinical acceptance criteria. All thresholds
must be justified and approved independently before inspecting outcomes.
Choose IID design only if its fixed-sample independence assumptions are
justified; enter the three probabilities only in that case. No values are
prefilled. The eligible prediction roster is derived from the validated test
partition, not typed independently. The underlying engines check cohort leakage,
role separation, scope, memberships and statistical inputs.

Validate and stage specifications writes four new files exclusively: request-spec,
actors, predictive-spec and binary-spec (JSON). It does not create a workspace,
commit a plan, or read outcomes. If any of those files already exists the action
is refused; preserve or move them using approved local file management before
restaging. Handled write failures remove newly created files only. A process
crash can leave partial staging. The existing setup preview and explicit freeze
must still be performed. Retain the approved checkpoints independently. Temporary
validation keys are discarded; the setup preview generates the actual keys.

The form is available only in an unfrozen active workspace. It uses the active
workspace action token, checks the same loopback credential, host, origin and
request limits as the selector and refuses duplicate, unexpected or oversized
fields. It never accepts arbitrary file paths. The form body is capped at
128 KiB. Invalid submissions are not persisted; the operator can re-enter them.
A browser refresh does not restore unsubmitted values.

This remains a trusted single-operator tool. The cohort, memberships, predictions,
reference outcomes and vendor documents must still be prepared separately.
No PHI, secrets or patient identifiers should be entered. Declared roles are not
authenticated institutional identities. No source verification, clinical validity,
release decision, automatic acceptance criterion or regulatory claim is supplied.
The original workspace selector and all workbench entrypoints remain unchanged.

    python -m pytest -q tests_unit/test_assessment_protocol_web.py
