# Local predictive model execution

This standalone adapter collects risk probabilities for one frozen predictive
research arm and produces a native prediction bundle. It is not a general LLM
connector, clinical decision tool, model identity attestation or external
provider integration. It requires an approved local model service implementing
the contract below. The operator must independently verify model identity,
feature definitions and source data, and ensure the service does not forward
or retain clinical inputs inappropriately. No reference labels are sent by the
adapter; features must be independently checked to exclude outcome leakage.

## Approve the exact run

Freeze the predictive study as in PREDICTIVE_RESEARCH.md. Prepare a private
JSON list with one row per eligible unit, exactly matching the frozen roster:

    [{"unit_id":"unit-a","features":{"score":1.25,"indicator":null}}]

Feature names are approved tokens; values are finite numbers (absolute value
at most 10^15), booleans or null. No text, nested features or extra fields.
Use only approved pseudonymous data. Numeric inputs can still be sensitive.
Prepare options.json with these exact fields (illustrative, not clinical advice):

    {"role":"candidate","port":8765,"feature_names":["score","indicator"],
     "feature_protocol_sha256":"REPLACE_WITH_APPROVED_64_HEX_DIGEST",
     "max_calls":100,"timeout_seconds":5,"max_seconds":120}

The role is baseline or candidate, never reference. Use the actual approved
feature protocol digest, local service port, and call budget (1..1500 and no
more than the cohort size). At most 128 features. The per-socket inactivity
timeout is 1..30 seconds, and max_seconds (1..900) is checked before starting
another call, not a hard wall-clock deadline for an already active request.
A slow local service can exceed this budget. Use a supervised local service.

    python -m caeval.predictive_local_execution preview --plan plan.json --expected-plan PLAN_DIGEST --options options.json --rows features.json --output execution.json

No model calls occur during preview. Retain the printed execution digest
independently; it binds the plan, role, version, endpoint, horizon, local port,
feature protocol and exact input inventory and budgets. Independently review
these before running. The CLI bounds individual input files to 64 MiB.

## Model service contract

The adapter POSTs JSON to literal IPv4 loopback (127.0.0.1) at the approved
port and fixed path /predict, with these fields: schema
(predictive-local-execution/1), unit_id, model_version, endpoint, horizon,
features, and request_sha256 (SHA-256 of canonical JSON of the other fields).
Canonical JSON is ASCII, sorted keys, compact separators, no NaN/Infinity.
Return HTTP 200 application/json with exactly request_sha256, unit_id,
model_version and probability_bp (integer 0..10000). Echo the request digest,
unit and exact version; the echo is a consistency check, not an attestation.
There is no generic URL, DNS lookup of a user host, proxy or redirect following,
credential forwarding, or implicit retry. No raw response bytes are retained.

    python -m caeval.predictive_local_execution run --plan plan.json --expected-plan PLAN_DIGEST --rows features.json --execution execution.json --expected-execution EXECUTION_DIGEST --confirm-loopback --output receipt.json

Every planned unit is represented, even if the request fails or the budget
runs out. Errors are classified as transport_error, http_error or
invalid_response, and unattempted units as not_attempted_budget; all carry
null probabilities. Responses are limited to 64 KiB. Unit order is fixed
lexically; a partial budget is not a randomized sample. No clinical safety
inference should be based on failure or nonattempt. The receipt is written
at completion; interruption can lose in-memory progress. This is not a
resumable job queue. Do not silently rerun and replace an approved result.

## Seal into the existing workbench

Retain the printed receipt digest independently. Run:

    python -m caeval.predictive_local_execution seal --plan plan.json --expected-plan PLAN_DIGEST --rows features.json --execution execution.json --expected-execution EXECUTION_DIGEST --receipt receipt.json --expected-receipt RECEIPT_DIGEST --output candidate.json

This replays the scope, input inventory and receipt structure and emits the
native sealed prediction bundle for the chosen role. It makes no model calls.
Use the bundle and its printed digest directly with predictive_research;
collect the other prediction arm and reference labels independently. To use
browser staging, transfer the bundle's rows and retained receipt digest into
the documented collection files under an approved operator workflow. Direct
browser execution and durable collection jobs are not implemented here.

All files are restricted; use an approved private directory and ACLs. The
CLI creates output exclusively, owner-only on POSIX. Hashes do not de-identify
data or authenticate the model; a party controlling both files and checkpoints
can rewrite history. The local service is trusted and HTTP is not TLS. No
clinical validation, independent source verification or deployment approval is
claimed. Run synthetic integration tests:

    python -m pytest -q tests_unit/test_predictive_local_execution.py
