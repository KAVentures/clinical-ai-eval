# Corrections log

This project is a **candidate protocol and reference harness**, not a validated
standard. This file records conclusion-affecting problems that have been found and
what was done about them, so the repository does not over-claim.

## v0.2 — responding to an internal scientific review (2026-07)

A detailed review raised several conclusion-changing problems. The fixes below
were applied; the items that are irreducibly human-dependent are marked as such.

### Fixed in code
1. **Fail-open judging → fail-closed.** Previously, if judge calls failed, `n=0`
   made `panel_any_unsafe=0` (apparently safe), and missing JSON fields were coerced
   to 0. Now: `parse_judge_json` rejects any response missing a mandatory field; a
   cell without a ≥2-distinct-provider quorum of successful judges is
   `incomplete_quorum` → NA, excluded from the headline, **never counted safe**.
2. **Pseudoreplication.** Multiple variants share a base case and reuse the same
   original response. The primary safety CI is now a **case-clustered bootstrap**
   (resampling `item_id`s); Wilson is retained only as an explicitly-labelled
   unadjusted comparator; the McNemar p is labelled unclustered/exploratory.
3. **Reviewer/judge cueing.** The blinded human-review sheet no longer shows
   `perturbation_type` or `expected_missing_evidence` — reviewers judge the
   product's answer to the (perturbed) case and state, unprompted, what is missing.
4. **L2 gate hardened.** Ties are no longer counted "safe" (they are `contested` and
   must be resolved). L2 now requires ≥2 reviewers, **100%** of mandatory
   high-severity cells resolved to a clear verdict, and adequate inter-rater
   agreement; otherwise the run stays L1 with the specific gap named.
5. **Review routing.** The report no longer routes *every* auto-screened cell to
   human review; it routes the mandatory strata plus a bounded calibration sample
   (regression mode is automated; calibration mode is a sample + all critical fails).
6. **Provenance.** Records the real harness git commit (was a placeholder).
7. **Evaluator language.** Removed the incorrect slogan "a 'safe' verdict is weaker
   than an 'unsafe' flag." For a high-sensitivity/low-specificity screen the
   opposite holds (unsafe flags have low PPV); predictive values depend on
   prevalence and are not asserted until L2. Individual-judge / panel-any /
   panel-majority are now named as distinct endpoints.

### Relabelled (honesty, not new capability)
8. **"Validated subset" → "auto-screened (structural pre-filter)".** The automated
   validity audit only confirms an edit occurred and named some evidence. It does
   **not** establish clinical load-bearingness, determinacy, or that a safe response
   is definable — those are marked `requires_human_validity_confirmation` and are
   collected via a new `validity_review.csv` (original + perturbed case + clinician
   validity questions) for confirmation at L2. `safe_response_is_definable` is no
   longer hardcoded `True`.
9. **Scope.** Described as a harness for **missing-information and
   conflicting-evidence robustness in text-based, clinician-facing decision support**
   — not a broad or patient-facing clinical-AI evaluation harness. Patient-facing
   fields (`missed_red_flag`, `over_reassurance`) are not in the scoring schema and
   are disclosed as unscored; the audience is threaded, not hardcoded.
10. **"Standard" → "protocol / reference harness / candidate specification".**
11. **Demo numbers are software fixtures.** The mock judge, mock subject, and
    self-tests were authored together; the arms table demonstrates pipeline wiring,
    not clinical detection. The report now banners this.

### Not fixable in code (requires people)
- **Non-circular validation** needs defects specified by clinicians, implemented by
  a separate developer, with the harness locked and evaluators blinded to defect
  status. The current self-validation is a software fixture.
- **Perturbation validity** must be human-anchored on a calibration sample before
  any product interpretation (§5). The harness now collects the form; it does not
  manufacture the labels.
- **A genuine L2** requires ≥2 real clinicians. The gate is enforced; the people are
  not synthesisable.
