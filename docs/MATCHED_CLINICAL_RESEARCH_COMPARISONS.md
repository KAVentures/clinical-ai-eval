# Matched clinical research comparisons (experimental)

The institutional research studio can now compare two to five analyzed
studies of distinct target name/version pairs, using the same authored
clinical protocol and fixed clinical opportunity denominators. This is a
retrospective research comparison, not the canonical procurement comparator.

## Create a comparison

Open **Clinical research studio → Matched clinical research comparisons**.
Choose a baseline study and up to four comparison studies, then explicitly
approve the descriptive analysis. Administrators may compare institutional
studies; operators may compare only studies they own. Reviewers and observers
cannot access this comparison registry or its target-level exports.

Studies must have identical regenerated specifications except target name
and version, identical scientific definition hashes, identical reviewer
identities and roles, and the same collection mode (operator-supplied
or supervised local). Target name/version pairs must be distinct. The
study's frozen plan, recorded responses, review/attestation ledger,
collection receipt (where applicable) and report digest are checked by
the existing studio. Do not assume that a matching roster establishes
reviewer independence, that endpoint configuration establishes product
identity, or that the same collection mode means identical model settings.
Use a genuinely consistent approved collection protocol.

## Clinical units

Each endpoint is defined on a fixed inventory, before looking at whether
the candidates have scored observations:

- Citation grounding: one query per authored probe, completion, and
  whether at least one unsupported claim was found. A negative finding
  requires at least one claim, complete extraction coverage and all
  claims supported. Candidate-specific claim counts are not paired.
- Scribe: completed note, predeclared reference-fact omissions and
  contradictions, critical reference-fact omissions, and whether a
  note contains an independently assessed documentation defect. The
  reference fact denominator is fixed; actual note claim counts vary.
- Decision certifiability: structured completion, verdict agreement,
  certification on reference-noncertifiable cases, missed blocks,
  unnecessary deferral and information sufficiency in the relevant
  fixed reference strata. This remains relative to the authored finite
  rule bundle, not clinical authorization.
- Robustness: completed pairs, unsupported inference and unsafe response
  findings, decision-group flips, acceptable decisions, adverse
  degradation, new unsupported inference or unsafe responses, and
  changed-context adaptation. Reference validity and the underlying
  module's axis-specific eligibility still apply. Initially safe/good
  subsets can differ between products.

A known defect can establish presence despite uncertainty about another
claim. Absence requires determinate coverage of all included claims.
No claims or unassessed reference facts do not count as a negative finding.
Collection completion is an operational endpoint, not a safety outcome.

## Interpret the comparison

For each endpoint, the report preserves the fixed planned-unit count,
the number jointly assessed, baseline-only assessed, candidate-only
assessed and neither assessed. Within the jointly assessed subset,
it reports both-event, baseline-event-only, candidate-event-only and
neither-event counts, both rates, and the candidate-minus-baseline
difference. A zero matched denominator yields null rates. Marginal
module summaries are retained separately, including candidate-dependent
claim-level rates. Do not subtract those claim-level rates as if they
had a common fixed denominator.

Positive differences are not always improvements: some endpoints are
adverse events and others favorable outcomes. The browser displays no
aggregate ranking. Shared cases, repeated perturbations and common
reviewers induce dependence. The report does not provide confidence
intervals, hypothesis tests, noninferiority, clinical equivalence, or
a validated purchase recommendation. Missingness can select a biased
jointly assessed subset, so inspect the coverage before interpreting
any numerical difference.

## Evidence integrity

The immutable comparison is recorded in the institutional SQLite store
with its digest, protocol and comparison-definition hashes, source
plan/response/report/receipt digests and separate descriptive metrics.
Reopening or downloading it regenerates the comparison from the verified
source studies. Changed source evidence or software definitions make
the comparison unavailable rather than silently substituting results.
Only trusted institution filesystem/database administrators are assumed
to control the underlying storage. The existing stopped-service backup
boundary includes the comparison registry.

All comparisons are marked decision_grade=false and
claim_authority=EXPLORATORY_ONLY. They are not accepted as canonical
assessment manifests or procurement evidence. Canonical executor
registration, validated extraction, representative held-out case packs,
clinical calibration and prospective study design remain separate work.
