# Offline buyer-owned vendor clarification ledger

This standalone administrative state machine complements the institutional
evidence-gap projections and vendor document review. It tracks individual
requests for clarification, vendor responses, buyer review and reopening.
It does not replace the programme's evidence gates or create a clinical
acceptance decision.

## Freeze scope

    from caeval.vendor_clarification import freeze, append, report
    frozen = freeze({
        "request_sha256": independently_retained_buyer_request_digest,
        "version": "v1",
        "requester": "buyer-operator",
        "reviewer": "buyer-reviewer",
        "vendor": "vendor-representative",
        "requirements": ["safety", "monitoring"]
    })

Use distinct authorized account tokens and a version-specific buyer request
digest. The caller must independently verify the actual request contents,
requirement IDs, exact version and identity of each participant. This
module enforces declared role separation, not authentication. Retain the
frozen plan digest separately in a controlled local record.

## Record events

Each event body has exactly eight fields:
- id: unique clarification token (not a patient identifier).
- requirement: one of the frozen requirement IDs.
- actor: authenticated actor token supplied by trusted caller.
- action: open, respond, accept, reject or reopen.
- date: ISO calendar date, nondecreasing across the ledger.
- due: ISO calendar deadline for open; null for every other action.
- evidence_sha256: hash of exact question, response or review record
  stored independently in a restricted evidence store.
- response_sha256: null for open/respond, exact current response digest
  for accept/reject/reopen.

Example:

    events = []
    head = frozen["plan_sha256"]
    event = append(frozen, frozen["plan_sha256"], events, head, {
        "id": "q1", "requirement": "safety",
        "actor": "buyer-operator", "action": "open",
        "date": "2026-11-28", "due": "2026-12-05",
        "evidence_sha256": question_document_digest,
        "response_sha256": None
    })

Persist the event and advance the separately retained head atomically.
Append is a pure function; it does not persist data, acquire locks or
prevent concurrent callers from creating forks. A production caller must
perform an atomic compare-and-swap on the stored head under authorization.

## State transitions

| Action | Required actor | Prior state | Result |
|---|---|---|---|
| open | requester | absent | OPEN |
| respond | vendor | OPEN or REOPENED | AWAITING_REVIEW |
| accept | reviewer | AWAITING_REVIEW | ADMINISTRATIVELY_RESOLVED |
| reject | reviewer | AWAITING_REVIEW | REOPENED |
| reopen | reviewer | ADMINISTRATIVELY_RESOLVED | REOPENED |

Review binds the exact most recent response, preventing an old response
from being accepted after a new response. Reopening retains the original
deadline; a separately authorized new clarification is needed for a new
deadline. The ledger is capped at 200 items and 2,000 events.

## Verify and project

    snapshot = report(frozen, retained_plan_digest, events,
                      retained_head_digest, "2026-12-06")

Replay validates event hashes, sequence, scope, declared roles and transitions.
The independent expected head detects truncation or changes relative to the
trusted record. The initial head is the plan digest. An unresolved item is
overdue strictly after its due date, including an item awaiting buyer review.
The as-of date must not precede the last event date. The date comes from
the caller; no trusted time source is implemented.

An administratively resolved item does not mean evidence sufficiency,
compliance, clinical validity or that all requirements are addressed.
A zero unresolved count does not imply a complete procurement file.

## Integration and trust boundaries

No database, browser, network transport, central CLI or dependencies are
changed. The caller must provide authenticated identities, verify the
actual source bytes against hashes, enforce access, retain the authoritative
plan/head, and commit events atomically. The ledger is a hash chain, not
a digital signature or independent attestation; a party controlling the
trusted head can rewrite history. Actor tokens, hashes and dates remain
restricted data. Outputs set decision_grade and publication_safe false.
No patient records, raw vendor text or attachments are placed in the ledger.

Run synthetic tests:

    python -m pytest -q tests_unit/test_vendor_clarification.py
