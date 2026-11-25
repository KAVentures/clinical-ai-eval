# Institutional multi-turn candidate assessments

Administrators and operators can queue experimental patient-facing conversational
assessments from the institutional browser. This is separate from generic
single-turn response exchange and uses the existing canonical patient episode
runner. No browser request launches a candidate or judge call.

## Approved inputs

Create a saved patient-triage evaluation plan using candidate intake and
assessment setup. The runnable scope must be patient_red_flag only. Required
but unavailable families remain explicitly listed.

Trusted IT stages a connector in approved-conversations/CONNECTOR_ID.json
inside the workspace. For example:

    {
      "vendor_id": "candidate",
      "target_name": "Example product",
      "target_version": "1.0",
      "url": "http://127.0.0.1:8080/chat",
      "history_field": "messages",
      "answer_path": "answer",
      "session_field": "session",
      "headers": {},
      "timeout_seconds": 30
    }

The vendor, product and version must match the saved plan. The canonical
restricted transport accepts literal loopback endpoints only, with explicit
port, no redirects or proxies, no arbitrary extra request fields or retries,
and only approved environment-variable credential references. The product
itself may still communicate externally; enforce institutional egress policy.
An endpoint's declared version is not proof of its deployed version.

Stage a patient-world case pack in approved-patient-packs/PACK_ID with
cases.json and optional pack.json. Use the canonical patient pack format.
The shipped builtin:public_smoke is also available for demonstration only.
Stage a reviewed judge panel in approved-panels. Do not use identifiable
clinical records without the institution's approved data-handling process.

## Queue and execute

Open Patient conversation assessments. Select the saved plan, connector,
patient pack and judge panel, and answer all governance questions. The
browser validates and freezes their content hashes, scientific definitions,
target scope and conservative candidate call budget, then creates a queued
job. Mock-panel and builtin-pack work is demonstration evidence.

The browser displays the exact approval hash. A separately authorized worker
executes only the approved job:

    python -m caeval.institution_conversation_cli /srv/caeval/workspace \
      --job JOB_ID --approve-binding BINDING_HASH --allow-candidate-calls

Real judge calls additionally require --allow-model-calls. The worker checks
the current inputs against the frozen binding, creates a fresh private
workspace, and runs the canonical CLI with a hard subprocess timeout. The
job owner must still be enabled and authorized. Queued jobs can be cancelled
by their owner or an administrator; already running work requires supervised
process management. There is no automatic retry or silent fallback to a
single-turn assessment.

## Verify and interpret

The dashboard reports job status, verified manifest hash, episode count,
review-unit count and claim ceiling. Completion requires a valid canonical
patient evidence package, re-derived project binding, conformance and claim,
unique baseline and episode identities, and no recorded episode transport
errors. The separate job receipt hashes all output files including the
executor-specific episode artifacts. Changed inputs, definitions or output
files invalidate verification. This is integrity checking under trusted
filesystem administration, not cryptographic proof of candidate identity.

Patient cases, raw transcripts, connector headers and judge configuration are
not displayed in the overview. The separate generic paired-text review
workflow does not accept these patient jobs. Clinical interpretation remains required. The separate [independent patient transcript review](PATIENT_TRANSCRIPT_REVIEW.md) provides blinded observable-transcript judgments, not hidden-world validity adjudication or clinical approval. No patient care, deployment
approval or procurement decision is issued.

Stop all workers before backup or upgrade; running conversation jobs block
checkpoint verification. Investigate interrupted work rather than resetting
or reusing its output directory. The existing release and recovery procedures
apply. Test real endpoint interoperability, institutional access, data handling
and security before deployment.
