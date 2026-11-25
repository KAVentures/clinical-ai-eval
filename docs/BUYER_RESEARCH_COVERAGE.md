# Buyer plan research coverage (experimental)

The institutional research studio supports explicit, immutable associations
between individual studies and saved canonical buyer scope previews. This
bridges the procurement and research interfaces without treating an
experimental result as a canonical execution receipt.

## Associate a study

Register the candidate and save its scientific evaluation plan in Assessment
setup. In the Research studio, enter the saved buyer plan ID and confirm the
association. The study owner or administrator may link a study exactly once.
Product name and version must match exactly, the vendor and current buyer
scope must match the saved plan, and at least one mapped research topic must
be required by current selection rules. An association is recorded against
the exact study plan hash and records whether it occurred before collection
(planned state) or retrospectively. It is not independent preregistration or
proof of the correct vendor's execution.

Mappings are intentionally restricted:

- Citation grounding: citation_verification.
- Scribe assessment: omission and fabrication.
- Native decision certificates: decision_certifiability.
- Paired robustness: unsupported_inference and rewording_robustness.

The association only indicates topical relevance, not adequate case coverage
or complete evaluation of the named family.

## Inspect coverage

Use the link on the saved plan in Assessment setup. Administrators see all
linked studies; operators see their own linked studies only. Reviewers and
observers cannot inspect this view. It shows canonical availability and
required-but-not-run separately from associated research study state, report
integrity, synthetic provenance, and prospective versus retrospective
association. A verified experimental report may still be incomplete, contain
no assessed observations or report clinical failures. A non-synthetic flag
is declared provenance, not independently confirmed real-world validity.

The view rechecks buyer scope, selection rules, current definitions, the
study plan, and the underlying study report and review/collection provenance.
A corrupt association or report is unavailable rather than credited to a
suite. Reports remain downloadable through the existing scoped study endpoint.
Links are stored in the institution's existing access database and remain
inside its backup boundary. They do not modify the buyer evidence register
or procurement decision logic.

The existing canonical executor registry, required-but-not-run inventory,
maturity levels, canonical acceptance gates and clinical claim authority
remain unchanged. The view does not inspect canonical execution receipts or
assert that any canonical requirement is satisfied. It provides related
research evidence for human investigation, not certification or a deployment
recommendation. Full canonical integration remains separate work.
