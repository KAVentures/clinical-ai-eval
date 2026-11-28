# Collection-to-assessment handoff

An administrator-staged vendor connector can collect canonical prompts
through the standard candidate endpoint or a custom JSON adapter. An
approved local worker executes the exact collection binding. The completed
collection appears under Candidate response collections in the browser.

Choose a staged judge panel, complete governance fields and enter the exact
activated procurement conditions hash when applicable. Preview binds the
collection receipt, current connector configuration, complete response
inventory, candidate version, panel, case pack, scientific definitions and
prepared assessment digest. Only an administrator can approve preparation.
A local active risk protocol cannot be bypassed through this handoff.

The same workflow is available to trusted local CLI operators:

    clinical-ai-eval buyer collection-assessment-preview /workspace --collection-id COLLECT_ID --panel-id mock --file governance.json
    clinical-ai-eval buyer collection-assessment-prepare /workspace --collection-id COLLECT_ID --panel-id mock --file governance.json --approve-binding DIGEST --confirm-handoff

Use --conditions-hash HASH in both commands for activated risk conditions.
The governance file is the existing four-question governance JSON object.
Preparation never calls a model or creates a job. A verified prepared
assessment can subsequently be granted to an approved external application
through the evaluator API and queued through normal institutional approval.

Incomplete or invalid receipts, changed connector or scientific inputs and
changed approval digest fail closed. Identical preparation is idempotent.
Candidate collection is still operator-attested, not independent vendor
identity or production deployment attestation. Existing manual import remains
available for disconnected environments.
