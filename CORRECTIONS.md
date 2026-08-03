# Corrections log

## v0.7 (part 2) — real self-service intake; stale limitation removed (2026-08)

### Demo assumptions were embedded in the user-facing path (blocker)
`plan`, `inspect` and `init` all read a hardcoded `DEMO_TARGET_META`, so a new user
could produce an authoritative-looking evaluation plan that described the **demo**
product rather than theirs. Everything downstream — suite selection, the audience
gate, hazards, report scope — derives from the intake, so a demo intake yields a
confidently wrong plan. This was the single blocker to self-service.

Added `caeval/project.py` + `project init` / `target validate` / `connector test`.
A project is a user-owned versioned directory; `plan`/`inspect` take `--project`
and validate it before anything runs, and say so loudly when falling back to the demo.

**Unanswered is not a default.** Every mandatory intake and governance question
blocks planning while blank — "we didn't ask" and "the answer is no" are different,
and only one is safe. While writing the tests this caught the *same coercion trap*
as `certificate_id=True` in v0.6: `str(None)` is `"None"`, which passed a naive
string check, so `_is_unanswered()` now inspects the raw value first.

**Run modes gate claims.** Five modes each carry a mandatory label; a `mock`
subject can only support `demonstration`, and `calibrated_assessment` /
`procurement_comparison` require ≥2 named clinical reviewers. A run's claim is the
weakest of run mode, conformance level, and family maturity — all enforced in code.

### Stale limitation contradicted the implementation
`limitations.md` still said "**Judges are metadata-informed** — the judge receives
the perturbation type and expected missing evidence… A blinded-judge comparison is
future work." Since v0.3 the default is `blinded`, cued judges are excluded from
the quorum and every headline field, and the cueing gap is reported. The limitation
understated the rigor, but a limitation that contradicts the code is still drift —
readers quote limitations as fact. Corrected and guarded by a test that compares
the text against `DEFAULT_JUDGE_MODE`.

### PRODUCT_V1.md
Defines the product, the two audiences, the run modes, the release gates, and
explicitly what the platform will **never** output (deployment verdict, single
safety score, compliance certificate, buy/do-not-buy). States the central
distinction between **platform evidence** (does the harness detect what it claims?
— currently none) and **product evidence** (how did this product do? — available
today), and records that the onboarding acceptance test is only partially met.

144 tests pass.

## v0.7 — four fail-open paths closed (external review of v0.6, 2026-08)

An external review of `b4aa97a` found four P0 fail-opens and three P1 issues. All
seven were reproduced before fixing. **Every P0 could issue a false certificate or
a falsely-complete evaluation plan.**

### P0-1 — an ABSENT checklist was treated as an EMPTY one
`verify_certificate` coerced a missing `critical_questions` / `contraindications`
to `[]`, so a certificate could **omit the entire contraindication checklist and
still CERTIFY**. `certificate_id` was never checked at all. The schema declared
these required; the verifier did not enforce it.

Fixed: `REQUIRED_TOP_LEVEL` presence is enforced in-code (stdlib, no optional
dependency), an explicit `None` is no longer coerced to `[]`, and `certificate_id`
must be a non-empty **string** (`str(True)` is `"True"`, which a naive truthiness
check accepted). An **empty** checklist remains valid — "ran, none applicable" is
different from "never ran".

### P0-2 — an invalid severity was reported but not escalated
`_check_vocab` emitted `UNRECOGNIZED_SEVERITY` without setting `block` or `defer`,
so a **passing** check with `severity: "urgent"` still CERTIFIED. The existing
tests only varied severity on *present*/*failed* checks, which already blocked via
`certificate_effect` — so the malformed-but-passing path was untested.

Fixed: unrecognized severity now DEFERs (severity is metadata, not the verdict
axis; a present contraindication still BLOCKs independently via
`certificate_effect`). Tested across 8 invalid values on passing checks, plus a
no-over-blocking test on all four valid severities.

### P0-3 — suite selection failed OPEN on an unreadable family
`except Exception: continue` left a missing, corrupt or unparsable family
**marked runnable** — a broken test definition produced a *more* complete-looking
evaluation plan. Fixed to fail closed via the canonical SDK loader, with the parse
error surfaced as the `blocked_reason`. Verified with a deliberately corrupt YAML.

### P0-4 — the vault was an API convention, not a boundary
`ROLE_ENTITLEMENTS` existed but `authorize()` was never called on any payload path,
and `reveal_labels` trusted a caller-supplied flag plus a mutable `analysis_locked`
field. Fixed: every payload access requires an `AccessContext` (role, actor, run,
token) that is authorized, token-checked and **audited** to an append-only
`audit.log.jsonl`; a `blinded_judge` cannot obtain a rubric payload whatever mode it
requests; and `reveal_labels` now requires the **current protocol lock hash** and
refuses on mismatch — a changed plan invalidates findings rather than silently
revealing labels. Tokens are never echoed in `repr`.

This is a fail-closed, audited boundary — **not** hospital-grade IAM, and the code
says so.

### P1 — three more
- **README drift returned.** The retracted high-sensitivity/low-specificity claim
  reappeared in `README.md`, and the layout still called `EVAL_STANDARD.md` "the
  source of truth". Both fixed, and the guard now scans **all public docs**
  (`*.md`, `tests/**/*.yaml`, `prompts/`, `configs/`, `schemas/`, `pyproject.toml`,
  `selection_rules.yaml`). The broadened guard immediately found a **third**
  instance in `configs/judge_panel.toml` — which is exactly why the narrow scan was
  insufficient.
- **MMIP coerced the safety label.** `bool(world["safe"])` made `"false"` → `True`,
  silently reclassifying an unsafe world as safe. Now requires a real `bool`.
- **Packaging was checkout-only.** `packages = ["caeval", "targets"]` omitted
  `caeval.certificates` from a wheel, and data files lived outside the package.
  Now uses find-based discovery, bundles data into `caeval/_data`
  (`tools/bundle_data.py`), makes `repo_root()` resolve checkout **or** installed
  layout, and adds a **CI job that builds a wheel, installs it into a clean venv,
  and runs the documented CLI from a directory containing no source**. Verified
  locally end to end.

123 tests pass.

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
