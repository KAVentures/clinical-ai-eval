# Corrections log

## v0.4 — endpoint labelling, CI, maturity gating, hazard criteria (2026-07)

### Endpoint-labelling bug (the README quoted two incompatible headline values)
The README said "36% blinded" in one paragraph and "32%" in the table below. Both
were correct; they are **different endpoints** — `panel_any_unsafe` over 39
auto-screened *cells* (14/39 = 36%) versus mean per-judge over 78 *judge-cell
evaluations* (25/78 = 32%). Neither is now called "the unsafe rate": every figure
states endpoint · aggregation · numerator/denominator · subset · judge mode.

### README numbers are now GENERATED, and CI enforces it
`caeval/fixtures.py` + `python3 -m caeval.cli fixtures` regenerate the README's
numbers from a fresh deterministic run; `--check` fails if they drift. GitHub
Actions (`.github/workflows/ci.yml`) runs the full suite on 3.9/3.11/3.12, the
fixture check, and a CLI smoke test — so "N tests pass" is enforced, not asserted
in a commit message. The documented test command no longer hardcodes a count.

### A cued-judge leak into a headline field (bug, found BY the fixture discipline)
Generating the fixture table surfaced `harmful_tx = 100%` on the flawed arm, which
cannot exceed the unsafe rate (36%). Cause: the cell-level
`potentially_harmful_treatment` was computed over **all** judge scores including
rubric-aware ones (39/39) instead of blinded-only (14/39). That field is also a
**human-review routing trigger**, so cued verdicts were driving both a headline
number and the review queue. Fixed and regression-tested.

### Per-family maturity labels (claims gated by demonstrated validity)
`caeval/maturity.py` adds six levels — experimental → calibrated → validated →
externally_replicated → qualification_ready → surveillance_ready — declared per
family and enforced: an `experimental` family raises on `published_finding`,
`procurement_decision` or `release_gate`, while permitting `regression_screen`.
Both current families are `experimental` and name the exact evidence required to
advance. This lets the platform be broad while claims stay narrow.

### Hazard registry + predeclared acceptance criteria
`caeval/hazards.py` plus `hazards:` in each family YAML give the traceability chain
intended use → hazard → test → metric → predeclared threshold → verdict. Criteria
are declared **before** the run, so success cannot be defined after seeing results.
Every verdict carries family maturity and is explicitly marked non-decision-grade
while the family is experimental. Includes a guard hazard (`H-OVERABSTAIN-001`) so
"safety" by blanket refusal fails rather than passes.


## v0.3 — specification drift + evaluator cueing (2026-07)

### A fail-open in the AUDIENCE dimension (bug)
`patient_triage_chatbot` was a selectable profile, and `missing_information`
declared it applicable. But that family's **patient** bar names `missed_red_flag`
and `over_reassurance`, which **do not exist in the scoring schema** — so a
patient-facing product would have been scored against a bar the harness cannot
measure, with those hazards silently never firing and never routing to review.
This is the same fail-open class as the earlier judge bug, missed in the audience
dimension. Fixed: `audience_high_severity_fields` now raises
`UnscorableAudienceError`; the selection engine refuses the audience up front (a
patient profile now yields **zero** runnable suites); five drift-guard tests keep
the YAML, the schema, and the stated scope in agreement.

### Documentation/executable drift (bug)
The v0.2 narrowing was applied to the prose but not the executable artifacts. The
family YAML still claimed patient applicability and still asserted the **retracted**
"a 'safe' verdict is weaker evidence than an 'unsafe' flag" slogan; `pyproject.toml`
still advertised "patient- and clinician-facing". All three fixed and now tested.

### Evaluator cueing — CONCLUSION-CHANGING
The judge was shown the perturbation type and the expected missing evidence, which
inflates apparent detection. Two modes now exist (`mode` in `configs/judge_panel.toml`):

* **`blinded`** — sees only the case-as-shown and the answer. **This is the headline.**
* **`rubric_aware`** — additionally sees the defect specification. A high-sensitivity
  defect detector for triage/regression, **not** a clinical-quality estimate.

Rubric-aware judges are **excluded from the quorum and the panel vote** (they are the
same evaluators with a hint, not independent votes) and are reported as a cueing
sensitivity analysis. Effect on the demo fixture: any-unsafe **100% cued → 36%
blinded, a +64 pp cueing gap**. Previously-reported demo rates (e.g. "86% unsafe")
were cued numbers and are superseded by the blinded figures.

Consequence for the retracted slogan: in the fixture the **blinded** judge is not
low-specificity at all (specificity 1.0) — the over-flagging was a property of the
**cued** evaluator. The test suite no longer asserts any sensitivity/specificity
direction; the operating point is an empirical question for a real L2.


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
