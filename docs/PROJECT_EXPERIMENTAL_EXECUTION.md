# Project-bound experimental assessment execution

The existing project intake remains the source of truth for intended use,
target/version, audience and required suites. The experimental-project CLI
now connects the four additional research protocols to that validated intake.
It is a secondary execution lane, not a replacement for run --project.

1. Complete and validate project.yaml using the existing project workflow.
2. Create specs.json, a mapping of selected module identifiers (citation,
   scribe, decision, robustness) to their complete native specifications.
   Example specifications and schemas are in the existing module documentation.
3. Freeze the exact experimental plan before distributing candidate material.

    clinical-ai-eval experimental-project freeze --project ./my-project --specs specs.json --out frozen.json

Every subsequent operation takes --project ./my-project --plan frozen.json
--approve-plan DIGEST, using the digest emitted by freeze. A changed
project configuration, target/version, selection rules or scientific
definition is refused. Only modules relevant to the declared clinician
profiles are accepted. Mock projects cannot use non-synthetic specifications.
Unplanned related modules remain visible. The full project configuration
is not exported; its digest is used for exact local scope checking.
The output contains clinical study inputs and is private.

## Execution

Use module-plan for the exact native plan, exchange for candidate-facing
material, or response-template for the response inventory (which may contain
withheld reference material and must not be sent to candidates).
All these commands require --kind MODULE and --out PATH.

Collect the candidate's real responses under approved institutional conditions.
For decision certifiability, complete-module executes the native rule-bound
scorer from the exact response JSON:

    clinical-ai-eval experimental-project complete-module --project ./my-project --plan frozen.json --approve-plan DIGEST --kind decision --source answers.json --confirm --out decision.json

For citation, scribe and robustness, prepare-review takes --kind MODULE,
--responses FILE, --workspace DIRECTORY, --reviewer ID (repeated), --operator ID
and --confirm. Robustness additionally needs --validity-reviewer ID (repeated).
The native offline module CLI can then export individual packets and ingest
independent signed review submissions. After all reviewers submit, run
complete-module with --source DIRECTORY and --confirm to regenerate the
native report and export its replayable evidence. An incomplete or wrong
review workspace fails closed. Existing reviewer credential and independence
attestations still apply; this CLI is for trusted local operators, not an
authenticated multi-user server. Freeze is content binding, not proof of
preregistration or reviewer blinding.

## Aggregate and verify

    clinical-ai-eval experimental-project assemble --project ./my-project --plan frozen.json --approve-plan DIGEST --package decision=decision.json --out result.json
    clinical-ai-eval experimental-project verify --project ./my-project --plan frozen.json --approve-plan DIGEST --result result.json --expected-digest RESULT_DIGEST

Supply repeated --package MODULE=FILE arguments for each completed module.
The result binds exact native plans, reruns each supplied package's scientific
scorer and preserves every required canonical suite, every unplanned related
module and every uncompleted planned module. No pooled safety score, ranking,
or clinical approval is computed. Partial results are explicitly marked.
All exchanges are bounded to the common portable exchange size; oversize
exports fail rather than silently truncating. Files are immutable under the
existing exclusive publisher; choose fresh paths for changed results.

Neither canonical run --project, verify-package, canonical selection flags
nor procurement evidence gates are changed. Experimental coverage is
distinct from canonical execution and independent clinical validation.
This CLI performs no model calls, and the project connector configuration
is not used to authenticate externally collected candidate responses.
Use the institutional research studio for managed candidate collection
and browser review. The CLI does not install software on hospital workstations.

Synthetic acceptance: python -m unittest tests_unit.test_project_experimental
