# Corrections log

## v0.6 — evidence-grounding layer implemented and hardened; protocol reconciled (2026-08)

v0.5 declared `decision_certifiability` but shipped no verifier, so the repository
was *ClinArgCert-aware and safely scaffolded, but not ClinArgCert-implemented*.
That gap is now closed — **without unblocking the family**.

### The severity fail-open, fixed structurally (not just patched)
An external reference verifier skipped any check whose `severity` was not the exact
lowercase string `"critical"`. A skipped check cannot BLOCK, cannot DEFER, and
emits no finding — so a **PRESENT contraindication certified with zero findings**
when severity was spelled `"high"`, `"Critical"`, or omitted. That collided with
this repository's own vocabulary (`high | moderate | low`), in which `"critical"`
never appears: importing it unchanged would have certified everything.

The root cause was **conflating two axes**. They are now separate and both required:

| field | meaning | decides the verdict? |
|---|---|---|
| `severity` | clinical importance (`critical\|high\|moderate\|low`) | **never** |
| `certificate_effect` | verdict semantics (`block\|defer`) | **only this** |

An unrecognized value in either escalates and is reported; `certificate_effect`
fails closed to `block`. Verified across 15 severity spellings — including `""`,
missing, `1`, `True`, `[]`, `{}` — none can silence a check.

### Corrected minimum-information solver (`caeval/certificates/mmip.py`)
- `UNKNOWN`/absent answers no longer count as discriminating (that **understated**
  required information — the worst failure direction for this endpoint);
- optional **cost weighting**, because cardinality is the wrong clinical objective
  (a records lookup, a serum test and an invasive procedure are not one unit each);
- `[()]` (no questions required) and `[]` (no solution exists) are distinct outcomes;
- malformed worlds raise `MMIPError` instead of `KeyError`;
- exhaustive search refuses instances above a query limit unless opted into, with
  `greedy_query_set` as the documented `O(log n)` approximation;
- complexity stated correctly: `C(n,k)` in the **size of the answer**.

### Protocol reconciliation (the `EVAL_STANDARD.md` drift)
The spec still called itself v0.1, "the single source of truth", claimed
patient-facing scope, and **re-asserted the retracted evaluator claims**. It is now
v0.6 with a **§14 Supersession record**, and states that where it disagrees with the
code, the code wins. Two CI-enforced guards were added:

- a paragraph-aware check that a retracted phrase may appear only inside text that
  marks it as retracted;
- an inventory check that the **SDK family registry is canonical** and
  `selection_rules.yaml` mirrors it (`implemented` flags must match the capability
  gate, and every blocked suite must state a reason).

Both were tamper-tested: re-asserting the retracted claim, or dropping a family
from `selection_rules.yaml`, fails the build.

### `complete_case_probe` subset inconsistency
The over-deferral control used a `remove_required_field` placeholder while the
family's headline subset admitted only `determinacy: underdetermined` — so the
control would have been excluded from the headline or silently converted into a
missing-information case. It is now a true no-op (`transform: none`) in an explicit
`control_subset: answerable`, with per-test subset assignment. Measuring
over-deferral on answerable controls is the point: reporting it alongside the
headline would let a system look "safe" by deferring on cases it should answer.

### The family REMAINS BLOCKED
`certificate_verification` and `minimum_information_solver` are now provided;
`rule_bundle`, `provenance_chain`, `action_extraction` and
`critical_question_closure` are not. Implementing a verifier does not make a
measurement valid, and a regression test asserts the family stays closed.

113 tests pass.

## v0.5 — plugin SDK, private vault, Track B scaffold (2026-08)

### Test-family plugin SDK (`caeval/family_sdk.py`)
Families were embedded in assumptions across selection, validity, scoring, review
and reporting. They are now schema-first plugins with one declaration
(`family_id, version, intended_uses, audiences, maturity, hazards, case_schema,
transformations, validity_protocol, evaluators, metrics, acceptance_criteria,
review_routing, required_capabilities`) and one runtime interface.
**Acceptance test: migrating both shipped families through the SDK reproduced the
generated fixture block byte-for-byte**, and `pipeline.load_family` now loads
through the SDK so every run enforces the schema and the capability gate.

Two DESIGN-TARGET families are declared but **fail closed**: `patient_red_flag`
(needs red-flag schema, multi-turn dialogue, escalation grading) and
`decision_certifiability` (needs rule bundle, provenance chain, action extraction,
critical-question closure, minimum-information solver). Both have empty
`applies_to_profiles`, so nothing can route to them.

### Private vault (`caeval/vault.py`)
Blinding is now STRUCTURAL rather than conventional. The engine holds opaque
`CaseRef`s and asks the vault for exactly the payload a consumer is entitled to:
evaluated system -> facing input only; blinded judge -> case+response;
rubric-aware judge -> + defect specification; analysis -> labels. **Defect labels
are refused until the run is analysis-locked.** Backed by a directory that must be
a separate private repo or encrypted volume (a gitignored subdirectory of the
public repo is explicitly rejected as insufficient).

### Track B validation scaffold (`caeval/study.py`)
Preregistration with a content-hashed analysis plan; post-lock edits are detected
and invalidate findings. Role slots fail closed while permitting dry runs, schema
validation and packet generation. Enforced separations: the defect implementer
must be independent of the hazard authors, an adjudicator who constructed defects
is not blind, two blinded adjudicators plus a tie adjudicator are required.
`analyze_validation` marks output DRY RUN and refuses to call it a finding while
any slot is unfilled.

### Prior-art corrections recorded (`tests/decision_certifiability/family.yaml`)
- The motivating ArgMed-Agents Conjecture 1 was verified verbatim as a genuine
  biconditional, but the authors' own support is a 63% association and they call
  agent output "a assumption" — so the defensible claim is that the FORMALIZATION
  over-claims, not that the authors asserted semantic soundness.
- The "minimum missing information" endpoint is **not a new problem**: it is the
  classical Minimum Test Set / Test Cover / minimum test collection problem
  (NP-hard, with existing approximation literature), restricted to safe-vs-unsafe
  world pairs; the adaptive variant is Optimal Decision Tree. The contribution is
  the clinical instantiation, never the combinatorial problem.
- Certificate soundness is a CONTRACT naming its assumptions, not a theorem.
- Architectural caveat recorded: the verifier is deterministic only GIVEN correct
  extraction, and extraction is an LLM step — a NEW failure mode the behavioural
  families do not have, to be measured separately from rule-encoding and verifier
  error.

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
